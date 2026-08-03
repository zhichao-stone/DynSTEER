import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.evaluate.evaluator import DynSTEEREvaluator
from dynsteer.evaluate.state_summary import apply_runtime_initial_state
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.harness.paths import case_output_dir
from dynsteer.metrics import (
    activate_runtime_metrics_recorder,
    append_execution_timing,
    build_runtime_metrics,
    reset_runtime_metrics_recorder,
)
from dynsteer.model import (
    AgentStepTracker,
    HarnessEvaluationOutput,
    JsonObject,
    RuntimeMetricsRecorder,
    StateSnapshot,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.progress import CaseProgressReporter
from dynsteer.utils import as_number, json_safe, read_json_file
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


def trajectory_output_summary(trajectory: Trajectory, runtime_metrics: JsonObject | None = None) -> JsonObject:
    """构造 trajectory.json 的轻量索引摘要。"""
    metrics = runtime_metrics or {}
    return {
        "path": "trajectory.json",
        "step_count": metrics.get("step_count"),
        "raw_step_count": len(trajectory.steps),
        "snapshot_count": len(trajectory.snapshots),
        "final_state_present": trajectory.final_state is not None,
        "execution_timing_available": bool(metrics.get("execution_timing_available", False)),
        "execution_batch_count": int(metrics.get("execution_batch_count", 0) or 0),
        "unattributed_execution_seconds": float(metrics.get("unattributed_execution_seconds", 0.0) or 0.0),
    }


def existing_case_output(
    config: HarnessRunConfig,
    case_id: str,
    method_fallback: str,
    report_name: str,
) -> HarnessEvaluationOutput | None:
    """如果 case 输出已完整存在，则返回现有输出。"""
    if config is None or not case_id.strip() or not method_fallback.strip() or not report_name.strip():
        raise ValueError("config、case_id、method_fallback 和 report_name 不能为空")
    raw_case_dir = case_output_dir(config.runs_dir, config, case_id, method_fallback)
    result_dir = case_output_dir(config.results_dir, config, case_id, method_fallback)
    raw_summary_path = raw_case_dir / "raw_summary.json"
    trajectory_path = raw_case_dir / "trajectory.json"
    summary_path = result_dir / "summary.json"
    report_path = result_dir / report_name
    if not (
        raw_summary_path.exists()
        and trajectory_path.exists()
        and summary_path.exists()
        and report_path.exists()
    ):
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
        cached = existing_case_output(config, task_case.case_id, "dynsteer_evaluate", "report.json")
        if cached is not None:
            return cached
    harness_result = evaluator.evaluate(harness, config, task_case, progress_reporter=progress_reporter)
    case_id = task_case.case_id
    report = harness_result.evaluation_report
    if report is None:
        raise ValueError("evaluator.evaluate 必须返回 evaluation_report")
    raw_summary = dict(harness_result.raw_summary)
    runtime_metrics = dict(report.runtime_metrics)
    if runtime_metrics and not isinstance(raw_summary.get("runtime_metrics"), dict):
        raw_summary["runtime_metrics"] = runtime_metrics
    trajectory = harness_result.trajectory
    raw_summary["trajectory_output"] = trajectory_output_summary(trajectory, runtime_metrics)
    raw_summary.update(
        {
            "terminated_by_policy": harness_result.termination.should_stop,
            "termination_code": harness_result.termination.termination_code,
            "termination_reason": harness_result.termination.termination_reason,
            "stage_settlements": [settlement.to_dict() for settlement in harness_result.stage_settlements],
        }
    )
    return _write_output_payloads(
        raw_run_dir=case_output_dir(config.runs_dir, config, case_id, "dynsteer_evaluate"),
        result_dir=case_output_dir(config.results_dir, config, case_id, "dynsteer_evaluate"),
        report_name="report.json",
        summary=report.to_summary_dict(),
        report=report.to_dict(),
        raw_summary=raw_summary,
        trajectory=trajectory,
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
        cached = existing_case_output(config, case_id, "default", "default_report.json")
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
        while True:
            advance = harness.timed_advance_case(session)
            append_execution_timing(trajectory, advance)
            trajectory.extend_snapshots(advance.snapshots)
            completed_agent_steps = 0
            for step in advance.steps:
                trajectory.append_step(step)
                closure = tracker.ingest(step)
                if closure is not None:
                    completed_agent_steps += 1
            if progress_reporter is not None and completed_agent_steps > 0:
                progress_reporter.case_advanced(case_id, completed_agent_steps)
            trajectory.final_state = harness.final_state_from_session(session)
            trajectory.metrics = harness.metrics_from_session(session)
            if not advance.continue_running:
                closure = tracker.finalize()
                if closure is not None:
                    completed_agent_steps += 1
                break
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
                "benchmark": config.benchmark,
                "experiment_id": config.metadata.get("experiment_id"),
                "method": str(config.metadata.get("method") or "default"),
                "case_id": case_id,
                "default_result": default_result.to_dict(),
                "runtime_metrics": runtime_metrics,
                "trajectory_output": trajectory_output_summary(trajectory, runtime_metrics),
            }
        )
        summary = {
            "task_id": task_case.task_id,
            "benchmark": config.benchmark,
            "experiment_id": config.metadata.get("experiment_id"),
            "method": str(config.metadata.get("method") or "default"),
            "overall_score": default_result.score,
            "default_score": default_result.score,
            "resolved": default_result.resolved,
            "default_prefix_execution_seconds": None,
            "effective_elapsed_seconds": None,
            "timing_available": None,
            "runtime_metrics": runtime_metrics,
            "metadata": {
                "benchmark": config.benchmark,
                "experiment_id": config.metadata.get("experiment_id"),
                "method": str(config.metadata.get("method") or "default"),
                "model_id": config.metadata.get("model_id"),
                "repeat_index": config.metadata.get("repeat_index"),
                "default_result": default_result.to_dict(),
            },
        }
        report = {
            **summary,
            "trajectory": {
                "task_id": trajectory.task_id,
                "step_count": len(trajectory.steps),
                "snapshot_count": len(trajectory.snapshots),
            },
            "raw": dict(default_result.raw),
        }
        return _write_output_payloads(
            raw_run_dir=raw_run_dir,
            result_dir=result_dir,
            report_name="default_report.json",
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
    default_reference: JsonObject | None = None,
    force_eval: bool = False,
) -> HarnessEvaluationOutput:
    """对完整轨迹执行 replay，并写入结果。"""
    if config is None or evaluator is None or task_case is None or trajectory is None or harness is None:
        raise ValueError("config、evaluator、task_case、trajectory 和 harness 不能为空")
    metadata = dict(config.metadata)
    if default_reference is not None:
        metadata["default_reference"] = dict(default_reference)
        default_score = default_reference.get("score")
        if isinstance(default_score, (int, float)):
            metadata["default_score"] = float(default_score)
    replay_config = replace(config, metadata=metadata)
    if not force_eval:
        cached = existing_case_output(replay_config, task_case.case_id, "dynsteer_replay", "report.json")
        if cached is not None:
            cached_summary = read_json_file(cached.summary_path, f"场景摘要: {cached.summary_path}", dict)
            cached_metrics = cached_summary.get("runtime_metrics")
            # 旧 replay 产物没有 timing 字段时重新生成，使不可用语义也能落盘。
            if isinstance(cached_metrics, dict) and "timing_available" in cached_metrics:
                return cached
    harness_result = evaluator.evaluate_replay(
        task_case=task_case,
        trajectory=trajectory,
        scorer=harness.constraint_scorer(),
        config=replay_config,
    )
    report = harness_result.evaluation_report
    if report is None:
        raise ValueError("evaluate_replay 必须返回 evaluation_report")
    raw_run_dir = case_output_dir(replay_config.runs_dir, replay_config, task_case.case_id, "dynsteer_replay")
    result_dir = case_output_dir(replay_config.results_dir, replay_config, task_case.case_id, "dynsteer_replay")
    raw_summary = dict(harness_result.raw_summary)
    if default_reference is not None:
        raw_summary["default_reference"] = dict(default_reference)
    raw_summary["benchmark"] = config.benchmark
    raw_summary["experiment_id"] = config.metadata.get("experiment_id")
    raw_summary["trajectory_output"] = trajectory_output_summary(harness_result.trajectory, report.runtime_metrics)
    replay_execution = report.metadata.get("replay_execution")
    summary_payload = report.to_summary_dict()
    report_payload = report.to_dict()
    if isinstance(replay_execution, dict):
        raw_summary["replay_execution"] = replay_execution
        summary_payload["replay_execution"] = replay_execution
        report_payload["replay_execution"] = replay_execution
    return _write_output_payloads(
        raw_run_dir=raw_run_dir,
        result_dir=result_dir,
        report_name="report.json",
        summary=summary_payload,
        report=report_payload,
        raw_summary=raw_summary,
        trajectory=harness_result.trajectory,
    )


def _write_output_payloads(
    *,
    raw_run_dir: Path,
    result_dir: Path,
    report_name: str,
    summary: JsonObject,
    report: JsonObject,
    raw_summary: JsonObject,
    trajectory: Trajectory,
) -> HarnessEvaluationOutput:
    """写出通用 case 产物。"""
    report_path = result_dir / report_name
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
    total_step_count = 0
    total_llm_tokens = 0
    total_trajectory_tokens = 0
    elapsed_values: list[float] = []
    default_prefix_values: list[float] = []
    effective_elapsed_values: list[float] = []
    timing_available_case_count = 0
    for output in outputs:
        summary_data = read_json_file(output.summary_path, f"场景摘要: {output.summary_path}", dict)
        coverage = str(summary_data.get("milestone_coverage", "unknown"))
        coverage_counts[coverage] = coverage_counts.get(coverage, 0) + 1
        score_sum += float(summary_data.get("overall_score", 0.0))
        metrics = summary_data.get("runtime_metrics")
        if isinstance(metrics, dict):
            total_step_count += int(metrics.get("step_count", 0) or 0)
            total_llm_tokens += int(metrics.get("llm_total_tokens", 0) or 0)
            total_trajectory_tokens += int(metrics.get("trajectory_total_tokens", 0) or 0)
            elapsed_values.append(float(metrics.get("elapsed_seconds", 0.0) or 0.0))
            if metrics.get("timing_available") is True:
                timing_available_case_count += 1
                prefix = metrics.get("default_prefix_execution_seconds")
                effective = metrics.get("effective_elapsed_seconds")
                if isinstance(prefix, (int, float)):
                    default_prefix_values.append(float(prefix))
                if isinstance(effective, (int, float)):
                    effective_elapsed_values.append(float(effective))
        metadata = summary_data.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        method = str(summary_data.get("method") or metadata.get("method") or method_dir.name)
        benchmark = _summary_benchmark(method_dir, metadata)
        model_id = _summary_model_id(metadata)
        experiment_id = _summary_experiment_id(metadata)
        default_score = _score_from_summary(summary_data, metadata, "default_score")
        dynsteer_score = _score_from_summary(summary_data, metadata, "overall_score")
        case_summary: dict[str, object] = dict(summary_data)
        case_summary.update(
            {
                "case_id": output.result_dir.name,
                "benchmark": benchmark,
                "experiment_id": experiment_id,
                "method": method,
                "model_id": model_id,
                "repeat_index": metadata.get("repeat_index"),
                "default_score": default_score,
                "dynsteer_score": None if method == "default" else dynsteer_score,
                "agent_cost_available": bool(metrics.get("trajectory_cost_available")) if isinstance(metrics, dict) else False,
                "summary_path": output.summary_path.relative_to(method_dir).as_posix(),
                "report_path": output.report_path.relative_to(method_dir).as_posix(),
            }
        )
        cases.append(case_summary)
    case_count = len(cases)
    return {
        "experiment_id": cases[0].get("experiment_id") if cases else _summary_experiment_id({}),
        "benchmark": cases[0].get("benchmark") if cases else _summary_benchmark(method_dir, {}),
        "model_id": cases[0].get("model_id") if cases else _summary_model_id({}),
        "method": method_dir.name,
        "case_count": case_count,
        "average_overall_score": score_sum / case_count if case_count else 0.0,
        "milestone_coverage_counts": coverage_counts,
        "total_step_count": total_step_count,
        "total_llm_tokens": total_llm_tokens,
        "total_trajectory_tokens": total_trajectory_tokens,
        "average_elapsed_seconds": sum(elapsed_values) / len(elapsed_values) if elapsed_values else 0.0,
        "average_default_prefix_execution_seconds": (
            sum(default_prefix_values) / len(default_prefix_values) if default_prefix_values else 0.0
        ),
        "average_effective_elapsed_seconds": (
            sum(effective_elapsed_values) / len(effective_elapsed_values) if effective_elapsed_values else 0.0
        ),
        "timing_available_case_count": timing_available_case_count,
        "cases": cases,
    }


def _summary_benchmark(method_dir: Path, metadata: dict[str, object]) -> str:
    """从 summary 元数据或路径中推断 benchmark。"""
    benchmark = str(metadata.get("benchmark") or "").strip()
    if benchmark:
        return benchmark
    model_id = _summary_model_id(metadata)
    if model_id and method_dir.parent.name == model_id:
        parent = method_dir.parent.parent.name.strip()
        if parent:
            return parent
    if method_dir.parent.name:
        return method_dir.parent.name
    return method_dir.name


def _summary_model_id(metadata: dict[str, object]) -> str | None:
    """从 summary 元数据中提取 model_id。"""
    model_id = str(metadata.get("model_id") or "").strip()
    return model_id or None


def _summary_experiment_id(metadata: dict[str, object]) -> str | None:
    """从 summary 元数据中提取 experiment_id。"""
    experiment_id = str(metadata.get("experiment_id") or "").strip()
    return experiment_id or None


def _score_from_summary(summary_data: dict[str, object], metadata: dict[str, object], key: str) -> float | None:
    """从 summary 或 metadata 中读取分数。"""
    value = as_number(summary_data.get(key))
    if value is not None:
        return value
    value = as_number(metadata.get(key))
    if value is not None:
        return value
    default_result = metadata.get("default_result")
    if key == "default_score" and isinstance(default_result, dict):
        return as_number(default_result.get("score"))
    return None
