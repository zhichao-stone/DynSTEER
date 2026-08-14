from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from dynsteer.model import Actor, ConstraintTarget, JsonObject, Operator
from dynsteer.utils import json_safe, stable_json_digest


TurnDisposition = Literal[
    "executable", "needs_clarification", "no_action", "response_only"
]


@dataclass(frozen=True)
class MilestoneGenerationConfig:
    """控制执行前 milestone 自动生成。"""

    use_origin_milestone: bool = True
    target_candidate_graph_count: int = 6
    max_candidate_batch_count: int = 4
    generator: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.use_origin_milestone, bool):
            raise TypeError("use_origin_milestone 必须是 bool")
        if isinstance(self.target_candidate_graph_count, bool) or not isinstance(
            self.target_candidate_graph_count, int
        ):
            raise TypeError("target_candidate_graph_count 必须是整数")
        if not 2 <= self.target_candidate_graph_count <= 8:
            raise ValueError("target_candidate_graph_count 必须位于 2～8")
        if isinstance(self.max_candidate_batch_count, bool) or not isinstance(
            self.max_candidate_batch_count, int
        ):
            raise TypeError("max_candidate_batch_count 必须是整数")
        if not 1 <= self.max_candidate_batch_count <= 4:
            raise ValueError("max_candidate_batch_count 必须位于 1～4")
        if self.target_candidate_graph_count > 2 * self.max_candidate_batch_count:
            raise ValueError("候选图目标数不能超过批次数的两倍")
        if not isinstance(self.generator, dict):
            raise TypeError("generator 必须是 JSON 对象")


@dataclass(frozen=True)
class GeneratorTurn:
    """描述执行前已公开的一个用户轮次。"""

    turn_id: str
    instruction: str
    source_ref: str


@dataclass(frozen=True)
class PublicEvidence:
    """声明 compiler 可以选择的公开工具调用证据。"""

    evidence_id: str
    target: ConstraintTarget
    selector: str
    operator: Operator
    source_ref: str
    evaluator_hint: str = "rule"
    namespace: str | None = None
    expected_policy: Literal["public_literal", "none"] = "public_literal"
    role: Literal["milestone", "context", "minefield"] = "milestone"
    matching_route: tuple[Actor, Actor] | None = None
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class GeneratorTaskView:
    """提供给生成器的执行前白名单任务视图。"""

    benchmark: str
    task_id: str
    case_id: str
    language: str
    turns: tuple[GeneratorTurn, ...]
    public_assets: list[JsonObject]
    public_state: JsonObject
    simulation_state: JsonObject
    tool_schema: JsonObject
    tool_contracts: JsonObject
    environment_rules: JsonObject
    evidence_catalog: tuple[PublicEvidence, ...]

    @property
    def instruction(self) -> str:
        return self.turns[0].instruction if self.turns else ""

    def digest(self) -> str:
        """返回全部合法生成输入的稳定摘要。"""
        return stable_json_digest(self)


@dataclass(frozen=True)
class GenerationReport:
    """记录候选图生成、校验和聚合的审计摘要。"""

    generation_status: Literal["generated", "generation_failed"]
    turn_dispositions: JsonObject
    target_candidate_graph_count: int
    max_candidate_batch_count: int
    request_count: int
    request_success_count: int
    returned_graph_count: int
    parsed_graph_count: int
    valid_graph_count: int
    accepted_observation_count: int
    global_unique_graph_count: int
    within_batch_duplicate_count: int
    rejected_graph_count: int
    target_reached: bool
    graph_returned: bool
    graph_empty: bool
    empty_reason: str | None
    aggregated_node_count: int
    aggregated_edge_count: int
    minefield_count: int
    low_sample_count: bool
    low_diversity: bool
    cross_request_signature_counts: JsonObject = field(default_factory=dict)
    candidate_summaries: tuple[JsonObject, ...] = ()
    aggregation_support: JsonObject = field(default_factory=dict)
    validation_issues: tuple[JsonObject, ...] = ()
    response_digests: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> JsonObject:
        return json_safe(self)
