from __future__ import annotations

from pathlib import Path

import pytest

from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.harness.model import BenchmarkCase, HarnessAdvanceResult, HarnessRunConfig
from dynsteer.model import Actor, EventType, StateSnapshot, TaskCase, TrajectoryStep


def _task_case() -> TaskCase:
    return TaskCase(task_id="task-1", task_description="完成测试任务", case_id="case-1")


def _step(index: int) -> TrajectoryStep:
    return TrajectoryStep(
        step_id=f"s{index}",
        index=index,
        actor=Actor.AGENT,
        event_type=EventType.MESSAGE,
        content=f"step {index}",
    )


def _config(tmp_path: Path) -> HarnessRunConfig:
    return HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )


class FakeHarness(BaseBenchmarkHarness):
    benchmark = "fake"

    def __init__(self, batches: list[HarnessAdvanceResult]) -> None:
        super().__init__()
        self.batches = list(batches)
        self.case_finished_called = False
        self.task_case = _task_case()

    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        self.prepare_config(config)
        return [BenchmarkCase(benchmark=self.benchmark, case_id="case-1")]

    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> object:
        return {"case_id": case_id, "raw_output_dir": raw_output_dir}

    def advance_case(self, session: object) -> HarnessAdvanceResult:
        if not self.batches:
            raise RuntimeError("benchmark session 未完成但没有新增轨迹步骤")
        return self.batches.pop(0)

    def case_finished(self, session: object) -> bool:
        self.case_finished_called = True
        raise AssertionError("DynSTEEREvaluator.evaluate 不应调用 case_finished 控制主循环")


def test_harness_advance_result_rejects_none_steps() -> None:
    with pytest.raises(ValueError, match="steps"):
        HarnessAdvanceResult(steps=None, snapshots=[], continue_running=False)  # type: ignore[arg-type]


def test_harness_advance_result_accepts_batch_snapshots() -> None:
    snapshot = StateSnapshot(
        snapshot_id="snap-1",
        after_step_id="s0",
        after_step_index=0,
        namespaces={"SETTING": [{"wifi": True}]},
    )

    result = HarnessAdvanceResult(
        steps=[_step(0)],
        snapshots=[snapshot],
        continue_running=False,
        reason="benchmark 已自然完成",
    )

    assert result.snapshots == [snapshot]


def test_harness_advance_result_requires_snapshots() -> None:
    with pytest.raises(TypeError, match="snapshots"):
        HarnessAdvanceResult(steps=[], continue_running=False)  # type: ignore[call-arg]


def test_harness_advance_result_rejects_none_snapshots() -> None:
    with pytest.raises(ValueError, match="snapshots"):
        HarnessAdvanceResult(steps=[], snapshots=None, continue_running=False)  # type: ignore[arg-type]


def test_base_benchmark_harness_member_sections_are_grouped() -> None:
    source = Path("dynsteer/adapter/base.py").read_text(encoding="utf-8")
    class_start = source.index("class BaseBenchmarkHarness")
    class_source = source[class_start:]
    abstract_marker = "## 子类必须继承实现"
    base_marker = "## 基类自身实现"

    abstract_marker_index = class_source.index(abstract_marker)
    base_marker_index = class_source.index(base_marker)
    abstract_methods = [
        "list_cases",
        "start_case",
        "advance_case",
        "case_finished",
    ]
    base_methods = [
        "constraint_scorer",
        "prepare_config",
        "build_run_id",
        "metrics_from_session",
        "final_state_from_session",
        "raw_summary_from_session",
        "stop_case",
        "teardown_case",
        "_project_root",
        "_validate_config",
    ]

    assert abstract_marker_index < base_marker_index
    for method_name in abstract_methods:
        method_index = class_source.index(f"    def {method_name}(")
        assert abstract_marker_index < method_index < base_marker_index
    for method_name in base_methods:
        method_index = class_source.index(f"    def {method_name}(")
        assert method_index > base_marker_index
    assert "snapshots_from_session" not in class_source
    assert "task_case_from_session" not in class_source
    assert "_load_manifest" not in class_source
    assert "_ensure_source_root" not in class_source
    assert "_import_module" not in class_source


def test_evaluator_consumes_public_harness_api(tmp_path: Path) -> None:
    from dynsteer.evaluate import DynSTEEREvaluator

    harness = FakeHarness(
        [
            HarnessAdvanceResult(steps=[_step(0)], snapshots=[], continue_running=True),
            HarnessAdvanceResult(
                steps=[_step(1)],
                snapshots=[],
                continue_running=False,
                reason="benchmark 已自然完成",
            ),
        ]
    )
    result = DynSTEEREvaluator().evaluate(harness, _config(tmp_path), harness.task_case)

    assert result.task_case.task_id == "task-1"
    assert [step.index for step in result.trajectory.steps] == [0, 1]
    assert result.stage_settlements[0].kind == "start"
    assert result.stage_settlements[-1].kind == "finish"
    assert harness.case_finished_called is False


def test_evaluator_exits_on_advance_continue_running_false(tmp_path: Path) -> None:
    from dynsteer.evaluate import DynSTEEREvaluator

    harness = FakeHarness(
        [HarnessAdvanceResult(steps=[], snapshots=[], continue_running=False, reason="benchmark 已自然完成")]
    )
    result = DynSTEEREvaluator().evaluate(harness, _config(tmp_path), harness.task_case)

    assert result.trajectory.steps == []
    assert result.stage_settlements[-1].kind == "finish"
    assert harness.case_finished_called is False


def test_harness_owns_empty_step_error(tmp_path: Path) -> None:
    from dynsteer.evaluate import DynSTEEREvaluator

    harness = FakeHarness([])

    with pytest.raises(RuntimeError, match="没有新增轨迹步骤"):
        DynSTEEREvaluator().evaluate(harness, _config(tmp_path), harness.task_case)


def test_toolsandbox_starting_context_uses_scenario_attribute() -> None:
    from dynsteer.adapter.toolsandbox.harness import ToolSandboxHarness

    class NativeScenario:
        def __init__(self) -> None:
            self.starting_context = object()

        def get_starting_context(self) -> object:
            raise AssertionError("不应调用非标准 get_starting_context")

        def create_context(self) -> object:
            raise AssertionError("不应调用非标准 create_context")

    scenario = NativeScenario()
    context = ToolSandboxHarness()._starting_context_from_scenario(scenario)

    assert context is scenario.starting_context


def test_toolsandbox_harness_removes_call_native_adapter_layer() -> None:
    from dynsteer.adapter.toolsandbox.harness import ToolSandboxHarness

    assert not hasattr(ToolSandboxHarness, "_call_native")
    assert not hasattr(ToolSandboxHarness, "_native_kwargs_bind")


def test_toolsandbox_harness_removes_unused_respond_roles_fallback() -> None:
    from dynsteer.adapter.toolsandbox.harness import ToolSandboxHarness

    assert not hasattr(ToolSandboxHarness, "_respond_roles_once")


def test_toolsandbox_steps_use_raw_sandbox_message_index() -> None:
    from dynsteer.adapter.toolsandbox.adapter import sandbox_rows_to_step_dicts

    rows = [
        {
            "sandbox_message_index": 28,
            "sender": "AGENT",
            "recipient": "EXECUTION_ENVIRONMENT",
            "content": "call_x_response = get_current_location()",
            "tool_trace": '{"tool_name": "get_current_location", "arguments": {}}',
        },
        {
            "sandbox_message_index": 29,
            "sender": "EXECUTION_ENVIRONMENT",
            "recipient": "AGENT",
            "content": "PermissionError",
            "tool_call_exception": "PermissionError",
        },
    ]

    steps = sandbox_rows_to_step_dicts(rows)

    assert [step["index"] for step in steps] == [28, 29]
    assert [step["step_id"] for step in steps] == ["s28", "s29"]
    assert steps[0]["raw_sandbox_message_index"] == 28
