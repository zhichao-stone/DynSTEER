from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Iterator

from dynsteer.model import EventType, JsonObject, Trajectory


@dataclass(frozen=True)
class LLMCallMetrics:
    """单次 LLM provider 调用统计。"""

    provider: str
    model: str
    elapsed_seconds: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    success: bool = True
    error: str | None = None

    def to_dict(self) -> JsonObject:
        """转换为 JSON 可序列化字典。"""
        return {
            "provider": self.provider,
            "model": self.model,
            "elapsed_seconds": self.elapsed_seconds,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "success": self.success,
            "error": self.error,
        }


@dataclass
class RuntimeMetricsRecorder:
    """记录单个 case 评估期间的运行统计。"""

    started_monotonic: float = field(default_factory=time.perf_counter)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    llm_calls: list[LLMCallMetrics] = field(default_factory=list)

    def record_llm_call(self, call: LLMCallMetrics) -> None:
        """记录一次 LLM provider 调用。"""
        if call is None:
            raise ValueError("call 不能为空")
        self.llm_calls.append(call)


_CURRENT_RECORDER: ContextVar[RuntimeMetricsRecorder | None] = ContextVar(
    "dynsteer_runtime_metrics",
    default=None,
)


@contextmanager
def record_runtime_metrics() -> Iterator[RuntimeMetricsRecorder]:
    """创建并激活当前上下文的运行统计 recorder。"""
    recorder = RuntimeMetricsRecorder()
    token = activate_runtime_metrics_recorder(recorder)
    try:
        yield recorder
    finally:
        reset_runtime_metrics_recorder(token)


def activate_runtime_metrics_recorder(recorder: RuntimeMetricsRecorder) -> Token[RuntimeMetricsRecorder | None]:
    """激活指定 recorder，并返回用于恢复上下文的 token。"""
    if recorder is None:
        raise ValueError("recorder 不能为空")
    return _CURRENT_RECORDER.set(recorder)


def reset_runtime_metrics_recorder(token: Token[RuntimeMetricsRecorder | None]) -> None:
    """恢复 metrics recorder 上下文。"""
    if token is None:
        raise ValueError("token 不能为空")
    _CURRENT_RECORDER.reset(token)


def current_runtime_metrics_recorder() -> RuntimeMetricsRecorder | None:
    """返回当前上下文中的运行统计 recorder。"""
    return _CURRENT_RECORDER.get()


def build_runtime_metrics(
    *,
    started_monotonic: float,
    finished_monotonic: float,
    started_at: str,
    finished_at: str,
    trajectory: Trajectory,
    llm_calls: list[LLMCallMetrics],
) -> JsonObject:
    """聚合 trajectory 与 LLM 调用，生成运行统计 JSON。"""
    if trajectory is None or llm_calls is None:
        raise ValueError("trajectory 和 llm_calls 不能为空")
    elapsed_seconds = max(float(finished_monotonic) - float(started_monotonic), 0.0)
    step_count = len(trajectory.steps)
    snapshot_count = len(trajectory.snapshots)
    tool_call_count = sum(
        1
        for step in trajectory.steps
        if step.tool_call is not None or step.event_type == EventType.TOOL_CALL
    )
    trajectory_tokens = [step.cost.tokens for step in trajectory.steps]
    trajectory_latency = [step.cost.latency_ms for step in trajectory.steps]
    llm_prompt_tokens = _sum_optional_int([call.prompt_tokens for call in llm_calls])
    llm_completion_tokens = _sum_optional_int([call.completion_tokens for call in llm_calls])
    llm_total_tokens = _sum_optional_int([call.total_tokens for call in llm_calls])
    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": elapsed_seconds,
        "step_count": step_count,
        "snapshot_count": snapshot_count,
        "tool_call_count": tool_call_count,
        "trajectory_total_tokens": _sum_optional_int(trajectory_tokens) or 0,
        "trajectory_total_latency_ms": _sum_optional_int(trajectory_latency) or 0,
        "llm_call_count": len(llm_calls),
        "llm_failed_call_count": sum(1 for call in llm_calls if not call.success),
        "llm_prompt_tokens": llm_prompt_tokens,
        "llm_completion_tokens": llm_completion_tokens,
        "llm_total_tokens": llm_total_tokens,
        "llm_calls": [call.to_dict() for call in llm_calls],
    }


def _sum_optional_int(values: list[int | None]) -> int | None:
    total = 0
    has_value = False
    for value in values:
        if value is None:
            continue
        total += int(value)
        has_value = True
    return total if has_value else None
