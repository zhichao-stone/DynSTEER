from __future__ import annotations

import sys
from functools import partial
from pathlib import Path

import pytest

from dynsteer.adapter.utils import (
    callable_name,
    callable_spec,
    ensure_source_root,
    import_module,
    load_manifest,
    rows_from_dataframe,
)


class FakeDataFrame:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def to_dicts(self) -> list[dict[str, object]]:
        return self._rows


def sample_callable(value: object | None = None) -> object | None:
    return value


def test_rows_from_dataframe_supports_to_dicts_and_list_dict() -> None:
    assert rows_from_dataframe(FakeDataFrame([{"a": 1}])) == [{"a": 1}]
    assert rows_from_dataframe([{"b": 2}]) == [{"b": 2}]
    assert rows_from_dataframe(None) == []


def test_import_module_validates_name_and_wraps_dependency_message() -> None:
    with pytest.raises(ValueError, match="module_name 不能为空"):
        import_module("")

    with pytest.raises(ImportError, match="依赖提示"):
        import_module("missing_dynsteer_dependency_for_test", "依赖提示")


def test_manifest_and_source_root_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = tmp_path / "project"
    source_root = project_root / "benchmarks" / "fake"
    data_root = tmp_path / "data"
    source_root.mkdir(parents=True)
    data_root.mkdir()
    (data_root / "benchmark.json").write_text(
        '{"benchmark": "fake", "source_root": "benchmarks/fake"}',
        encoding="utf-8",
    )
    source_text = str(source_root.resolve())
    monkeypatch.setattr(sys, "path", [item for item in sys.path if item != source_text])

    manifest = load_manifest(data_root, "fake")
    ensure_source_root(data_root, project_root, "fake")

    assert manifest["benchmark"] == "fake"
    assert sys.path[0] == source_text


def test_callable_spec_handles_partial_keywords() -> None:
    spec = callable_spec(partial(sample_callable, value=sample_callable))

    assert callable_name(sample_callable) == "sample_callable"
    assert spec == {
        "callable": "sample_callable",
        "partial_keywords": {"value": "sample_callable"},
    }
