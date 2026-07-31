import functools
import importlib
import logging
import os
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar
from dynsteer.model import JsonValue
from dynsteer.utils import json_safe, read_json_file

def callable_name(value: object) -> str:
    """读取 callable 的稳定名称。"""
    if isinstance(value, functools.partial):
        return callable_name(value.func)
    name = getattr(value, "__name__", None)
    if isinstance(name, str) and name:
        return name
    return str(value)

def callable_spec(value: object) -> JsonValue:
    """将 callable 或 functools.partial 转换为 JSON 安全规格。"""
    if isinstance(value, functools.partial):
        return {
            "callable": callable_name(value.func),
            "partial_keywords": {
                str(key): callable_name(item) if callable(item) else json_safe(item)
                for key, item in dict(value.keywords or {}).items()
            },
        }
    return callable_name(value) if callable(value) else json_safe(value)

T = TypeVar("T")


def retry_call(
    operation: Callable[[], T],
    *,
    max_retries: int,
    retry_base_seconds: float,
    retry_max_seconds: float,
    logger: logging.Logger,
    warning_message: str,
    extra: Mapping[str, object],
    non_retry_errors: tuple[type[Exception], ...] = (),
) -> T:
    """按指数退避重复执行 operation。"""
    if operation is None:
        raise ValueError("操作不能为空")
    if logger is None:
        raise ValueError("logger 不能为空")
    if extra is None:
        raise ValueError("extra 不能为空")
    if isinstance(max_retries, bool) or max_retries < 1:
        raise ValueError("max_retries 必须大于 0")
    if retry_base_seconds < 0 or retry_max_seconds < 0:
        raise ValueError("重试等待时间不能为负数")

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return operation()
        except non_retry_errors:
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            delay_seconds = min(retry_base_seconds * 2 ** max(attempt - 1, 0), retry_max_seconds)
            logger.warning(
                warning_message,
                extra={
                    **dict(extra),
                    "attempt": attempt,
                    "max_retries": max_retries,
                    "delay_seconds": delay_seconds,
                    "error": str(exc),
                },
            )
            if delay_seconds > 0:
                time.sleep(delay_seconds)

    if last_error is None:
        raise RuntimeError("retry_call 未捕获到异常")
    raise last_error

def rows_from_dataframe(dataframe: object | None) -> list[dict[str, object]]:
    """将第三方 dataframe 或 list[dict] 转为普通行字典。"""
    if dataframe is None:
        return []
    to_dicts = getattr(dataframe, "to_dicts", None)
    if callable(to_dicts):
        return [dict(row) for row in to_dicts()]
    if isinstance(dataframe, list):
        return [dict(row) for row in dataframe]
    if isinstance(dataframe, dict):
        keys = list(dataframe)
        lengths = [len(value) for value in dataframe.values() if isinstance(value, list)]
        if lengths and len(set(lengths)) == 1:
            return [{key: dataframe[key][index] if isinstance(dataframe[key], list) else dataframe[key] for key in keys} for index in range(lengths[0])]
    raise TypeError(f"不支持的 dataframe 类型: {type(dataframe)!r}")

def load_manifest(data_root: Path, benchmark: str) -> dict[str, object]:
    """读取 benchmark.json。"""
    if data_root is None:
        raise ValueError("data_root 不能为空")
    if benchmark is None or not benchmark.strip():
        raise ValueError("benchmark 不能为空")
    manifest_path = data_root / "benchmark.json"
    if not manifest_path.exists():
        return {"benchmark": benchmark, "source_root": None}
    return read_json_file(manifest_path, "benchmark.json", dict)

def ensure_source_root(data_root: Path, project_root: Path, benchmark: str) -> None:
    """把运行时覆盖路径或 benchmark.json 中的 source_root 加入 sys.path。"""
    if project_root is None:
        raise ValueError("project_root 不能为空")
    raw_source_root = os.environ.get("DYNSTEER_BENCHMARK_SOURCE_ROOT")
    source_label = "DYNSTEER_BENCHMARK_SOURCE_ROOT"
    if raw_source_root is None:
        manifest = load_manifest(data_root, benchmark)
        raw_source_root = manifest.get("source_root")
        source_label = "benchmark.json source_root"
    if raw_source_root is None:
        return
    if not isinstance(raw_source_root, str) or not raw_source_root.strip():
        raise ValueError(f"{source_label} 必须是非空字符串")
    source_root = Path(raw_source_root.strip())
    if not source_root.is_absolute():
        source_root = project_root / source_root
    if not source_root.exists():
        raise FileNotFoundError(f"{benchmark} source_root 不存在: {source_root}")
    source_text = str(source_root.resolve())
    if source_text not in sys.path:
        sys.path.insert(0, source_text)

def import_module(module_name: str, dependency_error_message: str | None=None) -> Any:
    """导入 benchmark 运行期依赖模块。"""
    if not isinstance(module_name, str) or not module_name.strip():
        raise ValueError("module_name 不能为空")
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        message = dependency_error_message or f"缺少运行期依赖模块: {module_name}"
        raise ImportError(message) from exc
