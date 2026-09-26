from dataclasses import dataclass, field
from pathlib import Path

from dynsteer.milestone.model import MilestoneGenerationConfig
from dynsteer.model import (
    EvaluationTerminationState,
    JsonObject,
    StateSnapshot,
    Trajectory,
    TrajectoryEvaluationReport,
    TrajectoryStep,
)


@dataclass(frozen=True)
class HarnessRunConfig:
    """Benchmark Runs configurations."""
    benchmark: str
    data_root: Path
    case_ids: tuple[str, ...] | None = None
    runs_dir: Path = Path("runs")
    results_dir: Path = Path("results")
    stop_on_ready_frontier_no_progress: bool = True
    use_milestone_graph: bool = True
    ready_frontier_patience: int = 8
    ready_frontier_min_delta: float = 0.02
    milestone_generation: MilestoneGenerationConfig = field(
        default_factory=MilestoneGenerationConfig
    )
    metadata: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.benchmark or not self.benchmark.strip():
            raise ValueError('Benchmark cannot be empty.')
        if self.data_root is None:
            raise ValueError('Data_root cannot be empty')
        if self.case_ids is not None:
            if not self.case_ids:
                object.__setattr__(self, "case_ids", None)
            elif any((not isinstance(case_id, str) or not case_id.strip() for case_id in self.case_ids)):
                raise ValueError('Case_ids cannot contain empty case ID')
        if self.runs_dir is None:
            raise ValueError('Runs_dir cannot be empty')
        if self.results_dir is None:
            raise ValueError('results_dir must not be empty')
        if not isinstance(self.stop_on_ready_frontier_no_progress, bool):
            raise TypeError('Stop_on_ready_frontier_no_progress must be bool')
        if not isinstance(self.use_milestone_graph, bool):
            raise TypeError("use_milestone_graph must be bool")
        if not isinstance(self.ready_frontier_patience, int):
            raise TypeError('ready_frontier_patience must be an integer')
        if self.ready_frontier_patience < 1:
            raise ValueError('ready_frontier_patience must be greater than 0')
        if not isinstance(self.ready_frontier_min_delta, (int, float)):
            raise TypeError("ready_frontier_min_delta must be a number")
        if self.ready_frontier_min_delta < 0:
            raise ValueError("ready_frontier_min_delta cannot be negative")

@dataclass(frozen=True)
class HarnessAdvanceResult:
    """Benchmark single push results."""
    steps: list[TrajectoryStep]
    snapshots: list[StateSnapshot]
    continue_running: bool
    execution_latency_ms: int | None = None

    def __post_init__(self) -> None:
        if self.steps is None:
            raise ValueError('Steps cannot be empty.')
        if self.snapshots is None:
            raise ValueError("Snapshots can't be empty.")
        if not isinstance(self.continue_running, bool):
            raise TypeError('Continue_running must be bool')
        if self.execution_latency_ms is not None:
            if isinstance(self.execution_latency_ms, bool) or not isinstance(self.execution_latency_ms, int):
                raise TypeError('Execulation_latency_ms must be a non-negative integer or None')
            if self.execution_latency_ms < 0:
                raise ValueError('Execution_latency_ms')

@dataclass(frozen=True)
class HarnessStageSettlement:
    """Records the settlement node of the stage during the Harness period."""
    settlement_id: str
    stage_id: str
    kind: str
    milestone_id: str | None
    start_step_index: int
    end_step_index: int
    boundary_id: str | None = None
    boundary_step_index: int | None = None
    score: float | None = None
    status: str | None = None
    evidence: list[str] = field(default_factory=list)
    metadata: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.settlement_id or not self.settlement_id.strip():
            raise ValueError("It's not empty.")
        if self.kind not in {"start", "milestone", "finish"}:
            raise ValueError('Kind must be start, milestone or finish')
        if self.kind == "milestone" and (self.milestone_id is None or not self.milestone_id.strip()):
            raise ValueError('milestone settlement must contain milestone_id')
        if self.start_step_index < 0 or self.end_step_index < 0:
            raise ValueError('Stage Step index cannot be negative')
        if self.end_step_index < self.start_step_index:
            raise ValueError('End_step_index cannot be less than start_step_index')

    def to_dict(self) -> JsonObject:
        """Converts to a serialized dictionary for JSON."""
        return {
            "settlement_id": self.settlement_id,
            "stage_id": self.stage_id,
            "kind": self.kind,
            "milestone_id": self.milestone_id,
            "start_step_index": self.start_step_index,
            "end_step_index": self.end_step_index,
            "boundary_id": self.boundary_id,
            "boundary_step_index": self.boundary_step_index,
            "score": self.score,
            "status": self.status,
            "evidence": list(self.evidence),
            "metadata": dict(self.metadata),
        }

@dataclass(frozen=True)
class HarnessRunResult:
    """Run result for benchmark operation."""
    trajectory: Trajectory
    evaluation_report: TrajectoryEvaluationReport
    raw_summary: JsonObject = field(default_factory=dict)
    stage_settlements: list[HarnessStageSettlement] = field(default_factory=list)
    termination: EvaluationTerminationState = field(default_factory=EvaluationTerminationState)
