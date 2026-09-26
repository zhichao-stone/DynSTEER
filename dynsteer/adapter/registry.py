from dynsteer.adapter.base import BaseBenchmarkAdapter, BaseBenchmarkHarness
from dynsteer.adapter.swebench_pro.adapter import SWEBenchProAdapter
from dynsteer.adapter.swebench_pro.harness import SWEBenchProHarness
from dynsteer.adapter.toolsandbox.adapter import ToolSandboxAdapter
from dynsteer.adapter.toolsandbox.harness import ToolSandboxHarness


_ADAPTERS: dict[str, type[BaseBenchmarkAdapter]] = {
    "toolsandbox": ToolSandboxAdapter,
    "swebench_pro": SWEBenchProAdapter,
}

_HARNESSES: dict[str, type[BaseBenchmarkHarness]] = {
    "toolsandbox": ToolSandboxHarness,
    "swebench_pro": SWEBenchProHarness,
}


def get_adapter(benchmark: str) -> BaseBenchmarkAdapter:
    """Return the adapter for a supported benchmark."""
    name = benchmark.strip().lower().replace("-", "_")
    if name not in _ADAPTERS:
        raise KeyError(f"Unsupported benchmark:{benchmark}. Option benchmark:{list(_ADAPTERS)}.")
    return _ADAPTERS[name]()


def get_harness(benchmark: str) -> BaseBenchmarkHarness:
    """Return the runtime harness for a benchmark name."""
    name = benchmark.strip().lower().replace("-", "_")
    if name not in _HARNESSES:
        raise KeyError(f"Unsupported benchmark: {benchmark}. Available benchmarks: {list(_HARNESSES)}.")
    return _HARNESSES[name]()
