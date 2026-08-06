from collections.abc import Callable, Mapping

from dynsteer.adapter.agentcompass.runtime import AgentCompassTaskRecord, load_task_records
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import JsonObject, TaskCase

_STATUSES = frozenset({"completed", "run_error", "eval_error", "run_error_or_eval_error", "skipped"})


def adapt_record_case(
    benchmark: str,
    config: HarnessRunConfig,
    case_id: str,
    builder: Callable[[AgentCompassTaskRecord], TaskCase],
) -> TaskCase:
    """查找 AgentCompass record 并构造 TaskCase。"""
    if config is None or not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("config 和 case_id 不能为空")
    try:
        return builder(load_task_records(benchmark, config)[case_id])
    except KeyError as exc:
        raise KeyError(f"AgentCompass case 不存在: {benchmark}/{case_id}") from exc


def task_case_from_record(
    record: AgentCompassTaskRecord,
    *,
    benchmark: str,
    description: str,
    metadata: JsonObject,
) -> TaskCase:
    """使用 AgentCompass 公共字段构造 TaskCase。"""
    return TaskCase(
        task_id=f"{benchmark}::{record.task_id}",
        case_id=record.task_id,
        task_description=description,
        metadata={"benchmark": benchmark, "category": record.category, **metadata},
    )


def validated_attempt(detail: Mapping[str, object]) -> tuple[Mapping[str, object], Mapping[str, object], str]:
    """校验公共 detail、attempt、evaluation 结构并返回状态。"""
    if not isinstance(detail, Mapping):
        raise TypeError("detail 必须是对象")
    attempt = detail.get("attempt")
    if not isinstance(attempt, Mapping):
        raise TypeError("detail.attempt 必须是对象")
    evaluation = attempt.get("evaluation")
    if not isinstance(evaluation, Mapping):
        raise TypeError("attempt.evaluation 必须是对象")
    status = attempt.get("status")
    if not isinstance(status, str) or status not in _STATUSES:
        raise ValueError(f"AgentCompass status 不合法: {status}")
    return attempt, evaluation, status


def status_flags(status: str) -> JsonObject:
    """返回 AgentCompass 公共错误状态标志。"""
    return {
        "run_error": status in {"run_error", "run_error_or_eval_error"},
        "eval_error": status in {"eval_error", "run_error_or_eval_error"},
    }
