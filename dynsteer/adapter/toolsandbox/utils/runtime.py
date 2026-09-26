from collections.abc import Callable
import os
from pathlib import Path
from threading import Lock
from dynsteer.adapter.utils import import_module, load_manifest
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.utils import enum_name


TOOL_SANDBOX_DEPENDENCY_ERROR = "ToolSandbox Harness needs to install ToolSandbox and its dependence. Confirm that data/toolsandbox/benchmark.json's source_root can be imported or installed in the current uv environment"
_NAMED_SCENARIOS_CACHE: dict[tuple[str, str, str], dict[str, object]] = {}
_NAMED_SCENARIOS_CACHE_LOCK = Lock()

def toolsandbox_project_root() -> Path:
    """returns the DynSTEER project root directory."""
    return Path(__file__).resolve().parents[4]

def load_toolsandbox_module(module_name: str) -> object:
    """Imports the ToolSandbox runtime module."""
    return import_module(module_name, TOOL_SANDBOX_DEPENDENCY_ERROR)

def tool_backend(config: HarnessRunConfig, module_loader: Callable[[str], object]=load_toolsandbox_module) -> object:
    """Read ToolSandbox tool backend."""
    if config is None:
        raise ValueError("Can't be empty.")
    manifest = load_manifest(config.data_root, "toolsandbox")
    raw_backend = config.metadata.get("tool_backend", manifest.get("tool_backend", "DEFAULT"))
    if not isinstance(raw_backend, str) or not raw_backend.strip():
        raise ValueError('ToolSandbox tool_backend')
    discovery_module = module_loader("tool_sandbox.common.tool_discovery")
    tool_backend_type = getattr(discovery_module, "ToolBackend")
    backend_name = raw_backend.strip()
    try:
        return tool_backend_type[backend_name]
    except (KeyError, TypeError):
        try:
            return tool_backend_type(backend_name)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Unsupported ToolSandbox tool_backend:{backend_name}") from exc

def load_named_scenarios(
    config: HarnessRunConfig,
    module_loader: Callable[[str], object] = load_toolsandbox_module,
) -> dict[str, object]:
    """Returns the current process, the scenario dictionary shared with the current backend/data root."""
    if config is None or module_loader is None:
        raise ValueError('Config and module_loader cannot be empty')
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
            raise ValueError('ToolSandbox named_scenarios must return the dictionary')
        normalized = {str(key): value for key, value in scenarios.items()}
        _NAMED_SCENARIOS_CACHE[cache_key] = normalized
        return normalized

def clear_named_scenarios_cache() -> None:
    """Clears the test or new experimental boundary scenario cache."""
    with _NAMED_SCENARIOS_CACHE_LOCK:
        _NAMED_SCENARIOS_CACHE.clear()

def _source_root_cache_key(config: HarnessRunConfig) -> str:
    """Returns the stable path that affects the ToolSandbox module source."""
    raw_source_root = os.environ.get("DYNSTEER_BENCHMARK_SOURCE_ROOT")
    if raw_source_root is None:
        raw_source_root = load_manifest(config.data_root, "toolsandbox").get("source_root")
    if not isinstance(raw_source_root, str) or not raw_source_root.strip():
        return ""
    source_root = Path(raw_source_root.strip())
    if not source_root.is_absolute():
        source_root = toolsandbox_project_root() / source_root
    return str(source_root.resolve())
