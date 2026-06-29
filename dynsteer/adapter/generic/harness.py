from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.harness.model import BenchmarkCase, HarnessAdvanceResult, HarnessRunConfig
from dynsteer.model import JsonObject, StateSnapshot, TaskCase, Trajectory, TrajectoryStep


@dataclass
class GenericSession:
    """通用 JSON 轨迹回放 session。"""

    task_case: TaskCase
    remaining_steps: list[TrajectoryStep]
    snapshots: list[StateSnapshot]
    final_state: JsonObject | None
    metrics: JsonObject


class GenericHarness(BaseBenchmarkHarness):
    """通用 JSON benchmark harness，用于逐步回放已有轨迹。"""

    benchmark = "generic"

    # override 基类的函数实现

    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        """列出通用 benchmark case。

        Args:
            config: harness 运行配置。

        Returns:
            metadata 中声明的单个 case；未声明时返回空列表。
        """
        self.prepare_config(config)
        case_id = config.metadata.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            return []
        return [BenchmarkCase(benchmark=self.benchmark, case_id=case_id)]

    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> GenericSession:
        """从 config.metadata 初始化通用回放 session。

        Args:
            config: harness 运行配置。
            case_id: benchmark 场景 ID。
            raw_output_dir: 原生输出目录。

        Returns:
            通用回放 session。
        """
        if config is None or raw_output_dir is None:
            raise ValueError("config 和 raw_output_dir 不能为空")
        task_case = config.metadata.get("task_case")
        trajectory = config.metadata.get("trajectory")
        if not isinstance(task_case, TaskCase) or not isinstance(trajectory, Trajectory):
            raise ValueError("GenericHarness 需要在 metadata 中提供 task_case 和 trajectory")
        return GenericSession(
            task_case=task_case,
            remaining_steps=list(trajectory.steps),
            snapshots=list(trajectory.snapshots),
            final_state=trajectory.final_state,
            metrics=dict(trajectory.metrics),
        )

    def task_case_from_session(self, session: object) -> TaskCase:
        """从回放 session 提取任务定义。"""
        if not isinstance(session, GenericSession):
            raise TypeError("session 必须是 GenericSession")
        return session.task_case

    def advance_case(self, session: object) -> HarnessAdvanceResult:
        """回放一个轨迹步骤。"""
        if not isinstance(session, GenericSession):
            raise TypeError("session 必须是 GenericSession")
        if not session.remaining_steps:
            return HarnessAdvanceResult(
                steps=[],
                snapshots=list(session.snapshots),
                continue_running=False,
                reason="benchmark 已自然完成",
            )
        step = session.remaining_steps.pop(0)
        return HarnessAdvanceResult(
            steps=[step],
            snapshots=list(session.snapshots),
            continue_running=bool(session.remaining_steps),
            reason=None if session.remaining_steps else "benchmark 已自然完成",
        )

    def case_finished(self, session: object) -> bool:
        """判断回放是否完成。"""
        if not isinstance(session, GenericSession):
            raise TypeError("session 必须是 GenericSession")
        return not session.remaining_steps

    def metrics_from_session(self, session: object) -> JsonObject:
        """返回离线轨迹 metrics。"""
        if not isinstance(session, GenericSession):
            raise TypeError("session 必须是 GenericSession")
        return dict(session.metrics)

    def final_state_from_session(self, session: object) -> JsonObject | None:
        """返回离线轨迹 final_state。"""
        if not isinstance(session, GenericSession):
            raise TypeError("session 必须是 GenericSession")
        return dict(session.final_state) if session.final_state is not None else None

    def teardown_case(self, session: object) -> None:
        """释放通用回放 session 中的大对象引用。"""
        if not isinstance(session, GenericSession):
            return
        session.remaining_steps.clear()
        session.snapshots.clear()
        session.final_state = None
        session.metrics.clear()

    # GenericHarness 独有函数实现
