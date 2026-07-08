from pathlib import Path

from dynsteer.adapter.base import BaseBenchmarkAdapter
from dynsteer.adapter.loader import load_task_case
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import TaskCase
from main import _parse_args


class _FakeAdapter(BaseBenchmarkAdapter):
    benchmark = "fake"

    def __init__(self) -> None:
        self.call_count = 0

    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase:
        self.call_count += 1
        return TaskCase(
            task_id=f"fake::{case_id}",
            task_description=f"fresh description {self.call_count}",
            case_id=case_id,
        )

    def create_harness(self) -> object:
        raise NotImplementedError("测试替身不需要创建 harness")


def _config(tmp_path: Path) -> HarnessRunConfig:
    return HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        case_ids=("case-a",),
    )


def test_load_task_case_keeps_existing_file_by_default(tmp_path: Path) -> None:
    adapter = _FakeAdapter()
    config = _config(tmp_path)

    first = load_task_case(config, adapter)
    second = load_task_case(config, adapter)

    assert adapter.call_count == 1
    assert first[0].task_description == "fresh description 1"
    assert second[0].task_description == "fresh description 1"


def test_load_task_case_force_adapt_overwrites_existing_file(tmp_path: Path) -> None:
    adapter = _FakeAdapter()
    config = _config(tmp_path)

    load_task_case(config, adapter)
    refreshed = load_task_case(config, adapter, force_adapt=True)

    assert adapter.call_count == 2
    assert refreshed[0].task_description == "fresh description 2"


def test_parse_force_adapt_argument() -> None:
    args = _parse_args(["--benchmark", "toolsandbox", "--only_adapt", "--force_adapt"])

    assert args.only_adapt is True
    assert args.force_adapt is True
