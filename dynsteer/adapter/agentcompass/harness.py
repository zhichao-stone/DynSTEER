from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from dynsteer.adapter.agentcompass.runtime import (
    load_task_records,
    run_agentcompass_case,
)
from dynsteer.adapter.agentcompass.trajectory import convert_actf_steps
from dynsteer.adapter.base import BaseBenchmarkHarness, BenchmarkDefaultResult
from dynsteer.harness.model import HarnessAdvanceResult, HarnessRunConfig
from dynsteer.model import JsonObject


@dataclass(frozen=True)
class AgentCompassRunData:
    """保存单 case 已转换且已脱敏的最终数据。"""

    default_result: BenchmarkDefaultResult
    raw_summary: JsonObject
    final_state: JsonObject | None


def agentcompass_run_summary(detail: JsonObject, summary: JsonObject, score_source: str) -> JsonObject:
    """构造两个 AgentCompass benchmark 共用的运行摘要。"""
    provenance = detail.get("provenance")
    if not isinstance(provenance, dict):
        raise TypeError("detail.provenance 必须是对象")
    return {
        "score_source": score_source,
        "run_error": summary["run_error"],
        "eval_error": summary["eval_error"],
        "error_present": summary["error_present"],
        "provenance": dict(provenance),
    }


@dataclass
class AgentCompassSession:
    """保存 AgentCompass 整任务执行的最小生命周期状态。"""

    case_id: str
    config: HarnessRunConfig
    raw_output_dir: Path
    run_data: AgentCompassRunData | None = None


class BaseAgentCompassHarness(BaseBenchmarkHarness, ABC):
    """实现 AgentCompass benchmark 共用的单次整任务 harness。"""

    def list_case_ids(self, config: HarnessRunConfig) -> list[str]:
        """按 task ID 排序列出 AgentCompass case。"""
        self._validate_config(config)
        return sorted(load_task_records(self.benchmark, config))

    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> AgentCompassSession:
        """校验 case 存在并初始化最小 session。"""
        if config is None or raw_output_dir is None or not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("config、case_id 和 raw_output_dir 不能为空")
        self._validate_config(config)
        if case_id not in load_task_records(self.benchmark, config):
            raise KeyError(f"AgentCompass case 不存在: {case_id}")
        return AgentCompassSession(case_id=case_id, config=config, raw_output_dir=raw_output_dir)

    def advance_case(self, session: object) -> HarnessAdvanceResult:
        """执行完整 AgentCompass case，并一次性返回全部转换步骤。"""
        current = self._require_session(session)
        if current.run_data is not None:
            return HarnessAdvanceResult(steps=[], snapshots=[], continue_running=False)
        detail = run_agentcompass_case(self.benchmark, current.case_id, current.config, current.raw_output_dir)
        attempt = detail.get("attempt")
        if not isinstance(attempt, dict):
            raise TypeError("脱敏 AgentCompass detail 缺少 attempt 对象")
        steps = convert_actf_steps(attempt.get("trajectory"), benchmark=self.benchmark, case_id=current.case_id)
        current.run_data = self._build_run_data(detail)
        return HarnessAdvanceResult(
            steps=steps,
            snapshots=[],
            continue_running=False,
        )

    def final_state_from_session(self, session: object) -> JsonObject | None:
        """返回已构造且不含正文的最终状态摘要。"""
        return self._require_run_data(session).final_state

    def raw_summary_from_session(self, session: object) -> JsonObject:
        """返回已构造的脱敏原生摘要。"""
        return dict(self._require_run_data(session).raw_summary)

    def default_result_from_session(self, session: object) -> BenchmarkDefaultResult:
        """返回 AgentCompass 原生评分映射结果。"""
        return self._require_run_data(session).default_result

    ## 子类接口

    @abstractmethod
    def _build_run_data(self, detail: JsonObject) -> AgentCompassRunData:
        """将 benchmark 专用 detail 映射为 DynSTEER 运行数据。"""

    ## 内部函数

    def _require_session(self, session: object) -> AgentCompassSession:
        if not isinstance(session, AgentCompassSession):
            raise TypeError("session 必须是 AgentCompassSession")
        return session

    def _require_run_data(self, session: object) -> AgentCompassRunData:
        current = self._require_session(session)
        if current.run_data is None:
            raise RuntimeError("AgentCompass session 尚未完成")
        return current.run_data
