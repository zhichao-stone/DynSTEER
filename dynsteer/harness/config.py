from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import JsonObject


def load_judge_config_from_env(env: Mapping[str, str] | None = None) -> JsonObject:
    """从环境变量读取 LLMJudge 配置。

    Args:
        env: 环境变量映射；测试时可传入 fake env。

    Returns:
        不包含 API key 明文的 judge 配置摘要；未配置 provider 时返回空字典。
    """
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


def _read_json_object(path: Path, label: str) -> JsonObject:
    """读取 JSON 对象配置文件。

    Args:
        path: JSON 文件路径。
        label: 面向错误消息的配置名称。

    Returns:
        JSON 对象。
    """
    if path is None:
        raise ValueError(f"{label} 路径不能为空")
    if not path.exists():
        raise ValueError(f"{label} 不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} 不是合法 JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{label} 必须是 JSON 对象")
    return data


def _read_json_array(path: Path, label: str) -> list[Any]:
    """读取 JSON 数组配置文件。

    Args:
        path: JSON 文件路径。
        label: 面向错误消息的配置名称。

    Returns:
        JSON 数组。
    """
    if path is None:
        raise ValueError(f"{label} 路径不能为空")
    if not path.exists():
        raise ValueError(f"{label} 不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} 不是合法 JSON: {path}") from exc
    if not isinstance(data, list):
        raise ValueError(f"{label} 必须是 JSON 数组")
    return data


def _required_str(data: dict[str, Any], key: str, label: str) -> str:
    """读取必填字符串字段。

    Args:
        data: 配置对象。
        key: 字段名。
        label: 配置名称。

    Returns:
        去除首尾空白后的字符串。
    """
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须提供非空字符串字段 {key}")
    return value.strip()


def _optional_str(data: dict[str, Any], key: str) -> str | None:
    """读取可选字符串字段。

    Args:
        data: 配置对象。
        key: 字段名。

    Returns:
        去除首尾空白后的字符串；未配置时返回 None。
    """
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _manifest_language(manifest: dict[str, Any]) -> str:
    """读取 benchmark prompt 语言配置。

    Args:
        manifest: benchmark.json 内容。

    Returns:
        prompt 语言代码，默认 en。
    """
    value = manifest.get("language", "en")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("benchmark.json language 必须是非空字符串")
    return value.strip()


def _case_ids_from_spec(spec: dict[str, Any], index: int) -> tuple[str, ...] | None:
    scenarios = spec.get("scenarios", [])
    if scenarios is None:
        return None
    if not isinstance(scenarios, list):
        raise ValueError(f"run_config.json 第 {index} 项的 scenarios 必须是字符串数组")
    if not scenarios:
        return None
    case_ids: list[str] = []
    for scenario in scenarios:
        if not isinstance(scenario, str) or not scenario.strip():
            raise ValueError(f"run_config.json 第 {index} 项的 scenarios 不能包含空字符串")
        case_ids.append(scenario.strip())
    return tuple(case_ids)


def load_harness_run_configs(
    benchmark: str,
    data_root: Path,
    runs_dir: Path,
    results_dir: Path,
) -> list[HarnessRunConfig]:
    """从 benchmark data-root 加载多组 harness 运行配置。

    Args:
        benchmark: benchmark 名称。
        data_root: 包含 benchmark.json 与 run_config.json 的目录。
        runs_dir: benchmark 原生输出与中间产物目录。
        results_dir: DynSTEER 评估结果目录。

    Returns:
        按 run_config.json 顺序生成的 HarnessRunConfig 列表。
    """
    if benchmark is None or not benchmark.strip():
        raise ValueError("benchmark 不能为空")
    if data_root is None:
        raise ValueError("data_root 不能为空")
    normalized_benchmark = benchmark.strip().lower()
    manifest = _read_json_object(data_root / "benchmark.json", "benchmark.json")
    manifest_benchmark = _required_str(manifest, "benchmark", "benchmark.json").lower()
    if manifest_benchmark != normalized_benchmark:
        raise ValueError(f"benchmark.json 中的 benchmark 必须是 {normalized_benchmark}")
    _required_str(manifest, "source_root", "benchmark.json")
    tool_backend = _required_str(manifest, "tool_backend", "benchmark.json")
    language = _manifest_language(manifest)

    raw_specs = _read_json_array(data_root / "run_config.json", "run_config.json")
    if not raw_specs:
        raise ValueError("run_config.json 至少需要包含一组运行配置")

    configs: list[HarnessRunConfig] = []
    seen_run_ids: set[str] = set()
    judge_config = load_judge_config_from_env()
    for index, raw_spec in enumerate(raw_specs):
        if not isinstance(raw_spec, dict):
            raise ValueError(f"run_config.json 第 {index} 项必须是 JSON 对象")
        metadata: JsonObject = {str(key): value for key, value in raw_spec.items() if key != "scenarios"}
        metadata["language"] = language
        if normalized_benchmark == "toolsandbox":
            _required_str(raw_spec, "agent", f"run_config.json 第 {index} 项")
            _required_str(raw_spec, "user", f"run_config.json 第 {index} 项")
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
            raise ValueError(f"run_config.json 中 run_id 重复: {run_id}")
        seen_run_ids.add(run_id)
        metadata["run_config_index"] = index
        metadata["run_id"] = run_id
        name = _optional_str(raw_spec, "name")
        if name is not None:
            metadata["run_config_name"] = name
        if judge_config:
            metadata["judge"] = judge_config
        configs.append(
            HarnessRunConfig(
                benchmark=normalized_benchmark,
                data_root=data_root,
                case_ids=_case_ids_from_spec(raw_spec, index),
                runs_dir=runs_dir,
                results_dir=results_dir,
                metadata=metadata,
            )
        )
    return configs
