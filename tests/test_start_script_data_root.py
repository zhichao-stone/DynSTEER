from __future__ import annotations

from pathlib import Path


def test_start_script_keeps_docker_adapted_cases_under_data_root() -> None:
    script = Path("scripts/start.sh").read_text(encoding="utf-8")

    assert ".dynsteer-runtime/data" not in script
    assert "prepare_runtime_data_root" not in script
