from __future__ import annotations

import inspect
from types import ModuleType

from dynsteer import boundary, utils
from dynsteer.evaluate import score
from dynsteer.llm import anthropic
from dynsteer.model import Actor, Boundary, EventType, StateSnapshot, Trajectory, TrajectoryStep


def _step(step_id: str, index: int) -> TrajectoryStep:
    return TrajectoryStep(
        step_id=step_id,
        index=index,
        actor=Actor.AGENT,
        event_type=EventType.MESSAGE,
        content=f"step-{index}",
    )


def test_boundary_lookup_helpers_live_in_boundary_module() -> None:
    boundary_step = getattr(boundary, "boundary_step", None)
    boundary_snapshot = getattr(boundary, "boundary_snapshot", None)
    trajectory = Trajectory(
        run_id="run-1",
        task_id="task-1",
        steps=[_step("s1", 1), _step("s3", 3)],
    )
    snapshots = [
        StateSnapshot(snapshot_id="snap-1", after_step_id="s1", after_step_index=1),
        StateSnapshot(snapshot_id="snap-2", after_step_id="s2", after_step_index=2),
    ]
    boundary_item = Boundary(boundary_id="b0", step_index=3, snapshot_id=None, reason="agent_message")

    assert boundary_step is not None
    assert boundary_snapshot is not None
    assert boundary_step(trajectory, boundary_item) == trajectory.steps[1]
    assert boundary_snapshot(boundary_item, snapshots) == snapshots[1]


def test_boundary_snapshot_prefers_explicit_snapshot_id() -> None:
    boundary_snapshot = getattr(boundary, "boundary_snapshot", None)
    snapshots = [
        StateSnapshot(snapshot_id="snap-1", after_step_id="s1", after_step_index=1),
        StateSnapshot(snapshot_id="snap-2", after_step_id="s2", after_step_index=2),
    ]
    boundary_item = Boundary(boundary_id="b0", step_index=3, snapshot_id="snap-1", reason="agent_message")

    assert boundary_snapshot is not None
    assert boundary_snapshot(boundary_item, snapshots) == snapshots[0]


def test_target_modules_functions_have_docstrings() -> None:
    for module in (boundary, score, utils, anthropic):
        _assert_module_function_docstrings(module)


def _assert_module_function_docstrings(module: ModuleType) -> None:
    missing: list[str] = []
    for name, item in inspect.getmembers(module, inspect.isfunction):
        if item.__module__ == module.__name__ and not inspect.getdoc(item):
            missing.append(name)
    for class_name, class_item in inspect.getmembers(module, inspect.isclass):
        if class_item.__module__ != module.__name__:
            continue
        for method_name, method in inspect.getmembers(class_item, inspect.isfunction):
            if method.__module__ == module.__name__ and not inspect.getdoc(method):
                missing.append(f"{class_name}.{method_name}")
    assert missing == []
