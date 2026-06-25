from __future__ import annotations

import importlib
import json
import re
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from dynsteer.harness.model import BenchmarkCase, HarnessAdvanceResult, HarnessRunConfig
from dynsteer.model import JsonObject, StateSnapshot, TaskCase, Trajectory


class BaseBenchmarkAdapter(ABC):
    """benchmark 离线数据转换与 harness 工厂基类。"""

    benchmark: str

    @abstractmethod
    def load_experiment(self, data: JsonObject) -> tuple[TaskCase, Trajectory]:
        """将离线输入字典转换为 DynSTEER 任务和轨迹。

        Args:
            data: benchmark 原始实验字典。

        Returns:
            统一任务与轨迹模型。
        """

    @abstractmethod
    def create_harness(self) -> "BaseBenchmarkHarness":
        """创建当前 benchmark 对应的运行期 harness。

        Returns:
            可执行 benchmark case 的 harness。
        """


class BaseBenchmarkHarness(ABC):
    """benchmark 运行期执行接口基类。

    Harness 只负责 benchmark 原生 session 生命周期、增量步骤采集和原生摘要提取。
    阶段式动态评估、权重更新和 fail-fast 决策由 `DynSTEEREvaluator` 负责。
    """

    benchmark: str
    dependency_error_message: str | None = None

    @abstractmethod
    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        """列出可运行 case。

        Args:
            config: harness 运行配置。

        Returns:
            benchmark case 列表。
        """

    @abstractmethod
    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> object:
        """初始化 benchmark 原生 session。

        Args:
            config: harness 运行配置。
            case_id: benchmark 场景 ID。
            raw_output_dir: 原生输出目录。

        Returns:
            子类私有 session 对象。
        """

    @abstractmethod
    def task_case_from_session(self, session: object) -> TaskCase:
        """从原生 session 提取 DynSTEER 任务定义。

        Args:
            session: 子类私有 session 对象。

        Returns:
            DynSTEER 任务定义，不能为 None。
        """

    @abstractmethod
    def advance_case(self, session: object) -> HarnessAdvanceResult:
        """推进 benchmark session 一个可中断执行批次。

        Args:
            session: 子类私有 session 对象。

        Returns:
            本批次新增步骤与是否继续推进的结构化结果。
        """

    @abstractmethod
    def case_finished(self, session: object) -> bool:
        """判断 benchmark session 是否自然完成。

        该方法保留为公开查询接口或子类内部辅助能力，`DynSTEEREvaluator.evaluate()`
        不使用它作为主循环条件。
        """

    def prepare_config(self, config: HarnessRunConfig) -> None:
        """校验配置并准备 benchmark source_root。

        Args:
            config: harness 运行配置。
        """
        self._validate_config(config)
        self._ensure_source_root(config.data_root)

    def build_run_id(self, config: HarnessRunConfig, case_id: str) -> str:
        """构造安全的运行 ID。

        Args:
            config: harness 运行配置。
            case_id: benchmark 场景 ID。

        Returns:
            可用于目录名的 run_id。
        """
        if config is None or case_id is None:
            raise ValueError("config 和 case_id 不能为空")
        raw_run_id = config.metadata.get("run_id")
        if isinstance(raw_run_id, str) and raw_run_id.strip():
            candidate = raw_run_id.strip()
        else:
            candidate = f"{self.benchmark}_{case_id}_run"
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", candidate).strip("._")
        if not safe:
            raise ValueError("run_id 不能为空")
        return safe

    def snapshots_from_session(self, session: object) -> list[StateSnapshot]:
        """从 session 提取当前可见状态快照。

        Args:
            session: 子类私有 session 对象。

        Returns:
            当前快照列表；默认没有快照。
        """
        if session is None:
            raise ValueError("session 不能为空")
        return []

    def metrics_from_session(self, session: object) -> JsonObject:
        """从 session 提取运行期 metrics。

        Args:
            session: 子类私有 session 对象。

        Returns:
            当前 metrics 字典；默认为空。
        """
        if session is None:
            raise ValueError("session 不能为空")
        return {}

    def final_state_from_session(self, session: object) -> JsonObject | None:
        """从 session 提取最终或当前状态。

        Args:
            session: 子类私有 session 对象。

        Returns:
            当前 final_state 字典；默认为空。
        """
        if session is None:
            raise ValueError("session 不能为空")
        return None

    def raw_summary_from_session(self, session: object) -> JsonObject:
        """提取 benchmark 原生摘要。

        Args:
            session: 子类私有 session 对象。

        Returns:
            原生摘要字典。
        """
        if session is None:
            raise ValueError("session 不能为空")
        return {}

    def stop_case(self, session: object, reason: str) -> None:
        """按 DynSTEER 策略终止当前 benchmark session。

        Args:
            session: 子类私有 session 对象。
            reason: 中文终止原因。
        """
        if session is None:
            raise ValueError("session 不能为空")
        if not reason:
            raise ValueError("reason 不能为空")

    def teardown_case(self, session: object) -> None:
        """释放 benchmark 原生 session 资源。

        Args:
            session: 子类私有 session 对象。
        """
        if session is None:
            return

    def _project_root(self) -> Path:
        """返回 DynSTEER 项目根目录。"""
        return Path(__file__).resolve().parents[2]

    def _validate_config(self, config: HarnessRunConfig) -> None:
        """校验共享 harness 运行配置。"""
        if config is None:
            raise ValueError("config 不能为空")
        if config.benchmark.strip().lower() != self.benchmark:
            raise ValueError(f"benchmark 必须是 {self.benchmark}")
        if config.data_root is None:
            raise ValueError("data_root 不能为空")

    def _load_manifest(self, data_root: Path) -> dict[str, object]:
        """从 data_root 读取 benchmark.json。"""
        if data_root is None:
            raise ValueError("data_root 不能为空")
        manifest_path = data_root / "benchmark.json"
        if not manifest_path.exists():
            return {"benchmark": self.benchmark, "source_root": None}
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"benchmark.json 不是合法 JSON: {manifest_path}") from exc
        if not isinstance(data, dict):
            raise ValueError("benchmark.json 必须是 JSON 对象")
        return data

    def _ensure_source_root(self, data_root: Path) -> None:
        """将 benchmark.json 中的 source_root 加入 sys.path。"""
        manifest = self._load_manifest(data_root)
        raw_source_root = manifest.get("source_root")
        if raw_source_root is None:
            return
        if not isinstance(raw_source_root, str) or not raw_source_root.strip():
            raise ValueError("benchmark.json source_root 必须是非空字符串")
        source_root = Path(raw_source_root.strip())
        if not source_root.is_absolute():
            source_root = self._project_root() / source_root
        if not source_root.exists():
            raise FileNotFoundError(f"{self.benchmark} source_root 不存在: {source_root}")
        source_text = str(source_root.resolve())
        if source_text not in sys.path:
            sys.path.insert(0, source_text)

    def _import_module(self, module_name: str) -> Any:
        """懒加载 benchmark 运行期依赖。"""
        if not isinstance(module_name, str) or not module_name.strip():
            raise ValueError("module_name 不能为空")
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            message = self.dependency_error_message or (
                f"{self.benchmark} harness 需要 benchmark 包及其依赖。"
                "请确认 benchmark.json source_root 可导入，或在当前 uv 环境安装 benchmark。"
            )
            raise ImportError(message) from exc
