from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal

from dynsteer.model import ConstraintTarget, JsonObject, Operator
from dynsteer.utils import json_safe


@dataclass(frozen=True)
class MilestoneGenerationConfig:
    """控制适配期 milestone 自动生成。"""

    use_origin_milestone: bool = True
    simulated_path_count: int = 6
    generator: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.use_origin_milestone, bool):
            raise TypeError("use_origin_milestone 必须是 bool")
        if isinstance(self.simulated_path_count, bool) or not isinstance(
            self.simulated_path_count, int
        ):
            raise TypeError("simulated_path_count 必须是整数")
        if self.simulated_path_count < 3:
            raise ValueError("simulated_path_count 必须大于等于 3")
        if not isinstance(self.generator, dict):
            raise TypeError("generator 必须是 JSON 对象")


@dataclass(frozen=True)
class PublicEvidence:
    """声明 compiler 可以选择的公开、可执行评分证据。"""

    evidence_id: str
    target: ConstraintTarget
    selector: str
    operator: Operator
    source_ref: str
    evaluator_hint: str = "rule"
    namespace: str | None = None
    expected_policy: Literal["public_literal", "none"] = "public_literal"
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class PublicInvariant:
    """声明公开任务契约中的禁止项或不变量。"""

    invariant_id: str
    description: str
    source_ref: str
    evidence_id: str
    severity: Literal["warning", "error", "fatal"] = "warning"


@dataclass(frozen=True)
class GeneratorTaskView:
    """提供给生成器的公开任务白名单视图。"""

    benchmark: str
    task_id: str
    case_id: str
    language: str
    instruction: str
    public_assets: list[JsonObject]
    tool_schema: JsonObject
    environment_schema: JsonObject
    output_contract: JsonObject
    evidence_catalog: tuple[PublicEvidence, ...]
    invariant_catalog: tuple[PublicInvariant, ...]

    def digest(self) -> str:
        """返回公开视图稳定 JSON 的 SHA-256。"""
        payload = json_safe(self)
        serialized = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GenerationReport:
    """记录 milestone 编译成功或自动拒绝的审计摘要。"""

    generation_status: Literal["generated", "auto_rejected"]
    valid_path_count: int
    distinct_path_count: int
    contract_atom_count: int
    consensus_node_count: int
    graph_valid: bool
    leakage_count: int
    reasons: tuple[str, ...] = ()
    path_summaries: tuple[JsonObject, ...] = ()

    def to_dict(self) -> JsonObject:
        """转换为 JSON 可序列化字典。"""
        return json_safe(self)


class MilestoneGenerationError(ValueError):
    """自动生成被 compiler 拒绝时抛出，并携带完整报告。"""

    def __init__(self, message: str, report: GenerationReport) -> None:
        super().__init__(message)
        self.report = report
