from __future__ import annotations

from pathlib import Path


def test_main_builds_dynsteer_evaluator_for_benchmark_mode() -> None:
    source = Path("main.py").read_text(encoding="utf-8")

    assert "run_harness_configs(" in source
    assert "outputs: list[HarnessEvaluationOutput]" in source
    assert "--max-workers" in source


def test_main_does_not_import_legacy_judge_modules() -> None:
    source = Path("main.py").read_text(encoding="utf-8")

    assert "from dynsteer.evaluate import evaluate_trajectory" not in source
    assert "from dynsteer.judges.llm import" not in source
    assert "from dynsteer.judge import" not in source
