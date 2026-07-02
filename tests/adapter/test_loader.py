from __future__ import annotations

from pathlib import Path

import pytest

from dynsteer.adapter.loader import (
    adapterd_case_path,
    load_task_case,
    load_task_case_file,
    safe_case_file_name,
    save_task_case,
)
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import TaskCase


class DummyAdapter:
    benchmark = "fake"

    def __init__(self) -> None:
        self.adapted: list[str] = []

    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase:
        self.adapted.append(case_id)
        return TaskCase(
            task_id=f"task-{case_id}",
            task_description=f"任务 {case_id}",
            case_id=case_id,
        )


def _config(tmp_path: Path, case_ids: tuple[str, ...]) -> HarnessRunConfig:
    return HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        case_ids=case_ids,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
    )


def test_safe_case_file_name_rejects_empty_normalized_id() -> None:
    assert safe_case_file_name("case/1") == "case_1.json"
    with pytest.raises(ValueError, match="case_id 不能转换为空文件名"):
        safe_case_file_name("///")


def test_load_task_case_reads_only_requested_case_file(tmp_path: Path) -> None:
    config = _config(tmp_path, ("case-1",))
    save_task_case(adapterd_case_path(tmp_path, "case-1"), DummyAdapter().adapt_task_case(config, "case-1"))
    other_path = adapterd_case_path(tmp_path, "case-2")
    other_path.parent.mkdir(parents=True, exist_ok=True)
    other_path.write_text("{not json", encoding="utf-8")

    cases = load_task_case(config, DummyAdapter())

    assert [case.case_id for case in cases] == ["case-1"]


def test_load_task_case_adapts_missing_case_once_and_reloads_file(tmp_path: Path) -> None:
    config = _config(tmp_path, ("case-1", "case-2"))
    adapter = DummyAdapter()
    save_task_case(adapterd_case_path(tmp_path, "case-1"), adapter.adapt_task_case(config, "case-1"))
    adapter.adapted.clear()

    cases = load_task_case(config, adapter)

    assert [case.case_id for case in cases] == ["case-1", "case-2"]
    assert adapter.adapted == ["case-2"]
    assert adapterd_case_path(tmp_path, "case-2").exists()


def test_load_task_case_file_rejects_case_id_mismatch(tmp_path: Path) -> None:
    path = adapterd_case_path(tmp_path, "case-1")
    save_task_case(path, DummyAdapter().adapt_task_case(_config(tmp_path, ("case-1",)), "case-1"))

    with pytest.raises(ValueError, match="TaskCase.case_id 与文件对应 case_id 不一致"):
        load_task_case_file(path, expected_case_id="case-2")
