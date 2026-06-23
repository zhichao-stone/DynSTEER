from dynsteer.boundary import generate_candidate_boundaries
from dynsteer.model import Actor, EventType, Trajectory, TrajectoryStep


def test_generate_boundary_for_tool_result() -> None:
    trajectory = Trajectory(
        run_id="r",
        task_id="t",
        steps=[
            TrajectoryStep(
                step_id="s1",
                index=1,
                actor=Actor.ENVIRONMENT,
                event_type=EventType.TOOL_RESULT,
            )
        ],
    )

    boundaries = generate_candidate_boundaries(trajectory)

    assert boundaries[0].step_index == 1
    assert boundaries[0].reason == "tool_result"


def test_error_reason_has_priority() -> None:
    trajectory = Trajectory(
        run_id="r",
        task_id="t",
        steps=[
            TrajectoryStep(
                step_id="s1",
                index=1,
                actor=Actor.AGENT,
                event_type=EventType.ERROR,
            )
        ],
    )

    boundaries = generate_candidate_boundaries(trajectory)

    assert len(boundaries) == 1
    assert boundaries[0].reason == "error"

