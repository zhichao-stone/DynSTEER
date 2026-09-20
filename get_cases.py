import argparse
import json
import logging
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
import datasets
from pathlib import Path
import shutil
from types import ModuleType
import urllib.request
import zipfile


if sys.platform == "win32" and "fcntl" not in sys.modules:
    # Windows 只导出 task catalog 时，不需要未启用 benchmark 的真实 Unix 文件锁。
    fcntl = ModuleType("fcntl")
    fcntl.LOCK_EX = 2
    fcntl.LOCK_SH = 1
    fcntl.LOCK_UN = 8
    fcntl.flock = lambda *args, **kwargs: None
    sys.modules["fcntl"] = fcntl

from dynsteer.adapter.agentcompass.runtime import (
    AGENTCOMPASS_COMMIT,
    AgentCompassTaskRecord,
    load_task_records,
)
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.log import configure_logger
from dynsteer.model import JsonObject


PROJECT_ROOT = Path(__file__).resolve().parent
BENCHMARK_CONFIGS = {
    "swebench_pro": {
        "case_id_source": "dataset instance_id",
        "stratification_field": "repo",
        "benchmark_params": {"eval_timeout": 3600},
        "harness": "mini_swe_agent",
        "environment": "docker",
    },
    "skillsbench": {
        "case_id_source": "task directory name",
        "stratification_field": "category",
        "benchmark_params": {
            "data_version": "1.1",
            "execute_timeout_multiplier": 20.0,
            "verifier_timeout_multiplier": 8.0,
        },
        "harness": "openhands",
        "environment": "docker",
    },
}
OUTPUT_PATHS = {
    "swebench_pro": PROJECT_ROOT / "docs" / "cases_swe-bench-pro.json",
    "skillsbench": PROJECT_ROOT / "docs" / "cases_skills-bench.json",
}
SKILLSBENCH_VERSION = "1.1"
SKILLSBENCH_DATASET_URL = "https://github.com/benchflow-ai/skillsbench/archive/refs/tags/v1.1.zip"
logger = logging.getLogger("dynsteer")
SWEBENCH_PRO_DATASET = "ScaleAI/SWE-bench_Pro"


def collect_case_reports(data_dir: Path) -> dict[str, JsonObject]:
    """从 AgentCompass 官方 task catalog 导出两个 benchmark 的全量 case 报告。

    入参：
        data_dir: AgentCompass 数据缓存目录。
    输出：
        按 benchmark 名称索引、可 JSON 序列化的 case 与统计报告。
    """
    if data_dir is None:
        raise ValueError("data_dir 不能为空")
    return {
        benchmark: _case_report(benchmark, load_task_records(benchmark, _catalog_config(benchmark, data_dir)))
        for benchmark in ("swebench_pro", "skillsbench")
    }


def main() -> int:
    """执行 CLI 导出并将结果写入 docs 目录。

    输出：
        进程退出码；全部导出成功返回 0，任一失败返回 1。
    """
    parser = argparse.ArgumentParser(description="导出 AgentCompass SWE-bench Pro 与 SkillsBench 全量 case list")
    parser.add_argument(
        "--data-dir",
        default="data/agentcompass",
        help="AgentCompass 数据缓存目录，相对路径按项目根解析",
    )
    args = parser.parse_args()
    logger = configure_logger(PROJECT_ROOT / "logs")
    data_dir = _resolve_project_path(Path(args.data_dir))
    try:
        _ensure_skillsbench_dataset(data_dir)
        _ensure_swebench_pro_dataset(data_dir)
        reports = collect_case_reports(data_dir)
        for benchmark, report in reports.items():
            output_path = OUTPUT_PATHS[benchmark]
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            logger.info(
                "agentcompass_case_export_completed",
                extra={"事件": "Case list导出完成", "benchmark": benchmark, "case_count": report["stats"]["total_case_count"], "output_path": str(output_path)},
            )
    except Exception:
        logger.exception("agentcompass_case_export_failed", extra={"事件": "Case list导出失败", "data_dir": str(data_dir)})
        return 1
    return 0


def _case_report(benchmark: str, records: Mapping[str, AgentCompassTaskRecord]) -> JsonObject:
    """构造单个 benchmark 的 case ID、来源与分层统计。"""
    case_ids = sorted(records)
    category_distribution = _distribution(record.category for record in records.values())
    stratification = BENCHMARK_CONFIGS[benchmark]
    metadata_field = str(stratification["stratification_field"])
    case_values = [
        {
            "case_id": case_id,
            "stratification_value": (
                records[case_id].metadata.get(metadata_field)
                if benchmark == "swebench_pro"
                else records[case_id].category
            ),
        }
        for case_id in case_ids
    ]
    stratification_values = [
        record.metadata.get(metadata_field) if benchmark == "swebench_pro" else record.category
        for record in records.values()
    ]
    distribution = _distribution(str(value) for value in stratification_values if value is not None)
    return {
        "benchmark": benchmark,
        "display_name": "SWE-bench Pro" if benchmark == "swebench_pro" else "SkillsBench",
        "source": "AgentCompass task catalog",
        "case_id_source": stratification["case_id_source"],
        "agentcompass_commit": AGENTCOMPASS_COMMIT,
        "case_ids": case_ids,
        "cases": case_values,
        "stats": {
            "total_case_count": len(records),
            "unique_case_count": len(case_ids),
            "duplicate_case_count": len(records) - len(case_ids),
            "category_distribution": category_distribution,
            "stratification_field": metadata_field,
            "stratification_distribution": distribution,
            "stratification_missing_count": sum(value is None for value in stratification_values),
            "validated_task_field_counts": {
                "task_id": len(records),
                "question": sum(bool(record.question) for record in records.values()),
                "category": sum(bool(record.category) for record in records.values()),
            },
            "metadata_field_counts": {
                key: sum(key in record.metadata for record in records.values())
                for key in ("repo", "base_commit", "requirements", "interface")
            },
        },
    }


def _catalog_config(benchmark: str, data_dir: Path) -> HarnessRunConfig:
    """构造只读取 task catalog 的 HarnessRunConfig。"""
    settings = BENCHMARK_CONFIGS[benchmark]
    return HarnessRunConfig(
        benchmark=benchmark,
        data_root=PROJECT_ROOT / "data" / benchmark,
        metadata={
            "model_id": "task-catalog",
            "agentcompass": {
                "harness": settings["harness"],
                "environment": settings["environment"],
                "model_api_protocol": "openai-chat",
                "data_dir": str(data_dir),
                "benchmark_params": settings["benchmark_params"],
                "auto_install_dependencies": False,
            },
        },
    )


def _distribution(values: Iterable[object]) -> dict[str, int]:
    """将可迭代标量值转换为排序后的计数分布。"""
    return dict(sorted(Counter(values).items()))


def _ensure_swebench_pro_dataset(data_dir: Path) -> None:
    """将 Hugging Face test split 固化为本地 parquet，供 AgentCompass 离线加载。"""
    dataset_dir = data_dir / "swe_bench_pro"
    dataset_path = dataset_dir / "test.parquet"
    if dataset_path.is_file() and dataset_path.stat().st_size > 0:
        return

    dataset_dir.mkdir(parents=True, exist_ok=True)
    logger.info("agentcompass_swebench_pro_download_started", extra={"事件": "SWE-bench Pro数据下载开始", "dataset": SWEBENCH_PRO_DATASET})
    dataset = datasets.load_dataset(SWEBENCH_PRO_DATASET, split="test")
    dataset.to_parquet(str(dataset_path))
    logger.info("agentcompass_swebench_pro_dataset_ready", extra={"事件": "SWE-bench Pro数据准备完成", "dataset_path": str(dataset_path), "case_count": len(dataset)})


def _ensure_skillsbench_dataset(data_dir: Path) -> None:
    """使用标准库准备 SkillsBench 离线数据，规避 Windows 下缺失 wget 的问题。"""
    tasks_dir = data_dir / f"skillsbench-{SKILLSBENCH_VERSION}" / "tasks"
    if tasks_dir.is_dir() and any(tasks_dir.iterdir()):
        return

    # 固定发布版 ZIP 属于受信来源，仍按 data filter 阻断压缩包路径穿越。
    zip_path = data_dir / f"skillsbench-{SKILLSBENCH_VERSION}.zip"
    data_dir.mkdir(parents=True, exist_ok=True)
    if not zip_path.is_file() or zip_path.stat().st_size == 0:
        logger.info("agentcompass_skillsbench_download_started", extra={"事件": "SkillsBench数据下载开始", "url": SKILLSBENCH_DATASET_URL})
        with urllib.request.urlopen(SKILLSBENCH_DATASET_URL, timeout=60) as response, zip_path.open("wb") as target:
            shutil.copyfileobj(response, target)
    root = data_dir.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                logger.warning("agentcompass_skillsbench_symlink_skipped", extra={"事件": "跳过SkillsBench符号链接", "path": info.filename})
                continue
            target = (root / info.filename).resolve()
            if not target.is_relative_to(root):
                raise RuntimeError(f"SkillsBench数据包含越界路径: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
    if not tasks_dir.is_dir() or not any(tasks_dir.iterdir()):
        raise RuntimeError(f"SkillsBench数据解压失败: {tasks_dir}")
    zip_path.unlink()
    logger.info("agentcompass_skillsbench_dataset_ready", extra={"事件": "SkillsBench数据准备完成", "tasks_dir": str(tasks_dir)})


def _resolve_project_path(path: Path) -> Path:
    """把相对 CLI 路径解析到当前项目根。"""
    if path.is_absolute():
        return path.resolve()
    return (PROJECT_ROOT / path).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
