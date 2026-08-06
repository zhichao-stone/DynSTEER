from collections.abc import Mapping

from dynsteer.adapter.agentcompass.result import status_flags, validated_attempt
from dynsteer.model import JsonObject


def native_result_summary(detail: Mapping[str, object]) -> JsonObject:
    """校验并提取 SWE-bench Pro 原生评分摘要。"""
    attempt, evaluation, status = validated_attempt(detail)
    correct = attempt.get("correct")
    resolved = evaluation.get("resolved")
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
        **status_flags(status),
        "error_present": bool(attempt.get("error_present")) or bool(evaluation.get("error_present")),
        "evaluation_completed": completed,
        "evaluation_timed_out": timed_out,
        "returncode": returncode,
    }
