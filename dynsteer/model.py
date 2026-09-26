from __future__ import annotations
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any, Mapping, Optional, Union

if TYPE_CHECKING:
    from dynsteer.agent import OpenAIClientConfig, ToolAgentResult, ToolExecutionResult
    from dynsteer.harness.model import HarnessRunConfig, HarnessStageSettlement
    from dynsteer.adapter.skillsbench.adapter import SkillsBenchTask
    from dynsteer.adapter.skillsbench.runtime import SkillsBenchRuntime
    from dynsteer.adapter.swebench_pro.adapter import SWEBenchProSample
    from dynsteer.adapter.swebench_pro.evaluator import NativeEvaluationResult
    from dynsteer.adapter.swebench_pro.runtime import SWEBenchProRuntime


JsonValue = Union[str, int, float, bool, None, dict[str, "JsonValue"], list["JsonValue"]]
JsonObject = dict[str, JsonValue]
MISSING = object()
DEFAULT_JUDGE_TEMPERATURE = 0.2

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
        pending_steps: 按稳定内部 key 保存各闭包已收集的 raw steps；首元素即 outbound。
        completed_count: 已闭合的 agent step 数量。
    输出：
        `ingest()` 在闭包完成时返回完整闭包，否则返回 None。
    """
    pending_steps: dict[str, list[TrajectoryStep]] = field(default_factory=dict)
    completed_count: int = 0

    def ingest(self, raw_step: TrajectoryStep) -> AgentStepClosure | None:
        """摄入一条 raw step，并在闭合 agent step 时返回完整闭包。"""
        if raw_step is None:
            raise ValueError("raw_step 不能为空")
        if self._is_agent_outbound(raw_step):
            self._add_pending(raw_step)
            return None
        if not self.pending_steps:
            return None

        key = self._matching_pending_key(raw_step)
        if key is not None:
            self.pending_steps[key].append(raw_step)
            return self._complete_pending(key)
        if len(self.pending_steps) == 1:
            only_key = next(iter(self.pending_steps))
            self.pending_steps[only_key].append(raw_step)
            return None
        raise self._protocol_error("并行 pending 期间的 step 无法唯一归属", raw_step)

    def finalize(self) -> AgentStepClosure | None:
        """自然结束时闭合允许自闭合的唯一终局 agent message。"""
        if len(self.pending_steps) != 1:
            return None
        key, steps = next(iter(self.pending_steps.items()))
        pending = steps[0]
        if pending.actor == Actor.AGENT and pending.recipient == Actor.USER and pending.event_type in {EventType.MESSAGE, EventType.FINAL}:
            return self._complete_pending(key)
        return None

    ## 内部函数

    def _add_pending(self, raw_step: TrajectoryStep) -> None:
        """新增 outbound，并校验连续 outbound 是否为合法并行工具调用。"""
        key = self._pending_key(raw_step)
        if self.pending_steps:
            pending_values = [steps[0] for steps in self.pending_steps.values()]
            if not all(self._is_parallel_tool_outbound(step) for step in [*pending_values, raw_step]):
                raise self._protocol_error("上一个 agent outbound 尚未闭合，不能继续接收新的 agent outbound", raw_step)
            missing_ids = [step.step_id for step in [*pending_values, raw_step] if self._tool_call_id(step) is None]
            if missing_ids:
                raise self._protocol_error(f"并行 tool outbound 缺少 correlation id: step_ids={missing_ids}", raw_step)
            if key in self.pending_steps:
                raise self._protocol_error("并行 tool outbound 使用重复 correlation id", raw_step)
        self.pending_steps[key] = [raw_step]

    def _complete_pending(self, key: str) -> AgentStepClosure:
        """完成指定 pending closure，并更新 tracker 状态。"""
        steps = tuple(self.pending_steps.get(key, []))
        if not steps:
            raise AgentStepProtocolError("agent step closure 缺少 pending steps")
        del self.pending_steps[key]
        self.completed_count += 1
        return AgentStepClosure(steps=steps)

    def _matching_pending_key(self, feedback: TrajectoryStep) -> str | None:
        """按 correlation ID 优先、reciprocal route 次之解析 feedback。"""
        call_id = self._tool_call_id(feedback)
        if call_id is not None:
            key = f"tool:{call_id}"
            pending_steps = self.pending_steps.get(key)
            if not pending_steps:
                raise self._protocol_error("feedback 使用未知 correlation id", feedback)
            pending = pending_steps[0]
            if not self._is_reciprocal_feedback(pending, feedback):
                raise self._protocol_error("feedback correlation id 匹配但 route 不匹配", feedback)
            return key

        candidates = [
            key
            for key, steps in self.pending_steps.items()
            if self._is_reciprocal_feedback(steps[0], feedback)
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
        pending_steps = [steps[0] for steps in self.pending_steps.values()]
        pending_ids = [step.step_id for step in pending_steps]
        pending_call_ids = [
            call_id
            for step in pending_steps
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
    expected_template: JsonValue = None
    namespace: Optional[str] = None
    reference_milestone_id: Optional[str] = None
    weight: float = 1.0
    threshold: float = 1.0
    hard: bool = False
    evaluator_hint: str = "rule"
    stage_goal_semantics: JsonObject | None = None
    metadata: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.expected is not None and self.expected_template is not None:
            raise ValueError("Constraint.expected 与 expected_template 不能同时非空")
        _validate_binding_template(self.expected_template)


def _validate_binding_template(value: JsonValue) -> None:
    """递归校验 expected_template 中固定的运行时 binding 叶节点。"""
    if isinstance(value, dict):
        if "$binding" in value:
            if set(value) != {"$binding"} or not isinstance(value["$binding"], dict):
                raise ValueError("expected_template binding 叶节点结构无效")
            binding = value["$binding"]
            if set(binding) != {"source_milestone_id", "selector", "cardinality"}:
                raise ValueError("expected_template binding 字段无效")
            if not isinstance(binding["source_milestone_id"], str) or not binding["source_milestone_id"]:
                raise ValueError("expected_template source_milestone_id 必须是非空字符串")
            if not isinstance(binding["selector"], str) or not binding["selector"]:
                raise ValueError("expected_template selector 必须是非空字符串")
            if binding["cardinality"] not in {"one", "all"}:
                raise ValueError("expected_template cardinality 只允许 one/all")
            return
        for item in value.values():
            _validate_binding_template(item)
    elif isinstance(value, list):
        for item in value:
            _validate_binding_template(item)

@dataclass
class Milestone:
    milestone_id: str
    name: str
    description: str
    constraints: list[Constraint]
    pass_threshold: Optional[float] = None
    metadata: JsonObject = field(default_factory=dict)

    matching_route: tuple[Actor, Actor] | None = None

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
class MilestoneTopology:
    milestone_by_id: Mapping[str, Milestone]
    predecessors_by_id: Mapping[str, tuple[str, ...]]
    successors_by_id: Mapping[str, tuple[str, ...]]
    stage_anchor_by_id: Mapping[str, str]
    order_by_id: Mapping[str, int]
    terminal_ids: tuple[str, ...]
    finish_anchor_id: str


@dataclass
class MilestoneGraph:
    nodes: list[Milestone] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    minefields: list[Minefield] = field(default_factory=list)
    metadata: JsonObject = field(default_factory=dict)
    topology: MilestoneTopology | None = field(default=None, init=False, repr=False, metadata={"json_safe": False})

    @property
    def finish_anchor_id(self) -> str:
        return self.topology.finish_anchor_id if self.topology is not None else "__start__"

@dataclass
class MilestoneFrontierState:
    """保存 milestone ready frontier 的运行期增量状态。

    入参：
        topology: milestone graph 的静态拓扑索引。
        remaining_predecessor_count: 未匹配前驱数量。
        ready_ids: 当前 ready frontier，按 graph 原始拓扑顺序维护。
    输出：
        供运行期 step 分析和 match 后推进复用的状态对象。
    """
    topology: MilestoneTopology
    remaining_predecessor_count: dict[str, int]
    ready_ids: list[str]

@dataclass
class TaskCase:
    task_id: str
    task_description: str
    case_id: str
    environment_schema: JsonObject = field(default_factory=dict)
    tool_schema: JsonObject = field(default_factory=dict)
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
    _snapshot_positions: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        """校验已有 step 顺序并建立 snapshot 索引。"""
        for position, step in enumerate(self.steps):
            if step is None:
                raise ValueError("trajectory.steps 不能包含空 step")
            if position and step.index <= self.steps[position - 1].index:
                raise ValueError("trajectory step index 必须递增")
        self._rebuild_snapshot_positions()

    @property
    def first_step_index(self) -> int:
        """返回首个 step index；空轨迹返回 0。"""
        return self.steps[0].index if self.steps else 0

    @property
    def latest_step_index(self) -> int | None:
        """返回最后一个 step index。"""
        return self.steps[-1].index if self.steps else None

    def append_step(self, step: TrajectoryStep) -> None:
        """按递增 index 追加单个 step。"""
        if self.steps and step.index <= self.steps[-1].index:
            raise ValueError("trajectory step index 必须递增")
        self.steps.append(step)

    def first_step_after(self, boundary_index: int, end_index: int) -> int:
        """返回 `(boundary_index, end_index]` 内首个 step index。"""
        position = bisect_right(self.steps, boundary_index, key=lambda step: step.index)
        return self.steps[position].index if position < len(self.steps) and self.steps[position].index <= end_index else end_index

    def extend_snapshots(self, snapshots: list[StateSnapshot]) -> None:
        """按 snapshot_id 去重追加状态快照。"""
        needs_sort = False
        for snapshot in snapshots:
            position = self._snapshot_positions.get(snapshot.snapshot_id)
            if position is not None:
                self.snapshots[position] = snapshot
                key = _snapshot_key(snapshot)
                needs_sort |= position > 0 and key < _snapshot_key(self.snapshots[position - 1])
                needs_sort |= position + 1 < len(self.snapshots) and key > _snapshot_key(self.snapshots[position + 1])
            else:
                needs_sort |= bool(self.snapshots and _snapshot_key(snapshot) < _snapshot_key(self.snapshots[-1]))
                self._snapshot_positions[snapshot.snapshot_id] = len(self.snapshots)
                self.snapshots.append(snapshot)
        if needs_sort:
            self.snapshots.sort(key=_snapshot_key)
            self._rebuild_snapshot_positions()

    def snapshot_at_or_before(self, step_index: int) -> StateSnapshot | None:
        """Return the latest snapshot whose step index is at most ``step_index``."""
        position = bisect_right(self.snapshots, step_index, key=lambda snapshot: snapshot.after_step_index) - 1
        return self.snapshots[position] if position >= 0 else None

    def get_interval(self, min_index: int, max_index: int) -> list[TrajectoryStep]:
        """返回指定 step index 区间内的轨迹步骤。"""
        if min_index >= max_index:
            raise ValueError("min_index 必须小于 max_index")
        if self.latest_step_index is None:
            return []
        if max_index < self.first_step_index or min_index >= self.latest_step_index:
            return []
        left = bisect_right(self.steps, min_index, key=lambda step: step.index)
        right = bisect_right(self.steps, max_index, key=lambda step: step.index)
        return self.steps[left:right]

    def _rebuild_snapshot_positions(self) -> None:
        """校验 snapshot id 唯一性并重建位置索引。"""
        self._snapshot_positions = {}
        for position, snapshot in enumerate(self.snapshots):
            if snapshot.snapshot_id in self._snapshot_positions:
                raise ValueError(f"trajectory snapshot_id 重复: {snapshot.snapshot_id}")
            self._snapshot_positions[snapshot.snapshot_id] = position


def _snapshot_key(snapshot: StateSnapshot) -> tuple[int, str]:
    """返回 snapshot 的稳定排序键。"""
    return snapshot.after_step_index, snapshot.snapshot_id

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
    hit: tuple[Milestone, TrajectoryStep, MilestoneScore] | None = None
    attempt_detail: JsonObject | None = None
    blocked_detail: JsonObject | None = None
    requires_semantic_review: bool = False

@dataclass(frozen=True)
class ScoringContext:
    task_case: TaskCase | None = None
    trajectory: Trajectory | None = None
    matched_step_indexes: Mapping[str, int] = field(default_factory=dict)
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
    termination_code: str | None = None
    termination_reason: str | None = None
    termination_detail: JsonObject | None = None

    @property
    def should_stop(self) -> bool:
        """是否已产生明确终止代码。"""
        return self.termination_code is not None

    def to_dict(self) -> JsonObject:
        """转换为 JSON 可序列化终止状态。"""
        return {
            "should_stop": self.should_stop,
            "code": self.termination_code,
            "reason": self.termination_reason,
            "detail": self.termination_detail or {},
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
    milestone_frontier: MilestoneFrontierState
    matched_settlements: dict[str, HarnessStageSettlement] = field(default_factory=dict)
    reference_anchor_snapshots: dict[str, StateSnapshot] = field(default_factory=dict)
    referenced_milestone_ids: frozenset[str] = frozenset()
    scoring_context_cache: ScoringContext | None = None
    stage_reports: list[StageEvaluationResult] = field(default_factory=list)
    match_attempts: list[JsonObject] = field(default_factory=list)
    final_milestone_diagnostics: list[JsonObject] | None = None
    evaluation_policy: EvaluationPolicyState = field(default_factory=initial_evaluation_policy)
    minefield_matches: list[JsonObject] = field(default_factory=list)
    evaluated_minefield_sources: set[str] = field(default_factory=set)
    max_minefield_score: float = 0.0
    fatal_minefield: bool = False
    ready_frontier_progress_watch: ReadyFrontierProgressWatch | None = None
    agent_step_tracker: AgentStepTracker = field(default_factory=AgentStepTracker)
    evaluation_termination: EvaluationTerminationState = field(default_factory=EvaluationTerminationState)
    interventions: list[JsonObject] = field(default_factory=list)

@dataclass
class RuntimeEvaluationDecision:
    """单步运行期阶段评估决策。"""
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
    case_id: str
    step_count: int = 0

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
    temperature: float = DEFAULT_JUDGE_TEMPERATURE
    max_tokens: int | None = None
    max_retries: int = 3
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 8.0
    seed: int | None = None

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
    raw_run_dir: Path
    result_dir: Path

@dataclass(frozen=True)
class HarnessCaseTask:
    """已加载 TaskCase 后的单 case 执行任务。"""
    order: int
    config: HarnessRunConfig
    task_case: TaskCase

@dataclass
class ToolSandboxSession:
    """ToolSandbox 原生执行 session。"""
    scenario: object | None
    roles: dict[object, object]
    context: object | None
    initial_state: JsonObject | None
    case_id: str
    initial_max_sandbox_message_index: int
    last_sandbox_message_index: int
    max_messages: int
    system_environment_messages_prepared: bool = False
    stop_reason: str | None = None
    termination_reason: str | None = None
    usage_recorder: object | None = None
    finished: bool = False


@dataclass
class SWEBenchProSession:
    """SWE-bench Pro 源码直连执行 session。"""
    case_id: str
    task_id: str
    sample: SWEBenchProSample
    client_config: OpenAIClientConfig
    config: HarnessRunConfig
    raw_output_dir: Path
    runtime: SWEBenchProRuntime | None
    agent_result: ToolAgentResult | None
    probe: JsonObject
    patch: str | None
    native_result: NativeEvaluationResult | None
    stop_reason: str | None
    finished: bool = False


@dataclass
class SkillsBenchSession:
    """SkillsBench 源码直连执行 session。"""
    case_id: str
    task_id: str
    task: SkillsBenchTask
    client_config: OpenAIClientConfig
    config: HarnessRunConfig
    raw_output_dir: Path
    start_state: JsonObject
    runtime: SkillsBenchRuntime | None
    agent_result: ToolAgentResult | None
    verifier_result: ToolExecutionResult | None
    reward: float | None
    stop_reason: str | None
    finished: bool = False

def ensure_json_object(value: Any) -> JsonObject:
    """校验输入是否为 JSON 对象。"""
    if value is None or not isinstance(value, dict):
        raise ValueError("输入必须是 JSON 对象")
    return value
