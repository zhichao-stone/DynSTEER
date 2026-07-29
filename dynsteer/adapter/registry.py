from importlib import import_module
from typing import TypeVar
from dynsteer.adapter.base import BaseBenchmarkAdapter, BaseBenchmarkHarness
_T = TypeVar('_T')
_RegisteredType = type[_T] | str
_ADAPTERS: dict[str, _RegisteredType[BaseBenchmarkAdapter]] = {'toolsandbox': 'dynsteer.adapter.toolsandbox.adapter:ToolSandboxAdapter', 'swebench_pro': 'dynsteer.adapter.swebench.adapter:SwebenchProAdapter'}
_HARNESSES: dict[str, _RegisteredType[BaseBenchmarkHarness]] = {'toolsandbox': 'dynsteer.adapter.toolsandbox.harness:ToolSandboxHarness', 'swebench_pro': 'dynsteer.adapter.swebench.harness:SwebenchProHarness'}

def get_adapter(benchmark: str) -> BaseBenchmarkAdapter:
    """按 benchmark 名称获取 adapter。"""
    return _get_registered_class_by_benchmark(benchmark, _ADAPTERS, BaseBenchmarkAdapter)()

def get_harness(benchmark: str) -> BaseBenchmarkHarness:
    """按 benchmark 名称获取运行期 harness。"""
    return _get_registered_class_by_benchmark(benchmark, _HARNESSES, BaseBenchmarkHarness)()

def _get_registered_class_by_benchmark(benchmark: str, type_mapping: dict, base_type: type[_T]) -> type[_T]:
    class_type = type_mapping.get(benchmark.strip().lower().replace('-', '_'))
    if class_type is None:
        raise KeyError(f'不支持的 benchmark: {benchmark}。可选 benchmark: {list(_ADAPTERS.keys())}。')
    return _load_registered_type(class_type, base_type)

def _load_registered_type(target: _RegisteredType[_T], base_type: type[_T]) -> type[_T]:
    if isinstance(target, type):
        loaded = target
    else:
        module_name, separator, attr_name = target.partition(':')
        if not module_name or separator != ':' or (not attr_name):
            raise ValueError(f'注册类型路径非法: {target}')
        loaded = getattr(import_module(module_name), attr_name)
    if not isinstance(loaded, type) or not issubclass(loaded, base_type):
        raise TypeError(f'注册类型必须继承 {base_type.__name__}: {loaded}')
    return loaded
