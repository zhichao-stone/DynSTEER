from pathlib import Path

import pytest

from dynsteer.harness.model import HarnessRunConfig


def test_toolsandbox_harness_lists_real_cases() -> None:
    pytest.importorskip("polars")
    pytest.importorskip("tool_sandbox")

    from dynsteer.adapter.toolsandbox_harness import ToolSandboxHarness

    config = HarnessRunConfig(
        benchmark="toolsandbox",
        data_root=Path("../ToolSandbox"),
        scenario="cellular_off",
    )
    harness = ToolSandboxHarness()

    cases = harness.list_cases(config)

    assert any(case.case_id == "cellular_off" for case in cases)
