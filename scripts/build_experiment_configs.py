from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.get_cases import export_swebench_pro, export_toolsandbox, stratified_sample

SEED = 202608
MODELS = ("deepseek-v4-pro", "deepseek-v4-flash", "qwen-plus-2025-12-01", "qwen3-max-2026-01-23")
MAIN_METHODS = ("default", "dynsteer_replay", "dynsteer_replay_static")
SWE_ABLATION_METHODS = (
    "default",
    "dynsteer_replay",
    "dynsteer_replay_static_routing",
    "dynsteer_replay_static_weighting",
    "dynsteer_replay_no_minefields",
    "dynsteer_replay_no_milestone_graph",
    "dynsteer_replay_no_policy_stop",
)
TOOL_ABLATION_METHODS = (
    "default",
    "dynsteer_replay",
    "dynsteer_replay_static",
    "dynsteer_replay_static_routing",
    "dynsteer_replay_static_weighting",
    "dynsteer_replay_no_minefields",
    'Generates an official configuration with a case equal to the case share share.',
    'Generate source-code experiment configurations',
)


def main(argv: list[str] | None = None) -> int:
    """Generate credential-free experiment configurations for public benchmarks."""
    parser = argparse.ArgumentParser(description="Generate DynSTEER experiment configurations")
    parser.add_argument("--force", action="store_true", help="overwrite existing configurations")
    args = parser.parse_args(argv)
    reports = _source_reports()
    tool_cases = list(reports["toolsandbox"]["case_ids"])
    swe_cases = stratified_sample(reports["swebench_pro"], min(100, len(reports["swebench_pro"]["case_ids"])), SEED)
    configs = [
        _config("toolsandbox", "main", 3, MAIN_METHODS, tool_cases),
        _config("swebench_pro", "main", 3, MAIN_METHODS, swe_cases),
        _config("toolsandbox", "ablation", 3, TOOL_ABLATION_METHODS, tool_cases[:100]),
        _config("swebench_pro", "ablation", 3, SWE_ABLATION_METHODS, swe_cases[:50]),
    ]
    output_dir = PROJECT_ROOT / "data/experiments"
    for config in configs:
        _write_config(output_dir / f"{config['experiment_id']}.json", config, force=args.force)
    print(json.dumps({"generated": len(configs), "output_dir": str(output_dir)}))
    return 0


def _source_reports() -> dict[str, dict[str, Any]]:
    return {
        "toolsandbox": export_toolsandbox(_manifest_source_root("toolsandbox")),
        "swebench_pro": export_swebench_pro(PROJECT_ROOT, PROJECT_ROOT / "data/swebench_pro"),
    }


def _config(
    benchmark: str,
    experiment_type: str,
    repeats: int,
    methods: tuple[str, ...],
    case_ids: list[str],
) -> dict[str, Any]:
    return {
        "experiment_id": f"{benchmark}_{experiment_type}",
        "repeats": repeats,
        "models": [_model(benchmark, model_id) for model_id in MODELS],
        "methods": [{"method": method, "judge_profile": "fixed-judge"} for method in methods],
        "judge_profiles": {"fixed-judge": _llm_profile("qwen3-max-2026-01-23", 0.2)},
        "threshold_profiles": {"default": {}},
        "benchmarks": [{
            "benchmark": benchmark,
            "data_root": f"data/{benchmark}",
            "case_ids": sorted(case_ids),
            "metadata": {"capture_agent_usage": True},
            "milestone_generation": {
                "target_candidate_graph_count": 6,
                "max_candidate_batch_count": 4,
                "generator": _llm_profile("qwen3-max-2026-01-23", 0.0),
            },
        }],
        **({"source_experiments": [{
            "config": f"{benchmark}_main.json",
            "methods": ["default", "dynsteer_replay"],
        }]} if experiment_type == "ablation" else {}),
    }


def _model(benchmark: str, model_id: str) -> dict[str, Any]:
    if benchmark == "toolsandbox":
        harness_metadata = {
            "agent": model_id,
            "user": "qwen-plus-2025-12-01",
            "agent_client": _client_profile("DYNSTEER_AGENT"),
            "max_messages": 100,
        }
    else:
        harness_metadata = {
            "client": {**_client_profile("DYNSTEER_AGENT"), "model": model_id},
            "max_tool_calls": 60,
            "command_timeout_seconds": 600,
            "model_temperature": 0.0,
        }
    return {"model_id": model_id, "harness_metadata": harness_metadata}


def _client_profile(prefix: str) -> dict[str, str]:
    return {"api_key_env": f"{prefix}_API_KEY", "base_url_env": f"{prefix}_BASE_URL"}


def _llm_profile(model: str, temperature: float) -> dict[str, object]:
    return {
        "provider": "openai_compatible",
        "model": model,
        "api_key_env": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
        "temperature": temperature,
    }


def _write_config(path: Path, config: dict[str, Any], *, force: bool) -> None:
    if path.is_file() and not force:
        raise FileExistsError(f"Experiment configuration already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _manifest_source_root(benchmark: str) -> Path:
    manifest = json.loads((PROJECT_ROOT / f"data/{benchmark}/benchmark.json").read_text(encoding="utf-8"))
    source_root = Path(str(manifest["source_root"]))
    return source_root if source_root.is_absolute() else PROJECT_ROOT / source_root


if __name__ == "__main__":
    raise SystemExit(main())
