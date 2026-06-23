from __future__ import annotations

from dynsteer.harness.model import BenchmarkHarness


def get_harness(benchmark: str) -> BenchmarkHarness:
    """按 benchmark 名称获取 harness 适配器。

    Args:
        benchmark: benchmark 名称。

    Returns:
        对应 benchmark harness。

    Raises:
        ValueError: benchmark 为空时抛出。
        KeyError: benchmark 不支持时抛出。
    """
    if not benchmark or not benchmark.strip():
        raise ValueError("benchmark 不能为空")
    normalized = benchmark.strip().lower()
    if normalized == "toolsandbox":
        from dynsteer.adapter.toolsandbox_harness import ToolSandboxHarness

        return ToolSandboxHarness()
    raise KeyError(f"不支持的 benchmark: {benchmark}")
