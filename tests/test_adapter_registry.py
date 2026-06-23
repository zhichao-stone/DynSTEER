import pytest

from dynsteer.adapter.registry import get_harness


def test_get_harness_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="benchmark 不能为空"):
        get_harness("")


def test_get_harness_rejects_unknown_name() -> None:
    with pytest.raises(KeyError, match="不支持的 benchmark"):
        get_harness("unknown")


def test_get_harness_toolsandbox() -> None:
    harness = get_harness("toolsandbox")

    assert harness.benchmark == "toolsandbox"
