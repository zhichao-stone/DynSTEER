from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any, Literal, Mapping, Optional, Union

if TYPE_CHECKING:
    from dynsteer.harness.model import HarnessRunConfig, HarnessStageSettlement


JsonValue = Union[str, int, float, bool, None, dict[str, "JsonValue"], list["JsonValue"]]
JsonObject = dict[str, JsonValue]
MISSING = object()

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

class StageGoalSemanticKind(str, Enum):
    SET_STATE = "set_state"
    PRESERVE_STATE = "preserve_state"
    EMIT_MESSAGE = "emit_message"
    TOOL_CALL = "tool_call"

@dataclass(frozen=True)
class ThresholdConfig:
    pass_threshold: float = 0.8
    warn_threshold: float = 0.6
    fail_threshold: float = 0.4
    low_dimension_uncertainty: float = 0.2
    high_dimension_uncertainty: float = 0.45
    safe_minefield_threshold: float = 0.2
    risky_minefield_threshold: float = 0.5
    fatal_minefield_threshold: float = 0.95
    threshold_margin: float = 0.05

@dataclass(frozen=True)
class DynamicWeightConfig:
    alpha: float = 1.0
    beta: float = 1.0

@dataclass
class ToolCall:
    name: str
    arguments: JsonObject = field(default_factory=dict)


@dataclass
class ToolResult:
    success: bool = False
    content: JsonValue = None
    exception: Optional[str] = None


@dataclass
class StepCost:
    tokens: Optional[int] = None
    latency_ms: Optional[int] = None

@dataclass
class TrajectoryStep:
    step_id: str
    index: int
    event_type: EventType
    actor: Actor
    recipient: Optional[Actor] = None
    timestamp: Optional[str] = None
    content: Optional[str] = None
    tool_call: Optional[ToolCall] = None
    tool_result: Optional[ToolResult] = None
    state_delta_refs: list[str] = field(default_factory=list)
    cost: StepCost = field(default_factory=StepCost)
    raw: JsonObject = field(default_factory=dict)

@dataclass(frozen=True)
class AgentStepClosure:
    """描述一次已闭合的 agent outbound 执行步骤组。

    入参：
        steps: 从 Agent -> X 起点到闭包终点的完整 raw steps。
    输出：
        `end_step` 返回闭包终点 step。
    """
    steps: tuple[TrajectoryStep, ...]

    @property
    def end_step(self) -> TrajectoryStep:
        """返回闭包终点 step。"""
        if not self.steps:
            raise ValueError("agent step closure 不能为空")
        return self.steps[-1]

class AgentStepProtocolError(RuntimeError):
    """agent step 闭包协议不合法时抛出。"""

@dataclass
class AgentStepTracker:
    """跟踪串行或带 correlation ID 的并行 agent outbound 闭包状态。

    入参：
        pending_outbounds: 按稳定内部 key 保存尚未收到反馈的 Agent -> X step。
        pending_steps: 按稳定内部 key 保存各闭包已收集的 raw steps。
        completed_count: 已闭合的 agent step 数量。
    输出：
        `ingest()` 在闭包完成时返回完整闭包，否则返回 None。
    """
    pending_outbounds: dict[str, TrajectoryStep] = field(default_factory=dict)
    pending_steps: dict[str, list[TrajectoryStep]] = field(default_factory=dict)
    completed_count: int = 0

    def ingest(self, raw_step: TrajectoryStep) -> AgentStepClosure | None:
        """摄入一条 raw step，并在闭合 agent step 时返回完整闭包。"""
        if raw_step is None:
            raise ValueError("raw_step 不能为空")
        if self._is_agent_outbound(raw_step):
            self._add_pending(raw_step)
            return None
        if not self.pending_outbounds:
            return None

        key = self._matching_pending_key(raw_step)
        if key is not None:
            self.pending_steps[key].append(raw_step)
            return self._complete_pending(key)
        if len(self.pending_outbounds) == 1:
            only_key = next(iter(self.pending_outbounds))
            self.pending_steps[only_key].append(raw_step)
            return None
        raise self._protocol_error("并行 pending 期间的 step 无法唯一归属", raw_step)

    def finalize(self) -> AgentStepClosure | None:
        """自然结束时闭合允许自闭合的唯一终局 agent message。"""
        if len(self.pending_outbounds) != 1:
            return None
        key, pending = next(iter(self.pending_outbounds.items()))
        if pending.actor == Actor.AGENT and pending.recipient == Actor.USER and pending.event_type in {EventType.MESSAGE, EventType.FINAL}:
            return self._complete_pending(key)
        return None

    ## 内部函数

    def _add_pending(self, raw_step: TrajectoryStep) -> None:
        """新增 outbound，并校验连续 outbound 是否为合法并行工具调用。"""
        key = self._pending_key(raw_step)
        if self.pending_outbounds:
            pending_values = list(self.pending_outbounds.values())
            if not all(self._is_parallel_tool_outbound(step) for step in [*pending_values, raw_step]):
                raise self._protocol_error("上一个 agent outbound 尚未闭合，不能继续接收新的 agent outbound", raw_step)
            missing_ids = [step.step_id for step in [*pending_values, raw_step] if self._tool_call_id(step) is None]
            if missing_ids:
                raise self._protocol_error(f"并行 tool outbound 缺少 correlation id: step_ids={missing_ids}", raw_step)
            if key in self.pending_outbounds:
                raise self._protocol_error("并行 tool outbound 使用重复 correlation id", raw_step)
        self.pending_outbounds[key] = raw_step
        self.pending_steps[key] = [raw_step]

    def _complete_pending(self, key: str) -> AgentStepClosure:
        """完成指定 pending closure，并更新 tracker 状态。"""
        steps = tuple(self.pending_steps.get(key, []))
        if not steps:
            raise AgentStepProtocolError("agent step closure 缺少 pending steps")
        del self.pending_outbounds[key]
        del self.pending_steps[key]
        self.completed_count += 1
        return AgentStepClosure(steps=steps)

    def _matching_pending_key(self, feedback: TrajectoryStep) -> str | None:
        """按 correlation ID 优先、reciprocal route 次之解析 feedback。"""
        call_id = self._tool_call_id(feedback)
        if call_id is not None:
            key = f"tool:{call_id}"
            pending = self.pending_outbounds.get(key)
            if pending is None:
                raise self._protocol_error("feedback 使用未知 correlation id", feedback)
            if not self._is_reciprocal_feedback(pending, feedback):
                raise self._protocol_error("feedback correlation id 匹配但 route 不匹配", feedback)
            return key

        candidates = [
            key
            for key, pending in self.pending_outbounds.items()
            if self._is_reciprocal_feedback(pending, feedback)
        ]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise self._protocol_error("并行 feedback 缺少 correlation id", feedback)
        return None

    def _pending_key(self, step: TrajectoryStep) -> str:
        """生成 pending outbound 的稳定内部 key。"""
        call_id = self._tool_call_id(step)
        return f"tool:{call_id}" if call_id is not None else f"step:{step.step_id}"

    def _protocol_error(self, category: str, current: TrajectoryStep) -> AgentStepProtocolError:
        """构造包含 pending、route 与 correlation 上下文的协议异常。"""
        pending_ids = [step.step_id for step in self.pending_outbounds.values()]
        pending_call_ids = [
            call_id
            for step in self.pending_outbounds.values()
            if (call_id := self._tool_call_id(step)) is not None
        ]
        return AgentStepProtocolError(
            f"{category}: pending_step_ids={pending_ids}, current_step_id={current.step_id}, "
            f"current={current.actor.value}/{current.recipient.value if current.recipient else None}/{current.event_type.value}, "
            f"current_call_id={self._tool_call_id(current)}, pending_call_ids={pending_call_ids}"
        )

    def _tool_call_id(self, step: TrajectoryStep) -> str | None:
        """读取并清理 step raw 中的 OpenAI tool call correlation ID。"""
        value = step.raw.get("openai_tool_call_id")
        if not isinstance(value, str) or not value.strip():
            return None
        return value.strip()

    def _is_parallel_tool_outbound(self, step: TrajectoryStep) -> bool:
        """判断 step 是否属于允许并行的 Agent -> Environment tool call。"""
        return step.actor == Actor.AGENT and step.recipient == Actor.ENVIRONMENT and step.event_type == EventType.TOOL_CALL

    def _is_reciprocal_feedback(self, pending: TrajectoryStep, feedback: TrajectoryStep) -> bool:
        """判断 feedback route 是否与 pending outbound 相反。"""
        return feedback.actor == pending.recipient and feedback.recipient == Actor.AGENT

    def _is_agent_outbound(self, raw_step: TrajectoryStep) -> bool:
        """判断 raw step 是否为 Agent -> X 的行为起点。"""
        return raw_step.actor == Actor.AGENT and raw_step.recipient in {Actor.USER, Actor.ENVIRONMENT}

@dataclass
class StateSnapshot:
    snapshot_id: str
    after_step_id: str
    after_step_index: int
    namespaces: dict[str, JsonValue] = field(default_factory=dict)
    raw: JsonObject = field(default_factory=dict)

@dataclass
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
    stage_goal_semantics: JsonObject | None = None
    metadata: JsonObject = field(default_factory=dict)

@dataclass
class Milestone:
    milestone_id: str
    name: str
    description: str
    constraints: list[Constraint]
    pass_threshold: Optional[float] = None
    metadata: JsonObject = field(default_factory=dict)
    dependency_predecessor_ids: list[str] = field(default_factory=list)
    stage_anchor_predecessor_id: Optional[str] = None

@dataclass
class MinefieldPenalty:
    mode: str = "fixed"
    value: float = 0.0


@dataclass
class Minefield:
    minefield_id: str
    name: str
    description: str
    severity: str
    constraints: list[Constraint]
    penalty: MinefieldPenalty
    metadata: JsonObject = field(default_factory=dict)

@dataclass
class MilestoneGraph:
    nodes: list[Milestone] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    minefields: list[Minefield] = field(default_factory=list)
    default_thresholds: dict[str, float] = field(default_factory=dict)
    metadata: JsonObject = field(default_factory=dict)

@dataclass
class MilestoneFrontierState:
    """保存 milestone ready frontier 的运行期增量状态。

    入参：
        milestone_by_id: milestone id 到 milestone 对象引用的映射。
        dependents_by_id: milestone id 到直接后继 milestone id 的映射。
        remaining_predecessor_count: 未匹配前驱数量。
        ready_ids: 当前 ready frontier，按 graph 原始拓扑顺序维护。
        blocked_candidate_ids: 已靠近执行前沿但仍缺少前驱的诊断候选，按 graph 原始拓扑顺序维护。
        order_by_id: graph.nodes 原始顺序，用于稳定输出。
    输出：
        供运行期 step 分析和 match 后推进复用的状态对象。
    """
    milestone_by_id: dict[str, Milestone]
    dependents_by_id: dict[str, tuple[str, ...]]
    remaining_predecessor_count: dict[str, int]
    ready_ids: list[str]
    blocked_candidate_ids: list[str]
    order_by_id: dict[str, int]

@dataclass
class TaskCase:
    task_id: str
    task_description: str
    case_id: str
    environment_schema: JsonObject = field(default_factory=dict)
    tool_schema: JsonObject = field(default_factory=dict)
    policy_constraints: list[JsonObject] = field(default_factory=list)
    initial_state: Optional[JsonObject] = None
    milestone_graph: Optional[MilestoneGraph] = None
    stage_goal_templates: dict[str, str] = field(default_factory=dict)
    stage_goals: dict[str, str] = field(default_factory=dict)
    stage_evaluation_specs: dict[str, "StageEvaluationSpec"] = field(default_factory=dict)
    task_types: list[TaskType] = field(default_factory=list)
    metadata: JsonObject = field(default_factory=dict)

@dataclass
class StageEvaluationSpec:
    """描述单个阶段实际需要评估的维度。

    入参：
        focus_dimensions: 本阶段参与 judge 与综合分计算的维度。
        dimension_rationale: 每个维度被纳入评估的原因。
    输出：
        供阶段评估、策略更新和展示层复用的聚焦维度配置。
    """
    focus_dimensions: list[Dimension]
    dimension_rationale: dict[Dimension, str] = field(default_factory=dict)

@dataclass
class Trajectory:
    task_id: str
    steps: list[TrajectoryStep]
    snapshots: list[StateSnapshot] = field(default_factory=list)
    final_state: Optional[JsonObject] = None
    metrics: JsonObject = field(default_factory=dict)
    raw: JsonObject = field(default_factory=dict)
    first_step_index: int = 0
    successor_by_boundary: dict[int, int] = field(default_factory=dict)
    latest_step_index: int | None = None

    def __post_init__(self) -> None:
        """根据已有 step 序列维护阶段边界 O(1) 查询上下文。"""
        self.first_step_index = 0
        self.successor_by_boundary = {}
        self.latest_step_index: int | None = None
        for step in self.steps:
            if step is None:
                raise ValueError("trajectory.steps 不能包含空 step")
            self._append_step_index(step.index)

    def _append_step_index(self, step_index: int) -> None:
        """仅追加 step index，用于维护阶段边界后继表。"""
        previous_index = self.latest_step_index
        if previous_index is not None and step_index <= previous_index:
            raise ValueError("trajectory step index 必须递增")
        if previous_index is None:
            self.first_step_index = step_index
            self.successor_by_boundary[step_index - 1] = step_index
        else:
            self.successor_by_boundary[previous_index] = step_index
        self.latest_step_index = step_index

    def append_step(self, step: TrajectoryStep) -> None:
        """追加单个 step，并同步维护首个 step 与 boundary 后继表。"""
        self.steps.append(step)
        self._append_step_index(step.index)

    def extend_snapshots(self, snapshots: list[StateSnapshot]) -> None:
        """按 snapshot_id 去重追加状态快照。"""
        if not snapshots:
            return
        snapshot_by_id = {snapshot.snapshot_id: snapshot for snapshot in self.snapshots}
        for snapshot in snapshots:
            snapshot_by_id[snapshot.snapshot_id] = snapshot
        self.snapshots = sorted(snapshot_by_id.values(), key=lambda item: (item.after_step_index, item.snapshot_id))

    def get_interval(self, min_index: int, max_index: int) -> list[TrajectoryStep]:
        """返回指定 step index 区间内的轨迹步骤。"""
        if min_index >= max_index:
            raise ValueError("min_index 必须小于 max_index")
        if self.latest_step_index is None:
            return []
        if max_index < self.first_step_index or min_index >= self.latest_step_index:
            return []
        lower_bound = max(min_index, self.first_step_index - 1)
        upper_bound = min(max_index, self.latest_step_index)
        return [step for step in self.steps if lower_bound < step.index <= upper_bound]

@dataclass
class Boundary:
    boundary_id: str
    step_index: int
    snapshot_id: Optional[str]
    reason: str
    step_id: Optional[str] = None

def _enum_key_dict(values: Mapping[Enum, object]) -> JsonObject:
    """将 enum key 字典转换为 JSON key 字典。"""
    return {key.value: value.value if isinstance(value, Enum) else value for key, value in values.items()}

@dataclass
class ConstraintScore:
    constraint_id: str
    score: float
    missing: bool = False
    evidence: list[str] = field(default_factory=list)
    actual: JsonValue = None

@dataclass
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
class MilestoneStepAnalysis:
    hit: tuple[Milestone, Boundary, MilestoneScore] | None = None
    attempt_detail: JsonObject | None = None
    blocked_detail: JsonObject | None = None
    requires_semantic_review: bool = False

@dataclass(frozen=True)
class ScoringContext:
    task_case: TaskCase | None = None
    matched_boundaries: Mapping[str, Boundary] = field(default_factory=dict)
    matched_snapshots: Mapping[str, StateSnapshot] = field(default_factory=dict)
    metadata: JsonObject = field(default_factory=dict)

@dataclass
class StageInterval:
    stage_id: str
    milestone_id: Optional[str]
    stage_anchor_milestone_id: Optional[str]
    start_boundary_step_index: int
    start_step_index: int
    end_step_index: int
    status: StageStatus
    milestone_score: Optional[MilestoneScore] = None
    evidence: list[str] = field(default_factory=list)

@dataclass
class StageEvaluationResult:
    stage_id: str
    milestone_id: Optional[str]
    status: StageStatus
    stage_score: float
    dimension_scores: dict[Dimension, float]
    dimension_levels: dict[Dimension, EvaluationLevel] = field(default_factory=dict)
    dimension_confidence: dict[Dimension, float] = field(default_factory=dict)
    dimension_uncertainty: dict[Dimension, float] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    diagnosis: list[str] = field(default_factory=list)
    next_weights: dict[Dimension, float] = field(default_factory=dict)
    fatal: bool = False
    hard_constraints_all_pass: bool = True
    required_fields_missing_ratio: float = 0.0
    minefield_score: float = 0.0
    fatal_minefield_score: float = 0.0
    minefield_evidence_is_structural: bool = True
    metadata: JsonObject = field(default_factory=dict)

    def to_dict(self) -> JsonObject:
        """转换为 JSON 可序列化字典。"""
        return {
            "stage_id": self.stage_id,
            "milestone_id": self.milestone_id,
            "status": self.status.value,
            "stage_score": self.stage_score,
            "dimension_scores": _enum_key_dict(self.dimension_scores),
            "dimension_levels": _enum_key_dict(self.dimension_levels),
            "dimension_confidence": _enum_key_dict(self.dimension_confidence),
            "dimension_uncertainty": _enum_key_dict(self.dimension_uncertainty),
            "evidence": list(self.evidence),
            "diagnosis": list(self.diagnosis),
            "next_weights": _enum_key_dict(self.next_weights),
            "fatal": self.fatal,
            "hard_constraints_all_pass": self.hard_constraints_all_pass,
            "required_fields_missing_ratio": self.required_fields_missing_ratio,
            "minefield_score": self.minefield_score,
            "fatal_minefield_score": self.fatal_minefield_score,
            "metadata": dict(self.metadata),
        }

@dataclass
class TrajectoryEvaluationReport:
    task_id: str
    milestone_coverage: str
    overall_score: float
    stage_reports: list[StageEvaluationResult] = field(default_factory=list)
    minefield_matches: list[JsonObject] = field(default_factory=list)
    first_failure_stage_id: Optional[str] = None
    runtime_metrics: JsonObject = field(default_factory=dict)
    metadata: JsonObject = field(default_factory=dict)

    def to_dict(self) -> JsonObject:
        """转换为完整 JSON 可序列化报告。"""
        return {
            "task_id": self.task_id,
            "milestone_coverage": self.milestone_coverage,
            "overall_score": self.overall_score,
            "stage_reports": [stage.to_dict() for stage in self.stage_reports],
            "minefield_matches": list(self.minefield_matches),
            "first_failure_stage_id": self.first_failure_stage_id,
            "runtime_metrics": dict(self.runtime_metrics),
            "metadata": dict(self.metadata),
        }

    def to_summary_dict(self) -> JsonObject:
        """转换为主实验摘要报告。"""
        return {
            "task_id": self.task_id,
            "milestone_coverage": self.milestone_coverage,
            "overall_score": self.overall_score,
            "stage_count": sum(
                (1 for stage in self.stage_reports if _is_matched_milestone_stage(stage))
            ),
            "first_failure_stage_id": self.first_failure_stage_id,
            "runtime_metrics": dict(self.runtime_metrics),
            "elapsed_seconds": self.runtime_metrics.get("elapsed_seconds"),
            "step_count": self.runtime_metrics.get("step_count"),
            "tool_call_count": self.runtime_metrics.get("tool_call_count"),
            "llm_call_count": self.runtime_metrics.get("llm_call_count"),
            "llm_total_tokens": self.runtime_metrics.get("llm_total_tokens"),
            "trajectory_total_tokens": self.runtime_metrics.get("trajectory_total_tokens"),
            "trajectory_cost_available": self.runtime_metrics.get("trajectory_cost_available"),
            "trajectory_latency_available": self.runtime_metrics.get("trajectory_latency_available"),
            "default_prefix_execution_seconds": self.runtime_metrics.get("default_prefix_execution_seconds"),
            "effective_elapsed_seconds": self.runtime_metrics.get("effective_elapsed_seconds"),
            "timing_available": self.runtime_metrics.get("timing_available"),
            "virtual_stop_step_index": self.runtime_metrics.get("virtual_stop_step_index"),
            "metadata": dict(self.metadata),
        }

def _is_matched_milestone_stage(stage: StageEvaluationResult) -> bool:
    """判断阶段是否来自已匹配 milestone 的动态评估。"""
    if stage is None or stage.milestone_id is None:
        return False
    if stage.metadata.get("synthetic_pending_milestone") is True:
        return False
    if stage.status == StageStatus.MISSING:
        return False
    return True

@dataclass(frozen=True)
class EvaluationPolicyState:
    """保存当前阶段采用的跨阶段评估粒度策略。"""
    base_level: EvaluationLevel
    dimension_levels: dict[Dimension, EvaluationLevel]
    reason: str = "initial"

    def to_dict(self) -> JsonObject:
        """转换为可序列化策略字典。"""
        return {
            "base_level": self.base_level.value,
            "dimension_levels": _enum_key_dict(self.dimension_levels),
            "reason": self.reason,
        }

@dataclass
class EvaluationTerminationState:
    """统一描述评估链路中的终止状态。"""
    should_stop: bool = False
    termination_code: str | None = None
    termination_reason: str | None = None
    termination_detail: JsonObject | None = None

    def to_dict(self) -> JsonObject:
        """转换为 JSON 可序列化终止状态。"""
        return {
            "should_stop": self.should_stop,
            "termination_code": self.termination_code,
            "termination_reason": self.termination_reason,
            "termination_detail": self.termination_detail,
        }

def initial_evaluation_policy() -> EvaluationPolicyState:
    """创建默认初始评估策略。"""
    return EvaluationPolicyState(base_level=EvaluationLevel.CHEAP, dimension_levels={dimension: EvaluationLevel.CHEAP for dimension in Dimension}, reason="initial")

@dataclass
class ReadyMilestoneProgress:
    """记录单个 ready milestone 在当前 frontier 中的最好进展。"""
    milestone_id: str
    best_score: float
    best_status: str
    best_boundary_step_index: int | None
    last_improved_step_index: int

@dataclass
class ReadyFrontierProgressWatch:
    """记录当前 ready frontier 的整体无进展观察状态。"""
    frontier_key: tuple[str, ...]
    ready_since_step_index: int
    last_observed_step_index: int
    last_frontier_improved_step_index: int
    stale_frontier_observation_count: int = 0
    frontier_observation_count: int = 0
    milestone_progress: dict[str, ReadyMilestoneProgress] = field(default_factory=dict)

@dataclass
class RuntimeEvaluationState:
    """保存单个 case 运行期间的评估状态。"""
    weights: dict[Dimension, float]
    settlements: list[HarnessStageSettlement]
    matched_settlements: dict[str, HarnessStageSettlement] = field(default_factory=dict)
    stage_reports: list[StageEvaluationResult] = field(default_factory=list)
    match_attempts: list[JsonObject] = field(default_factory=list)
    evaluation_policy: EvaluationPolicyState = field(default_factory=initial_evaluation_policy)
    minefield_matches: list[JsonObject] = field(default_factory=list)
    max_minefield_score: float = 0.0
    fatal_minefield: bool = False
    ready_frontier_progress_watch: ReadyFrontierProgressWatch | None = None
    milestone_frontier: MilestoneFrontierState | None = None
    agent_step_tracker: AgentStepTracker = field(default_factory=AgentStepTracker)
    evaluation_termination: EvaluationTerminationState = field(default_factory=EvaluationTerminationState)

@dataclass
class RuntimeEvaluationDecision:
    """单步运行期阶段评估决策。"""
    next_state: RuntimeEvaluationState
    checkpoint: HarnessStageSettlement | None = None
    stage_result: StageEvaluationResult | None = None
    termination: EvaluationTerminationState = field(default_factory=EvaluationTerminationState)

@dataclass(frozen=True)
class LLMCallMetrics:
    """单次 LLM provider 调用统计。"""
    provider: str
    model: str
    elapsed_seconds: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    success: bool = True
    error: str | None = None

    def to_dict(self) -> JsonObject:
        """转换为 JSON 可序列化字典。"""
        return {
            "provider": self.provider,
            "model": self.model,
            "elapsed_seconds": self.elapsed_seconds,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "success": self.success,
            "error": self.error,
        }

@dataclass
class RuntimeMetricsRecorder:
    """记录单个 case 评估期间的运行统计。"""
    started_monotonic: float = field(default_factory=time.perf_counter)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    llm_calls: list[LLMCallMetrics] = field(default_factory=list)

    def record_llm_call(self, call: LLMCallMetrics) -> None:
        """记录一次 LLM provider 调用。"""
        self.llm_calls.append(call)

@dataclass(frozen=True)
class CaseProgressEvent:
    """跨线程传递的 case 进度事件。"""
    kind: Literal["case_started", "case_advanced", "case_finished"]
    case_id: str
    step_count: int = 0
    message: str | None = None

@dataclass
class CaseProgressState:
    """单个 case 的进度条状态。"""
    case_id: str
    started_at: float
    case_index: int | None = None
    step_count: int = 0
    elapsed_seconds: float = 0.0
    avg_step_seconds: float | None = None
    finished: bool = False

@dataclass(frozen=True)
class LLMMessage:
    """单条对话消息。"""
    role: str
    content: str

@dataclass(frozen=True)
class LLMConfig:
    """LLM provider 运行配置。"""
    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    timeout_seconds: float = 60.0
    temperature: float = 0.0
    max_tokens: int | None = None
    max_retries: int = 3
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 8.0

@dataclass(frozen=True)
class ValidatedJudgePayload:
    """已通过 schema 校验的 Judge payload。"""
    status: StageStatus
    dimension_scores: dict[Dimension, float]
    evidence: list[str]
    diagnosis: list[str]
    metadata: JsonObject

@dataclass(frozen=True)
class HarnessEvaluationOutput:
    """harness 运行与 DynSTEER 评估输出路径。"""
    run_dir: Path
    raw_run_dir: Path
    result_dir: Path
    report_path: Path
    summary_path: Path
    raw_summary_path: Path
    trajectory_path: Path

@dataclass(frozen=True)
class HarnessCaseTask:
    """已加载 TaskCase 后的单 case 执行任务。"""
    order: int
    config: HarnessRunConfig
    case_id: str
    task_case: TaskCase

@dataclass
class ToolSandboxSession:
    """ToolSandbox 原生执行 session。"""
    scenario: object | None
    roles: dict[object, object]
    context: object | None
    initial_state: JsonObject | None
    case_id: str
    raw_output_dir: Path
    initial_max_sandbox_message_index: int
    last_sandbox_message_index: int
    max_messages: int
    system_environment_messages_prepared: bool = False
    finished: bool = False
    stop_reason: str | None = None

def ensure_json_object(value: Any) -> JsonObject:
    """校验输入是否为 JSON 对象。"""
    if value is None or not isinstance(value, dict):
        raise ValueError("输入必须是 JSON 对象")
    return value
