from __future__ import annotations

import pytest

from dynsteer.boundary import candidate_boundary_for_current_step
from dynsteer.evaluate.milestone import analyze_milestone_step
from dynsteer.graph import enrich_milestone_graph
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import (
    Actor,
    Constraint,
    ConstraintTarget,
    EventType,
    Milestone,
    MilestoneGraph,
    Operator,
    StageStatus,
    StateSnapshot,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)


def _step(index: int, content: str, event_type: EventType = EventType.MESSAGE) -> TrajectoryStep:
    return TrajectoryStep(
        step_id=f"s{index}",
        index=index,
        actor=Actor.AGENT,
        event_type=event_type,
        content=content,
    )


def _milestone(milestone_id: str) -> Milestone:
    return Milestone(
        milestone_id=milestone_id,
        name=milestone_id,
        description=f"complete {milestone_id}",
        constraints=[
            Constraint(
                constraint_id=f"c:{milestone_id}",
                target=ConstraintTarget.STEP,
                selector="$.content",
                operator=Operator.CONTAINS,
                expected=milestone_id,
            )
        ],
    )


def _task_case(graph: MilestoneGraph) -> TaskCase:
    return TaskCase(
        task_id="task",
        task_description="complete task",
        case_id="case",
        milestone_graph=enrich_milestone_graph(graph),
    )


def _matched(milestone_id: str, end_step_index: int) -> HarnessStageSettlement:
    return HarnessStageSettlement(
        settlement_id=f"st:{milestone_id}",
        kind="milestone",
        milestone_id=milestone_id,
        start_step_index=end_step_index,
        end_step_index=end_step_index,
        boundary_id=f"b{end_step_index}",
        boundary_step_index=end_step_index,
    )


def test_candidate_boundary_for_current_step_uses_step_reason_and_latest_snapshot() -> None:
    step = _step(12, "agent reply")
    trajectory = Trajectory(
        run_id="run",
        task_id="task",
        steps=[_step(10, "first"), step],
        snapshots=[
            StateSnapshot("snap10", "s10", 10),
            StateSnapshot("snap13", "s13", 13),
        ],
    )

    boundary = candidate_boundary_for_current_step(trajectory, step)

    assert boundary.boundary_id == "runtime:b12"
    assert boundary.step_index == 12
    assert boundary.step_id == "s12"
    assert boundary.reason == "agent_message"
    assert boundary.snapshot_id == "snap10"


def test_candidate_boundary_for_current_step_uses_last_step_for_non_candidate_event() -> None:
    step = _step(20, "tool call placeholder", event_type=EventType.TOOL_CALL)
    trajectory = Trajectory(run_id="run", task_id="task", steps=[step])

    boundary = candidate_boundary_for_current_step(trajectory, step)

    assert boundary.boundary_id == "runtime:b20"
    assert boundary.reason == "last_step"


def test_analyze_milestone_step_returns_ready_hit_before_blocked_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> object:
        raise AssertionError("运行期分析不应扫描全量候选边界")

    monkeypatch.setattr("dynsteer.evaluate.milestone.generate_candidate_boundaries", fail_if_called, raising=False)
    task_case = _task_case(MilestoneGraph(nodes=[_milestone("m0"), _milestone("m1")], edges=[("m0", "m1")]))
    step = _step(1, "m0 and m1")
    trajectory = Trajectory(run_id="run", task_id="task", steps=[step])

    analysis = analyze_milestone_step(task_case, trajectory, step, {})

    assert analysis.hit is not None
    assert analysis.hit[0].milestone_id == "m0"
    assert analysis.hit[2].status == StageStatus.PASS
    assert analysis.blocked_detail is None


def test_analyze_milestone_step_reports_linear_blocked_hit() -> None:
    task_case = _task_case(MilestoneGraph(nodes=[_milestone("m0"), _milestone("m1")], edges=[("m0", "m1")]))
    step = _step(1, "m1 only")
    trajectory = Trajectory(run_id="run", task_id="task", steps=[step])

    analysis = analyze_milestone_step(task_case, trajectory, step, {})

    assert analysis.hit is None
    assert analysis.blocked_detail is not None
    assert analysis.blocked_detail["diagnostic_type"] == "blocked_milestone_hit"
    assert analysis.blocked_detail["milestone_id"] == "m1"
    assert analysis.blocked_detail["missing_predecessors"] == ["m0"]


def test_analyze_milestone_step_reports_join_blocked_hit() -> None:
    task_case = _task_case(
        MilestoneGraph(
            nodes=[_milestone("m1"), _milestone("m3"), _milestone("m4"), _milestone("m5")],
            edges=[("m1", "m3"), ("m1", "m4"), ("m3", "m5"), ("m4", "m5")],
        )
    )
    step = _step(6, "m5 only")
    trajectory = Trajectory(run_id="run", task_id="task", steps=[_step(1, "m1"), _step(3, "m3"), step])
    matched = {"m1": _matched("m1", 1), "m3": _matched("m3", 3)}

    analysis = analyze_milestone_step(task_case, trajectory, step, matched)

    assert analysis.hit is None
    assert analysis.blocked_detail is not None
    assert analysis.blocked_detail["milestone_id"] == "m5"
    assert analysis.blocked_detail["missing_predecessors"] == ["m4"]
