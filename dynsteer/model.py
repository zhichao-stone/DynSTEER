from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Union

## JSON 类型

JsonValue = Union[str, int, float, bool, None, dict[str, "JsonValue"], list["JsonValue"]]
JsonObject = dict[str, JsonValue]

MISSING = object()


## 枚举定义

class Actor(str, Enum):
    SYSTEM = "system"
    USER = "user"
    AGENT = "agent"
    ENVIRONMENT = "environment"
    EVALUATOR = "evaluator"


class EventType(str, Enum):
    MESSAGE = "message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    STATE_UPDATE = "state_update"
    ARTIFACT_UPDATE = "artifact_update"
    FINAL = "final"
    ERROR = "error"


class ConstraintTarget(str, Enum):
    STEP = "step"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    STATE_SNAPSHOT = "state_snapshot"
    STATE_DELTA = "state_delta"
    ARTIFACT = "artifact"
    METRIC = "metric"
    SEMANTIC = "semantic"


class Operator(str, Enum):
    EQUALS = "equals"
    CONTAINS = "contains"
    ONE_OF = "one_of"
    FUZZY_MATCH = "fuzzy_match"
    JSON_SUBSUMES = "json_subsumes"
    ADDED = "added"
    UPDATED = "updated"
    REMOVED = "removed"
    UNCHANGED_SINCE = "unchanged_since"
    AST_MATCH = "ast_match"
    CUSTOM = "custom"


class Dimension(str, Enum):
    PROGRESS = "progress"
    STATE_CONSISTENCY = "state_consistency"
    TOOL_QUALITY = "tool_quality"
    EFFICIENCY = "efficiency"
    SAFETY = "safety"
    INTERACTION_QUALITY = "interaction_quality"
    RECOVERY = "recovery"


class TaskType(str, Enum):
    GENERAL = "general_task"
    STATEFUL_TOOL = "stateful_tool_task"
    DIALOGUE_INTERACTION = "dialogue_interaction_task"
    ARTIFACT = "artifact_task"
    SAFETY_SENSITIVE = "safety_sensitive_task"


class EvaluationLevel(str, Enum):
    CHEAP = "cheap"
    STANDARD = "standard"
    EXPENSIVE = "expensive"


class StageStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    INVALID = "invalid"


## 轨迹与状态模型

@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    success: bool
    content: JsonValue = None
    exception: Optional[str] = None


@dataclass(frozen=True)
class StepCost:
    tokens: Optional[int] = None
    latency_ms: Optional[int] = None


@dataclass(frozen=True)
class TrajectoryStep:
    step_id: str
    index: int
    actor: Actor
    event_type: EventType
    timestamp: Optional[str] = None
    content: Optional[str] = None
    tool_call: Optional[ToolCall] = None
    tool_result: Optional[ToolResult] = None
    state_delta_refs: list[str] = field(default_factory=list)
    cost: StepCost = field(default_factory=StepCost)
    raw: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class StateSnapshot:
    snapshot_id: str
    after_step_id: str
    after_step_index: int
    namespaces: dict[str, JsonValue] = field(default_factory=dict)
    raw: JsonObject = field(default_factory=dict)


## Milestone 与约束模型

@dataclass(frozen=True)
class Constraint:
    constraint_id: str
    target: ConstraintTarget
    selector: str
    operator: Operator
    expected: JsonValue = None
    namespace: Optional[str] = None
    reference_milestone_id: Optional[str] = None
    weight: float = 1.0
    threshold: float = 1.0
    hard: bool = False
    evaluator_hint: str = "rule"
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class Milestone:
    milestone_id: str
    name: str
    description: str
    constraints: list[Constraint]
    required: bool = True
    pass_threshold: Optional[float] = None
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class MinefieldPenalty:
    mode: str
    value: float


@dataclass(frozen=True)
class Minefield:
    minefield_id: str
    name: str
    description: str
    severity: str
    constraints: list[Constraint]
    penalty: MinefieldPenalty
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class MilestoneGraph:
    nodes: list[Milestone] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    minefields: list[Minefield] = field(default_factory=list)
    default_thresholds: dict[str, float] = field(default_factory=dict)
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class TaskCase:
    task_id: str
    task_description: str
    environment_schema: JsonObject = field(default_factory=dict)
    tool_schema: JsonObject = field(default_factory=dict)
    policy_constraints: list[JsonObject] = field(default_factory=list)
    initial_state: Optional[JsonObject] = None
    milestone_graph: Optional[MilestoneGraph] = None
    task_types: list[TaskType] = field(default_factory=list)
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class Trajectory:
    run_id: str
    task_id: str
    steps: list[TrajectoryStep]
    snapshots: list[StateSnapshot] = field(default_factory=list)
    final_state: Optional[JsonObject] = None
    metrics: JsonObject = field(default_factory=dict)
    raw: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class Boundary:
    boundary_id: str
    step_index: int
    snapshot_id: Optional[str]
    reason: str
    step_id: Optional[str] = None


@dataclass(frozen=True)
class ConstraintScore:
    constraint_id: str
    score: float
    missing: bool = False
    evidence: list[str] = field(default_factory=list)
    actual: JsonValue = None


@dataclass(frozen=True)
class MilestoneScore:
    milestone_id: str
    boundary_id: str
    score: float
    status: StageStatus
    evidence: list[str] = field(default_factory=list)
    missing_ratio: float = 0.0
    hard_constraints_all_pass: bool = True
    constraint_scores: list[ConstraintScore] = field(default_factory=list)


@dataclass(frozen=True)
class MilestoneMappingItem:
    milestone_id: str
    boundary_id: str
    boundary_step_index: int
    score: MilestoneScore


@dataclass(frozen=True)
class MilestoneMapping:
    assignments: dict[str, MilestoneMappingItem] = field(default_factory=dict)
    missing_required: list[str] = field(default_factory=list)
    objective: float = 0.0
    evidence: list[str] = field(default_factory=list)


## 评估决策与结果模型

@dataclass(frozen=True)
class StageInterval:
    stage_id: str
    milestone_id: Optional[str]
    start_step_index: int
    end_step_index: int
    status: StageStatus
    milestone_score: Optional[MilestoneScore] = None
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EvaluationDecision:
    level: EvaluationLevel
    reason: str


@dataclass(frozen=True)
class StageEvaluationResult:
    stage_id: str
    milestone_id: Optional[str]
    evaluator_level: EvaluationLevel
    status: StageStatus
    stage_score: float
    uncertainty: float
    dimension_scores: dict[Dimension, float]
    evidence: list[str] = field(default_factory=list)
    diagnosis: list[str] = field(default_factory=list)
    next_weights: dict[Dimension, float] = field(default_factory=dict)
    fatal: bool = False
    hard_constraints_all_pass: bool = True
    required_fields_missing_ratio: float = 0.0
    minefield_score: float = 0.0
    fatal_minefield_score: float = 0.0
    judge_confidence: float = 1.0
    minefield_evidence_is_structural: bool = True
    metadata: JsonObject = field(default_factory=dict)

    def to_dict(self) -> JsonObject:
        """转换为 JSON 可序列化字典。"""
        return {
            "stage_id": self.stage_id,
            "milestone_id": self.milestone_id,
            "evaluator_level": self.evaluator_level.value,
            "status": self.status.value,
            "stage_score": self.stage_score,
            "uncertainty": self.uncertainty,
            "dimension_scores": {key.value: value for key, value in self.dimension_scores.items()},
            "evidence": list(self.evidence),
            "diagnosis": list(self.diagnosis),
            "next_weights": {key.value: value for key, value in self.next_weights.items()},
            "fatal": self.fatal,
            "hard_constraints_all_pass": self.hard_constraints_all_pass,
            "required_fields_missing_ratio": self.required_fields_missing_ratio,
            "minefield_score": self.minefield_score,
            "fatal_minefield_score": self.fatal_minefield_score,
            "judge_confidence": self.judge_confidence,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class TrajectoryEvaluationReport:
    run_id: str
    task_id: str
    milestone_coverage: str
    overall_score: float
    stage_reports: list[StageEvaluationResult] = field(default_factory=list)
    minefield_matches: list[JsonObject] = field(default_factory=list)
    first_failure_stage_id: Optional[str] = None

    def to_dict(self) -> JsonObject:
        """转换为完整 JSON 可序列化报告。"""
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "milestone_coverage": self.milestone_coverage,
            "overall_score": self.overall_score,
            "stage_reports": [stage.to_dict() for stage in self.stage_reports],
            "minefield_matches": list(self.minefield_matches),
            "first_failure_stage_id": self.first_failure_stage_id,
        }

    def to_summary_dict(self) -> JsonObject:
        """转换为主实验摘要报告。"""
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "milestone_coverage": self.milestone_coverage,
            "overall_score": self.overall_score,
            "stage_count": len(self.stage_reports),
            "first_failure_stage_id": self.first_failure_stage_id,
        }


## 通用校验函数

def ensure_json_object(value: Any) -> JsonObject:
    """校验输入是否为 JSON 对象。

    Args:
        value: 待校验的任意值。

    Returns:
        校验后的 JSON 对象。

    Raises:
        ValueError: 当输入不是字典时抛出。
    """
    if value is None or not isinstance(value, dict):
        raise ValueError("输入必须是 JSON 对象")
    return value
