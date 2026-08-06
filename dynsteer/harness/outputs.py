import json
import time
from datetime import datetime, timezone
from pathlib import Path

from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.evaluate.evaluator import DynSTEEREvaluator
from dynsteer.evaluate.state_summary import apply_runtime_initial_state
from dynsteer.harness.model import HarnessRunConfig, HarnessRunResult
from dynsteer.harness.paths import case_output_dir
from dynsteer.harness.session import collect_session_trajectory
from dynsteer.metrics import (
    activate_runtime_metrics_recorder,
    build_runtime_metrics,
    reset_runtime_metrics_recorder,
)
from dynsteer.model import (
    AgentStepTracker,
    EvaluationTerminationState,
    HarnessEvaluationOutput,
    JsonObject,
    RuntimeMetricsRecorder,
    StateSnapshot,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.progress import CaseProgressReporter
from dynsteer.utils import json_safe, read_json_file

RESULT_SCHEMA_VERSION = 3

def trajectory_to_json(trajectory: Trajectory) -> JsonObject:
    """把 Trajectory 转成 JSON 对象。"""
    raw_fields = {str(key): json_safe(value) for key, value in trajectory.raw.items()}
    return {
        **raw_fields,
        "task_id": trajectory.task_id,
        "steps": [trajectory_step_to_json(step) for step in trajectory.steps],
        "snapshots": [snapshot_to_json(snapshot) for snapshot in trajectory.snapshots],
        "final_state": json_safe(trajectory.final_state),
        "metrics": json_safe(trajectory.metrics),
    }


def trajectory_step_to_json(step: TrajectoryStep) -> JsonObject:
    """把 TrajectoryStep 转成 JSON 对象。"""
    tool_call: JsonObject | None = None
    if step.tool_call is not None:
        tool_call = {"name": step.tool_call.name, "arguments": dict(step.tool_call.arguments)}
    tool_result: JsonObject | None = None
    if step.tool_result is not None:
        tool_result = {
            "success": step.tool_result.success,
            "content": json_safe(step.tool_result.content),
            "exception": step.tool_result.exception,
        }
    raw_fields = {str(key): json_safe(value) for key, value in step.raw.items()}
    raw_fields.update(
        {
            "step_id": step.step_id,
            "index": step.index,
            "actor": step.actor.value,
            "recipient": step.recipient.value if step.recipient is not None else None,
            "event_type": step.event_type.value,
            "timestamp": step.timestamp,
            "content": step.content,
            "tool_call": tool_call,
            "tool_result": tool_result,
            "state_delta_refs": list(step.state_delta_refs),
            "cost": {"tokens": step.cost.tokens, "latency_ms": step.cost.latency_ms},
        }
    )
    return raw_fields


def snapshot_to_json(snapshot: StateSnapshot) -> JsonObject:
    """把 StateSnapshot 转成 JSON 对象。"""
    return {
        "snapshot_id": snapshot.snapshot_id,
        "after_step_id": snapshot.after_step_id,
        "after_step_index": snapshot.after_step_index,
        "namespaces": json_safe(snapshot.namespaces),
        **{str(key): json_safe(value) for key, value in snapshot.raw.items()},
    }


def trajectory_output_summary(trajectory: Trajectory) -> JsonObject:
    """构造 trajectory.json 的轻量索引摘要。"""
    return {
        "path": "trajectory.json",
        "snapshot_count": len(trajectory.snapshots),
        "final_state_present": trajectory.final_state is not None,
    }


def existing_case_output(
    config: HarnessRunConfig,
    case_id: str,
    method_fallback: str,
) -> HarnessEvaluationOutput | None:
    """如果 case 输出已完整存在，则返回现有输出。"""
    if config is None or not case_id.strip() or not method_fallback.strip():
        raise ValueError("config、case_id 和 method_fallback 不能为空")
    raw_case_dir = case_output_dir(config.runs_dir, config, case_id, method_fallback)
    result_dir = case_output_dir(config.results_dir, config, case_id, method_fallback)
    raw_summary_path = raw_case_dir / "raw_summary.json"
    trajectory_path = raw_case_dir / "trajectory.json"
    summary_path = result_dir / "summary.json"
    report_path = result_dir / "report.json"
    if not (
        raw_summary_path.exists()
        and trajectory_path.exists()
        and summary_path.exists()
        and report_path.exists()
    ):
        return None
    summary = read_json_file(summary_path, f"场景摘要: {summary_path}", dict)
    if summary.get("result_schema_version") != RESULT_SCHEMA_VERSION:
        return None
    return HarnessEvaluationOutput(
        run_dir=result_dir,
        raw_run_dir=raw_case_dir,
        result_dir=result_dir,
        report_path=report_path,
        summary_path=summary_path,
        raw_summary_path=raw_summary_path,
        trajectory_path=trajectory_path,
    )


def write_case_outputs(
    config: HarnessRunConfig,
    harness: BaseBenchmarkHarness,
    evaluator: DynSTEEREvaluator,
    task_case: TaskCase,
    progress_reporter: CaseProgressReporter | None = None,
    force_eval: bool = False,
) -> HarnessEvaluationOutput:
    """执行单个 case，并写入中间产物与最终结果。"""
    if not force_eval:
        cached = existing_case_output(config, task_case.case_id, "dynsteer_evaluate")
        if cached is not None:
            return cached
    harness_result = evaluator.evaluate(harness, config, task_case, progress_reporter=progress_reporter)
    raw_summary, summary_payload, report_payload = _evaluation_payloads(harness_result, config)
    return _write_output_payloads(
        raw_run_dir=case_output_dir(config.runs_dir, config, task_case.case_id, "dynsteer_evaluate"),
        result_dir=case_output_dir(config.results_dir, config, task_case.case_id, "dynsteer_evaluate"),
        summary=summary_payload,
        report=report_payload,
        raw_summary=raw_summary,
        trajectory=harness_result.trajectory,
    )


def write_default_case_outputs(
    config: HarnessRunConfig,
    harness: BaseBenchmarkHarness,
    task_case: TaskCase,
    progress_reporter: CaseProgressReporter | None = None,
    force_eval: bool = False,
) -> HarnessEvaluationOutput:
    """执行原生 benchmark case，并写入 Default 结果。"""
    if config is None or harness is None or task_case is None:
        raise ValueError("config、harness 和 task_case 不能为空")
    case_id = task_case.case_id
    if not force_eval:
        cached = existing_case_output(config, case_id, "default")
        if cached is not None:
            return cached
    harness.prepare_config(config)
    raw_run_dir = case_output_dir(config.runs_dir, config, case_id, "default")
    raw_output_dir = raw_run_dir / "raw"
    result_dir = case_output_dir(config.results_dir, config, case_id, "default")
    raw_output_dir.mkdir(parents=True, exist_ok=True)
    session: object | None = None
    metrics_recorder = RuntimeMetricsRecorder()
    metrics_token = activate_runtime_metrics_recorder(metrics_recorder)
    trajectory = Trajectory(task_id=task_case.task_id, steps=[])
    tracker = AgentStepTracker()
    runtime_initial_state_summary: JsonObject | None = None
    try:
        session = harness.start_case(config, case_id, raw_output_dir)
        runtime_initial_state = harness.initial_state_from_session(session)
        if isinstance(runtime_initial_state, dict):
            runtime_initial_state_summary = apply_runtime_initial_state(
                task_case, runtime_initial_state, "harness_session"
            )
            trajectory.raw["runtime_initial_state"] = runtime_initial_state
        def on_step(step: TrajectoryStep) -> tuple[int, bool]:
            return int(tracker.ingest(step) is not None), False

        def on_finish() -> tuple[int, bool]:
            return int(tracker.finalize() is not None), False

        collect_session_trajectory(
            harness,
            session,
            trajectory,
            on_step,
            on_finish,
            (lambda count: progress_reporter.case_advanced(case_id, count)) if progress_reporter is not None else None,
        )
        default_result = harness.default_result_from_session(session)
        runtime_metrics = build_runtime_metrics(
            started_monotonic=metrics_recorder.started_monotonic,
            finished_monotonic=time.perf_counter(),
            started_at=metrics_recorder.started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            trajectory=trajectory,
            llm_calls=metrics_recorder.llm_calls,
            agent_step_count=tracker.completed_count,
        )
        raw_summary = dict(harness.raw_summary_from_session(session))
        if runtime_initial_state_summary is not None:
            raw_summary["runtime_initial_state_source"] = "harness_session"
            raw_summary["runtime_initial_state_summary"] = runtime_initial_state_summary
        raw_summary.update(
            {
                "result_schema_version": RESULT_SCHEMA_VERSION,
                "benchmark": config.benchmark,
                "experiment_id": config.metadata.get("experiment_id"),
                "method": str(config.metadata.get("method") or "default"),
                "case_id": case_id,
                "runtime_metrics": runtime_metrics,
                "trajectory_output": trajectory_output_summary(trajectory),
            }
        )
        default_raw = default_result.raw
        score_components = {
            key: default_raw[key]
            for key in ("similarity", "milestone_similarity", "minefield_similarity")
            if isinstance(default_raw.get(key), (int, float))
        }
        native_minefield_mapping = default_raw.get("minefield_mapping")
        minefield_match_count = (
            sum(
                1
                for item in native_minefield_mapping.values()
                if isinstance(item, dict) and float(item.get("similarity", 0.0) or 0.0) > 0.0
            )
            if isinstance(native_minefield_mapping, dict)
            else 0
        )
        termination = EvaluationTerminationState(
            termination_code=raw_summary.pop("termination_code", None),
            termination_reason=raw_summary.pop("termination_reason", None),
            termination_detail=raw_summary.pop("termination_detail", {}) or {},
        ).to_dict()
        raw_summary["termination"] = termination
        summary = {
            "result_schema_version": RESULT_SCHEMA_VERSION,
            "task_id": task_case.task_id,
            "score": default_result.score,
            "score_components": score_components,
            "milestone_coverage": None,
            "runtime_metrics": runtime_metrics,
            "termination": termination,
            "minefield_match_count": minefield_match_count,
            "metadata": {
                "benchmark": config.benchmark,
                "experiment_id": config.metadata.get("experiment_id"),
                "method": str(config.metadata.get("method") or "default"),
                "model_id": config.metadata.get("model_id"),
                "repeat_index": config.metadata.get("repeat_index"),
            },
        }
        report = {
            **summary,
            "default_result": default_result.to_dict(),
        }
        return _write_output_payloads(
            raw_run_dir=raw_run_dir,
            result_dir=result_dir,
            summary=summary,
            report=report,
            raw_summary=raw_summary,
            trajectory=trajectory,
        )
    finally:
        try:
            if session is not None:
                harness.teardown_case(session)
        finally:
            reset_runtime_metrics_recorder(metrics_token)


def write_replay_case_outputs(
    config: HarnessRunConfig,
    evaluator: DynSTEEREvaluator,
    task_case: TaskCase,
    trajectory: Trajectory,
    harness: BaseBenchmarkHarness,
    force_eval: bool = False,
) -> HarnessEvaluationOutput:
    """对完整轨迹执行 replay，并写入结果。"""
    if config is None or evaluator is None or task_case is None or trajectory is None or harness is None:
        raise ValueError("config、evaluator、task_case、trajectory 和 harness 不能为空")
    if not force_eval:
        cached = existing_case_output(config, task_case.case_id, "dynsteer_replay")
        if cached is not None:
            return cached
    harness_result = evaluator.evaluate_replay(
        task_case=task_case,
        trajectory=trajectory,
        scorer=harness.constraint_scorer(),
        config=config,
    )
    raw_summary, summary_payload, report_payload = _evaluation_payloads(harness_result, config)
    return _write_output_payloads(
        raw_run_dir=case_output_dir(config.runs_dir, config, task_case.case_id, "dynsteer_replay"),
        result_dir=case_output_dir(config.results_dir, config, task_case.case_id, "dynsteer_replay"),
        summary=summary_payload,
        report=report_payload,
        raw_summary=raw_summary,
        trajectory=harness_result.trajectory,
    )


def _evaluation_payloads(
    harness_result: HarnessRunResult,
    config: HarnessRunConfig,
) -> tuple[JsonObject, JsonObject, JsonObject]:
    """组装 live/replay 共用的 v3 评估产物。"""
    report = harness_result.evaluation_report
    if report is None:
        raise ValueError("评估结果必须包含 evaluation_report")
    termination = harness_result.termination.to_dict()
    raw_summary = {
        **harness_result.raw_summary,
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "benchmark": config.benchmark,
        "experiment_id": config.metadata.get("experiment_id"),
        "method": config.metadata.get("method"),
        "trajectory_output": trajectory_output_summary(harness_result.trajectory),
        "termination": termination,
        "stage_settlements": [item.to_dict() for item in harness_result.stage_settlements],
    }
    summary = {
        **report.to_summary_dict(),
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "score": report.overall_score,
        "score_components": {
            "stage_scores": {stage.stage_id: stage.stage_score for stage in report.stage_reports},
        },
        "minefield_match_count": len(report.minefield_matches),
        "termination": termination,
    }
    summary.pop("overall_score", None)
    full_report = {
        **report.to_dict(),
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "termination": termination,
    }
    return raw_summary, summary, full_report


def _write_output_payloads(
    *,
    raw_run_dir: Path,
    result_dir: Path,
    summary: JsonObject,
    report: JsonObject,
    raw_summary: JsonObject,
    trajectory: Trajectory,
) -> HarnessEvaluationOutput:
    """写出通用 case 产物。"""
    report_path = result_dir / "report.json"
    summary_path = result_dir / "summary.json"
    raw_summary_path = raw_run_dir / "raw_summary.json"
    trajectory_path = raw_run_dir / "trajectory.json"
    raw_run_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(json_safe(report), ensure_ascii=False, indent=4), encoding="utf-8")
    summary_path.write_text(json.dumps(json_safe(summary), ensure_ascii=False, indent=4), encoding="utf-8")
    raw_summary_path.write_text(json.dumps(json_safe(raw_summary), ensure_ascii=False, indent=4), encoding="utf-8")
    trajectory_path.write_text(json.dumps(trajectory_to_json(trajectory), ensure_ascii=False, indent=4), encoding="utf-8")
    return HarnessEvaluationOutput(
        run_dir=result_dir,
        raw_run_dir=raw_run_dir,
        result_dir=result_dir,
        report_path=report_path,
        summary_path=summary_path,
        raw_summary_path=raw_summary_path,
        trajectory_path=trajectory_path,
    )


def write_method_level_summaries(outputs: list[HarnessEvaluationOutput]) -> None:
    """按 benchmark/method 写出方法级汇总。"""
    outputs_by_method_dir: dict[Path, list[HarnessEvaluationOutput]] = {}
    for output in outputs:
        if output is None:
            raise ValueError("outputs 不能包含空值")
        outputs_by_method_dir.setdefault(output.result_dir.parent, []).append(output)
    for method_dir, method_outputs in outputs_by_method_dir.items():
        method_dir.mkdir(parents=True, exist_ok=True)
        (method_dir / "summary.json").write_text(
            json.dumps(_build_method_level_summary(method_dir, method_outputs), ensure_ascii=False, indent=4),
            encoding="utf-8",
        )


def _build_method_level_summary(method_dir: Path, outputs: list[HarnessEvaluationOutput]) -> dict[str, object]:
    """构造单个 benchmark/method 下的汇总摘要。"""
    cases: list[dict[str, object]] = []
    coverage_counts: dict[str, int] = {}
    score_sum = 0.0
    for output in outputs:
        summary_data = read_json_file(output.summary_path, f"场景摘要: {output.summary_path}", dict)
        coverage = str(summary_data.get("milestone_coverage", "unknown"))
        coverage_counts[coverage] = coverage_counts.get(coverage, 0) + 1
        score_sum += float(summary_data.get("score", 0.0))
        metadata = summary_data.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        termination = summary_data.get("termination")
        case_summary: dict[str, object] = {
            "case_id": output.result_dir.name,
            "score": summary_data.get("score"),
            "milestone_coverage": summary_data.get("milestone_coverage"),
            "minefield_match_count": summary_data.get("minefield_match_count"),
            "termination_code": termination.get("code") if isinstance(termination, dict) else None,
            "summary_path": output.summary_path.relative_to(method_dir).as_posix(),
            "report_path": output.report_path.relative_to(method_dir).as_posix(),
        }
        cases.append(case_summary)
    case_count = len(cases)
    return {
        "experiment_id": metadata.get("experiment_id") if outputs else None,
        "benchmark": metadata.get("benchmark") if outputs else None,
        "model_id": metadata.get("model_id") if outputs else None,
        "method": method_dir.name,
        "case_count": case_count,
        "average_overall_score": score_sum / case_count if case_count else 0.0,
        "milestone_coverage_counts": coverage_counts,
        "cases": cases,
}
