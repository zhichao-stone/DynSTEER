import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from pathlib import Path

from dynsteer.adapter.utils import ensure_source_root
from dynsteer.evaluate.scoring import GeneralScorer
from dynsteer.harness.model import HarnessAdvanceResult, HarnessRunConfig
from dynsteer.milestone.model import GeneratorTaskView
from dynsteer.model import JsonObject, MilestoneGraph, TaskCase


@dataclass(frozen=True)
class BenchmarkDefaultResult:
    """Native benchmark DEFAULT result.

    Args:
        score: Primary score normalized to ``[0, 1]``.
        raw: Native uncompressed summary.
        metrics: Native evaluation statistics.

    Returns:
        ``to_dict()`` returns a JSON-serializable structure.
    """
    score: float | None
    raw: JsonObject = field(default_factory=dict)
    metrics: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.score is not None and (self.score < 0.0 or self.score > 1.0):
            raise ValueError('Default score must be in [0, 1]')

    def to_dict(self) -> JsonObject:
        """Converts to a serialized dictionary for JSON."""
        return {
            "score": self.score,
            "raw": dict(self.raw),
            "metrics": dict(self.metrics),
        }

class BaseBenchmarkAdapter(ABC):
    """Base class for converting benchmark data to DynSTEER task cases."""
    benchmark: str

    @abstractmethod
    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase:
        """Convert the benchmark native case to DynSTEER TaskCase."""

    @abstractmethod
    def generator_task_view(
        self,
        config: HarnessRunConfig,
        task_case: TaskCase,
        case_id: str,
    ) -> GeneratorTaskView:
        """Project public task fields readable by the milestone generator."""

    def reference_milestone_graph(
        self, config: HarnessRunConfig, case_id: str
    ) -> MilestoneGraph | None:
        """Return the complete manual reference graph used only for reliability."""
        return None

    def reliability_tool_aliases(
        self, config: HarnessRunConfig, case_id: str
    ) -> dict[str, str]:
        """Return reference-to-agent tool-name aliases used only for reliability."""
        return {}

    def refresh_task_case_for_experiment(
        self,
        config: HarnessRunConfig,
        task_case: TaskCase,
        case_id: str,
    ) -> TaskCase:
        """Update the dynamic target and stage goal using this experiment source."""
        return task_case

class BaseBenchmarkHarness(ABC):
    """Base class for benchmark runtime harnesses."""
    benchmark: str

    @abstractmethod
    def list_case_ids(self, config: HarnessRunConfig) -> list[str]:
        """List runnable case IDs."""

    @abstractmethod
    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> object:
        """Initialize the benchmark native session."""

    @abstractmethod
    def advance_case(self, session: object) -> HarnessAdvanceResult:
        """Advance the benchmark session by one interruptible execution batch."""

    def timed_advance_case(self, session: object) -> HarnessAdvanceResult:
        """Advance one batch and record batch-level wall-clock latency in milliseconds.

        Args:
            session: the current benchmark session.
        Returns:
            The batch result with ``execution_latency_ms``; harness exceptions propagate unchanged.
        """
        started = time.perf_counter()
        try:
            advance = self.advance_case(session)
        finally:
            finished = time.perf_counter()
        latency_ms = max(0, int(round((finished - started) * 1000)))
        return replace(advance, execution_latency_ms=latency_ms)

    def constraint_scorer(self) -> GeneralScorer:
        """Return the constraint scorer bound to this benchmark."""
        return GeneralScorer()

    def prepare_config(self, config: HarnessRunConfig) -> None:
        """Verify configuration and prepare benchmark source_root."""
        self._validate_config(config)
        ensure_source_root(config.data_root, Path(__file__).resolve().parents[2], self.benchmark)

    def metrics_from_session(self, session: object) -> JsonObject:
        """Extract native evaluation metrics from the session."""
        return {}

    def initial_state_from_session(self, session: object) -> JsonObject | None:
        """Extract the true initial execution state from the session."""
        return None

    def final_state_from_session(self, session: object) -> JsonObject | None:
        """Extract the final or current state from the session."""
        return None

    def raw_summary_from_session(self, session: object) -> JsonObject:
        """Extract the native benchmark summary."""
        return {}

    @abstractmethod
    def default_result_from_session(self, session: object) -> BenchmarkDefaultResult:
        """Extract the native DEFAULT result from the completed execution session."""

    def stop_case(self, session: object, reason: str) -> None:
        """Terminate the current benchmark session using the DynSTEER policy."""
        if session is None:
            raise ValueError('Session cannot be empty.')
        if not reason:
            raise ValueError("reason cannot be empty.")

    def send_guidance(self, session: object, message: str) -> None:
        """Write an execution-time guidance message to a benchmark that supports intervention."""
        if session is None:
            raise ValueError('Session cannot be empty.')
        if not message.strip():
            raise ValueError('Message cannot be empty.')
        raise NotImplementedError('This benchmark does not support execution-time guidance')

    def teardown_case(self, session: object) -> None:
        """Release native benchmark session resources."""
        if session is None:
            return

    def _validate_config(self, config: HarnessRunConfig) -> None:
        """Validate the shared harness run configuration."""
        if config.benchmark.strip().lower() != self.benchmark:
            raise ValueError(f"Benchmark must be {self.benchmark}")
