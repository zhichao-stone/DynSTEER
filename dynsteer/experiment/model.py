from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

from dynsteer.milestone.model import MilestoneGenerationConfig
from dynsteer.model import EvaluationLevel, JsonObject, ThresholdConfig


class ExperimentMethod(str, Enum):
    DEFAULT = "default"
    DYNSTEER_EVALUATE = "dynsteer_evaluate"
    DYNSTEER_REPLAY = "dynsteer_replay"
    DYNSTEER_REPLAY_STATIC = "dynsteer_replay_static"
    DYNSTEER_REPLAY_STATIC_WEIGHTING = "dynsteer_replay_static_weighting"
    DYNSTEER_REPLAY_STATIC_ROUTING = "dynsteer_replay_static_routing"

@dataclass(frozen=True)
class EvaluationStrategyConfig:
    dynamic_routing: bool = True
    dynamic_weighting: bool = True
    policy_stop: bool = True
    fixed_judge_level: EvaluationLevel = EvaluationLevel.CHEAP
    replay_continue_after_virtual_stop: bool = False
    metadata: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.dynamic_routing, bool):
            raise TypeError("dynamic_routing 必须是 bool")
        if not isinstance(self.dynamic_weighting, bool):
            raise TypeError("dynamic_weighting 必须是 bool")
        if not isinstance(self.policy_stop, bool):
            raise TypeError("policy_stop 必须是 bool")
        if not isinstance(self.fixed_judge_level, EvaluationLevel):
            raise TypeError("fixed_judge_level 必须是 EvaluationLevel")
        if not isinstance(self.replay_continue_after_virtual_stop, bool):
            raise TypeError("replay_continue_after_virtual_stop 必须是 bool")

    def to_dict(self) -> JsonObject:
        return asdict(self)

@dataclass(frozen=True)
class ExperimentRunSpec:
    experiment_id: str
    benchmark: str
    data_root: Path
    runs_dir: Path
    results_dir: Path
    case_ids: tuple[str, ...] | None
    model_id: str
    repeat_index: int
    method: ExperimentMethod
    repeat_count: int = 1
    judge_profile: str | None = None
    judge_config: JsonObject = field(default_factory=dict)
    threshold_profile: str | None = None
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    strategy: EvaluationStrategyConfig = field(default_factory=EvaluationStrategyConfig)
    milestone_generation: MilestoneGenerationConfig = field(
        default_factory=MilestoneGenerationConfig
    )
    metadata: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("experiment_id 不能为空")
        if not self.benchmark.strip():
            raise ValueError("benchmark 不能为空")
        if self.data_root is None or self.runs_dir is None or self.results_dir is None:
            raise ValueError("data_root、runs_dir 和 results_dir 不能为空")
        if self.case_ids is not None and any((not case_id.strip() for case_id in self.case_ids)):
            raise ValueError("case_ids 不能包含空字符串")
        if not self.model_id.strip():
            raise ValueError("model_id 不能为空")
        if self.repeat_index < 0:
            raise ValueError("repeat_index 不能为负数")
        if self.repeat_count < 1 or self.repeat_index >= self.repeat_count:
            raise ValueError("repeat_index must satisfy 0 <= repeat_index < repeat_count")

    def to_metadata(self) -> JsonObject:
        metadata: JsonObject = dict(self.metadata)
        metadata.update({
            "experiment_id": self.experiment_id,
            "method": self.method.value,
            "model_id": self.model_id,
            "repeat_index": self.repeat_index,
            "repeat_count": self.repeat_count,
            "judge_profile": self.judge_profile,
            "judge": dict(self.judge_config),
            "threshold_profile": self.threshold_profile,
            "thresholds": asdict(self.thresholds),
            "strategy": self.strategy.to_dict()
        })
        return metadata

@dataclass(frozen=True)
class ExperimentCaseResult:
    experiment_id: str
    benchmark: str
    case_id: str
    model_id: str
    repeat_index: int
    method: ExperimentMethod
    score: float | None = None
    milestone_coverage: str | None = None
    minefield_match_count: int | None = None
    termination_code: str | None = None
    termination_detail: JsonObject = field(default_factory=dict)
    adaptation_cost: JsonObject = field(default_factory=dict)
    adaptation_usage: JsonObject = field(default_factory=dict)
    runtime_metrics: JsonObject = field(default_factory=dict)
    output_paths: JsonObject = field(default_factory=dict)

    def to_index_dict(self) -> JsonObject:
        return {
            "score": self.score,
            "milestone_coverage": self.milestone_coverage,
            "minefield_match_count": self.minefield_match_count,
            "termination_code": self.termination_code,
            "termination_detail": dict(self.termination_detail),
            "adaptation_cost": dict(self.adaptation_cost),
            "adaptation_usage": dict(self.adaptation_usage),
            "runtime_metrics": dict(self.runtime_metrics),
            "output_paths": dict(self.output_paths),
        }
