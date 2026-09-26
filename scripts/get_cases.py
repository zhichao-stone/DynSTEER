from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import random
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from dynsteer.utils import read_json_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUTS = {
    "toolsandbox": "docs/cases_toolsandbox.json",
    "swebench_pro": "docs/cases_swebench_pro.json",
    "skillsbench": "docs/cases_skills-bench.json",
}
DEFAULT_SIZES = {"toolsandbox": 509, "swebench_pro": 100, "skillsbench": 87}
SKILLS_EXCLUDED_TASKS = frozenset({"mhc-layer-impl"})


def export_toolsandbox(source_root: Path) -> dict[str, Any]:
    """从 ToolSandbox 源码模块导出全量 scenario 清单。"""
    if source_root not in sys.path:
        sys.path.insert(0, str(source_root))
    scenarios_module = importlib.import_module("tool_sandbox.scenarios")
    discovery_module = importlib.import_module("tool_sandbox.common.tool_discovery")
    scenarios = scenarios_module.named_scenarios(
        preferred_tool_backend=discovery_module.ToolBackend.DEFAULT
    )
    if not isinstance(scenarios, dict) or not scenarios:
        raise ValueError("ToolSandbox named_scenarios 必须返回非空字典")
    cases: list[dict[str, Any]] = []
    for case_id, scenario in sorted((str(key), value) for key, value in scenarios.items()):
        categories = tuple(
            getattr(item, "value", item) for item in getattr(scenario, "categories", []) or []
        )
        cases.append({"case_id": case_id, "strata": {"categories": list(categories)}})
    return _report(
        benchmark="toolsandbox",
        source="tool_sandbox.scenarios",
        cases=cases,
        stratum_field="categories",
        source_digest=_directory_digest(source_root / "tool_sandbox"),
        source_git_commit=_git_commit(source_root),
    )


def export_swebench_pro(project_root: Path, data_root: Path) -> dict[str, Any]:
    """从本地 SWE-bench Pro evaluator 快照导出 case 清单。"""
    samples_path = data_root / "source" / "eval_samples.jsonl"
    digest_path = data_root / "source" / ".archive_digest"
    if not samples_path.is_file() or not digest_path.is_file():
        raise FileNotFoundError("缺少 SWE-bench Pro 快照；请先运行 scripts/prepare_swebench_pro.py")
    digest_data = read_json_file(digest_path, "SWE archive digest", dict)
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in samples_path.read_text(encoding="utf-8").splitlines():
        sample = json.loads(line)
        case_id = str(sample["instance_id"])
        if case_id in seen:
            raise ValueError(f"SWE instance_id 重复: {case_id}")
        seen.add(case_id)
        cases.append({"case_id": case_id, "strata": {"repo": str(sample["repo"])}})
    manifest = read_json_file(data_root / "benchmark.json", "SWE manifest", dict)
    source_root = _manifest_source_root(project_root, data_root, "swebench_pro")
    return _report(
        benchmark="swebench_pro",
        source="data/swebench_pro/source/eval_samples.jsonl",
        cases=cases,
        stratum_field="repo",
        source_digest=str(digest_data.get("sha256") or ""),
        archive_digest=str(digest_data.get("sha256") or ""),
        source_git_commit=str(digest_data.get("source_git_commit") or _git_commit(source_root)),
        dataset_archive=str((data_root / str(manifest.get("dataset_archive", ""))).resolve()),
    )


def export_skillsbench(source_root: Path) -> dict[str, Any]:
    """扫描 SkillsBench 本地任务目录并解析公开分类元数据。"""
    tasks_dir = source_root / "tasks"
    if not tasks_dir.is_dir():
        raise FileNotFoundError(f"SkillsBench tasks 目录不存在: {tasks_dir}")
    cases: list[dict[str, Any]] = []
    for task_dir in sorted(tasks_dir.iterdir()):
        task_path = task_dir / "task.md"
        if not task_dir.is_dir() or task_dir.name in SKILLS_EXCLUDED_TASKS:
            continue
        if not task_path.is_file():
            raise FileNotFoundError(f"SkillsBench task.md 不存在: {task_path}")
        metadata = _skills_metadata(task_path)
        category = str(metadata.get("category") or "")
        if not category:
            raise ValueError(f"SkillsBench task 缺少 metadata.category: {task_dir.name}")
        cases.append({
            "case_id": task_dir.name,
            "strata": {"category": category, "subcategory": str(metadata.get("subcategory") or "")},
        })
    if not cases:
        raise ValueError(f"SkillsBench 没有可用任务: {tasks_dir}")
    return _report(
        benchmark="skillsbench",
        source="tasks/*/task.md",
        cases=cases,
        stratum_field="category",
        source_digest=_tasks_digest(tasks_dir),
        source_git_commit=_git_commit(source_root),
    )


def stratified_sample(report: dict[str, Any], size: int, seed: int) -> list[str]:
    """按报告主分层字段做确定性比例抽样。"""
    cases = _report_cases(report)
    if size < 1:
        raise ValueError("sample size 必须大于 0")
    if size >= len(cases):
        return [case["case_id"] for case in cases]
    field = str(report["stats"]["stratification_field"])
    groups: dict[str, list[str]] = defaultdict(list)
    for case in cases:
        groups[str(case["strata"][field])].append(str(case["case_id"]))
    quotas = {name: len(ids) * size // len(cases) for name, ids in groups.items()}
    remaining = size - sum(quotas.values())
    order = sorted(groups, key=lambda name: (-len(groups[name]), name))
    for name in order[:remaining]:
        quotas[name] += 1
    rng = random.Random(seed)
    selected: list[str] = []
    for name in sorted(groups):
        candidates = sorted(groups[name])
        selected.extend(rng.sample(candidates, quotas[name]))
    return sorted(selected)


def _report(
    *,
    benchmark: str,
    source: str,
    cases: list[dict[str, Any]],
    stratum_field: str,
    source_digest: str,
    **extra: object,
) -> dict[str, Any]:
    if not source_digest:
        raise ValueError(f"{benchmark} source digest 不能为空")
    distribution = Counter(str(case["strata"][stratum_field]) for case in cases)
    return {
        "benchmark": benchmark,
        "source": source,
        "case_ids": sorted(str(case["case_id"]) for case in cases),
        "cases": sorted(cases, key=lambda case: str(case["case_id"])),
        "stats": {
            "total_case_count": len(cases),
            "unique_case_count": len({str(case["case_id"]) for case in cases}),
            "duplicate_case_count": 0,
            "stratification_field": stratum_field,
            "stratification_distribution": dict(sorted(distribution.items())),
        },
        "source_digest": source_digest,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **{str(key): value for key, value in extra.items()},
    }


def _report_cases(report: dict[str, Any]) -> list[dict[str, Any]]:
    cases = report.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("case report 缺少 cases")
    return [dict(item) for item in cases if isinstance(item, dict)]


def _manifest_source_root(project_root: Path, data_root: Path, benchmark: str) -> Path:
    manifest = read_json_file(data_root / "benchmark.json", "benchmark manifest", dict)
    source_root = Path(str(manifest.get("source_root") or ""))
    if not source_root:
        raise ValueError(f"{benchmark} benchmark.json source_root 不能为空")
    return source_root if source_root.is_absolute() else project_root / source_root


def _skills_metadata(task_path: Path) -> dict[str, Any]:
    text = task_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"SkillsBench task.md 缺少 front matter: {task_path}")
    end = text.find("\n---", 4)
    if end < 0:
        raise ValueError(f"SkillsBench task.md front matter 未闭合: {task_path}")
    payload = yaml.safe_load(text[4:end])
    metadata = payload.get("metadata") if isinstance(payload, dict) else None
    if not isinstance(metadata, dict):
        raise ValueError(f"SkillsBench task.md 缺少 metadata: {task_path}")
    return metadata


def _directory_digest(root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file() and path.suffix == ".py")
    if not files:
        raise ValueError(f"digest 目录没有 Python 源文件: {root}")
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _tasks_digest(tasks_dir: Path) -> str:
    digest = hashlib.sha256()
    files = [
        path
        for task_dir in sorted(tasks_dir.iterdir())
        if task_dir.is_dir() and task_dir.name not in SKILLS_EXCLUDED_TASKS
        for relative in ("task.md", "environment", "verifier")
        for path in ([task_dir / relative] if (task_dir / relative).is_file() else sorted((task_dir / relative).rglob("*")))
        if path.is_file()
    ]
    for path in sorted(files):
        digest.update(path.relative_to(tasks_dir).as_posix().encode("utf-8") + b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _git_commit(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "-c", f"safe.directory={root.resolve()}", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _load_report(path: Path, benchmark: str) -> dict[str, Any]:
    report = read_json_file(path, "case report", dict)
    if report.get("benchmark") != benchmark:
        raise ValueError(f"case report benchmark 不匹配: {report.get('benchmark')} != {benchmark}")
    return report

def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出源码直连 benchmark case 清单")
    parser.add_argument("--benchmark", required=True, choices=tuple(DEFAULT_OUTPUTS))
    parser.add_argument("--seed", type=int, default=202608)
    parser.add_argument("--size", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """执行 case 清单导出或源码一致性检查。"""
    args = _parse_args(argv)
    benchmark = args.benchmark
    output = args.output or PROJECT_ROOT / DEFAULT_OUTPUTS[benchmark]
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    size = args.size or DEFAULT_SIZES[benchmark]
    try:
        if benchmark == "toolsandbox":
            report = export_toolsandbox(_manifest_source_root(PROJECT_ROOT, PROJECT_ROOT / "data/toolsandbox", benchmark))
        elif benchmark == "swebench_pro":
            report = export_swebench_pro(PROJECT_ROOT, PROJECT_ROOT / "data/swebench_pro")
        else:
            report = export_skillsbench(_manifest_source_root(PROJECT_ROOT, PROJECT_ROOT / "data/skillsbench", benchmark))
        report["sample"] = {
            "seed": args.seed,
            "requested_size": size,
            "selected_size": min(size, int(report["stats"]["total_case_count"])),
            "case_ids": stratified_sample(report, size, args.seed),
        }
        if args.check:
            current = _load_report(output, benchmark)
            identity = {key: value for key, value in report.items() if key != "generated_at"}
            current_identity = {key: value for key, value in current.items() if key != "generated_at"}
            if current_identity != identity:
                raise ValueError("当前 case report 与本地源码 digest 不一致")
            return 0
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"benchmark": benchmark, "case_count": report["stats"]["total_case_count"], "output": str(output)}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"case 清单导出失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
