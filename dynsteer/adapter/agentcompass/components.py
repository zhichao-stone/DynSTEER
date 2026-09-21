from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType


_COMPONENT_COLLECTIONS = frozenset({"benchmarks", "harnesses", "environments", "recipes"})


def _collection_package(module_name: str) -> str | None:
    """返回 AgentCompass builtin 组件的第一层集合包名。"""
    parts = module_name.split(".")
    if len(parts) < 3 or parts[0] != "agentcompass" or parts[1] not in _COMPONENT_COLLECTIONS:
        return None
    return ".".join(parts[:2])


def import_agentcompass_component(module_name: str) -> ModuleType:
    """导入 AgentCompass 单个组件，避免集合包的 __init__ 导出全部组件。"""
    collection_package = _collection_package(module_name)
    if collection_package is not None and collection_package not in sys.modules:
        # 第三方可选依赖边界：AgentCompass 的集合包会导入全部同类组件。
        root = importlib.import_module("agentcompass")
        package = ModuleType(collection_package)
        package.__path__ = [str(Path(next(iter(root.__path__))) / collection_package.removeprefix("agentcompass."))]
        sys.modules[collection_package] = package
    return importlib.import_module(module_name)
