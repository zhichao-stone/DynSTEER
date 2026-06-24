from __future__ import annotations

from dynsteer.adapter.base import BaseBenchmarkAdapter, BaseBenchmarkHarness
from dynsteer.adapter.generic.adapter import GenericAdapter
from dynsteer.adapter.toolsandbox.adapter import ToolSandboxAdapter

_ADAPTERS: dict[str, type[BaseBenchmarkAdapter]] = {
    "generic": GenericAdapter,
    "toolsandbox": ToolSandboxAdapter,
}


def get_adapter(benchmark: str) -> BaseBenchmarkAdapter:
    """按 benchmark 名称获取 adapter。

    Args:
        benchmark: benchmark 名称。

    Returns:
        对应 benchmark adapter。

    Raises:
        ValueError: benchmark 为空时抛出。
        KeyError: benchmark 不支持时抛出。
    """
    if benchmark is None or not benchmark.strip():
        raise ValueError("benchmark 不能为空")
    normalized = benchmark.strip().lower()
    adapter_type = _ADAPTERS.get(normalized)
    if adapter_type is None:
        raise KeyError(f"不支持的 benchmark: {benchmark}")
    return adapter_type()


def get_harness(benchmark: str) -> BaseBenchmarkHarness:
    """按 benchmark 名称获取 harness。

    Args:
        benchmark: benchmark 名称。

    Returns:
        对应 benchmark harness。
    """
    return get_adapter(benchmark).create_harness()
