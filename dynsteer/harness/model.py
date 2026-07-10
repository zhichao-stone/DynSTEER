from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dynsteer.model import JsonObject, StateSnapshot, TaskCase, Trajectory, TrajectoryEvaluationReport, TrajectoryStep


@dataclass(frozen=True)
class HarnessRunConfig:
    """benchmark harness 单次运行配置。"""

    benchmark: str
    data_root: Path
    case_ids: tuple[str, ...] | None = None
    runs_dir: Path = Path("runs")
    results_dir: Path = Path("results")
    stop_on_stage_failure: bool = True
    stop_on_minefield: bool = True
    metadata: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.benchmark or not self.benchmark.strip():
            raise ValueError("benchmark 不能为空")
        if self.data_root is None:
            raise ValueError("data_root 不能为空")
        if self.case_ids is not None:
            if not self.case_ids:
                object.__setattr__(self, "case_ids", None)
            elif any(not isinstance(case_id, str) or not case_id.strip() for case_id in self.case_ids):
                raise ValueError("case_ids 不能包含空 case ID")
        if self.runs_dir is None:
            raise ValueError("runs_dir 不能为空")
        if self.results_dir is None:
            raise ValueError("results_dir 不能为空")


@dataclass(frozen=True)
class BenchmarkCase:
    """benchmark 中的单个测试任务描述。"""

    benchmark: str
    case_id: str
    categories: list[str] = field(default_factory=list)
    metadata: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.benchmark or not self.benchmark.strip():
            raise ValueError("benchmark 不能为空")
        if not self.case_id or not self.case_id.strip():
            raise ValueError("case_id 不能为空")

    def to_dict(self) -> JsonObject:
        """转换为 JSON 可序列化字典。"""
        return {
            "benchmark": self.benchmark,
            "case_id": self.case_id,
            "categories": list(self.categories),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class HarnessAdvanceResult:
    """benchmark 单次推进结果。"""

    steps: list[TrajectoryStep]
    snapshots: list[StateSnapshot]
    continue_running: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.steps is None:
            raise ValueError("steps 不能为空")
        if self.snapshots is None:
            raise ValueError("snapshots 不能为空")
        if not isinstance(self.continue_running, bool):
            raise TypeError("continue_running 必须是 bool")
        if self.reason is not None and not self.reason.strip():
            raise ValueError("reason 不能是空字符串")


@dataclass(frozen=True)
class HarnessStageSettlement:
    """记录 harness 运行期的阶段结算节点。"""

    settlement_id: str
    kind: str
    milestone_id: str | None
    start_step_index: int
    end_step_index: int
    boundary_id: str | None = None
    boundary_step_index: int | None = None
    score: float | None = None
    status: str | None = None
    checkpointed: bool = False
    evidence: list[str] = field(default_factory=list)
    metadata: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.settlement_id or not self.settlement_id.strip():
            raise ValueError("settlement_id 不能为空")
        if self.kind not in {"start", "milestone", "finish"}:
            raise ValueError("kind 必须是 start、milestone 或 finish")
        if self.kind == "milestone" and (self.milestone_id is None or not self.milestone_id.strip()):
            raise ValueError("milestone 结算必须包含 milestone_id")
        if self.start_step_index < 0 or self.end_step_index < 0:
            raise ValueError("阶段 step index 不能为负数")
        if self.end_step_index < self.start_step_index:
            raise ValueError("end_step_index 不能小于 start_step_index")

    def to_dict(self) -> JsonObject:
        """转换为 JSON 可序列化字典。"""
        return {
            "settlement_id": self.settlement_id,
            "kind": self.kind,
            "milestone_id": self.milestone_id,
            "start_step_index": self.start_step_index,
            "end_step_index": self.end_step_index,
            "boundary_id": self.boundary_id,
            "boundary_step_index": self.boundary_step_index,
            "score": self.score,
            "status": self.status,
            "checkpointed": self.checkpointed,
            "evidence": list(self.evidence),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class HarnessRunResult:
    """benchmark harness 运行结果。"""

    benchmark: str
    case_id: str
    run_id: str
    task_case: TaskCase | None
    trajectory: Trajectory | None
    raw_output_dir: Path
    raw_summary: JsonObject = field(default_factory=dict)
    stage_settlements: list[HarnessStageSettlement] = field(default_factory=list)
    evaluation_report: TrajectoryEvaluationReport | None = None
    terminated_by_policy: bool = False
    termination_code: str | None = None
    termination_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.benchmark or not self.benchmark.strip():
            raise ValueError("benchmark 不能为空")
        if not self.case_id or not self.case_id.strip():
            raise ValueError("case_id 不能为空")
        if not self.run_id or not self.run_id.strip():
            raise ValueError("run_id 不能为空")
        if self.task_case is None or self.trajectory is None:
            raise ValueError("task_case 和 trajectory 不能为空")
        if self.raw_output_dir is None:
            raise ValueError("raw_output_dir 不能为空")
