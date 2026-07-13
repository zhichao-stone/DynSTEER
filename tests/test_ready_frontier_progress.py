from __future__ import annotations

import json
from pathlib import Path

import pytest

from dynsteer.config import ThresholdConfig
from dynsteer.evaluate.runtime import RuntimeEvaluationState, update_ready_frontier_progress_watch
from dynsteer.harness.config import load_harness_run_configs, load_ready_frontier_patience_from_env
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import JsonObject, Milestone, MilestoneGraph, TaskCase


def test_ready_frontier_watch_resets_when_any_member_improves() -> None:
    task_case = _task_case(required_ids=["m1", "m2"])
    state = _state()

    update_ready_frontier_progress_watch(
        task_case,
        state,
        _attempt(1, ["m1", "m2"], {"m1": 0.10, "m2": 0.10}),
        ThresholdConfig(),
        True,
        patience=2,
        min_delta=0.02,
    )
    detail = update_ready_frontier_progress_watch(
        task_case,
        state,
        _attempt(2, ["m1", "m2"], {"m1": 0.13, "m2": 0.10}),
        ThresholdConfig(),
        True,
        patience=2,
        min_delta=0.02,
    )

    assert detail is None
    assert state.ready_frontier_progress_watch is not None
    assert state.ready_frontier_progress_watch.stale_frontier_observation_count == 0
    assert state.ready_frontier_progress_watch.milestone_progress["m1"].best_score == pytest.approx(0.13)


def test_ready_frontier_watch_does_not_require_all_parallel_milestones_to_improve() -> None:
    task_case = _task_case(required_ids=["m1", "m2", "m3"])
    state = _state()
    update_ready_frontier_progress_watch(
        task_case,
        state,
        _attempt(1, ["m1", "m2", "m3"], {"m1": 0.10, "m2": 0.10, "m3": 0.10}),
        ThresholdConfig(),
        True,
        patience=2,
        min_delta=0.02,
    )

    for step_index, score in enumerate([0.13, 0.16, 0.19], start=2):
        detail = update_ready_frontier_progress_watch(
            task_case,
            state,
            _attempt(step_index, ["m1", "m2", "m3"], {"m1": score, "m2": 0.10, "m3": 0.10}),
            ThresholdConfig(),
            True,
            patience=2,
            min_delta=0.02,
        )
        assert detail is None
        assert state.ready_frontier_progress_watch is not None
        assert state.ready_frontier_progress_watch.stale_frontier_observation_count == 0


def test_ready_frontier_watch_rebuilds_after_match() -> None:
    task_case = _task_case(required_ids=["m1", "m2"])
    state = _state()
    update_ready_frontier_progress_watch(
        task_case,
        state,
        _attempt(1, ["m1", "m2"], {"m1": 0.10, "m2": 0.10}),
        ThresholdConfig(),
        True,
        patience=3,
        min_delta=0.02,
    )
    update_ready_frontier_progress_watch(
        task_case,
        state,
        _attempt(2, ["m1", "m2"], {"m1": 0.10, "m2": 0.10}),
        ThresholdConfig(),
        True,
        patience=3,
        min_delta=0.02,
    )
    assert state.ready_frontier_progress_watch is not None
    assert state.ready_frontier_progress_watch.stale_frontier_observation_count == 1

    state.matched_settlements["m1"] = _settlement("m1")
    detail = update_ready_frontier_progress_watch(
        task_case,
        state,
        _attempt(3, ["m1", "m2"], {"m1": 0.10, "m2": 0.10}),
        ThresholdConfig(),
        True,
        patience=3,
        min_delta=0.02,
    )

    assert detail is None
    assert state.ready_frontier_progress_watch is not None
    assert state.ready_frontier_progress_watch.frontier_key == ("m2",)
    assert state.ready_frontier_progress_watch.stale_frontier_observation_count == 0
    assert state.ready_frontier_progress_watch.ready_since_step_index == 3


def test_ready_frontier_watch_stops_when_no_member_improves() -> None:
    task_case = _task_case(required_ids=["m1", "m2"])
    state = _state()
    update_ready_frontier_progress_watch(
        task_case,
        state,
        _attempt(1, ["m1", "m2"], {"m1": 0.10, "m2": 0.10}),
        ThresholdConfig(),
        True,
        patience=2,
        min_delta=0.02,
    )
    update_ready_frontier_progress_watch(
        task_case,
        state,
        _attempt(2, ["m1", "m2"], {"m1": 0.10, "m2": 0.10}),
        ThresholdConfig(),
        True,
        patience=2,
        min_delta=0.02,
    )
    detail = update_ready_frontier_progress_watch(
        task_case,
        state,
        _attempt(3, ["m1", "m2"], {"m1": 0.11, "m2": 0.10}),
        ThresholdConfig(),
        True,
        patience=2,
        min_delta=0.02,
    )

    assert detail is not None
    assert detail["code"] == "ready_frontier_no_progress:m1"
    assert detail["ready_milestone_ids"] == ["m1", "m2"]
    assert detail["stale_frontier_observation_count"] == 2
    assert detail["frontier_observation_count"] == 3


def test_single_ready_milestone_no_progress_uses_milestone_code() -> None:
    task_case = _task_case(required_ids=["m3"])
    state = _state()
    update_ready_frontier_progress_watch(
        task_case,
        state,
        _attempt(1, ["m3"], {"m3": 0.20}),
        ThresholdConfig(),
        True,
        patience=1,
        min_delta=0.02,
    )
    detail = update_ready_frontier_progress_watch(
        task_case,
        state,
        _attempt(2, ["m3"], {"m3": 0.20}),
        ThresholdConfig(),
        True,
        patience=1,
        min_delta=0.02,
    )

    assert detail is not None
    assert detail["code"] == "milestone_no_progress:m3"
    assert detail["most_promising_milestone_id"] == "m3"


def test_ready_frontier_watch_ignores_optional_milestones() -> None:
    task_case = _task_case(required_ids=[], optional_ids=["optional"])
    state = _state()

    detail = update_ready_frontier_progress_watch(
        task_case,
        state,
        _attempt(1, ["optional"], {"optional": 0.10}),
        ThresholdConfig(),
        True,
        patience=1,
        min_delta=0.02,
    )

    assert detail is None
    assert state.ready_frontier_progress_watch is None


def test_ready_frontier_patience_loads_from_env() -> None:
    assert load_ready_frontier_patience_from_env({}) == 8
    assert load_ready_frontier_patience_from_env({"DYNSTEER_READY_FRONTIER_PATIENCE": "16"}) == 16


def test_ready_frontier_patience_env_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="必须是整数"):
        load_ready_frontier_patience_from_env({"DYNSTEER_READY_FRONTIER_PATIENCE": "abc"})
    with pytest.raises(ValueError, match="必须大于 0"):
        load_ready_frontier_patience_from_env({"DYNSTEER_READY_FRONTIER_PATIENCE": "0"})


def test_run_config_loads_ready_frontier_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "benchmark.json").write_text(
        json.dumps({"benchmark": "testbench", "source_root": ".", "tool_backend": "fake"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (data_root / "run_configs.json").write_text(
        json.dumps(
            [
                {
                    "name": "case-run",
                    "stop_on_ready_frontier_no_progress": False,
                    "ready_frontier_min_delta": 0.05,
                    "ready_frontier_patience": 99,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DYNSTEER_READY_FRONTIER_PATIENCE", "16")

    configs = load_harness_run_configs("testbench", data_root, tmp_path / "runs", tmp_path / "results")

    assert len(configs) == 1
    assert configs[0].stop_on_ready_frontier_no_progress is False
    assert configs[0].ready_frontier_min_delta == pytest.approx(0.05)
    assert configs[0].ready_frontier_patience == 16
    assert "ready_frontier_patience" not in configs[0].metadata


def _state() -> RuntimeEvaluationState:
    return RuntimeEvaluationState(
        weights={},
        settlements=[],
        matched_settlements={},
        stage_reports=[],
        match_attempts=[],
    )


def _task_case(required_ids: list[str], optional_ids: list[str] | None = None) -> TaskCase:
    milestones = [
        _milestone(milestone_id, required=True)
        for milestone_id in required_ids
    ]
    milestones.extend(_milestone(milestone_id, required=False) for milestone_id in optional_ids or [])
    return TaskCase(
        task_id="task",
        task_description="测试任务",
        case_id="case",
        milestone_graph=MilestoneGraph(nodes=milestones),
    )


def _milestone(milestone_id: str, required: bool) -> Milestone:
    return Milestone(
        milestone_id=milestone_id,
        name=milestone_id,
        description=milestone_id,
        constraints=[],
        required=required,
    )


def _attempt(step_index: int, ready_ids: list[str], scores: dict[str, float]) -> JsonObject:
    return {
        "step_index": step_index,
        "ready_before": list(ready_ids),
        "candidate_scores": [
            {
                "milestone_id": milestone_id,
                "boundary": {
                    "boundary_id": f"b{step_index}",
                    "step_index": step_index,
                },
                "score": {
                    "milestone_id": milestone_id,
                    "boundary_id": f"b{step_index}",
                    "score": score,
                    "status": "pass" if score >= 0.8 else "fail",
                },
            }
            for milestone_id, score in scores.items()
        ],
    }


def _settlement(milestone_id: str) -> HarnessStageSettlement:
    return HarnessStageSettlement(
        settlement_id=f"st-{milestone_id}",
        kind="milestone",
        milestone_id=milestone_id,
        start_step_index=1,
        end_step_index=1,
    )
