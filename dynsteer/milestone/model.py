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
    max_candidate_path_count: int = 6
    enable_repair: bool = True
    generator: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.use_origin_milestone, bool):
            raise TypeError("use_origin_milestone 必须是 bool")
        if isinstance(self.max_candidate_path_count, bool) or not isinstance(
            self.max_candidate_path_count, int
        ):
            raise TypeError("max_candidate_path_count 必须是整数")
        if not 1 <= self.max_candidate_path_count <= 8:
            raise ValueError("max_candidate_path_count 必须位于 1～8")
        if not isinstance(self.enable_repair, bool):
            raise TypeError("enable_repair 必须是 bool")
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
    initial_state: JsonObject
    tool_schema: JsonObject
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
    """记录多路径生成、模拟和聚合的审计摘要。"""

    generation_status: Literal["generated"]
    turn_dispositions: JsonObject
    max_candidate_path_count: int
    returned_path_count: int
    parsed_path_count: int
    simulatable_path_count: int
    final_path_count: int
    selected_round: int | None
    graph_returned: bool
    graph_empty: bool
    empty_reason: str | None
    minefield_count: int
    repair_triggered: bool
    counterexample_removed_operations: tuple[str, ...] = ()
    round_summaries: tuple[JsonObject, ...] = ()
    path_summaries: tuple[JsonObject, ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> JsonObject:
        return json_safe(self)
