from __future__ import annotations

from pathlib import Path

from dynsteer.adapter import loader
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import TaskCase


class FakeAdapter:
    def __init__(self) -> None:
        self.adapted_case_ids: list[str] = []

    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase:
        self.adapted_case_ids.append(case_id)
        return TaskCase(
            task_id=f"task:{case_id}",
            task_description=f"task {case_id}",
            case_id=case_id,
        )


def test_load_task_case_wraps_case_loop_with_tqdm(tmp_path: Path, monkeypatch) -> None:
    tqdm_calls: list[dict[str, object]] = []

    def fake_tqdm(iterable, **kwargs):  # type: ignore[no-untyped-def]
        values = list(iterable)
        tqdm_calls.append({"values": values, **kwargs})
        return values

    monkeypatch.setattr(loader, "tqdm", fake_tqdm)
    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        case_ids=("case-a", "case-b"),
    )
    adapter = FakeAdapter()

    task_cases = loader.load_task_case(config, adapter)  # type: ignore[arg-type]

    assert [task_case.case_id for task_case in task_cases] == ["case-a", "case-b"]
    assert adapter.adapted_case_ids == ["case-a", "case-b"]
    assert len(tqdm_calls) == 1
    assert tqdm_calls[0]["values"] == ["case-a", "case-b"]
    assert tqdm_calls[0]["total"] == 2
    assert tqdm_calls[0]["unit"] == "case"

