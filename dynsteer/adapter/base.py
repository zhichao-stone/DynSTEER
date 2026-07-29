import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from dynsteer.adapter.utils import ensure_source_root
from dynsteer.evaluate.scoring import GeneralScorer
from dynsteer.harness.model import BenchmarkCase, HarnessAdvanceResult, HarnessRunConfig
from dynsteer.model import JsonObject, TaskCase

@dataclass(frozen=True)
class BenchmarkDefaultResult:
    """benchmark 原生 Default 结果。

    入参：
        score: 原生主分数，已归一到 [0, 1]。
        resolved: benchmark 是否判定为 resolved；无法判断时为 None。
        raw: 原生未压缩摘要。
        metrics: 原生评估统计。
    输出：
        `to_dict()` 返回 JSON 可序列化结构。
    """
    score: float
    resolved: bool | None = None
    raw: JsonObject = field(default_factory=dict)
    metrics: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.score < 0.0 or self.score > 1.0:
            raise ValueError('Default score 必须位于 [0, 1]')

    def to_dict(self) -> JsonObject:
        """转换为 JSON 可序列化字典。"""
        return {'score': self.score, 'resolved': self.resolved, 'raw': dict(self.raw), 'metrics': dict(self.metrics)}

class BaseBenchmarkConstraintScorer(GeneralScorer):
    """benchmark 约束评分器基类。"""

class BaseBenchmarkAdapter(ABC):
    """benchmark 离线数据转换基类。"""
    benchmark: str

    @abstractmethod
    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase:
        """将 benchmark 原生 case 转换为 DynSTEER TaskCase。"""

class BaseBenchmarkHarness(ABC):
    """benchmark 运行期执行接口基类。"""
    benchmark: str
    dependency_error_message: str | None = None

    @abstractmethod
    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        """列出可运行 case。"""

    @abstractmethod
    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> object:
        """初始化 benchmark 原生 session。"""

    @abstractmethod
    def advance_case(self, session: object) -> HarnessAdvanceResult:
        """推进 benchmark session 一个可中断执行批次。"""

    @abstractmethod
    def case_finished(self, session: object) -> bool:
        """判断 benchmark session 是否自然完成。"""

    def constraint_scorer(self) -> BaseBenchmarkConstraintScorer:
        """返回当前 benchmark 的约束评分器。"""
        return BaseBenchmarkConstraintScorer()

    def prepare_config(self, config: HarnessRunConfig) -> None:
        """校验配置并准备 benchmark source_root。"""
        self._validate_config(config)
        ensure_source_root(config.data_root, self._project_root(), self.benchmark)

    def build_run_id(self, config: HarnessRunConfig, case_id: str) -> str:
        """构造安全的运行 ID。"""
        if config is None or case_id is None:
            raise ValueError('config 和 case_id 不能为空')
        raw_run_id = config.metadata.get('run_id')
        if isinstance(raw_run_id, str) and raw_run_id.strip():
            candidate = raw_run_id.strip()
        else:
            candidate = f'{self.benchmark}_{case_id}_run'
        safe = re.sub('[^A-Za-z0-9_.-]+', '_', candidate).strip('._')
        if not safe:
            raise ValueError('run_id 不能为空')
        return safe

    def metrics_from_session(self, session: object) -> JsonObject:
        """从 session 提取运行期 metrics。"""
        if session is None:
            raise ValueError('session 不能为空')
        return {}

    def initial_state_from_session(self, session: object) -> JsonObject | None:
        """从 session 提取当前运行的真实初始状态。"""
        if session is None:
            raise ValueError('session 不能为空')
        return None

    def final_state_from_session(self, session: object) -> JsonObject | None:
        """从 session 提取最终或当前状态。"""
        if session is None:
            raise ValueError('session 不能为空')
        return None

    def raw_summary_from_session(self, session: object) -> JsonObject:
        """提取 benchmark 原生摘要。"""
        if session is None:
            raise ValueError('session 不能为空')
        return {}

    def default_result_from_session(self, session: object) -> BenchmarkDefaultResult:
        """从完整执行后的 session 提取 benchmark 原生 Default 结果。"""
        if session is None:
            raise ValueError('session 不能为空')
        raise NotImplementedError(f'{self.benchmark} 尚未实现 default_result_from_session')

    def stop_case(self, session: object, reason: str) -> None:
        """按 DynSTEER 策略终止当前 benchmark session。"""
        if session is None:
            raise ValueError('session 不能为空')
        if not reason:
            raise ValueError('reason 不能为空')

    def teardown_case(self, session: object) -> None:
        """释放 benchmark 原生 session 资源。"""
        if session is None:
            return

    def _project_root(self) -> Path:
        """返回 DynSTEER 项目根目录。"""
        return Path(__file__).resolve().parents[2]

    def _validate_config(self, config: HarnessRunConfig) -> None:
        """校验共享 harness 运行配置。"""
        if config is None:
            raise ValueError('config 不能为空')
        if config.benchmark.strip().lower() != self.benchmark:
            raise ValueError(f'benchmark 必须是 {self.benchmark}')
        if config.data_root is None:
            raise ValueError('data_root 不能为空')
