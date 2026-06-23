from dynsteer.config import TASK_TYPE_WEIGHTS
from dynsteer.evaluate import select_initial_weights, update_weights
from dynsteer.model import Dimension, TaskCase, TaskType


def test_task_type_weights_are_normalized() -> None:
    for weights in TASK_TYPE_WEIGHTS.values():
        assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_select_initial_weights_uses_general_when_empty() -> None:
    task = TaskCase(task_id="t", task_description="demo")

    weights = select_initial_weights(task)

    assert abs(sum(weights.values()) - 1.0) < 1e-9
    assert weights[Dimension.PROGRESS] > weights[Dimension.RECOVERY]


def test_select_initial_weights_mixes_multiple_task_types() -> None:
    task = TaskCase(
        task_id="t",
        task_description="demo",
        task_types=[TaskType.STATEFUL_TOOL, TaskType.SAFETY_SENSITIVE],
    )

    weights = select_initial_weights(task)

    assert abs(sum(weights.values()) - 1.0) < 1e-9
    assert weights[Dimension.SAFETY] > 0.2


def test_update_weights_increases_low_dimension() -> None:
    current = {dimension: 1 / len(Dimension) for dimension in Dimension}
    scores = {dimension: 0.9 for dimension in Dimension}
    scores[Dimension.SAFETY] = 0.2

    updated = update_weights(current, scores, uncertainty=0.4)

    assert abs(sum(updated.values()) - 1.0) < 1e-9
    assert updated[Dimension.SAFETY] > current[Dimension.SAFETY]

