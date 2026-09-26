from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol

from openai import OpenAI

from dynsteer.model import (
    Actor,
    EventType,
    JsonObject,
    StepCost,
    ToolCall,
    ToolResult,
    TrajectoryStep,
)


OUTPUT_LIMIT_CHARS = 64 * 1024
BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Run one shell command in the task container",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
    },
}


@dataclass(frozen=True)
class OpenAIClientConfig:
    model: str
    api_key: str
    base_url: str
    timeout_seconds: float = 120.0
    max_retries: int = 3
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 8.0

    @classmethod
    def from_mapping(cls, data: object, label: str) -> "OpenAIClientConfig":
        """把实验 JSON 中的 client 对象转换为 Agent 客户端配置。"""
        if not isinstance(data, Mapping):
            raise ValueError(f"{label} 必须是 JSON 对象")
        try:
            config = cls(
                model=str(data["model"]),
                api_key=str(data["api_key"]),
                base_url=str(data["base_url"]),
                timeout_seconds=float(data.get("timeout_seconds", 120.0)),
                max_retries=int(data.get("max_retries", 3)),
                retry_base_seconds=float(data.get("retry_base_seconds", 1.0)),
                retry_max_seconds=float(data.get("retry_max_seconds", 8.0)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{label} 不是合法的 Agent client 配置") from exc
        if not config.model.strip() or not config.api_key.strip() or not config.base_url.strip():
            raise ValueError(f"{label} 的 model/api_key/base_url 不能为空")
        if config.timeout_seconds <= 0 or config.max_retries < 1:
            raise ValueError(f"{label} 的 timeout/max_retries 超出范围")
        return config


@dataclass(frozen=True)
class ToolExecutionResult:
    command: str
    stdout: str
    stderr: str
    exit_code: int
    latency_ms: int
    timed_out: bool = False
    error_type: str | None = None


class ToolExecutor(Protocol):
    def execute(self, command: str, timeout_seconds: int | None = None) -> ToolExecutionResult:
        """执行一条 bash 命令并返回原样结果。"""


@dataclass
class ToolAgentResult:
    steps: list[TrajectoryStep]
    stop_reason: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    error_type: str | None = None
    error_message: str | None = None


def run_tool_agent(
    *,
    task_prompt: str,
    system_prompt: str,
    client_config: OpenAIClientConfig,
    executor: ToolExecutor,
    max_tool_calls: int,
    temperature: float,
) -> ToolAgentResult:
    """在 DynSTEER 内执行 OpenAI-compatible bash tool agent。"""
    if not task_prompt.strip() or not system_prompt.strip():
        raise ValueError("task_prompt 和 system_prompt 不能为空")
    if executor is None:
        raise ValueError("executor 不能为空")
    if max_tool_calls < 1:
        raise ValueError("max_tool_calls 必须大于 0")

    client = OpenAI(
        api_key=client_config.api_key,
        base_url=client_config.base_url,
        timeout=client_config.timeout_seconds,
        max_retries=0,
    )
    messages: list[dict[str, object]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task_prompt},
    ]
    steps: list[TrajectoryStep] = []
    totals: dict[str, int | None] = {"prompt": None, "completion": None, "total": None}
    for _ in range(max_tool_calls):
        try:
            response = _request_chat(client, client_config, messages, temperature)
        except Exception as exc:
            error = _error_step(len(steps), exc)
            steps.append(error)
            return ToolAgentResult(
                steps=steps,
                stop_reason="model_request_failed",
                prompt_tokens=totals["prompt"],
                completion_tokens=totals["completion"],
                total_tokens=totals["total"],
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

        usage = _usage(response)
        totals = _add_usage(totals, usage)
        choice = response.choices[0]
        tool_calls = list(getattr(choice.message, "tool_calls", None) or [])
        if not tool_calls:
            final_text = str(getattr(choice.message, "content", None) or "")
            steps.append(
                _step(
                    index=len(steps),
                    event_type=EventType.FINAL,
                    actor=Actor.AGENT,
                    recipient=Actor.USER,
                    content=final_text,
                    tokens=usage["total"],
                    raw={"usage": _nullable_usage(usage)},
                )
            )
            return _result(steps, "model_finished", totals, None, None)
        if len(tool_calls) != 1:
            error = _step(
                index=len(steps),
                event_type=EventType.ERROR,
                actor=Actor.AGENT,
                recipient=Actor.EVALUATOR,
                content="模型返回了多个 tool call，当前 agent 只允许每轮一个 bash 命令",
                tokens=usage["total"],
                raw={"usage": _nullable_usage(usage), "tool_call_count": len(tool_calls)},
            )
            steps.append(error)
            return _result(steps, "agent_protocol_error", totals, "AgentProtocolError", "multiple tool calls")

        tool_call = tool_calls[0]
        call_id = str(tool_call.id)
        if not call_id:
            error = _step(
                index=len(steps),
                event_type=EventType.ERROR,
                actor=Actor.AGENT,
                recipient=Actor.EVALUATOR,
                content="模型返回了空的 OpenAI tool call id",
                tokens=usage["total"],
                raw={"usage": _nullable_usage(usage), "error_type": "AgentProtocolError"},
            )
            steps.append(error)
            return _result(steps, "agent_protocol_error", totals, "AgentProtocolError", "empty tool call id")
        try:
            command = _tool_command(tool_call)
        except ValueError as exc:
            error = _step(
                index=len(steps),
                event_type=EventType.ERROR,
                actor=Actor.AGENT,
                recipient=Actor.EVALUATOR,
                content=str(exc),
                tokens=usage["total"],
                raw={"usage": _nullable_usage(usage), "error_type": "AgentProtocolError"},
            )
            steps.append(error)
            return _result(steps, "agent_protocol_error", totals, "AgentProtocolError", str(exc))
        if not command.strip():
            steps.append(
                _step(
                    index=len(steps),
                    event_type=EventType.TOOL_CALL,
                    actor=Actor.AGENT,
                    recipient=Actor.ENVIRONMENT,
                    tool_call=ToolCall(name="bash", arguments={"command": command}),
                    tokens=usage["total"],
                    raw={"openai_tool_call_id": call_id, "usage": _nullable_usage(usage)},
                )
            )
            return _result(steps, "empty_command", totals, "AgentProtocolError", "empty bash command")

        steps.append(
            _step(
                index=len(steps),
                event_type=EventType.TOOL_CALL,
                actor=Actor.AGENT,
                recipient=Actor.ENVIRONMENT,
                tool_call=ToolCall(name="bash", arguments={"command": command}),
                tokens=usage["total"],
                raw={"openai_tool_call_id": call_id, "usage": _nullable_usage(usage)},
            )
        )
        execution = executor.execute(command)
        stdout, stdout_raw = _truncate_output(execution.stdout)
        stderr, stderr_raw = _truncate_output(execution.stderr)
        result_content: JsonObject = {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": execution.exit_code,
        }
        steps.append(
            _step(
                index=len(steps),
                event_type=EventType.TOOL_RESULT,
                actor=Actor.ENVIRONMENT,
                recipient=Actor.AGENT,
                tool_result=ToolResult(success=execution.exit_code == 0, content=result_content),
                latency_ms=execution.latency_ms,
                raw={
                    "openai_tool_call_id": call_id,
                    "stdout": stdout_raw,
                    "stderr": stderr_raw,
                    "exit_code": execution.exit_code,
                    "timed_out": execution.timed_out,
                    "execution_error_type": execution.error_type,
                },
            )
        )
        messages.append(
            {
                "role": "assistant",
                "content": getattr(choice.message, "content", None),
                "tool_calls": [tool_call.model_dump()],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(result_content, ensure_ascii=False),
            }
        )

    return _result(steps, "max_tool_calls", totals, None, None)


def _request_chat(
    client: OpenAI,
    config: OpenAIClientConfig,
    messages: list[dict[str, object]],
    temperature: float,
) -> object:
    last_error: Exception | None = None
    for attempt in range(1, config.max_retries + 1):
        try:
            return client.chat.completions.create(
                model=config.model,
                messages=messages,
                tools=[BASH_TOOL],
                tool_choice="auto",
                temperature=temperature,
            )
        except Exception as exc:
            last_error = exc
            if attempt >= config.max_retries:
                break
            delay = min(config.retry_base_seconds * 2 ** (attempt - 1), config.retry_max_seconds)
            if delay:
                time.sleep(delay)
    raise last_error if last_error is not None else RuntimeError("OpenAI request failed")


def _usage(response: object) -> dict[str, int | None]:
    usage = getattr(response, "usage", None)

    def value(name: str) -> int | None:
        raw = getattr(usage, name, None)
        return raw if isinstance(raw, int) and not isinstance(raw, bool) else None

    return {"prompt": value("prompt_tokens"), "completion": value("completion_tokens"), "total": value("total_tokens")}


def _nullable_usage(usage: dict[str, int | None]) -> JsonObject:
    return {
        "prompt_tokens": usage["prompt"],
        "completion_tokens": usage["completion"],
        "total_tokens": usage["total"],
    }


def _add_usage(
    totals: dict[str, int | None],
    usage: dict[str, int | None],
) -> dict[str, int | None]:
    """累计已观测到的 usage；尚未观测到的字段保持 null。"""
    accumulated: dict[str, int | None] = {}
    for key in totals:
        observed = usage[key]
        if observed is None:
            accumulated[key] = totals[key]
        elif totals[key] is None:
            accumulated[key] = observed
        else:
            accumulated[key] = totals[key] + observed
    return accumulated


def _tool_command(tool_call: object) -> str:
    function = getattr(tool_call, "function", None)
    raw_arguments = getattr(function, "arguments", "{}")
    if str(getattr(function, "name", "")) != "bash":
        raise ValueError("agent 只能调用 bash 工具")
    try:
        arguments = json.loads(str(raw_arguments))
    except json.JSONDecodeError as exc:
        raise ValueError("bash tool arguments 不是合法 JSON") from exc
    if not isinstance(arguments, dict):
        raise ValueError("bash tool arguments 必须是 JSON 对象")
    return str(arguments.get("command", ""))


def _truncate_output(value: str) -> tuple[str, JsonObject]:
    original_bytes = len(value.encode("utf-8", errors="replace"))
    truncated = len(value) > OUTPUT_LIMIT_CHARS
    return (
        value[:OUTPUT_LIMIT_CHARS],
        {
            "text": value[:OUTPUT_LIMIT_CHARS],
            "original_bytes": original_bytes,
            "truncated": truncated,
        },
    )


def _step(
    *,
    index: int,
    event_type: EventType,
    actor: Actor,
    recipient: Actor | None,
    content: str | None = None,
    tool_call: ToolCall | None = None,
    tool_result: ToolResult | None = None,
    tokens: int | None = None,
    latency_ms: int | None = None,
    raw: JsonObject | None = None,
) -> TrajectoryStep:
    return TrajectoryStep(
        step_id=f"agent-{index + 1}",
        index=index,
        event_type=event_type,
        actor=actor,
        recipient=recipient,
        timestamp=datetime.now(timezone.utc).isoformat(),
        content=content,
        tool_call=tool_call,
        tool_result=tool_result,
        cost=StepCost(
            tokens=tokens,
            latency_ms=latency_ms,
        ),
        raw=dict(raw or {}),
    )


def _error_step(index: int, exc: Exception) -> TrajectoryStep:
    return _step(
        index=index,
        event_type=EventType.ERROR,
        actor=Actor.AGENT,
        recipient=Actor.EVALUATOR,
        content=str(exc),
        raw={"error_type": type(exc).__name__},
    )


def _result(
    steps: list[TrajectoryStep],
    stop_reason: str,
    usage: dict[str, int | None],
    error_type: str | None,
    error_message: str | None,
) -> ToolAgentResult:
    return ToolAgentResult(
        steps=steps,
        stop_reason=stop_reason,
        prompt_tokens=usage.get("prompt"),
        completion_tokens=usage.get("completion"),
        total_tokens=usage.get("total"),
        error_type=error_type,
        error_message=error_message,
    )
