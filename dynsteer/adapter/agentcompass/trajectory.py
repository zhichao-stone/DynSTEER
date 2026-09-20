from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import datetime

from dynsteer.model import (
    Actor,
    EventType,
    JsonObject,
    StepCost,
    ToolCall,
    ToolResult,
    TrajectoryStep,
)

logger = logging.getLogger(__name__)


def convert_actf_steps(
    trajectory_data: object,
    *,
    benchmark: str,
    case_id: str,
) -> list[TrajectoryStep]:
    """将 ACTF v1.0 轨迹单次线性转换为 DynSTEER 步骤。"""
    if not benchmark or not case_id:
        raise ValueError("benchmark 和 case_id 不能为空")
    if trajectory_data is None:
        return []
    if not isinstance(trajectory_data, Mapping):
        raise TypeError(f"ACTF trajectory 必须是对象: {benchmark}/{case_id}")
    native_steps = trajectory_data.get("steps")
    if not isinstance(native_steps, list):
        raise TypeError(f"ACTF trajectory.steps 必须是数组: {benchmark}/{case_id}")

    output: list[TrajectoryStep] = []
    call_count = 0
    observation_count = 0
    for native_index, native_step in enumerate(native_steps):
        if not isinstance(native_step, Mapping):
            raise TypeError(f"ACTF step 必须是对象: {benchmark}/{case_id}/{native_index}")
        native_step_id = native_step.get("step_id", native_index)
        timestamp = _timestamp(native_step.get("started_at"))

        # 首个 step 的 system/user 内容属于任务初始输入，不重复写入轨迹。
        user_content = _text(native_step.get("user_content"))
        if native_index > 0 and user_content:
            index = len(output)
            output.append(TrajectoryStep(
                step_id=f"{benchmark}::{case_id}::step::{index}",
                index=index,
                actor=Actor.USER,
                recipient=Actor.AGENT,
                event_type=EventType.MESSAGE,
                timestamp=timestamp,
                content=user_content,
                cost=StepCost(tokens=0, latency_ms=0),
                raw={"actf_step_id": str(native_step_id)},
            ))

        assistant = native_step.get("assistant_content")
        assistant = assistant if isinstance(assistant, Mapping) else {}
        content = _text(assistant.get("content"))
        reasoning = _text(assistant.get("reasoning_content"))
        calls = _tool_calls(assistant.get("tool_calls"), benchmark, case_id, native_index)
        observations = _observations(native_step.get("observation"), benchmark, case_id, native_index)
        call_count += len(calls)
        observation_count += len(observations)

        metric = native_step.get("metric")
        metric = metric if isinstance(metric, Mapping) else {}
        tokens, llm_latency, env_latency = _cost(metric, benchmark, case_id, native_index)
        derived_tokens = 0 if tokens is not None else None
        derived_llm_latency = 0 if llm_latency is not None else None
        stop_reason = _text(metric.get("stop_reason"))

        call_ids: list[str] = []
        call_payloads: list[tuple[str, str, JsonObject, bool]] = []
        for call_index, call in enumerate(calls):
            function = call.get("function")
            if not isinstance(function, Mapping):
                raise TypeError(f"ACTF tool call.function 必须是对象: {benchmark}/{case_id}/{native_index}")
            name = function.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"ACTF tool call name 不能为空: {benchmark}/{case_id}/{native_index}")
            native_call_id = call.get("id")
            generated = not isinstance(native_call_id, str) or not native_call_id.strip()
            call_id = f"actf:{native_step_id}:call:{call_index}" if generated else native_call_id.strip()
            if call_id in call_ids:
                raise ValueError(f"ACTF 同一步骤包含重复 tool call ID: {call_id}")
            call_ids.append(call_id)
            call_payloads.append((call_id, name.strip(), _arguments(function.get("arguments"), benchmark, case_id), generated))

        # 同一 ACTF step 先输出全部并行 outbound，再输出对应 observation。
        for call_index, (call_id, name, arguments, generated) in enumerate(call_payloads):
            raw: JsonObject = {
                "actf_step_id": str(native_step_id),
                "actf_call_index": call_index,
                "openai_tool_call_id": call_id,
            }
            if generated:
                raw["generated_call_id"] = True
            if call_index == 0:
                if content:
                    raw["assistant_content"] = content
                if reasoning:
                    raw["reasoning_content"] = reasoning
                if stop_reason:
                    raw["stop_reason"] = stop_reason
            index = len(output)
            output.append(TrajectoryStep(
                step_id=f"{benchmark}::{case_id}::step::{index}",
                index=index,
                actor=Actor.AGENT,
                recipient=Actor.ENVIRONMENT,
                event_type=EventType.TOOL_CALL,
                timestamp=timestamp,
                tool_call=ToolCall(name=name, arguments=arguments),
                cost=StepCost(
                    tokens=tokens if call_index == 0 else derived_tokens,
                    latency_ms=llm_latency if call_index == 0 else derived_llm_latency,
                ),
                raw=raw,
            ))

        pairings: dict[int, int] = {}
        used_calls: set[int] = set()
        for observation_index, observation in enumerate(observations):
            observation_id = _observation_id(observation)
            if observation_id is not None and observation_id in call_ids:
                call_index = call_ids.index(observation_id)
                if call_index not in used_calls:
                    pairings[observation_index] = call_index
                    used_calls.add(call_index)
        if len(calls) == len(observations):
            for observation_index, observation in enumerate(observations):
                if observation_index in pairings or _observation_id(observation) is not None:
                    continue
                if observation_index not in used_calls:
                    pairings[observation_index] = observation_index
                    used_calls.add(observation_index)

        for observation_index, observation in enumerate(observations):
            call_index = pairings.get(observation_index)
            raw = {"actf_step_id": str(native_step_id)}
            is_first_environment_pair = bool(pairings) and observation_index == min(pairings)
            if call_index is not None:
                call_id = call_ids[call_index]
                raw["openai_tool_call_id"] = call_id
                if env_latency is not None and is_first_environment_pair:
                    if len(calls) > 1:
                        raw["aggregate_env_latency"] = True
                    latency = env_latency
                elif env_latency is not None:
                    latency = 0
                else:
                    latency = None
                success, exception, result_content = _observation_result(observation)
                index = len(output)
                output.append(TrajectoryStep(
                    step_id=f"{benchmark}::{case_id}::step::{index}",
                    index=index,
                    actor=Actor.ENVIRONMENT,
                    recipient=Actor.AGENT,
                    event_type=EventType.TOOL_RESULT,
                    timestamp=timestamp,
                    tool_result=ToolResult(success=success, content=result_content, exception=exception),
                    cost=StepCost(tokens=0, latency_ms=latency),
                    raw=raw,
                ))
            else:
                raw["unpaired_observation"] = True
                if env_latency is not None:
                    raw["non_leading_environment_latency_excluded"] = True
                index = len(output)
                output.append(TrajectoryStep(
                    step_id=f"{benchmark}::{case_id}::step::{index}",
                    index=index,
                    actor=Actor.ENVIRONMENT,
                    recipient=Actor.AGENT,
                    event_type=EventType.MESSAGE,
                    timestamp=timestamp,
                    content=_text(observation),
                    cost=StepCost(tokens=0, latency_ms=0 if env_latency is not None else None),
                    raw=raw,
                ))

        if not calls and content:
            raw = {"actf_step_id": str(native_step_id)}
            if reasoning:
                raw["reasoning_content"] = reasoning
            if stop_reason:
                raw["stop_reason"] = stop_reason
            index = len(output)
            output.append(TrajectoryStep(
                step_id=f"{benchmark}::{case_id}::step::{index}",
                index=index,
                actor=Actor.AGENT,
                recipient=Actor.USER,
                event_type=EventType.FINAL,
                timestamp=timestamp,
                content=content,
                cost=StepCost(tokens=tokens, latency_ms=llm_latency),
                raw=raw,
            ))

    logger.info(
        "agentcompass_actf_converted",
        extra={"事件": "ACTF轨迹转换完成", "benchmark": benchmark, "case_id": case_id, "step_count": len(output), "call_count": call_count, "observation_count": observation_count},
    )
    return output


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _tool_calls(value: object, benchmark: str, case_id: str, step_index: int) -> list[Mapping[str, object]]:
    if value is None or value == {}:
        return []
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise ValueError(f"ACTF tool_calls 必须是对象数组: {benchmark}/{case_id}/{step_index}")
    return value


def _observations(value: object, benchmark: str, case_id: str, step_index: int) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"ACTF observation 必须是数组: {benchmark}/{case_id}/{step_index}")
    return value


def _arguments(value: object, benchmark: str, case_id: str) -> JsonObject:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"ACTF tool arguments 不是合法 JSON: {benchmark}/{case_id}") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"ACTF tool arguments 必须是 JSON 对象: {benchmark}/{case_id}")


def _timestamp(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def _cost(metric: Mapping[str, object], benchmark: str, case_id: str, step_index: int) -> tuple[int | None, int | None, int | None]:
    prompt = metric.get("prompt_tokens_len")
    completion = metric.get("completion_tokens_len")
    for name, value in (("prompt_tokens_len", prompt), ("completion_tokens_len", completion)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError(f"ACTF {name} 必须是非负整数: {benchmark}/{case_id}/{step_index}")
    tokens = int(prompt) + int(completion) if prompt is not None and completion is not None else None

    latencies: list[int | None] = []
    for name in ("llm_infer_ms", "env_action_ms"):
        value = metric.get(name)
        if value is None:
            latencies.append(None)
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"ACTF {name} 必须是非负数字: {benchmark}/{case_id}/{step_index}")
        else:
            latencies.append(max(0, round(value)))
    return tokens, latencies[0], latencies[1]


def _observation_id(observation: object) -> str | None:
    if not isinstance(observation, Mapping):
        return None
    for key in ("tool_call_id", "call_id", "id"):
        value = observation.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _observation_result(observation: object) -> tuple[bool, str | None, object]:
    if not isinstance(observation, Mapping):
        return True, None, observation
    explicit_success = observation.get("success")
    error = observation.get("error") or observation.get("exception")
    success = explicit_success if isinstance(explicit_success, bool) else not bool(error)
    exception = _text(error) or None
    content = observation.get("content", observation.get("output", observation))
    return success, exception, content
