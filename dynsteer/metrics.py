from contextvars import ContextVar, Token
from collections.abc import Sequence
from dynsteer.model import (
    EventType,
    EvaluationTerminationState,
    JsonObject,
    LLMCallMetrics,
    RuntimeMetricsRecorder,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.harness.model import HarnessAdvanceResult


_CURRENT_RECORDER: ContextVar[RuntimeMetricsRecorder | None] = ContextVar("dynsteer_runtime_metrics", default=None)

def activate_runtime_metrics_recorder(recorder: RuntimeMetricsRecorder) -> Token[RuntimeMetricsRecorder | None]:
    """激活指定 recorder，并返回用于恢复上下文的 token。"""
    return _CURRENT_RECORDER.set(recorder)

def reset_runtime_metrics_recorder(token: Token[RuntimeMetricsRecorder | None]) -> None:
    """恢复 metrics recorder 上下文。"""
    _CURRENT_RECORDER.reset(token)

def current_runtime_metrics_recorder() -> RuntimeMetricsRecorder | None:
    """返回当前上下文中的运行统计 recorder。"""
    return _CURRENT_RECORDER.get()

def build_runtime_metrics(*, started_monotonic: float, finished_monotonic: float, started_at: str, finished_at: str, trajectory: Trajectory, llm_calls: list[LLMCallMetrics], agent_step_count: int) -> JsonObject:
    """聚合 trajectory 与 LLM 调用，生成运行统计 JSON。"""
    if agent_step_count < 0:
        raise ValueError("agent_step_count 不能为负数")
    elapsed_seconds = max(float(finished_monotonic) - float(started_monotonic), 0.0)
    raw_step_count = len(trajectory.steps)
    snapshot_count = len(trajectory.snapshots)
    tool_call_count = sum((1 for step in trajectory.steps if step.tool_call is not None or step.event_type == EventType.TOOL_CALL))
    token_summary = _trajectory_token_summary(trajectory.steps)
    trajectory_latency = [step.cost.latency_ms for step in trajectory.steps]
    trajectory_latency_available = any((value is not None for value in trajectory_latency))
    execution_records = _execution_timing_records(trajectory)
    unattributed_execution_seconds = _unattributed_execution_seconds(trajectory)
    raw_execution_records = trajectory.raw.get("execution_timing")
    execution_timing_available = _timing_schema_version(trajectory) >= 1 and (
        not trajectory.steps
        or (
            isinstance(raw_execution_records, list)
            and len(raw_execution_records) == len(execution_records)
            and _execution_timing_records_valid(execution_records)
            and all(isinstance(step.cost.latency_ms, int) and step.cost.latency_ms >= 0 for step in trajectory.steps)
        )
    )
    execution_total_latency_ms = sum(
        int(record["latency_ms"])
        for record in execution_records
        if isinstance(record.get("latency_ms"), int)
    )
    execution_total_latency_ms += int(round(unattributed_execution_seconds * 1000))
    llm_prompt_tokens = _sum_optional_int([call.prompt_tokens for call in llm_calls])
    llm_completion_tokens = _sum_optional_int([call.completion_tokens for call in llm_calls])
    llm_total_tokens = _sum_optional_int([call.total_tokens for call in llm_calls])
    metrics = {
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": elapsed_seconds,
        "step_count": agent_step_count,
        "raw_step_count": raw_step_count,
        "snapshot_count": snapshot_count,
        "tool_call_count": tool_call_count,
        "trajectory_total_tokens": token_summary["tokens"] or 0,
        "trajectory_total_latency_ms": _sum_optional_int(trajectory_latency) or 0,
        "trajectory_cost_available": token_summary["available"],
        "trajectory_token_value_count": token_summary["value_count"],
        "trajectory_token_coverage": token_summary["coverage"],
        "trajectory_latency_available": trajectory_latency_available,
        "timing_schema_version": _timing_schema_version(trajectory),
        "execution_timing_available": execution_timing_available,
        "execution_batch_count": len(execution_records),
        "execution_total_latency_ms": execution_total_latency_ms,
        "execution_total_seconds": execution_total_latency_ms / 1000,
        "unattributed_execution_seconds": unattributed_execution_seconds,
        "llm_call_count": len(llm_calls),
        "llm_failed_call_count": sum((1 for call in llm_calls if not call.success)),
        "llm_prompt_tokens": llm_prompt_tokens,
        "llm_completion_tokens": llm_completion_tokens,
        "llm_total_tokens": llm_total_tokens,
        "llm_calls": [call.to_dict() for call in llm_calls],
    }
    return metrics


def summarize_llm_calls(llm_calls: list[LLMCallMetrics]) -> JsonObject:
    """汇总一组 LLM 调用的耗时、失败数与 token 可用性。"""
    if llm_calls is None:
        raise ValueError("llm_calls 不能为空")
    if not llm_calls:
        prompt = completion = total = 0
        token_available = True
    else:
        prompt = _sum_optional_int([call.prompt_tokens for call in llm_calls])
        completion = _sum_optional_int([call.completion_tokens for call in llm_calls])
        total = _sum_optional_int([call.total_tokens for call in llm_calls])
        token_available = all(call.total_tokens is not None for call in llm_calls)
    return {
        "llm_call_count": len(llm_calls),
        "llm_failed_call_count": sum(not call.success for call in llm_calls),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "token_available": token_available,
        "llm_elapsed_seconds": sum(max(float(call.elapsed_seconds), 0.0) for call in llm_calls),
    }


def prefix_trajectory_cost(trajectory: Trajectory, stop_step_index: int | None = None) -> JsonObject:
    """按 raw step 边界汇总 trajectory 前缀 token。"""
    if trajectory is None:
        raise ValueError("trajectory 不能为空")
    steps = [step for step in trajectory.steps if stop_step_index is None or step.index <= stop_step_index]
    summary = _trajectory_token_summary(steps)
    return {
        "tokens": summary["tokens"],
        "token_value_count": summary["value_count"],
        "token_available": summary["available"],
        "token_coverage": summary["coverage"],
        "scanned_step_count": len(steps),
        "reason": None if summary["available"] else "trajectory step token 缺失",
    }


def _trajectory_token_summary(steps: Sequence[TrajectoryStep]) -> JsonObject:
    """聚合一组 trajectory steps 的 token 可用性。"""
    values = [step.cost.tokens for step in steps]
    count = sum(value is not None for value in values)
    return {
        "tokens": _sum_optional_int(values),
        "value_count": count,
        "available": count == len(values) if values else True,
        "coverage": count / len(values) if values else 1.0,
    }


def append_execution_timing(trajectory: Trajectory, advance: HarnessAdvanceResult) -> None:
    """将一个 advance batch 的耗时均匀分摊到其 raw steps。

    入参为待写入的 trajectory 和批次结果；无 raw step 的耗时记入未归属秒数，
    有 raw step 时更新 ``StepCost.latency_ms`` 与 ``execution_timing`` record。
    """
    if trajectory is None or advance is None:
        raise ValueError("trajectory 和 advance 不能为空")
    latency_ms = advance.execution_latency_ms
    if latency_ms is None:
        return
    records = _execution_timing_records(trajectory)
    if not advance.steps:
        trajectory.raw["timing_schema_version"] = 1
        trajectory.raw["execution_timing"] = records
        trajectory.raw["unattributed_execution_seconds"] = (
            _unattributed_execution_seconds(trajectory) + latency_ms / 1000
        )
        return
    step_count = len(advance.steps)
    base, remainder = divmod(latency_ms, step_count)
    for position, step in enumerate(advance.steps):
        value = base + (1 if position < remainder else 0)
        step.cost.latency_ms = value
    batch_index = len(records)
    step_indices = [step.index for step in advance.steps]
    records.append(
        {
            "batch_index": batch_index,
            "step_indices": step_indices,
            "first_step_index": step_indices[0],
            "last_step_index": step_indices[-1],
            "raw_step_count": step_count,
            "latency_ms": latency_ms,
            "allocation": "uniform_ms_with_leading_remainder",
        }
    )
    trajectory.raw["timing_schema_version"] = 1
    trajectory.raw["execution_timing"] = records


def prefix_execution_timing(trajectory: Trajectory, stop_step_index: int | None = None) -> JsonObject:
    """计算 source trajectory 在指定边界前的执行耗时。

    返回毫秒、秒数、扫描 step/batch 数和 timing 完整可用标记；历史或不完整
    轨迹返回不可用，而不是伪造零耗时。
    """
    if trajectory is None:
        raise ValueError("trajectory 不能为空")
    steps = sorted(trajectory.steps, key=lambda step: step.index)
    target_steps = [step for step in steps if stop_step_index is None or step.index <= stop_step_index]
    records = _execution_timing_records(trajectory)
    raw_records = trajectory.raw.get("execution_timing")
    records_valid = (
        isinstance(raw_records, list)
        and len(raw_records) == len(records)
        and _execution_timing_records_valid(records)
    )
    record_steps = {
        step_index
        for record in records
        for step_index in record.get("step_indices", [])
        if isinstance(step_index, int)
    }
    available = _timing_schema_version(trajectory) >= 1 and records_valid and all(
        step.index in record_steps and isinstance(step.cost.latency_ms, int) and step.cost.latency_ms >= 0
        for step in target_steps
    )
    if not available:
        return {
            "latency_ms": None,
            "seconds": None,
            "scanned_step_count": len(target_steps),
            "batch_count": None,
            "timing_available": False,
            "reason": "execution_timing 缺失或无法覆盖目标前缀",
        }
    involved = {
        record.get("batch_index")
        for record in records
        if any(step.index in record.get("step_indices", []) for step in target_steps)
    }
    latency = sum(int(step.cost.latency_ms) for step in target_steps)
    return {
        "latency_ms": latency,
        "seconds": latency / 1000,
        "scanned_step_count": len(target_steps),
        "batch_count": len(involved),
        "timing_available": True,
        "reason": None,
    }


def build_replay_timing_metrics(
    source_trajectory: Trajectory,
    replay_trajectory: Trajectory,
    termination: EvaluationTerminationState,
    elapsed_seconds: float,
) -> JsonObject:
    """构造 replay 墙钟、DEFAULT 前缀与有效总耗时指标。

    ``elapsed_seconds`` 保持 replay evaluator 墙钟语义；只有前缀 timing 可解释
    时才计算 ``effective_elapsed_seconds``。
    """
    if source_trajectory is None or replay_trajectory is None or termination is None:
        raise ValueError("source_trajectory、replay_trajectory 和 termination 不能为空")
    detail = termination.termination_detail if isinstance(termination.termination_detail, dict) else {}
    stop_index = detail.get("virtual_stop_step_index") if termination.should_stop else None
    stop_index = stop_index if isinstance(stop_index, int) and not isinstance(stop_index, bool) else None
    elapsed = max(float(elapsed_seconds), 0.0)
    if termination.should_stop and stop_index is None:
        full = prefix_execution_timing(source_trajectory, None)
        return {
            "elapsed_seconds": elapsed,
            "default_prefix_execution_latency_ms": None,
            "default_prefix_execution_seconds": None,
            "effective_elapsed_seconds": None,
            "timing_available": False,
            "timing_unavailable_reason": "virtual stop 缺少 virtual_stop_step_index",
            "virtual_stop_step_index": None,
            "source_execution_batch_count": len(_execution_timing_records(source_trajectory)),
            "prefix_execution_batch_count": None,
            "source_full_execution_seconds": full.get("seconds") if full.get("timing_available") else None,
        }
    prefix = prefix_execution_timing(source_trajectory, stop_index)
    prefix_cost = prefix_trajectory_cost(source_trajectory, stop_index)
    full = prefix_execution_timing(source_trajectory, None)
    available = bool(prefix.get("timing_available"))
    prefix_seconds = prefix.get("seconds") if available else None
    return {
        "elapsed_seconds": elapsed,
        "default_prefix_execution_latency_ms": prefix.get("latency_ms") if available else None,
        "default_prefix_execution_seconds": prefix_seconds,
        "effective_elapsed_seconds": elapsed + float(prefix_seconds) if available and prefix_seconds is not None else None,
        "timing_available": available,
        "timing_unavailable_reason": prefix.get("reason") if not available else None,
        "virtual_stop_step_index": stop_index,
        "source_execution_batch_count": len(_execution_timing_records(source_trajectory)),
        "prefix_execution_batch_count": prefix.get("batch_count") if available else None,
        "source_full_execution_seconds": full.get("seconds") if full.get("timing_available") else None,
        "default_prefix_trajectory_tokens": prefix_cost.get("tokens"),
        "prefix_token_available": prefix_cost.get("token_available"),
        "prefix_token_coverage": prefix_cost.get("token_coverage"),
        "prefix_token_unavailable_reason": prefix_cost.get("reason"),
    }


def _execution_timing_records(trajectory: Trajectory) -> list[dict[str, object]]:
    records = trajectory.raw.get("execution_timing")
    return [record for record in records if isinstance(record, dict)] if isinstance(records, list) else []


def _execution_timing_records_valid(records: list[dict[str, object]]) -> bool:
    """校验 timing record 的最小审计字段。"""
    for record in records:
        indices = record.get("step_indices")
        raw_count = record.get("raw_step_count")
        latency = record.get("latency_ms")
        if (
            not isinstance(indices, list)
            or not indices
            or any(isinstance(index, bool) or not isinstance(index, int) for index in indices)
            or not isinstance(raw_count, int)
            or raw_count != len(indices)
            or isinstance(latency, bool)
            or not isinstance(latency, int)
            or latency < 0
        ):
            return False
    return True


def _timing_schema_version(trajectory: Trajectory) -> int:
    value = trajectory.raw.get("timing_schema_version")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _unattributed_execution_seconds(trajectory: Trajectory) -> float:
    value = trajectory.raw.get("unattributed_execution_seconds", 0.0)
    return float(value) if isinstance(value, (int, float)) else 0.0

def _sum_optional_int(values: list[int | None]) -> int | None:
    total = 0
    has_value = False
    for value in values:
        if value is None:
            continue
        total += int(value)
        has_value = True
    return total if has_value else None
