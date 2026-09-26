import functools
import importlib
import logging
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar
from dynsteer.model import JsonValue
from dynsteer.utils import json_safe, read_json_file

def callable_name(value: object) -> str:
    """Reads the stable name of the callable."""
    if isinstance(value, functools.partial):
        return callable_name(value.func)
    name = getattr(value, "__name__", None)
    if isinstance(name, str) and name:
        return name
    return str(value)

def callable_spec(value: object) -> JsonValue:
    """Converts the callable or functools.partial to JSON security specifications."""
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
    """Do not repeat the execution by index."""
    if operation is None:
        raise ValueError('Operation cannot be empty')
    if logger is None:
        raise ValueError('Logger cannot be empty')
    if extra is None:
        raise ValueError("Extra, you can't be empty.")
    if isinstance(max_retries, bool) or max_retries < 1:
        raise ValueError('max_retries must be greater than 0')
    if retry_base_seconds < 0 or retry_max_seconds < 0:
        raise ValueError('The waiting time for retrying cannot be negative')

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
        raise RuntimeError('Not caught in an anomaly.')
    raise last_error

def rows_from_dataframe(dataframe: object | None) -> list[dict[str, object]]:
    """Converts a third party dataframe or list [dict] to a normal dictionaries."""
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
    raise TypeError(f"Unsupported dataframe type:{type(dataframe)!r}")

def load_manifest(data_root: Path, benchmark: str) -> dict[str, object]:
    """Read benchmark.json."""
    if data_root is None:
        raise ValueError('Data_root cannot be empty')
    if benchmark is None or not benchmark.strip():
        raise ValueError('Benchmark cannot be empty.')
    manifest_path = data_root / "benchmark.json"
    if not manifest_path.exists():
        return {"benchmark": benchmark, "source_root": None}
    return read_json_file(manifest_path, "benchmark.json", dict)


def resolve_source_root(data_root: Path, project_root: Path, benchmark: str) -> Path:
    """Resolve the read-only source root from a benchmark manifest."""
    if data_root is None or project_root is None:
        raise ValueError('Data_root and project_root cannot be empty')
    manifest = load_manifest(data_root, benchmark)
    raw_source_root = manifest.get("source_root")
    if raw_source_root is None:
        raise ValueError(f"{benchmark} benchmark.json must provide source_root")
    if not isinstance(raw_source_root, str) or not raw_source_root.strip():
        raise ValueError(f"{benchmark} benchmark.json source_root must be a non-empty string")
    source_root = Path(raw_source_root.strip())
    if not source_root.is_absolute():
        source_root = project_root / source_root
    if not source_root.exists():
        raise FileNotFoundError(f"{benchmark} source_root does not exist: {source_root}")
    return source_root.resolve()


def ensure_source_root(data_root: Path, project_root: Path, benchmark: str) -> None:
    """Add the source_root of the benchmark manifest to the sys.path."""
    if project_root is None:
        raise ValueError('Project_root cannot be empty')
    source_text = str(resolve_source_root(data_root, project_root, benchmark))
    if source_text not in sys.path:
        sys.path.insert(0, source_text)

def import_module(module_name: str, dependency_error_message: str | None=None) -> Any:
    """Import a benchmark runtime dependency module."""
    if not isinstance(module_name, str) or not module_name.strip():
        raise ValueError('module_name must not be empty')
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        message = dependency_error_message or f"missing runtime dependency module: {module_name}"
        raise ImportError(message) from exc
