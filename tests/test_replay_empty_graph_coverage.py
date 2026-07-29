from dynsteer.evaluate.evaluator import DynSTEEREvaluator
from dynsteer.model import StageStatus

from tests.support import make_empty_task_case, make_finish_stage, make_runtime_state, make_trajectory


def test_empty_graph_finish_pass_counts_as_full_coverage() -> None:
    report = _empty_graph_report_with_finish(StageStatus.PASS)

    assert report.milestone_coverage == "full"


def test_empty_graph_finish_warn_or_ambiguous_counts_as_partial_coverage() -> None:
    warn_report = _empty_graph_report_with_finish(StageStatus.WARN)
    ambiguous_report = _empty_graph_report_with_finish(StageStatus.AMBIGUOUS)

    assert warn_report.milestone_coverage == "partial"
    assert ambiguous_report.milestone_coverage == "partial"


def test_empty_graph_finish_fail_invalid_or_missing_counts_as_none_coverage() -> None:
    for status in (StageStatus.FAIL, StageStatus.INVALID, StageStatus.MISSING):
        report = _empty_graph_report_with_finish(status)

        assert report.milestone_coverage == "none"


def test_empty_graph_without_finish_counts_as_none_coverage() -> None:
    state = make_runtime_state()
    report = DynSTEEREvaluator()._runtime_report(make_empty_task_case(), make_trajectory(), state)

    assert report.milestone_coverage == "none"


def test_nonempty_graph_coverage_is_unchanged() -> None:
    from tests.support import make_nonempty_task_case

    state = make_runtime_state()
    report = DynSTEEREvaluator()._runtime_report(make_nonempty_task_case(), make_trajectory(), state)

    assert report.milestone_coverage == "none"


def _empty_graph_report_with_finish(status: StageStatus):
    state = make_runtime_state()
    state.stage_reports = [make_finish_stage(status)]
    return DynSTEEREvaluator()._runtime_report(make_empty_task_case(), make_trajectory(), state)
