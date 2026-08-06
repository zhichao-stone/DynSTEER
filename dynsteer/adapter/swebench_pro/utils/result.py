from collections.abc import Mapping

from dynsteer.model import JsonObject

_STATUSES = frozenset({"completed", "run_error", "eval_error", "run_error_or_eval_error", "skipped"})


def native_result_summary(detail: Mapping[str, object]) -> JsonObject:
    """校验并提取 SWE-bench Pro 原生评分摘要。"""
    if not isinstance(detail, Mapping):
        raise TypeError("detail 必须是对象")
    attempt = detail.get("attempt")
    if not isinstance(attempt, Mapping):
        raise TypeError("detail.attempt 必须是对象")
    evaluation = attempt.get("evaluation")
    if not isinstance(evaluation, Mapping):
        raise TypeError("attempt.evaluation 必须是对象")
    status = attempt.get("status")
    correct = attempt.get("correct")
    resolved = evaluation.get("resolved")
    if not isinstance(status, str) or status not in _STATUSES:
        raise ValueError(f"AgentCompass status 不合法: {status}")
    if not isinstance(correct, bool) or not isinstance(resolved, bool) or correct != resolved:
        raise ValueError("SWE-bench Pro correct 与 evaluation.resolved 必须是一致的 bool")
    completed = evaluation.get("completed")
    timed_out = evaluation.get("timed_out")
    returncode = evaluation.get("returncode")
    if not isinstance(completed, bool) or not isinstance(timed_out, bool):
        raise TypeError("SWE-bench Pro completed/timed_out 必须是 bool")
    if returncode is not None and (isinstance(returncode, bool) or not isinstance(returncode, int)):
        raise ValueError("SWE-bench Pro returncode 必须是整数或 None")
    return {
        "status": status,
        "resolved": resolved,
        "run_error": status in {"run_error", "run_error_or_eval_error"},
        "eval_error": status in {"eval_error", "run_error_or_eval_error"},
        "error_present": bool(attempt.get("error_present")) or bool(evaluation.get("error_present")),
        "evaluation_completed": completed,
        "evaluation_timed_out": timed_out,
        "returncode": returncode,
    }
