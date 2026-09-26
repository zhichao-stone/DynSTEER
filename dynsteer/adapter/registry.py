from dynsteer.adapter.base import BaseBenchmarkAdapter, BaseBenchmarkHarness
from dynsteer.adapter.skillsbench.adapter import SkillsBenchAdapter
from dynsteer.adapter.skillsbench.harness import SkillsBenchHarness
from dynsteer.adapter.swebench_pro.adapter import SWEBenchProAdapter
from dynsteer.adapter.swebench_pro.harness import SWEBenchProHarness
from dynsteer.adapter.toolsandbox.adapter import ToolSandboxAdapter
from dynsteer.adapter.toolsandbox.harness import ToolSandboxHarness


_ADAPTERS: dict[str, type[BaseBenchmarkAdapter]] = {
    "toolsandbox": ToolSandboxAdapter,
    "swebench_pro": SWEBenchProAdapter,
    "skillsbench": SkillsBenchAdapter,
}

_HARNESSES: dict[str, type[BaseBenchmarkHarness]] = {
    "toolsandbox": ToolSandboxHarness,
    "swebench_pro": SWEBenchProHarness,
    "skillsbench": SkillsBenchHarness,
}

def get_adapter(benchmark: str) -> BaseBenchmarkAdapter:
    """按 benchmark 名称获取 adapter。"""
    name = benchmark.strip().lower().replace("-", "_")
    if name not in _ADAPTERS:
        raise KeyError(f"不支持的 benchmark: {benchmark}。可选 benchmark: {list(_ADAPTERS)}。")
    return _ADAPTERS[name]()

def get_harness(benchmark: str) -> BaseBenchmarkHarness:
    """按 benchmark 名称获取运行期 harness。"""
    name = benchmark.strip().lower().replace("-", "_")
    if name not in _HARNESSES:
        raise KeyError(f"不支持的 benchmark: {benchmark}。可选 benchmark: {list(_ADAPTERS.keys())}。")
    return _HARNESSES[name]()
