from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dynsteer.model import JsonObject, StateSnapshot, TaskCase, Trajectory, TrajectoryEvaluationReport, TrajectoryStep


@dataclass(frozen=True)
class HarnessRunConfig:
    """benchmark harness 单次运行配置。

    Args:
        benchmark: benchmark 名称，例如 toolsandbox。
        data_root: benchmark 静态配置与 manifest 目录。
        case_ids: 可选 case ID 列表；为空时由 runner 运行全部 case。
        runs_dir: 中间过程、原生输出与 raw summary 目录。
        results_dir: 最终 DynSTEER 评估报告目录。
        metadata: 额外运行配置。
    """

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
    """benchmark 中的单个测试任务描述。

    Args:
        benchmark: benchmark 名称。
        case_id: benchmark 内唯一场景 ID。
        categories: 场景类别。
        metadata: 原始 benchmark 元数据。
    """

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
        """转换为 JSON 可序列化字典。

        Returns:
            可写入 JSON 的 benchmark case 字典。
        """
        return {
            "benchmark": self.benchmark,
            "case_id": self.case_id,
            "categories": list(self.categories),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class HarnessAdvanceResult:
    """benchmark 单次推进结果。

    Args:
        steps: 本次推进新增的轨迹步骤。
        snapshots: 本批推进后可见的状态快照。
        continue_running: 处理完本批步骤后是否继续推进。
        reason: 停止继续推进时的中文原因。
    """

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
    """记录 harness 运行期的阶段结算节点。

    Args:
        settlement_id: 结算节点 ID。
        kind: 结算类型，支持 start、milestone、finish。
        milestone_id: 命中的 milestone ID；start/finish 可为空。
        start_step_index: 本阶段评估区间起点 step index。
        end_step_index: 本阶段评估区间终点 step index。
        boundary_id: 触发 milestone 的候选边界 ID。
        boundary_step_index: 触发 milestone 的边界 step index。
        score: 阶段评估分数。
        status: 阶段评估状态。
        checkpointed: 是否因 milestone checkpoint 暂停 rollout 并执行阶段评估。
        evidence: 阶段证据摘要。
        metadata: 额外阶段评估元数据。
    """

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
        """转换为 JSON 可序列化字典。

        Returns:
            可写入 raw_summary 的阶段结算字典。
        """
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
    """benchmark harness 运行结果。

    Args:
        benchmark: benchmark 名称。
        case_id: 场景 ID。
        run_id: 本次运行 ID。
        task_case: 转换后的 DynSTEER 任务。
        trajectory: 转换后的 DynSTEER 轨迹。
        raw_output_dir: 原生 benchmark 输出目录。
        raw_summary: 原生 benchmark 摘要。
    """

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
