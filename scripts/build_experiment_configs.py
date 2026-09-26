from __future__ import annotations

import argparse
import json
import random
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any
from collections import defaultdict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.get_cases import (
    export_skillsbench,
    export_swebench_pro,
    export_toolsandbox,
    stratified_sample,
)


SEED = 202608
SHARD_COUNT = 4
SKILLS_MAIN_SIZE = 29
SKILLS_ABLATION_SIZE = 14
SKILLS_PILOT_CASE_IDS = (
    "earthquake-plate-calculation",
    "manufacturing-equipment-maintenance",
    "parallel-tfidf-search",
    "sales-pivot-analysis",
    "sec-financial-report",
)
SKILLS_EXCLUDED_CASE_ID = "fix-build-agentops"
MAIN_METHODS = ["default", "dynsteer_replay", "dynsteer_replay_static"]
REUSED_ABLATION_METHODS = MAIN_METHODS[:2]
REUSED_INTERVENTION_METHODS = MAIN_METHODS[:1]
ABLATION_METHODS = [
    "default",
    "dynsteer_replay",
    "dynsteer_replay_static_routing",
    "dynsteer_replay_static_weighting",
    "dynsteer_replay_no_minefields",
    "dynsteer_replay_no_milestone_graph",
    "dynsteer_replay_no_policy_stop",
]
TOOLSANDBOX_ABLATION_METHODS = [
    *ABLATION_METHODS[:2],
    "dynsteer_replay_static",
    *ABLATION_METHODS[2:],
]
PILOT_MODELS = ("deepseek-v4-pro", "qwen-plus-2025-12-01")
SKILLS_PILOT_MODELS = (*PILOT_MODELS, "deepseek-v4-flash")


def main(argv: list[str] | None = None) -> int:
    """生成正式配置与除 pilot 外的 case 均分 shard 配置。"""
    parser = argparse.ArgumentParser(description="生成源码直连实验配置")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    reports = _source_reports()
    toolsandbox = json.loads((PROJECT_ROOT / "data/experiments/toolsandbox_main.json").read_text(encoding="utf-8"))
    toolsandbox_main_ids = list(toolsandbox["benchmarks"][0]["case_ids"])
    swebench_baseline = _subset_report(reports["swebench_pro"], _sample(reports["swebench_pro"], 100))
    swebench_main_ids, swebench_ablation_ids = _nested_samples(
        swebench_baseline, main_size=39, ablation_size=19
    )
    skills_baseline = _subset_report(reports["skillsbench"], _sample(reports["skillsbench"], 39))
    skills_case_pool = [
        case_id for case_id in skills_baseline["case_ids"] if case_id != SKILLS_EXCLUDED_CASE_ID
    ]
    skills_main_ids, skills_ablation_ids = _nested_samples(
        _subset_report(skills_baseline, skills_case_pool),
        main_size=SKILLS_MAIN_SIZE,
        ablation_size=SKILLS_ABLATION_SIZE,
        required_case_ids=SKILLS_PILOT_CASE_IDS,
    )
    configs = [
        _config("toolsandbox", "main", toolsandbox, 3, MAIN_METHODS, toolsandbox_main_ids),
        _config("swebench_pro", "main", toolsandbox, 3, MAIN_METHODS, swebench_main_ids),
        _config("skillsbench", "main", toolsandbox, 3, MAIN_METHODS, skills_main_ids),
        _config("toolsandbox", "ablation", toolsandbox, 3, TOOLSANDBOX_ABLATION_METHODS, _sample(reports["toolsandbox"], 100)),
        _config("swebench_pro", "ablation", toolsandbox, 3, ABLATION_METHODS, swebench_ablation_ids),
        _config("skillsbench", "ablation", toolsandbox, 3, ABLATION_METHODS, skills_ablation_ids),
        _config("toolsandbox", "intervention", toolsandbox, 3, _intervention_methods(), _sample(reports["toolsandbox"], 200)),
        _config("toolsandbox", "pilot", toolsandbox, 1, MAIN_METHODS, _sample(reports["toolsandbox"], 10)),
        _config("swebench_pro", "pilot", toolsandbox, 1, MAIN_METHODS, _sample(reports["swebench_pro"], 3)),
        _config("skillsbench", "pilot", toolsandbox, 1, MAIN_METHODS, SKILLS_PILOT_CASE_IDS),
    ]
    output_dir = PROJECT_ROOT / "data/experiments"
    shard_dir = output_dir / "model_shards"
    for config in configs:
        _write_config(
            output_dir / f"{config['experiment_id']}.json",
            config,
            force=args.force,
        )
    shards = _model_shards(configs)
    for shard in shards:
        _write_config(shard_dir / f"{shard['experiment_id']}.json", shard, force=args.force)
    print(json.dumps({
        "generated": len(configs),
        "model_shards": len(shards),
        "output_dir": str(output_dir),
        "shard_dir": str(shard_dir),
    }, ensure_ascii=False))
    return 0


def _write_config(path: Path, config: dict[str, Any], *, force: bool) -> None:
    if path.is_file() and path.stem != "toolsandbox_main" and not force:
        raise FileExistsError(f"实验配置已存在: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _model_shards(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shards: list[dict[str, Any]] = []
    for config in configs:
        if str(config["experiment_id"]).endswith("_pilot"):
            continue
        benchmark = str(config["benchmarks"][0]["benchmark"])
        case_ids = [str(item) for item in config["benchmarks"][0]["case_ids"]]
        for number, shard_case_ids in enumerate(_case_shards(case_ids), start=1):
            shard = deepcopy(config)
            shard["models"] = deepcopy(config["models"])
            shard["benchmarks"][0]["case_ids"] = shard_case_ids
            shard["benchmarks"][0]["data_root"] = f"../../{benchmark}"
            for source in shard.get("source_experiments", []):
                source_path = Path(str(source["config"]))
                source["config"] = f"{source_path.stem}_{number}{source_path.suffix}"
            shards.append(shard)
    return shards


def _case_shards(case_ids: list[str]) -> list[list[str]]:
    """按索引取模将 case 均分为固定数量的互斥子集。"""
    return [case_ids[index::SHARD_COUNT] for index in range(SHARD_COUNT)]


def _source_reports() -> dict[str, dict[str, Any]]:
    toolsandbox_root = _manifest_source_root("toolsandbox")
    return {
        "toolsandbox": export_toolsandbox(toolsandbox_root),
        "swebench_pro": export_swebench_pro(PROJECT_ROOT, PROJECT_ROOT / "data/swebench_pro"),
        "skillsbench": export_skillsbench(_manifest_source_root("skillsbench")),
    }


def _config(
    benchmark: str,
    experiment_type: str,
    toolsandbox: dict[str, Any],
    repeats: int,
    methods: list[object],
    case_ids: list[str],
) -> dict[str, Any]:
    selected_ids = sorted(case_ids)
    models = _models(benchmark, toolsandbox, experiment_type)
    config = {
        "experiment_id": f"{benchmark}_{experiment_type}",
        "repeats": repeats,
        "models": models,
        "methods": _method_specs(methods),
        "judge_profiles": {"fixed-judge": _judge(toolsandbox)},
        "threshold_profiles": {"default": {}},
        "benchmarks": [{
            "benchmark": benchmark,
            "data_root": f"data/{benchmark}",
            "case_ids": selected_ids,
            "metadata": {"capture_agent_usage": True},
            "milestone_generation": {"generator": _generator(toolsandbox)},
        }],
    }
    if experiment_type == "ablation":
        config["source_experiments"] = [{
            "config": f"{benchmark}_main.json",
            "methods": REUSED_ABLATION_METHODS,
        }]
    elif experiment_type == "intervention":
        config["source_experiments"] = [{
            "config": f"{benchmark}_main.json",
            "methods": REUSED_INTERVENTION_METHODS,
        }]
    return config


def _intervention_methods() -> list[object]:
    return [
        "default",
        {
            "method": "dynsteer_evaluate",
            "metadata": {"collect_online_native_score": True},
        },
        {
            "method": "dynsteer_evaluate_guided",
            "metadata": {"collect_online_native_score": True},
        },
    ]


def _method_specs(methods: list[object]) -> list[object]:
    result: list[object] = []
    for method in methods:
        if isinstance(method, str):
            result.append(method if method == "default" else {"method": method, "judge_profile": "fixed-judge"})
            continue
        item = deepcopy(method)
        if str(item.get("method")) != "default":
            item["judge_profile"] = "fixed-judge"
        result.append(item)
    return result


def _models(benchmark: str, toolsandbox: dict[str, Any], experiment_type: str) -> list[dict[str, Any]]:
    pilot_models = SKILLS_PILOT_MODELS if benchmark == "skillsbench" else PILOT_MODELS
    selected = [
        deepcopy(model)
        for model in toolsandbox["models"]
        if experiment_type != "pilot" or str(model.get("model_id")) in pilot_models
    ]
    if benchmark == "toolsandbox":
        return selected
    result: list[dict[str, Any]] = []
    for model in selected:
        client = dict(model["harness_metadata"]["agent_client"])
        client["model"] = str(model["model_id"])
        result.append({
            "model_id": str(model["model_id"]),
            "harness_metadata": {"client": client},
        })
    return result


def _judge(toolsandbox: dict[str, Any]) -> dict[str, str]:
    client = _client_by_model(toolsandbox, "qwen3-max-2026-01-23")
    return {
        "provider": "openai_compatible",
        "model": "qwen3-max-2026-01-23",
        "api_key": str(client["api_key"]),
        "base_url": str(client["base_url"]),
    }


def _generator(toolsandbox: dict[str, Any]) -> dict[str, object]:
    judge = _judge(toolsandbox)
    return {**judge, "temperature": 0.0}


def _client_by_model(toolsandbox: dict[str, Any], model_id: str) -> dict[str, Any]:
    for model in toolsandbox["models"]:
        if str(model.get("model_id")) == model_id:
            client = model.get("harness_metadata", {}).get("agent_client")
            if not isinstance(client, dict):
                break
            return dict(client)
    raise ValueError(f"ToolSandbox 配置缺少模型凭据: {model_id}")


def _sample(report: dict[str, Any], size: int) -> list[str]:
    selected = stratified_sample(report, size, SEED)
    if size >= 2 and len({str(_stratum(report, case_id)) for case_id in selected}) < 2:
        strata = sorted({str(_stratum(report, case_id)) for case_id in report["case_ids"]})
        if len(strata) >= 2:
            replacement = next(
                case_id for case_id in report["case_ids"]
                if str(_stratum(report, case_id)) == strata[1] and case_id not in selected
            )
            selected[-1] = replacement
            selected.sort()
    return selected


def _nested_samples(
    report: dict[str, Any],
    *,
    main_size: int,
    ablation_size: int,
    required_case_ids: tuple[str, ...] = (),
) -> tuple[list[str], list[str]]:
    main_ids = _constrained_sample(report, main_size, required_case_ids)
    main_report = _subset_report(report, main_ids)
    return main_ids, _constrained_sample(main_report, ablation_size, required_case_ids)


def _constrained_sample(
    report: dict[str, Any],
    size: int,
    required_case_ids: tuple[str, ...],
) -> list[str]:
    """按分层比例抽样，并在抽样结果中固定包含指定 case。"""
    if not required_case_ids:
        return _sample(report, size)
    required = set(required_case_ids)
    report_ids = {str(case["case_id"]) for case in report["cases"]}
    missing = required - report_ids
    if missing:
        raise ValueError(f"抽样范围缺少必须保留的 case: {sorted(missing)}")

    field = str(report["stats"]["stratification_field"])
    groups: dict[str, list[str]] = defaultdict(list)
    for case in report["cases"]:
        groups[str(case["strata"][field])].append(str(case["case_id"]))
    quotas = {name: len(ids) * size // len(report["cases"]) for name, ids in groups.items()}
    remaining = size - sum(quotas.values())
    order = sorted(groups, key=lambda name: (-len(groups[name]), name))
    for name in order[:remaining]:
        quotas[name] += 1

    rng = random.Random(SEED)
    selected: list[str] = []
    for name in sorted(groups):
        candidates = sorted(groups[name])
        required_ids = sorted(required & set(candidates))
        optional_ids = [case_id for case_id in candidates if case_id not in required]
        if len(required_ids) > quotas[name]:
            raise ValueError(f"分层配额不足以保留全部指定 case: {name}")
        selected.extend(required_ids)
        selected.extend(rng.sample(optional_ids, quotas[name] - len(required_ids)))
    return sorted(selected)


def _subset_report(report: dict[str, Any], case_ids: list[str]) -> dict[str, Any]:
    selected = set(case_ids)
    cases = [dict(case) for case in report["cases"] if str(case["case_id"]) in selected]
    if len(cases) != len(selected):
        raise ValueError("case report 缺少待抽样的 case")
    subset = deepcopy(report)
    subset["cases"] = cases
    subset["case_ids"] = [str(case["case_id"]) for case in cases]
    subset["stats"]["total_case_count"] = len(cases)
    subset["stats"]["unique_case_count"] = len(cases)
    return subset


def _stratum(report: dict[str, Any], case_id: str) -> object:
    field = str(report["stats"]["stratification_field"])
    for case in report["cases"]:
        if str(case["case_id"]) == case_id:
            return case["strata"][field]
    raise KeyError(case_id)


def _manifest_source_root(benchmark: str) -> Path:
    manifest = json.loads((PROJECT_ROOT / f"data/{benchmark}/benchmark.json").read_text(encoding="utf-8"))
    source_root = Path(str(manifest["source_root"]))
    return source_root if source_root.is_absolute() else PROJECT_ROOT / source_root


if __name__ == "__main__":
    raise SystemExit(main())

