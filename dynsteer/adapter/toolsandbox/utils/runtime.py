from collections.abc import Callable
import os
from pathlib import Path
from threading import Lock
from dynsteer.adapter.utils import import_module, load_manifest
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.utils import enum_name


TOOL_SANDBOX_DEPENDENCY_ERROR = "ToolSandbox harness 需要安装 ToolSandbox 及其依赖。请确认 data/toolsandbox/benchmark.json 的 source_root 可导入，或在当前 uv 环境安装 ToolSandbox。"
_NAMED_SCENARIOS_CACHE: dict[tuple[str, str, str], dict[str, object]] = {}
_NAMED_SCENARIOS_CACHE_LOCK = Lock()

def toolsandbox_project_root() -> Path:
    """返回 DynSTEER 项目根目录。"""
    return Path(__file__).resolve().parents[4]

def load_toolsandbox_module(module_name: str) -> object:
    """导入 ToolSandbox 运行期模块。"""
    return import_module(module_name, TOOL_SANDBOX_DEPENDENCY_ERROR)

def tool_backend(config: HarnessRunConfig, module_loader: Callable[[str], object]=load_toolsandbox_module) -> object:
    """读取 ToolSandbox tool backend。"""
    if config is None:
        raise ValueError("config 不能为空")
    manifest = load_manifest(config.data_root, "toolsandbox")
    raw_backend = config.metadata.get("tool_backend", manifest.get("tool_backend", "DEFAULT"))
    if not isinstance(raw_backend, str) or not raw_backend.strip():
        raise ValueError("ToolSandbox tool_backend 不能为空")
    discovery_module = module_loader("tool_sandbox.common.tool_discovery")
    tool_backend_type = getattr(discovery_module, "ToolBackend")
    backend_name = raw_backend.strip()
    try:
        return tool_backend_type[backend_name]
    except (KeyError, TypeError):
        try:
            return tool_backend_type(backend_name)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"不支持的 ToolSandbox tool_backend: {backend_name}") from exc

def load_named_scenarios(
    config: HarnessRunConfig,
    module_loader: Callable[[str], object] = load_toolsandbox_module,
) -> dict[str, object]:
    """返回当前进程、当前 backend/data root 共享的 scenario 字典。"""
    if config is None or module_loader is None:
        raise ValueError("config 和 module_loader 不能为空")
    backend = tool_backend(config, module_loader)
    cache_key = (
        str(config.data_root.resolve()),
        enum_name(backend),
        _source_root_cache_key(config),
    )
    with _NAMED_SCENARIOS_CACHE_LOCK:
        cached = _NAMED_SCENARIOS_CACHE.get(cache_key)
        if cached is not None:
            return cached
        scenarios_module = module_loader("tool_sandbox.scenarios")
        scenarios = scenarios_module.named_scenarios(preferred_tool_backend=backend)
        if not isinstance(scenarios, dict):
            raise ValueError("ToolSandbox named_scenarios 必须返回字典")
        normalized = {str(key): value for key, value in scenarios.items()}
        _NAMED_SCENARIOS_CACHE[cache_key] = normalized
        return normalized

def clear_named_scenarios_cache() -> None:
    """清理测试或新实验边界的 scenario cache。"""
    with _NAMED_SCENARIOS_CACHE_LOCK:
        _NAMED_SCENARIOS_CACHE.clear()

def _source_root_cache_key(config: HarnessRunConfig) -> str:
    """返回影响 ToolSandbox 模块来源的稳定路径。"""
    raw_source_root = os.environ.get("DYNSTEER_BENCHMARK_SOURCE_ROOT")
    if raw_source_root is None:
        raw_source_root = load_manifest(config.data_root, "toolsandbox").get("source_root")
    if not isinstance(raw_source_root, str) or not raw_source_root.strip():
        return ""
    source_root = Path(raw_source_root.strip())
    if not source_root.is_absolute():
        source_root = toolsandbox_project_root() / source_root
    return str(source_root.resolve())
