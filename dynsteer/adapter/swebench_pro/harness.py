from dynsteer.adapter.agentcompass.harness import (
    AgentCompassRunData,
    BaseAgentCompassHarness,
    agentcompass_run_summary,
)
from dynsteer.adapter.base import BenchmarkDefaultResult
from dynsteer.adapter.swebench_pro.utils.patch import patch_summary
from dynsteer.adapter.swebench_pro.utils.result import native_result_summary
from dynsteer.model import JsonObject


class SWEBenchProHarness(BaseAgentCompassHarness):
    """通过 AgentCompass 执行并映射 SWE-bench Pro 原生评分。"""

    benchmark = "swebench_pro"

    def _build_run_data(self, detail: JsonObject) -> AgentCompassRunData:
        """构造 SWE-bench Pro 脱敏运行结果。"""
        summary = native_result_summary(detail)
        attempt = detail["attempt"]
        if not isinstance(attempt, dict):
            raise TypeError("detail.attempt 必须是对象")
        patch = patch_summary(attempt.get("final_answer"))
        default_raw: JsonObject = {
            "score_source": "agentcompass_swebench_pro",
            "status": summary["status"],
            "resolved": summary["resolved"],
            "evaluation_completed": summary["evaluation_completed"],
            "evaluation_timed_out": summary["evaluation_timed_out"],
        }
        metrics: JsonObject = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        if summary["returncode"] is not None:
            metrics["returncode"] = summary["returncode"]
        return AgentCompassRunData(
            default_result=BenchmarkDefaultResult(score=1.0 if summary["resolved"] else 0.0, raw=default_raw, metrics=metrics),
            final_state={"patch": patch},
            raw_summary=agentcompass_run_summary(detail, summary, "agentcompass_swebench_pro"),
        )
