from dynsteer.graph import FINISH_NODE_ID, START_NODE_ID
from dynsteer.model import Dimension, StageInterval, StageStatus
from dynsteer.prompt.judge import build_judge_prompt

from tests.support import make_empty_task_case, make_minefield, make_trajectory


def test_whole_trajectory_prompt_includes_final_state_without_default_reference() -> None:
    task_case = make_empty_task_case(minefields=[make_minefield()])
    trajectory = make_trajectory(task_id=task_case.task_id)
    interval = StageInterval(
        stage_id=f"{START_NODE_ID}->{FINISH_NODE_ID}",
        milestone_id=FINISH_NODE_ID,
        stage_anchor_milestone_id=START_NODE_ID,
        start_boundary_step_index=-1,
        start_step_index=0,
        end_step_index=0,
        status=StageStatus.AMBIGUOUS,
        evidence=["finish 结算节点"],
    )

    prompt = build_judge_prompt(
        "standard",
        interval,
        task_case,
        trajectory,
        target_dimensions=[Dimension.PROGRESS],
        extra={"default_reference": {"resolved": True, "score": 1.0}, "default_score": 1.0},
    )

    assert "whole_trajectory_context" in prompt
    assert "final_state" in prompt
    assert '"default_reference_used": false' in prompt
    assert '"default_reference"' not in prompt
    assert '"default_score"' not in prompt
