from dynsteer.adapter.agentcompass.harness import (
    AgentCompassRunData,
    BaseAgentCompassHarness,
)
from dynsteer.adapter.base import BenchmarkDefaultResult
from dynsteer.adapter.skillsbench.scorer import SkillsBenchConstraintScorer
from dynsteer.adapter.skillsbench.utils.artifact import artifact_manifest
from dynsteer.adapter.skillsbench.utils.result import native_result_summary
from dynsteer.model import JsonObject, TrajectoryStep


class SkillsBenchHarness(BaseAgentCompassHarness):
    """通过 AgentCompass 执行并映射 SkillsBench partial reward。"""

    benchmark = "skillsbench"

    def constraint_scorer(self) -> SkillsBenchConstraintScorer:
        """返回 SkillsBench 结构化约束评分扩展点。"""
        return SkillsBenchConstraintScorer()

    def _build_run_data(self, detail: JsonObject, steps: list[TrajectoryStep]) -> AgentCompassRunData:
        """构造 SkillsBench 脱敏运行结果。"""
        summary = native_result_summary(detail)
        attempt = detail["attempt"]
        if not isinstance(attempt, dict):
            raise TypeError("detail.attempt 必须是对象")
        artifacts = artifact_manifest(attempt.get("files"))
        default_raw: JsonObject = {
            "score_source": "agentcompass_skillsbench",
            "status": summary["status"],
            "correct": summary["correct"],
            "reward_available": summary["reward_available"],
            "test_error_present": summary["test_error_present"],
            "reward_error_present": summary["reward_error_present"],
        }
        metrics: JsonObject = {}
        if summary["test_return_code"] is not None:
            metrics["test_return_code"] = summary["test_return_code"]
        provenance = detail.get("provenance")
        if not isinstance(provenance, dict):
            raise TypeError("detail.provenance 必须是对象")
        score = float(summary["score"]) if summary["reward_available"] else 0.0
        return AgentCompassRunData(
            steps=steps,
            default_result=BenchmarkDefaultResult(score=score, raw=default_raw, metrics=metrics),
            final_state={"artifacts": artifacts},
            raw_summary={
                "score_source": "agentcompass_skillsbench",
                "run_error": summary["run_error"],
                "eval_error": summary["eval_error"],
                "error_present": summary["error_present"],
                "provenance": dict(provenance),
            },
        )
