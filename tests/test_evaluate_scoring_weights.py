import subprocess
import sys

import pytest

from dynsteer.config import TASK_TYPE_WEIGHTS
from dynsteer.evaluate.scoring import stage_score_from_dimensions
from dynsteer.evaluate.weights import normalize_weights, select_initial_weights, update_weights
from dynsteer.model import Dimension, DynamicWeightConfig, TaskCase, TaskType


def test_stage_score_from_dimensions_handles_empty_and_zero_weights() -> None:
    """覆盖阶段聚合分在空维度与零权重场景下的兜底行为。"""
    assert stage_score_from_dimensions({}, {}) == 0.0

    scores = {Dimension.PROGRESS: 0.2, Dimension.SAFETY: 0.8}
    weights = {dimension: 0.0 for dimension in Dimension}

    assert stage_score_from_dimensions(scores, weights) == pytest.approx(0.5)


def test_stage_score_from_dimensions_uses_present_weighted_dimensions() -> None:
    """覆盖阶段聚合分只根据已产出的维度分数计算。"""
    scores = {Dimension.PROGRESS: 0.4, Dimension.SAFETY: 1.0}
    weights = normalize_weights({Dimension.PROGRESS: 1.0, Dimension.SAFETY: 3.0})

    assert stage_score_from_dimensions(scores, weights) == pytest.approx(0.85)


def test_select_initial_weights_uses_default_and_merges_task_types() -> None:
    """覆盖初始权重的默认任务类型和多任务类型合并。"""
    default_case = TaskCase(task_id="task", task_description="desc", case_id="case")
    assert select_initial_weights(default_case) == normalize_weights(TASK_TYPE_WEIGHTS[TaskType.GENERAL])

    merged_case = TaskCase(
        task_id="task",
        task_description="desc",
        case_id="case",
        task_types=[TaskType.GENERAL, TaskType.SAFETY_SENSITIVE],
    )
    weights = select_initial_weights(merged_case)

    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights[Dimension.SAFETY] > TASK_TYPE_WEIGHTS[TaskType.GENERAL][Dimension.SAFETY]


def test_update_weights_prioritizes_low_score_high_uncertainty_dimensions() -> None:
    """覆盖动态权重对低分高不确定性维度的上调。"""
    current = normalize_weights({dimension: 1.0 for dimension in Dimension})
    next_weights = update_weights(
        current=current,
        scores={Dimension.PROGRESS: 0.2, Dimension.SAFETY: 0.9},
        dimension_uncertainty={Dimension.PROGRESS: 0.8, Dimension.SAFETY: 0.1},
        config=DynamicWeightConfig(alpha=1.0, beta=1.0),
    )

    assert sum(next_weights.values()) == pytest.approx(1.0)
    assert next_weights[Dimension.PROGRESS] > next_weights[Dimension.SAFETY]


def test_scoring_and_judges_import_order_has_no_cycle() -> None:
    """覆盖 scoring 与 judges 的正反导入顺序。"""
    commands = [
        "import dynsteer.evaluate.scoring; import dynsteer.judges",
        "import dynsteer.judges; import dynsteer.evaluate.scoring",
    ]
    for command in commands:
        completed = subprocess.run([sys.executable, "-c", command], check=False)
        assert completed.returncode == 0
