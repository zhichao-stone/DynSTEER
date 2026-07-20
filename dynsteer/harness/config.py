import json
import os
from pathlib import Path
from typing import Any, Mapping

from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import JsonObject

DEFAULT_READY_FRONTIER_PATIENCE = 8


def load_judge_config_from_env(env: Mapping[str, str] | None = None) -> JsonObject:
    """从环境变量读取 LLMJudge 配置。"""
    source = env or os.environ
    provider = source.get("DYNSTEER_JUDGE_PROVIDER")
    if provider is None or not provider.strip():
        return {}
    model = source.get("DYNSTEER_JUDGE_MODEL")
    if model is None or not model.strip():
        raise ValueError("DYNSTEER_JUDGE_MODEL 不能为空")
    return {
        "provider": provider.strip(),
        "model": model.strip(),
        "base_url": source.get("DYNSTEER_JUDGE_BASE_URL"),
        "timeout_seconds": float(source.get("DYNSTEER_JUDGE_TIMEOUT_SECONDS", "60")),
        "temperature": float(source.get("DYNSTEER_JUDGE_TEMPERATURE", "0")),
        "expensive_passes": int(source.get("DYNSTEER_EXPENSIVE_JUDGE_PASSES", "3")),
        "api_key_configured": bool(source.get("DYNSTEER_JUDGE_API_KEY")),
    }


def _read_json_file(path: Path, label: str, expected_type: type) -> Any:
    """读取 JSON 对象配置文件。"""
    if path is None:
        raise ValueError(f"{label} 路径不能为空")
    if not path.exists():
        raise ValueError(f"{label} 不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} 不是合法 JSON: {path}") from exc
    if not isinstance(data, expected_type):
        expected = "JSON 对象" if expected_type is dict else "JSON 数组"
        raise ValueError(f"{label} 必须是 {expected}")
    return data


def _required_str(data: dict[str, Any], key: str, label: str) -> str:
    """读取必填字符串字段。"""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须提供非空字符串字段 {key}")
    return value.strip()


def _optional_str(data: dict[str, Any], key: str) -> str | None:
    """读取可选字符串字段。"""
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def load_ready_frontier_patience_from_env(env: Mapping[str, str] | None = None) -> int:
    """从环境变量读取 ready frontier 无进展 patience。"""
    source = env if env is not None else os.environ
    raw_value = source.get("DYNSTEER_READY_FRONTIER_PATIENCE")
    if raw_value is None or not raw_value.strip():
        return DEFAULT_READY_FRONTIER_PATIENCE
    try:
        patience = int(raw_value.strip())
    except ValueError as exc:
        raise ValueError("DYNSTEER_READY_FRONTIER_PATIENCE 必须是整数") from exc
    if patience < 1:
        raise ValueError("DYNSTEER_READY_FRONTIER_PATIENCE 必须大于 0")
    return patience


def _case_ids_from_spec(spec: dict[str, Any], index: int) -> tuple[str, ...] | None:
    scenarios = spec.get("scenarios", [])
    if scenarios is None:
        return None
    if not isinstance(scenarios, list):
        raise ValueError(f"run_configs.json 第 {index} 项的 scenarios 必须是字符串数组")
    if not scenarios:
        return None
    case_ids: list[str] = []
    for scenario in scenarios:
        if not isinstance(scenario, str) or not scenario.strip():
            raise ValueError(f"run_configs.json 第 {index} 项的 scenarios 不能包含空字符串")
        case_ids.append(scenario.strip())
    return tuple(case_ids)


def load_harness_run_configs(
    benchmark: str, data_root: Path, runs_dir: Path, results_dir: Path
) -> list[HarnessRunConfig]:
    """从 benchmark data-root 加载多组 harness 运行配置。"""
    if benchmark is None or not benchmark.strip():
        raise ValueError("benchmark 不能为空")
    if data_root is None:
        raise ValueError("data_root 不能为空")
    normalized_benchmark = benchmark.strip().lower()
    manifest: dict = _read_json_file(data_root / "benchmark.json", "benchmark.json", dict)
    manifest_benchmark = _required_str(manifest, "benchmark", "benchmark.json").lower()
    if manifest_benchmark != normalized_benchmark:
        raise ValueError(f"benchmark.json 中的 benchmark 必须是 {normalized_benchmark}")
    _required_str(manifest, "source_root", "benchmark.json")
    tool_backend = _required_str(manifest, "tool_backend", "benchmark.json")
    language = manifest.get("language", "en")
    if not isinstance(language, str) or not language.strip():
        raise ValueError("benchmark.json language 必须是非空字符串")
    language = language.strip()
    manifest_max_workers = manifest.get("max_workers")
    if manifest_max_workers is None or not isinstance(manifest_max_workers, int):
        raise ValueError("benchmark.json max_workers 必须是整数")
    manifest_max_workers = max(manifest_max_workers, 1)

    raw_specs = _read_json_file(data_root / "run_configs.json", "run_configs.json", list)
    if not raw_specs:
        raise ValueError("run_configs.json 至少需要包含一组运行配置")

    configs: list[HarnessRunConfig] = []
    seen_run_ids: set[str] = set()
    judge_config = load_judge_config_from_env()
    ready_frontier_patience = load_ready_frontier_patience_from_env()
    for index, raw_spec in enumerate(raw_specs):
        if not isinstance(raw_spec, dict):
            raise ValueError(f"run_configs.json 第 {index} 项必须是 JSON 对象")
        metadata: JsonObject = {
            str(key): value for key, value in raw_spec.items() if key not in {"scenarios", "ready_frontier_patience"}
        }
        metadata["language"] = language
        metadata["benchmark_max_workers"] = manifest_max_workers
        if normalized_benchmark == "toolsandbox":
            _required_str(raw_spec, "agent", f"run_configs.json 第 {index} 项")
            _required_str(raw_spec, "user", f"run_configs.json 第 {index} 项")
        metadata.setdefault("tool_backend", tool_backend)
        run_id = _optional_str(raw_spec, "run_id") or _optional_str(raw_spec, "name")
        if run_id is None:
            agent = _optional_str(raw_spec, "agent")
            user = _optional_str(raw_spec, "user")
            if agent is not None and user is not None:
                run_id = f"run_{index}_{agent}_user_{user}"
            else:
                run_id = f"run_{index}"
        if run_id in seen_run_ids:
            raise ValueError(f"run_configs.json 中 run_id 重复: {run_id}")
        seen_run_ids.add(run_id)
        metadata["run_config_index"] = index
        metadata["run_id"] = run_id
        name = _optional_str(raw_spec, "name")
        if name is not None:
            metadata["run_config_name"] = name
        if judge_config:
            metadata["judge"] = judge_config
        stop_on_ready = raw_spec.get("stop_on_ready_frontier_no_progress", True)
        if not isinstance(stop_on_ready, bool):
            raise ValueError("run_configs.json 字段 stop_on_ready_frontier_no_progress 必须是布尔值")
        ready_min_delta = raw_spec.get("ready_frontier_min_delta", 0.02)
        if not isinstance(ready_min_delta, (int, float)):
            raise ValueError("run_configs.json 字段 ready_frontier_min_delta 必须是数字")
        ready_min_delta = float(ready_min_delta)
        if ready_min_delta < 0:
            raise ValueError("run_configs.json 字段 ready_frontier_min_delta 不能为负数")
        configs.append(
            HarnessRunConfig(
                benchmark=normalized_benchmark,
                data_root=data_root,
                case_ids=_case_ids_from_spec(raw_spec, index),
                runs_dir=runs_dir,
                results_dir=results_dir,
                stop_on_ready_frontier_no_progress=stop_on_ready,
                ready_frontier_patience=ready_frontier_patience,
                ready_frontier_min_delta=ready_min_delta,
                metadata=metadata,
            )
        )
    return configs
