from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import replace
from pathlib import Path

from dynsteer.boundary import generate_candidate_boundaries
from dynsteer.config import DynamicWeightConfig, ThresholdConfig
from dynsteer.evaluate import evaluate_minefields, update_weights
from dynsteer.harness.model import BenchmarkCase, HarnessRunConfig, HarnessRunResult, HarnessStageSettlement
from dynsteer.judge import Judge, LocalJudge
from dynsteer.model import (
    Boundary,
    Dimension,
    EvaluationLevel,
    JsonObject,
    Milestone,
    MilestoneGraph,
    MilestoneScore,
    StageEvaluationResult,
    StageInterval,
    StageStatus,
    StateSnapshot,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.score import score_milestone
from dynsteer.evaluate import select_initial_weights

logger = logging.getLogger(__name__)


class BaseBenchmarkAdapter(ABC):
    """benchmark 离线数据转换与 harness 工厂基类。"""

    benchmark: str

    # 子类需要继承并 override 的函数

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
    """benchmark 运行期基类，负责 checkpoint、阶段评估与策略终止。"""

    benchmark: str

    def __init__(
        self,
        judge: Judge | None = None,
        thresholds: ThresholdConfig | None = None,
        weight_config: DynamicWeightConfig | None = None,
    ) -> None:
        self._judge = judge or LocalJudge()
        self._thresholds = thresholds or ThresholdConfig()
        self._weight_config = weight_config

    # 子类需要继承并 override 的函数

    @abstractmethod
    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        """列出当前 benchmark 可运行的测试任务。

        Args:
            config: harness 运行配置。

        Returns:
            benchmark case 列表。
        """

    @abstractmethod
    def _start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> object:
        """初始化 benchmark 原生 session。

        Args:
            config: harness 运行配置。
            case_id: benchmark 场景 ID。
            raw_output_dir: 原生输出目录。

        Returns:
            子类私有 session 对象。
        """

    @abstractmethod
    def _task_case_from_session(self, session: object) -> TaskCase:
        """从原生 session 提取 DynSTEER 任务定义。

        Args:
            session: 子类私有 session 对象。

        Returns:
            DynSTEER 任务定义。
        """

    @abstractmethod
    def _advance_case(self, session: object) -> list[TrajectoryStep]:
        """推进 benchmark session 一个可中断执行批次。

        Args:
            session: 子类私有 session 对象。

        Returns:
            本批次新增的 DynSTEER 轨迹步骤。
        """

    @abstractmethod
    def _case_finished(self, session: object) -> bool:
        """判断 benchmark session 是否自然完成。

        Args:
            session: 子类私有 session 对象。

        Returns:
            自然完成时返回 True。
        """

    def _snapshots_from_session(self, session: object) -> list[StateSnapshot]:
        """从 session 提取当前可见状态快照。

        Args:
            session: 子类私有 session 对象。

        Returns:
            当前快照列表；默认没有快照。
        """
        if session is None:
            raise ValueError("session 不能为空")
        return []

    def _metrics_from_session(self, session: object) -> JsonObject:
        """从 session 提取运行期 metrics。

        Args:
            session: 子类私有 session 对象。

        Returns:
            当前 metrics 字典；默认为空。
        """
        if session is None:
            raise ValueError("session 不能为空")
        return {}

    def _final_state_from_session(self, session: object) -> JsonObject | None:
        """从 session 提取最终或当前状态。

        Args:
            session: 子类私有 session 对象。

        Returns:
            当前 final_state 字典；默认为空。
        """
        if session is None:
            raise ValueError("session 不能为空")
        return None

    def _raw_summary_from_session(self, session: object) -> JsonObject:
        """提取 benchmark 原生摘要。

        Args:
            session: 子类私有 session 对象。

        Returns:
            原生摘要字典。
        """
        if session is None:
            raise ValueError("session 不能为空")
        return {}

    def _stop_case(self, session: object, reason: str) -> None:
        """按 DynSTEER 策略终止当前 benchmark session。

        Args:
            session: 子类私有 session 对象。
            reason: 中文终止原因。
        """
        if session is None:
            raise ValueError("session 不能为空")
        if not reason:
            raise ValueError("reason 不能为空")

    def _teardown_case(self, session: object) -> None:
        """释放 benchmark 原生 session 资源。

        Args:
            session: 子类私有 session 对象。
        """
        if session is None:
            return

    # 基类自身实现逻辑、子类不覆盖的函数

    def run_case(self, config: HarnessRunConfig, case_id: str) -> HarnessRunResult:
        """运行一个 benchmark case，并在 milestone checkpoint 处阶段式评估。

        Args:
            config: harness 运行配置。
            case_id: benchmark 场景 ID。

        Returns:
            benchmark 运行结果，包含截断轨迹、阶段结算和策略终止信息。
        """
        if config is None:
            raise ValueError("config 不能为空")
        if case_id is None or not str(case_id).strip():
            raise ValueError("case_id 不能为空")
        if config.benchmark.strip().lower() != self.benchmark:
            raise ValueError(f"benchmark 必须是 {self.benchmark}")
        
        run_id = self._build_run_id(config, case_id)
        raw_output_dir = config.runs_dir / config.benchmark / run_id / case_id / "raw"
        raw_output_dir.mkdir(parents=True, exist_ok=True)
        
        session: object | None = None
        steps: list[TrajectoryStep] = []
        snapshots: list[StateSnapshot] = []
        settlements: list[HarnessStageSettlement] = []
        matched: dict[str, HarnessStageSettlement] = {}
        terminated_by_policy = False
        termination_code: str | None = None
        termination_reason: str | None = None
        task_case: TaskCase | None = None

        try:
            logger.info("harness_case_start", extra={"事件": "启动benchmark任务", "benchmark": self.benchmark, "case_id": case_id})
            session = self._start_case(config, case_id, raw_output_dir)
            task_case = self._task_case_from_session(session)
            if task_case is None:
                raise ValueError("task_case 不能为空")
            settlements.append(self._start_settlement(settlements))
            weights = select_initial_weights(task_case)

            while not self._case_finished(session):
                new_steps = self._advance_case(session)
                if new_steps is None:
                    raise ValueError("_advance_case 不能返回 None")
                if not new_steps:
                    if self._case_finished(session):
                        break
                    raise RuntimeError("benchmark session 未完成但没有新增轨迹步骤，无法继续真实中断执行")

                for step in new_steps:
                    steps.append(step)
                    snapshots = self._merge_snapshots(snapshots, self._snapshots_from_session(session))
                    trajectory = self._build_trajectory(run_id, task_case, steps, snapshots, session)
                    hit = self._find_hit_milestone(task_case, trajectory, step, matched)
                    if hit is None:
                        continue
                    milestone, boundary, milestone_score = hit
                    stage_result, weights = self._append_milestone_settlement(
                        settlements=settlements,
                        matched=matched,
                        task_case=task_case,
                        trajectory=trajectory,
                        milestone=milestone,
                        boundary=boundary,
                        milestone_score=milestone_score,
                        weights=weights,
                    )
                    stop_decision = self._should_stop_after_stage(config, task_case, trajectory, stage_result)
                    if stop_decision is not None:
                        termination_code, termination_reason = stop_decision
                        terminated_by_policy = True
                        logger.warning(
                            "harness_policy_stop",
                            extra={"事件": "策略提前终止", "case_id": case_id, "termination_code": termination_code},
                        )
                        self._stop_case(session, termination_reason)
                        break
                if terminated_by_policy:
                    break

            snapshots = self._merge_snapshots(snapshots, self._snapshots_from_session(session))
            trajectory = self._build_trajectory(run_id, task_case, steps, snapshots, session)
            if not terminated_by_policy:
                settlements.append(self._finish_settlement(settlements, task_case, trajectory, matched, weights))
            raw_summary = self._raw_summary_from_session(session)
            return HarnessRunResult(
                benchmark=self.benchmark,
                case_id=case_id,
                run_id=run_id,
                task_case=task_case,
                trajectory=trajectory,
                raw_output_dir=raw_output_dir,
                raw_summary=raw_summary,
                stage_settlements=settlements,
                terminated_by_policy=terminated_by_policy,
                termination_code=termination_code,
                termination_reason=termination_reason,
            )
        finally:
            if session is not None:
                self._teardown_case(session)

    def _build_run_id(self, config: HarnessRunConfig, case_id: str) -> str:
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

    def _build_trajectory(
        self,
        run_id: str,
        task_case: TaskCase,
        steps: list[TrajectoryStep],
        snapshots: list[StateSnapshot],
        session: object,
    ) -> Trajectory:
        """基于当前增量步骤构造 DynSTEER 轨迹。

        Args:
            run_id: 当前运行 ID。
            task_case: 当前任务定义。
            steps: 已采集步骤。
            snapshots: 已采集快照。
            session: 子类私有 session 对象。

        Returns:
            当前截断轨迹。
        """
        if not run_id or task_case is None or steps is None or snapshots is None or session is None:
            raise ValueError("构造轨迹所需参数不能为空")
        return Trajectory(
            run_id=run_id,
            task_id=task_case.task_id,
            steps=list(steps),
            snapshots=list(snapshots),
            final_state=self._final_state_from_session(session),
            metrics=self._metrics_from_session(session),
        )

    def _merge_snapshots(
        self,
        current: list[StateSnapshot],
        incoming: list[StateSnapshot],
    ) -> list[StateSnapshot]:
        """合并快照，按 snapshot_id 去重。

        Args:
            current: 已有快照。
            incoming: 新快照。

        Returns:
            去重后的快照列表。
        """
        if current is None or incoming is None:
            raise ValueError("快照列表不能为空")
        by_id = {snapshot.snapshot_id: snapshot for snapshot in current}
        for snapshot in incoming:
            by_id[snapshot.snapshot_id] = snapshot
        return sorted(by_id.values(), key=lambda item: (item.after_step_index, item.snapshot_id))

    def _start_settlement(self, settlements: list[HarnessStageSettlement]) -> HarnessStageSettlement:
        """创建 start 结算节点。

        Args:
            settlements: 已有结算列表。

        Returns:
            start 结算节点。
        """
        if settlements is None:
            raise ValueError("settlements 不能为空")
        return HarnessStageSettlement(
            settlement_id=f"st{len(settlements)}",
            kind="start",
            milestone_id=None,
            start_step_index=0,
            end_step_index=0,
            evidence=["start 结算节点"],
        )

    def _finish_settlement(
        self,
        settlements: list[HarnessStageSettlement],
        task_case: TaskCase,
        trajectory: Trajectory,
        matched: dict[str, HarnessStageSettlement],
        weights: dict[Dimension, float],
    ) -> HarnessStageSettlement:
        """创建自然完成时的 finish 结算节点。

        Args:
            settlements: 已有结算列表。
            task_case: 当前任务定义。
            trajectory: 当前完整轨迹。
            matched: 已命中 milestone 到结算节点的映射。
            weights: 当前阶段权重。

        Returns:
            finish 结算节点。
        """
        if settlements is None or task_case is None or trajectory is None or matched is None or weights is None:
            raise ValueError("finish 结算参数不能为空")
        last_step_index = max((step.index for step in trajectory.steps), default=0)
        predecessor_index = max(
            (settlement.end_step_index for settlement in matched.values()),
            default=settlements[0].end_step_index if settlements else 0,
        )
        end_step_index = max(predecessor_index, last_step_index)
        interval = StageInterval(
            stage_id=f"runtime:st{len(settlements)}",
            milestone_id=None,
            start_step_index=predecessor_index,
            end_step_index=end_step_index,
            status=StageStatus.PASS,
            evidence=["finish 结算节点"],
        )
        stage_result = self._evaluate_runtime_stage(interval, task_case, trajectory, EvaluationLevel.CHEAP, weights)
        next_weights = update_weights(weights, stage_result.dimension_scores, stage_result.uncertainty, self._weight_config)
        stage_result = replace(stage_result, next_weights=next_weights)
        return HarnessStageSettlement(
            settlement_id=f"st{len(settlements)}",
            kind="finish",
            milestone_id=None,
            start_step_index=predecessor_index,
            end_step_index=end_step_index,
            score=stage_result.stage_score,
            status=stage_result.status.value,
            evidence=list(stage_result.evidence),
            metadata={
                "stage_report": stage_result.to_dict(),
                "predecessor_milestone_ids": sorted(matched),
                "stage_start_step_index": predecessor_index,
                "stage_end_step_index": end_step_index,
            },
        )

    def _ready_milestones(
        self,
        graph: MilestoneGraph,
        matched: dict[str, HarnessStageSettlement],
    ) -> list[Milestone]:
        """获取直接前驱均已命中的待判定 milestone。

        Args:
            graph: milestone 图。
            matched: 已命中 milestone 映射。

        Returns:
            ready milestone 列表。
        """
        if graph is None or matched is None:
            raise ValueError("graph 和 matched 不能为空")
        matched_ids = set(matched)
        predecessors: dict[str, list[str]] = {node.milestone_id: [] for node in graph.nodes}
        for source, target in graph.edges:
            if target in predecessors:
                predecessors[target].append(source)
        ready = []
        for node in graph.nodes:
            if node.milestone_id in matched_ids:
                continue
            if all(source in matched_ids for source in predecessors.get(node.milestone_id, [])):
                ready.append(node)
        return ready

    def _find_hit_milestone(
        self,
        task_case: TaskCase,
        trajectory: Trajectory,
        step: TrajectoryStep,
        matched: dict[str, HarnessStageSettlement],
    ) -> tuple[Milestone, Boundary, MilestoneScore] | None:
        """判断当前 step 是否命中下一个 ready milestone。

        Args:
            task_case: 当前任务定义。
            trajectory: 当前截断轨迹。
            step: 刚追加的轨迹步骤。
            matched: 已命中 milestone 映射。

        Returns:
            命中时返回 milestone、boundary 和 milestone score；否则返回 None。
        """
        if task_case is None or trajectory is None or step is None or matched is None:
            raise ValueError("milestone 判定参数不能为空")
        graph = task_case.milestone_graph
        if graph is None or not graph.nodes:
            return None
        boundaries = [boundary for boundary in generate_candidate_boundaries(trajectory) if boundary.step_index == step.index]
        if not boundaries:
            return None
        best: tuple[Milestone, Boundary, MilestoneScore] | None = None
        for milestone in self._ready_milestones(graph, matched):
            predecessor_start = self._stage_start_for_milestone(graph, milestone.milestone_id, matched, None)
            for boundary in boundaries:
                if boundary.step_index <= predecessor_start and predecessor_start > 0:
                    continue
                score = score_milestone(milestone, boundary, trajectory, trajectory.snapshots)
                if score.status != StageStatus.PASS:
                    continue
                if best is None or score.score > best[2].score:
                    best = (milestone, boundary, score)
        return best

    def _stage_start_for_milestone(
        self,
        graph: MilestoneGraph,
        milestone_id: str,
        matched: dict[str, HarnessStageSettlement],
        start_settlement: HarnessStageSettlement | None,
    ) -> int:
        """计算 milestone 阶段区间起点。

        Args:
            graph: milestone 图。
            milestone_id: 当前 milestone ID。
            matched: 已命中 milestone 映射。
            start_settlement: start 结算节点。

        Returns:
            直接前驱 milestone 的最大命中 step index；无前驱时返回 start 结算终点。
        """
        if graph is None or not milestone_id or matched is None:
            raise ValueError("阶段起点参数不能为空")
        predecessors = [source for source, target in graph.edges if target == milestone_id]
        matched_predecessor_indexes = [
            matched[source].end_step_index
            for source in predecessors
            if source in matched
        ]
        if matched_predecessor_indexes:
            return max(matched_predecessor_indexes)
        return start_settlement.end_step_index if start_settlement is not None else 0

    def _evaluate_runtime_stage(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        level: EvaluationLevel,
        weights: dict[Dimension, float],
    ) -> StageEvaluationResult:
        """执行运行期阶段评估。

        Args:
            interval: 当前阶段区间。
            task_case: 当前任务定义。
            trajectory: 当前截断轨迹。
            level: 评估粒度。
            weights: 当前维度权重。

        Returns:
            阶段评估结果。
        """
        if interval is None or task_case is None or trajectory is None or level is None or weights is None:
            raise ValueError("阶段评估参数不能为空")
        return self._judge.evaluate_stage(interval, task_case, trajectory, level, weights)

    def _append_milestone_settlement(
        self,
        settlements: list[HarnessStageSettlement],
        matched: dict[str, HarnessStageSettlement],
        task_case: TaskCase,
        trajectory: Trajectory,
        milestone: Milestone,
        boundary: Boundary,
        milestone_score: MilestoneScore,
        weights: dict[Dimension, float],
    ) -> tuple[StageEvaluationResult, dict[Dimension, float]]:
        """追加 milestone checkpoint 结算节点。

        Args:
            settlements: 已有结算列表。
            matched: 已命中 milestone 映射。
            task_case: 当前任务定义。
            trajectory: 当前截断轨迹。
            milestone: 当前命中的 milestone。
            boundary: 命中边界。
            milestone_score: milestone 结构化分数。
            weights: 当前维度权重。

        Returns:
            阶段评估结果和下一阶段权重。
        """
        if (
            settlements is None
            or matched is None
            or task_case is None
            or trajectory is None
            or milestone is None
            or boundary is None
            or milestone_score is None
            or weights is None
        ):
            raise ValueError("milestone 结算参数不能为空")
        graph = task_case.milestone_graph or MilestoneGraph()
        start_settlement = settlements[0] if settlements else None
        start_step_index = self._stage_start_for_milestone(graph, milestone.milestone_id, matched, start_settlement)
        interval = StageInterval(
            stage_id=f"runtime:st{len(settlements)}",
            milestone_id=milestone.milestone_id,
            start_step_index=start_step_index,
            end_step_index=boundary.step_index,
            status=milestone_score.status,
            milestone_score=milestone_score,
            evidence=list(milestone_score.evidence),
        )
        stage_result = self._evaluate_runtime_stage(interval, task_case, trajectory, EvaluationLevel.CHEAP, weights)
        next_weights = update_weights(weights, stage_result.dimension_scores, stage_result.uncertainty, self._weight_config)
        stage_result = replace(stage_result, next_weights=next_weights)
        predecessor_milestone_ids = [source for source, target in graph.edges if target == milestone.milestone_id]
        settlement = HarnessStageSettlement(
            settlement_id=f"st{len(settlements)}",
            kind="milestone",
            milestone_id=milestone.milestone_id,
            start_step_index=start_step_index,
            end_step_index=boundary.step_index,
            boundary_id=boundary.boundary_id,
            boundary_step_index=boundary.step_index,
            score=stage_result.stage_score,
            status=stage_result.status.value,
            checkpointed=True,
            evidence=list(stage_result.evidence),
            metadata={
                "stage_report": stage_result.to_dict(),
                "predecessor_milestone_ids": predecessor_milestone_ids,
                "stage_start_step_index": start_step_index,
                "stage_end_step_index": boundary.step_index,
            },
        )
        settlements.append(settlement)
        matched[milestone.milestone_id] = settlement
        logger.info(
            "harness_milestone_checkpoint",
            extra={
                "事件": "命中milestone并执行阶段评估",
                "milestone_id": milestone.milestone_id,
                "step_index": boundary.step_index,
                "status": stage_result.status.value,
            },
        )
        return stage_result, next_weights

    def _should_stop_after_stage(
        self,
        config: HarnessRunConfig,
        task_case: TaskCase,
        trajectory: Trajectory,
        stage_result: StageEvaluationResult,
    ) -> tuple[str, str] | None:
        """判断阶段评估后是否需要 fail-fast 终止。

        Args:
            config: harness 运行配置。
            task_case: 当前任务定义。
            trajectory: 当前截断轨迹。
            stage_result: 当前阶段评估结果。

        Returns:
            需要终止时返回终止代码和中文原因，否则返回 None。
        """
        if config is None or task_case is None or trajectory is None or stage_result is None:
            raise ValueError("终止策略参数不能为空")
        graph = task_case.milestone_graph or MilestoneGraph()
        if config.stop_on_minefield:
            matches, max_score, fatal = evaluate_minefields(graph, trajectory)
            if matches and fatal:
                minefield_id = str(matches[0].get("minefield_id", "minefield"))
                return (
                    f"minefield:{minefield_id}",
                    f"触发 fatal minefield，提前终止执行：{minefield_id}",
                )
            if matches and max_score >= self._thresholds.fatal_minefield_threshold:
                return (
                    f"minefield_score:{max_score:.3f}",
                    f"minefield 分数 {max_score:.3f} 达到停止阈值，提前终止执行",
                )
        if config.stop_on_stage_failure:
            milestone_id = stage_result.milestone_id or "unknown"
            if stage_result.status in {StageStatus.FAIL, StageStatus.MISSING, StageStatus.INVALID}:
                return (
                    f"stage_failure:{milestone_id}",
                    f"阶段评估状态为 {stage_result.status.value}，提前终止执行：{milestone_id}",
                )
            if stage_result.stage_score < self._thresholds.fail_threshold:
                return (
                    f"stage_score:{milestone_id}",
                    f"阶段评估分数 {stage_result.stage_score:.3f} 低于失败阈值，提前终止执行：{milestone_id}",
                )
        return None


BenchmarkAdapter = BaseBenchmarkAdapter
BenchmarkHarness = BaseBenchmarkHarness
