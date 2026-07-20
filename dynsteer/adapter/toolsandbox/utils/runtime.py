from collections.abc import Callable
from pathlib import Path

from dynsteer.adapter.utils import import_module, load_manifest
from dynsteer.harness.model import HarnessRunConfig

TOOL_SANDBOX_DEPENDENCY_ERROR = (
    "ToolSandbox harness 需要安装 ToolSandbox 及其依赖。"
    "请确认 data/toolsandbox/benchmark.json 的 source_root 可导入，"
    "或在当前 uv 环境安装 ToolSandbox。"
)


def toolsandbox_project_root() -> Path:
    """返回 DynSTEER 项目根目录。"""
    return Path(__file__).resolve().parents[4]


def load_toolsandbox_module(module_name: str) -> object:
    """导入 ToolSandbox 运行期模块。"""
    return import_module(module_name, TOOL_SANDBOX_DEPENDENCY_ERROR)


def tool_backend(config: HarnessRunConfig, module_loader: Callable[[str], object] = load_toolsandbox_module) -> object:
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
