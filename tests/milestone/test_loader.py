from pathlib import Path

from dynsteer.adapter.base import BaseBenchmarkAdapter
from dynsteer.adapter.loader import load_task_case, refresh_task_cases_for_experiment
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.milestone import (
    GenerationReport,
    GeneratorTaskView,
    MilestoneGenerationConfig,
)
from dynsteer.model import (
    Constraint,
    ConstraintTarget,
    Milestone,
    MilestoneGraph,
    Operator,
    TaskCase,
)


def _graph(source: str = "origin") -> MilestoneGraph:
    return MilestoneGraph(
        nodes=[
            Milestone(
                milestone_id="m1",
                name="m1",
                description="m1",
                constraints=[
                    Constraint(
                        constraint_id="c1",
                        target=ConstraintTarget.STEP,
                        selector="$.content",
                        operator=Operator.EQUALS,
                        expected="done",
                    )
                ],
            )
        ],
        metadata={"source": source},
    )


class FakeAdapter(BaseBenchmarkAdapter):
    benchmark = "fake"

    def __init__(self, graph: MilestoneGraph | None) -> None:
        self.graph = graph
        self.adapt_calls = 0
        self.view_calls = 0
        self.refresh_calls = 0

    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase:
        self.adapt_calls += 1
        return TaskCase("task", "done", case_id, milestone_graph=self.graph)

    def generator_task_view(
        self, config: HarnessRunConfig, task_case: TaskCase, case_id: str
    ) -> GeneratorTaskView:
        self.view_calls += 1
        return GeneratorTaskView(
            "fake", "task", case_id, "en", "done", [], {}, {}, {}, (), ()
        )

    def refresh_task_case_for_experiment(
        self, config: HarnessRunConfig, task_case: TaskCase, case_id: str
    ) -> TaskCase:
        self.refresh_calls += 1
        return task_case


def _config(root: Path, *, use_origin: bool = True) -> HarnessRunConfig:
    return HarnessRunConfig(
        benchmark="fake",
        data_root=root,
        case_ids=("case",),
        milestone_generation=MilestoneGenerationConfig(
            use_origin_milestone=use_origin,
            generator={"provider": "openai", "model": "model"},
        ),
        metadata={"method": "dynsteer_evaluate", "stage_goal_generation": "stored"},
    )


def test_origin_graph_does_not_build_generator(tmp_path: Path, monkeypatch) -> None:
    adapter = FakeAdapter(_graph())
    monkeypatch.setattr(
        "dynsteer.adapter.loader._postprocess_task_case", lambda task, mode: task
    )
    monkeypatch.setattr(
        "dynsteer.adapter.loader.validate_stage_evaluation_specs", lambda *args: None
    )

    task = load_task_case(_config(tmp_path), adapter)[0]

    assert task.milestone_graph.metadata["source"] == "origin"
    assert adapter.view_calls == 0


def test_forced_generated_graph_is_persisted_once(tmp_path: Path, monkeypatch) -> None:
    adapter = FakeAdapter(_graph())
    report = GenerationReport("generated", 3, 3, 1, 1, True, 0)
    monkeypatch.setattr(
        "dynsteer.adapter.loader.build_llm_from_config", lambda config: object()
    )
    monkeypatch.setattr(
        "dynsteer.adapter.loader.compile_task_case",
        lambda *args: (_graph("generated"), report),
    )
    monkeypatch.setattr(
        "dynsteer.adapter.loader._postprocess_task_case", lambda task, mode: task
    )
    monkeypatch.setattr(
        "dynsteer.adapter.loader.validate_stage_evaluation_specs", lambda *args: None
    )

    task = load_task_case(_config(tmp_path, use_origin=False), adapter)[0]
    cached = load_task_case(_config(tmp_path), adapter)[0]

    assert task.metadata["milestone_generation"]["generation_status"] == "generated"
    assert cached.milestone_graph.metadata["source"] == "generated"
    assert adapter.adapt_calls == 1
    assert adapter.view_calls == 1


def test_generated_refresh_skips_origin_refresh(tmp_path: Path, monkeypatch) -> None:
    adapter = FakeAdapter(_graph())
    task = TaskCase("task", "done", "case", milestone_graph=_graph("generated"))
    monkeypatch.setattr(
        "dynsteer.adapter.loader.materialize_stage_goals", lambda task: {}
    )
    monkeypatch.setattr(
        "dynsteer.adapter.loader.generate_stage_evaluation_specs", lambda task: {}
    )

    refreshed = refresh_task_cases_for_experiment(_config(tmp_path), adapter, [task])

    assert refreshed == [task]
    assert adapter.refresh_calls == 0
