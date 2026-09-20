from collections.abc import Mapping

from dynsteer.adapter.agentcompass.result import status_flags, validated_attempt
from dynsteer.model import JsonObject


def native_result_summary(detail: Mapping[str, object]) -> JsonObject:
    """校验并提取 SkillsBench partial reward 原生摘要。"""
    attempt, evaluation, status = validated_attempt(detail)
    correct = attempt.get("correct")
    if correct is not None and not isinstance(correct, bool):
        raise TypeError("SkillsBench correct 必须是 bool 或 None")
    score: float | None
    reward_available = False
    if status == "completed":
        score_value = attempt.get("score")
        if score_value is None:
            raise ValueError("completed SkillsBench 结果必须包含 score")
        if isinstance(score_value, bool) or not isinstance(score_value, (int, float)):
            raise ValueError("SkillsBench score 必须是数字且不能是 bool")
        score = float(score_value)
        if score < 0.0 or score > 1.0:
            raise ValueError("SkillsBench score 必须位于 [0, 1]")
        reward_available = True
    else:
        score = None
        if correct is not None:
            raise ValueError("非 completed SkillsBench 结果不能包含 correct")
    reward = evaluation.get("reward")
    if status == "completed" and reward is not None:
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
        **status_flags(status),
        "error_present": bool(attempt.get("error_present")),
        "test_return_code": test_return_code,
        "test_error_present": bool(evaluation.get("test_error_present")),
        "reward_error_present": bool(evaluation.get("reward_error_present")),
    }
