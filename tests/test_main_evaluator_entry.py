from __future__ import annotations

from pathlib import Path


def test_main_builds_dynsteer_evaluator_for_benchmark_mode() -> None:
    source = Path("main.py").read_text(encoding="utf-8")

    assert "DynSTEEREvaluator" in source
    assert "run_harness_cases(config=config, harness=harness, evaluator=evaluator)" in source


def test_main_does_not_import_module_level_evaluate_trajectory() -> None:
    source = Path("main.py").read_text(encoding="utf-8")

    assert "from dynsteer.evaluate import evaluate_trajectory" not in source
