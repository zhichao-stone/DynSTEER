from collections.abc import Mapping

from dynsteer.model import JsonObject

_STATUSES = frozenset({"completed", "run_error", "eval_error", "run_error_or_eval_error", "skipped"})


def native_result_summary(detail: Mapping[str, object]) -> JsonObject:
    """校验并提取 SkillsBench partial reward 原生摘要。"""
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
    correct = attempt.get("correct")
    if not isinstance(correct, bool):
        raise TypeError("SkillsBench correct 必须是 bool")
    score_value = attempt.get("score")
    if score_value is None:
        if status == "completed":
            raise ValueError("completed SkillsBench 结果必须包含 score")
        score = 0.0
        reward_available = False
    else:
        if isinstance(score_value, bool) or not isinstance(score_value, (int, float)):
            raise ValueError("SkillsBench score 必须是数字且不能是 bool")
        score = float(score_value)
        if score < 0.0 or score > 1.0:
            raise ValueError("SkillsBench score 必须位于 [0, 1]")
        reward_available = True
    reward = evaluation.get("reward")
    if reward is not None:
        if isinstance(reward, bool) or not isinstance(reward, (int, float)):
            raise ValueError("SkillsBench evaluation.reward 必须是数字")
        if not reward_available or float(reward) != score:
            raise ValueError("SkillsBench evaluation.reward 与 attempt.score 不一致")
    if status == "completed" and correct != (score == 1.0):
        raise ValueError("SkillsBench correct 与满分状态不一致")
    test_return_code = evaluation.get("test_return_code")
    if test_return_code is not None and (isinstance(test_return_code, bool) or not isinstance(test_return_code, int)):
        raise ValueError("SkillsBench test_return_code 必须是整数或 None")
    return {
        "status": status,
        "score": score,
        "correct": correct,
        "reward_available": reward_available,
        "run_error": status in {"run_error", "run_error_or_eval_error"},
        "eval_error": status in {"eval_error", "run_error_or_eval_error"},
        "error_present": bool(attempt.get("error_present")),
        "test_return_code": test_return_code,
        "test_error_present": bool(evaluation.get("test_error_present")),
        "reward_error_present": bool(evaluation.get("reward_error_present")),
    }
