from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from dynsteer.model import JsonObject, TaskCase, Trajectory


@dataclass(frozen=True)
class HarnessRunConfig:
    """benchmark harness 单次运行配置。

    Args:
        benchmark: benchmark 名称，例如 toolsandbox。
        data_root: benchmark 静态配置与 manifest 目录。
        scenario: 可选场景 ID。
        agent: 可选 agent 名称。
        user: 可选 user simulator 名称。
        output_dir: 运行产物输出目录。
        pretty: 是否格式化 JSON 输出。
        metadata: 额外运行配置。
    """

    benchmark: str
    data_root: Path
    scenario: str | None = None
    agent: str | None = None
    user: str | None = None
    output_dir: Path = Path("runs")
    pretty: bool = False
    metadata: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.benchmark or not self.benchmark.strip():
            raise ValueError("benchmark 不能为空")
        if self.data_root is None:
            raise ValueError("data_root 不能为空")
        if self.output_dir is None:
            raise ValueError("output_dir 不能为空")


@dataclass(frozen=True)
class BenchmarkCase:
    """benchmark 中的单个测试任务描述。

    Args:
        benchmark: benchmark 名称。
        case_id: benchmark 内唯一场景 ID。
        categories: 场景类别。
        metadata: 原始 benchmark 元数据。
    """

    benchmark: str
    case_id: str
    categories: list[str] = field(default_factory=list)
    metadata: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.benchmark or not self.benchmark.strip():
            raise ValueError("benchmark 不能为空")
        if not self.case_id or not self.case_id.strip():
            raise ValueError("case_id 不能为空")

    def to_dict(self) -> JsonObject:
        """转换为 JSON 可序列化字典。

        Returns:
            可写入 JSON 的 benchmark case 字典。
        """
        return {
            "benchmark": self.benchmark,
            "case_id": self.case_id,
            "categories": list(self.categories),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class HarnessRunResult:
    """benchmark harness 运行结果。

    Args:
        benchmark: benchmark 名称。
        case_id: 场景 ID。
        run_id: 本次运行 ID。
        task_case: 转换后的 DynSTEER 任务。
        trajectory: 转换后的 DynSTEER 轨迹。
        raw_output_dir: 原生 benchmark 输出目录。
        raw_summary: 原生 benchmark 摘要。
    """

    benchmark: str
    case_id: str
    run_id: str
    task_case: TaskCase | None
    trajectory: Trajectory | None
    raw_output_dir: Path
    raw_summary: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.benchmark or not self.benchmark.strip():
            raise ValueError("benchmark 不能为空")
        if not self.case_id or not self.case_id.strip():
            raise ValueError("case_id 不能为空")
        if not self.run_id or not self.run_id.strip():
            raise ValueError("run_id 不能为空")
        if self.task_case is None or self.trajectory is None:
            raise ValueError("task_case 和 trajectory 不能为空")
        if self.raw_output_dir is None:
            raise ValueError("raw_output_dir 不能为空")


class BenchmarkHarness(Protocol):
    """benchmark harness 适配器协议。"""

    benchmark: str

    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        """列出当前 benchmark 可运行的测试任务。

        Args:
            config: harness 运行配置。

        Returns:
            benchmark case 列表。
        """

    def run_case(self, config: HarnessRunConfig, case_id: str) -> HarnessRunResult:
        """运行一个测试任务并转换为 DynSTEER 数据结构。

        Args:
            config: harness 运行配置。
            case_id: benchmark 场景 ID。

        Returns:
            benchmark 运行结果。
        """
