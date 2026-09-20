from __future__ import annotations

import hashlib
import inspect
import importlib
import json
import logging
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import JsonObject, JsonValue

AGENTCOMPASS_COMMIT = "04d138a1c1decd2c9caa8c2659c698d7ffb677b4"
SUPPORTED_BENCHMARKS = frozenset({"swebench_pro", "skillsbench"})
_AGENTCOMPASS_COMPONENT_MODULES = {
    "swebench_pro": (
        "agentcompass.benchmarks.swebench_pro",
    ),
    "skillsbench": (
        "agentcompass.benchmarks.skillsbench",
    ),
}
_SECRET_KEYS = frozenset({"api_key", "model_api_key", "base_url", "model_base_url", "token", "password", "secret"})
_STATUS_VALUES = frozenset({"completed", "run_error", "eval_error", "run_error_or_eval_error", "skipped"})
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentCompassTaskRecord:
    """保存 AgentCompass 任务的可见字段投影。"""

    task_id: str
    question: str
    category: str
    metadata: Mapping[str, JsonValue]


def load_task_records(benchmark: str, config: HarnessRunConfig) -> Mapping[str, AgentCompassTaskRecord]:
    """按 benchmark 和运行配置加载并缓存只读任务目录。"""
    normalized = _benchmark_name(benchmark)
    settings = _agentcompass_config(config)
    params = dict(settings["benchmark_params"])
    for key in ("sample_ids", "k", "avgk"):
        params.pop(key, None)
    params_json = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _load_task_records_cached(normalized, str(settings["data_dir"]), params_json, AGENTCOMPASS_COMMIT)


def run_agentcompass_case(
    benchmark: str,
    case_id: str,
    config: HarnessRunConfig,
    output_dir: Path,
) -> JsonObject:
    """执行一个 AgentCompass case，并返回严格脱敏的 detail。"""
    normalized = _benchmark_name(benchmark)
    if config is None or output_dir is None or not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("config、case_id 和 output_dir 不能为空")
    settings = _agentcompass_config(config)
    benchmark_params = dict(settings["benchmark_params"])
    benchmark_params.update({"sample_ids": [case_id], "k": 1, "avgk": True})
    run_key, run_id = _run_identity(config, normalized, case_id)
    api = _agentcompass_api()

    # AgentCompass 执行边界：密钥仅由环境变量注入 request。
    try:
        request = api["build_run_request"](
            benchmark=normalized,
            harness=str(settings["harness"]),
            model=str(config.metadata.get("model_id") or ""),
            environment=str(settings["environment"]),
            benchmark_params=benchmark_params,
            harness_params=dict(settings["harness_params"]),
            environment_params=dict(settings["environment_params"]),
            model_base_url=os.environ.get("MODEL_BASE_URL", ""),
            model_api_key=os.environ.get("MODEL_API_KEY", ""),
            model_api_protocol=str(settings["model_api_protocol"]),
            model_params=dict(settings["model_params"]),
            task_concurrency=1,
            enabled_recipes=list(settings["enabled_recipes"]),
            enable_analysis=False,
            run_name="dynsteer",
            run_id=run_id,
            reuse=False,
        )
        result = api["run_evaluation_request"](
            request,
            results_dir=str(output_dir / "agentcompass-results"),
            data_dir=str(settings["data_dir"]),
            timeout_seconds=int(settings["timeout_seconds"]),
            progress="none",
            auto_install_dependencies=bool(settings["auto_install_dependencies"]),
        )
    except Exception as exc:
        logger.exception(
            "agentcompass_case_failed",
            extra={"事件": "AgentCompass单任务执行失败", "benchmark": normalized, "case_id": case_id, "run_id": run_id},
        )
        raise RuntimeError(f"AgentCompass 执行失败: benchmark={normalized}, case_id={case_id}") from exc

    # 精确定位当前 run 的唯一 detail，避免跨 case 或跨运行误读。
    paths = result.get("paths") if isinstance(result, Mapping) else None
    if not isinstance(paths, Mapping):
        raise TypeError("AgentCompass 返回值缺少 paths 对象")
    run_path = paths.get("run_info") or paths.get("params")
    if not isinstance(run_path, str) or not run_path.strip():
        raise ValueError("AgentCompass paths 缺少 run_info/params")
    run_dir = Path(run_path).resolve().parent
    matches: list[tuple[Path, JsonObject]] = []
    for detail_path in (run_dir / "details").glob("*.json"):
        try:
            payload = json.loads(detail_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"AgentCompass detail 无法解析: {detail_path}") from exc
        if isinstance(payload, dict) and payload.get("task_id") == case_id:
            matches.append((detail_path, payload))
    if len(matches) != 1:
        raise ValueError(f"AgentCompass detail 匹配数量必须为 1，实际为 {len(matches)}: {case_id}")
    detail_path, raw_detail = matches[0]
    attempts = raw_detail.get("attempts")
    if not isinstance(attempts, Mapping) or len(attempts) != 1:
        raise ValueError(f"AgentCompass detail attempts 必须恰好包含一次尝试: {case_id}")
    raw_attempt = next(iter(attempts.values()))
    if not isinstance(raw_attempt, Mapping):
        raise TypeError("AgentCompass attempt 必须是对象")
    sanitized = _sanitize_detail(normalized, str(raw_detail["task_id"]), raw_attempt, run_dir, detail_path, run_key, run_id)
    logger.info(
        "agentcompass_case_completed",
        extra={"事件": "AgentCompass单任务完成", "benchmark": normalized, "case_id": case_id, "run_id": run_id, "detail_path": str(detail_path)},
    )
    return sanitized


@lru_cache(maxsize=4)
def _load_task_records_cached(
    benchmark: str,
    data_dir: str,
    benchmark_params_json: str,
    agentcompass_commit: str,
) -> Mapping[str, AgentCompassTaskRecord]:
    """从 AgentCompass 加载一次任务目录并执行可见字段白名单投影。"""
    if agentcompass_commit != AGENTCOMPASS_COMMIT:
        raise ValueError("AgentCompass commit 与适配器固定版本不一致")
    api = _agentcompass_api()
    try:
        api["bootstrap_runtime"](data_dir=data_dir, force=True)
        _load_agentcompass_components(benchmark)
        request = api["build_run_request"](
            benchmark=benchmark,
            harness="none",
            model="task-catalog",
            environment="host_process",
            benchmark_params=json.loads(benchmark_params_json),
            enable_analysis=False,
        )
        tasks = api["benchmarks"].create(benchmark).load_tasks(request)
    except Exception as exc:
        raise RuntimeError(f"AgentCompass 任务目录加载失败: {benchmark}") from exc
    if inspect.isawaitable(tasks):
        raise TypeError("固定 AgentCompass 版本的 load_tasks 必须是同步接口")

    records: dict[str, AgentCompassTaskRecord] = {}
    for task in tasks:
        task_id = str(getattr(task, "task_id", "") or "").strip()
        question = str(getattr(task, "question", "") or "").strip()
        category = str(getattr(task, "category", "") or "").strip()
        if not task_id or not question or not category:
            raise ValueError(f"AgentCompass TaskSpec 缺少 task_id/question/category: {benchmark}")
        if task_id in records:
            raise ValueError(f"AgentCompass TaskSpec task_id 重复: {task_id}")
        metadata: dict[str, JsonValue] = {}
        raw_metadata = getattr(task, "metadata", {})
        if benchmark == "swebench_pro" and isinstance(raw_metadata, Mapping):
            for key in ("repo", "base_commit", "requirements", "interface"):
                if key not in raw_metadata:
                    continue
                value = raw_metadata.get(key)
                if isinstance(value, (str, int, float, bool)) or value is None:
                    metadata[key] = value
        records[task_id] = AgentCompassTaskRecord(
            task_id=task_id,
            question=question,
            category=category,
            metadata=MappingProxyType(metadata),
        )
    logger.info("agentcompass_catalog_loaded", extra={"事件": "AgentCompass任务目录加载完成", "benchmark": benchmark, "task_count": len(records)})
    return MappingProxyType(records)


def _load_agentcompass_components(benchmark: str) -> None:
    """只加载目标 benchmark 的 AgentCompass 组件，避免无关依赖阻断 Windows。"""
    try:
        for module_name in _AGENTCOMPASS_COMPONENT_MODULES[benchmark]:
            importlib.import_module(module_name)
    except KeyError as exc:
        raise ValueError(f"AgentCompass benchmark 缺少组件加载配置: {benchmark}") from exc
    except Exception as exc:
        raise RuntimeError(f"AgentCompass 组件加载失败: {benchmark}") from exc


def _agentcompass_config(config: HarnessRunConfig) -> JsonObject:
    """读取并一次性校验 metadata.agentcompass 配置边界。"""
    if config is None or not isinstance(config.metadata, dict):
        raise ValueError("HarnessRunConfig.metadata 必须是 JSON 对象")
    value = config.metadata.get("agentcompass")
    if not isinstance(value, dict):
        raise TypeError("metadata.agentcompass 必须是 JSON 对象")
    _reject_secrets(value)
    if "model" in value:
        raise ValueError("metadata.agentcompass 不允许重复配置 model")
    if any(key in value for key in ("reuse", "reuse_run_id")):
        raise ValueError("AgentCompass 结果复用只允许由 DynSTEER 管理")
    model_id = config.metadata.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("metadata.model_id 必须是非空字符串")

    result: JsonObject = {}
    for key in ("harness", "environment", "model_api_protocol", "data_dir"):
        item = value.get(key)
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"metadata.agentcompass.{key} 必须是非空字符串")
        result[key] = item.strip()
    for key in ("benchmark_params", "harness_params", "environment_params", "model_params"):
        item = value.get(key, {})
        if not isinstance(item, dict):
            raise TypeError(f"metadata.agentcompass.{key} 必须是 JSON 对象")
        result[key] = dict(item)
    enabled_recipes = value.get("enabled_recipes", [])
    if not isinstance(enabled_recipes, list) or any(not isinstance(item, str) or not item.strip() for item in enabled_recipes):
        raise ValueError("metadata.agentcompass.enabled_recipes 必须是非空字符串数组")
    timeout = value.get("timeout_seconds", 360000)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise ValueError("metadata.agentcompass.timeout_seconds 必须是正整数")
    data_dir = Path(str(result["data_dir"])).expanduser()
    result["data_dir"] = str((Path.cwd() / data_dir).resolve() if not data_dir.is_absolute() else data_dir.resolve())
    result["enabled_recipes"] = list(enabled_recipes)
    result["timeout_seconds"] = timeout
    auto_install = value.get("auto_install_dependencies", False)
    if not isinstance(auto_install, bool):
        raise TypeError("metadata.agentcompass.auto_install_dependencies 必须是 bool")
    result["auto_install_dependencies"] = auto_install
    return result


def _run_identity(config: HarnessRunConfig, benchmark: str, case_id: str) -> tuple[str, str]:
    """返回稳定 run key 和每次执行唯一的 AgentCompass run ID。"""
    parts = [
        config.metadata.get("experiment_id"),
        benchmark,
        config.metadata.get("model_id"),
        config.metadata.get("method"),
        config.metadata.get("repeat_index"),
        case_id,
    ]
    raw = "__".join(str(part if part is not None else "") for part in parts)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._") or "dynsteer"
    if len(safe) > 120:
        safe = f"{safe[:96].rstrip('._')}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return safe, f"{safe}-{timestamp}-{secrets.token_hex(4)}"


def _sanitize_detail(
    benchmark: str,
    task_id: str,
    raw_attempt: Mapping[str, object],
    run_dir: Path,
    detail_path: Path,
    run_key: str,
    run_id: str,
) -> JsonObject:
    """将原始 detail 投影为 benchmark 所需的严格白名单结构。"""
    status = raw_attempt.get("status")
    if not isinstance(status, str) or status not in _STATUS_VALUES:
        raise ValueError(f"AgentCompass attempt status 不合法: {status}")
    correct = raw_attempt.get("correct")
    if not isinstance(correct, bool):
        raise TypeError("AgentCompass attempt correct 必须是 bool")
    extra = raw_attempt.get("extra")
    extra = extra if isinstance(extra, Mapping) else {}

    # detail 脱敏：只保留评分、ACTF 和必要 artifact 字段。
    attempt: JsonObject = {
        "status": status,
        "correct": correct,
        "error_present": bool(raw_attempt.get("error")),
        "trajectory": raw_attempt.get("trajectory") if isinstance(raw_attempt.get("trajectory"), dict) else None,
    }
    if benchmark == "swebench_pro":
        evaluation = extra.get("eval_raw_data")
        evaluation = evaluation if isinstance(evaluation, Mapping) else {}
        attempt["final_answer"] = raw_attempt.get("final_answer") if isinstance(raw_attempt.get("final_answer"), str) else ""
        attempt["evaluation"] = {
            "completed": evaluation.get("completed"),
            "resolved": evaluation.get("resolved"),
            "timed_out": evaluation.get("timed_out"),
            "returncode": evaluation.get("returncode"),
            "error_present": bool(evaluation.get("error")),
        }
    else:
        evaluation = extra.get("verify_log")
        evaluation = evaluation if isinstance(evaluation, Mapping) else {}
        artifacts = raw_attempt.get("artifacts")
        artifacts = artifacts if isinstance(artifacts, Mapping) else {}
        files = artifacts.get("file")
        attempt["score"] = raw_attempt.get("score")
        attempt["files"] = dict(files) if isinstance(files, Mapping) else {}
        attempt["evaluation"] = {
            "reward": evaluation.get("reward"),
            "test_return_code": evaluation.get("test_return_code"),
            "test_error_present": bool(evaluation.get("test_error")),
            "reward_error_present": bool(evaluation.get("reward_error")),
        }
    return {
        "task_id": task_id,
        "attempt": attempt,
        "provenance": {
            "agentcompass_commit": AGENTCOMPASS_COMMIT,
            "run_key": run_key,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "detail_path": str(detail_path),
            "detail_sha256": hashlib.sha256(detail_path.read_bytes()).hexdigest(),
        },
    }


def _benchmark_name(benchmark: str) -> str:
    if not isinstance(benchmark, str) or not benchmark.strip():
        raise ValueError("benchmark 不能为空")
    normalized = benchmark.strip().lower().replace("-", "_")
    if normalized not in SUPPORTED_BENCHMARKS:
        raise ValueError(f"AgentCompass benchmark 不受支持: {benchmark}")
    return normalized


def _agentcompass_api() -> dict[str, Any]:
    """在 AgentCompass benchmark 真正启用时加载其可选依赖边界。"""
    # 第三方可选依赖边界：基础 DynSTEER 启动不要求安装 AgentCompass。
    try:
        from agentcompass import build_run_request, run_evaluation_request
        from agentcompass.runtime import BENCHMARKS
        from agentcompass.runtime.config import bootstrap_runtime
    except ModuleNotFoundError as exc:
        raise ImportError(
            "当前环境未安装 AgentCompass。请使用 .venv-agentcompass 独立环境安装 ../AgentCompass，"
            "并设置 UV_PROJECT_ENVIRONMENT 与 DYNSTEER_SKIP_UV_SYNC=1；"
            "所选 harness 的 host 依赖可由 auto_install_dependencies=true 按需安装。"
        ) from exc
    return {
        "build_run_request": build_run_request,
        "run_evaluation_request": run_evaluation_request,
        "benchmarks": BENCHMARKS,
        "bootstrap_runtime": bootstrap_runtime,
    }


def _reject_secrets(value: Mapping[str, object]) -> None:
    for key, item in value.items():
        if str(key).strip().lower() in _SECRET_KEYS:
            raise ValueError(f"metadata.agentcompass 禁止配置敏感字段: {key}")
        if isinstance(item, Mapping):
            _reject_secrets(item)
