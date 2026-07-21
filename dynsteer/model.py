from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any, Literal, Mapping, Optional, Union

if TYPE_CHECKING:
    from dynsteer.harness.model import HarnessRunConfig, HarnessStageSettlement


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


## 配置模型

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


## 轨迹与状态模型

@dataclass
class ToolCall:
    name: str
    arguments: JsonObject = field(default_factory=dict)


@dataclass
class ToolResult:
    success: bool
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
    actor: Actor
    event_type: EventType
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
    """跟踪单个串行 agent outbound 的闭包状态。

    入参：
        pending_outbound: 尚未收到反馈的 Agent -> X 原始 step。
        pending_steps: 当前未闭合 agent step 已收集的 raw steps。
        completed_count: 已闭合的 agent step 数量。
    输出：
        `ingest()` 在闭包完成时返回完整闭包，否则返回 None。
    """

    pending_outbound: TrajectoryStep | None = None
    pending_steps: list[TrajectoryStep] = field(default_factory=list)
    completed_count: int = 0

    def ingest(self, raw_step: TrajectoryStep) -> AgentStepClosure | None:
        """摄入一条 raw step，并在闭合 agent step 时返回完整闭包。"""
        if self._is_agent_outbound(raw_step):
            if self.pending_outbound is not None:
                raise AgentStepProtocolError(
                    "上一个 agent outbound 尚未闭合，不能继续接收新的 agent outbound: "
                    f"pending_step_id={self.pending_outbound.step_id}, current_step_id={raw_step.step_id}"
                )
            self.pending_outbound = raw_step
            self.pending_steps = [raw_step]
            return None

        pending = self.pending_outbound
        if pending is None:
            return None
        self.pending_steps.append(raw_step)
        if raw_step.actor == pending.recipient and raw_step.recipient == Actor.AGENT:
            return self._complete_pending()
        return None

    def finalize(self) -> AgentStepClosure | None:
        """自然结束时闭合允许自闭合的终局 agent message。"""
        pending = self.pending_outbound
        if pending is None:
            return None
        if (
            pending.actor == Actor.AGENT
            and pending.recipient == Actor.USER
            and pending.event_type in {EventType.MESSAGE, EventType.FINAL}
        ):
            return self._complete_pending()
        return None

    def _complete_pending(self) -> AgentStepClosure:
        """完成当前 pending closure，并重置 tracker 状态。"""
        steps = tuple(self.pending_steps)
        if not steps:
            raise AgentStepProtocolError("agent step closure 缺少 pending steps")
        self.pending_outbound = None
        self.pending_steps = []
        self.completed_count += 1
        return AgentStepClosure(steps=steps)

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


## Milestone 与约束模型

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
    mode: str
    value: float


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


## 任务 Case 与轨迹模型

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
    run_id: str
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

    ## 可用接口
    def append_step(self, step: TrajectoryStep) -> None:
        """追加单个 step，并同步维护首个 step 与 boundary 后继表。"""
        self.steps.append(step)
        self._append_step_index(step.index)

    def get_interval(self, min_index: int, max_index: int) -> list[TrajectoryStep]:
        """返回指定 step index 区间内的轨迹步骤。"""
        # 保持区间语义有效，避免调用方传入反向边界。
        if min_index >= max_index:
            raise ValueError("min_index 必须小于 max_index")
        if self.latest_step_index is None:
            return []
        if max_index < self.first_step_index or min_index >= self.latest_step_index:
            return []

        # synthetic boundary 可能小于首个真实 step，需要钳到 first_step_index - 1，
        # 这样左开区间不会漏掉首个真实 step。
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


## 评分与阶段评估模型

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
            "dimension_scores": {key.value: value for key, value in self.dimension_scores.items()},
            "dimension_levels": {key.value: value.value for key, value in self.dimension_levels.items()},
            "dimension_confidence": {key.value: value for key, value in self.dimension_confidence.items()},
            "dimension_uncertainty": {key.value: value for key, value in self.dimension_uncertainty.items()},
            "evidence": list(self.evidence),
            "diagnosis": list(self.diagnosis),
            "next_weights": {key.value: value for key, value in self.next_weights.items()},
            "fatal": self.fatal,
            "hard_constraints_all_pass": self.hard_constraints_all_pass,
            "required_fields_missing_ratio": self.required_fields_missing_ratio,
            "minefield_score": self.minefield_score,
            "fatal_minefield_score": self.fatal_minefield_score,
            "metadata": dict(self.metadata),
        }


@dataclass
class TrajectoryEvaluationReport:
    run_id: str
    task_id: str
    milestone_coverage: str
    overall_score: float
    stage_reports: list[StageEvaluationResult] = field(default_factory=list)
    minefield_matches: list[JsonObject] = field(default_factory=list)
    first_failure_stage_id: Optional[str] = None
    runtime_metrics: JsonObject = field(default_factory=dict)

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
            "runtime_metrics": dict(self.runtime_metrics),
        }

    def to_summary_dict(self) -> JsonObject:
        """转换为主实验摘要报告。"""
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "milestone_coverage": self.milestone_coverage,
            "overall_score": self.overall_score,
            "stage_count": sum(1 for stage in self.stage_reports if _is_matched_milestone_stage(stage)),
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


## 评估策略模型

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
            "dimension_levels": {dimension.value: level.value for dimension, level in self.dimension_levels.items()},
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
    return EvaluationPolicyState(
        base_level=EvaluationLevel.CHEAP,
        dimension_levels={dimension: EvaluationLevel.CHEAP for dimension in Dimension},
        reason="initial",
    )


## 运行期评估状态模型

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


## 运行指标与进度模型

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


@dataclass
class CaseProgressBars:
    """单个 case 在终端中占用的标题行与进度条行。"""

    title_bar: Any
    progress_bar: Any


## LLM 与 Judge 模型

@dataclass(frozen=True)
class LLMMessage:
    """单条对话消息。"""

    role: str
    content: str


@dataclass(frozen=True)
class LLMTokenLogprob:
    """单个输出 token 的对数概率。"""

    token: str
    logprob: float


@dataclass(frozen=True)
class LLMResponse:
    """LLM 文本响应及可选 token logprob。"""

    text: str
    token_logprobs: list[LLMTokenLogprob] = field(default_factory=list)


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


## Harness 与适配器模型

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
    run_id: str
    raw_output_dir: Path
    initial_max_sandbox_message_index: int
    last_sandbox_message_index: int
    max_messages: int
    system_environment_messages_prepared: bool = False
    finished: bool = False
    stop_reason: str | None = None


## 通用校验函数

def ensure_json_object(value: Any) -> JsonObject:
    """校验输入是否为 JSON 对象。"""
    if value is None or not isinstance(value, dict):
        raise ValueError("输入必须是 JSON 对象")
    return value
