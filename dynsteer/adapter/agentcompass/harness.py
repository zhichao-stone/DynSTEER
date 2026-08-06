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
from dynsteer.harness.model import BenchmarkCase, HarnessAdvanceResult, HarnessRunConfig
from dynsteer.model import JsonObject, TrajectoryStep


@dataclass(frozen=True)
class AgentCompassRunData:
    """保存单 case 已转换且已脱敏的最终数据。"""

    steps: list[TrajectoryStep]
    default_result: BenchmarkDefaultResult
    raw_summary: JsonObject
    final_state: JsonObject | None


@dataclass
class AgentCompassSession:
    """保存 AgentCompass 整任务执行的最小生命周期状态。"""

    case_id: str
    config: HarnessRunConfig
    raw_output_dir: Path
    finished: bool = False
    run_data: AgentCompassRunData | None = None


class BaseAgentCompassHarness(BaseBenchmarkHarness, ABC):
    """实现 AgentCompass benchmark 共用的单次整任务 harness。"""

    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        """按 task ID 排序列出 AgentCompass case。"""
        self._validate_config(config)
        records = load_task_records(self.benchmark, config)
        return [
            BenchmarkCase(
                benchmark=self.benchmark,
                case_id=task_id,
                categories=[record.category] if record.category else [],
                metadata={"source": "agentcompass"},
            )
            for task_id, record in sorted(records.items())
        ]

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
        if current.finished:
            return HarnessAdvanceResult(steps=[], snapshots=[], continue_running=False, reason="agentcompass_completed")
        detail = run_agentcompass_case(self.benchmark, current.case_id, current.config, current.raw_output_dir)
        attempt = detail.get("attempt")
        if not isinstance(attempt, dict):
            raise TypeError("脱敏 AgentCompass detail 缺少 attempt 对象")
        steps = convert_actf_steps(attempt.get("trajectory"), benchmark=self.benchmark, case_id=current.case_id)
        current.run_data = self._build_run_data(detail, steps)
        current.finished = True
        return HarnessAdvanceResult(
            steps=current.run_data.steps,
            snapshots=[],
            continue_running=False,
            reason="agentcompass_completed",
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
    def _build_run_data(self, detail: JsonObject, steps: list[TrajectoryStep]) -> AgentCompassRunData:
        """将 benchmark 专用 detail 映射为 DynSTEER 运行数据。"""

    ## 内部函数

    def _require_session(self, session: object) -> AgentCompassSession:
        if not isinstance(session, AgentCompassSession):
            raise TypeError("session 必须是 AgentCompassSession")
        return session

    def _require_run_data(self, session: object) -> AgentCompassRunData:
        current = self._require_session(session)
        if not current.finished or current.run_data is None:
            raise RuntimeError("AgentCompass session 尚未完成")
        return current.run_data
