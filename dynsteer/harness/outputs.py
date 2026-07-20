import json
from pathlib import Path

from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.evaluate.evaluator import DynSTEEREvaluator
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import HarnessEvaluationOutput, JsonObject, StateSnapshot, TaskCase, Trajectory, TrajectoryStep
from dynsteer.progress import CaseProgressReporter
from dynsteer.utils import json_safe


def trajectory_to_json(trajectory: Trajectory) -> JsonObject:
    """将 Trajectory 转换为 JSON 对象。"""
    return {
        "run_id": trajectory.run_id,
        "task_id": trajectory.task_id,
        "steps": [trajectory_step_to_json(step) for step in trajectory.steps],
        "snapshots": [snapshot_to_json(snapshot) for snapshot in trajectory.snapshots],
        "final_state": json_safe(trajectory.final_state),
        "metrics": json_safe(trajectory.metrics),
    }


def trajectory_step_to_json(step: TrajectoryStep) -> JsonObject:
    """将 TrajectoryStep 转换为 JSON 对象。"""
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
    """将 StateSnapshot 转换为 JSON 对象。"""
    return {
        "snapshot_id": snapshot.snapshot_id,
        "after_step_id": snapshot.after_step_id,
        "after_step_index": snapshot.after_step_index,
        "namespaces": json_safe(snapshot.namespaces),
        **{str(key): json_safe(value) for key, value in snapshot.raw.items()},
    }


def write_case_outputs(
    config: HarnessRunConfig,
    harness: BaseBenchmarkHarness,
    evaluator: DynSTEEREvaluator,
    task_case: TaskCase,
    progress_reporter: CaseProgressReporter | None = None,
) -> HarnessEvaluationOutput:
    """执行单个 case 并分别写入中间产物和最终结果。"""
    harness_result = evaluator.evaluate(harness, config, task_case, progress_reporter=progress_reporter)
    case_id = task_case.case_id
    report = harness_result.evaluation_report
    if report is None:
        raise ValueError("evaluator.evaluate 必须返回 evaluation_report")

    raw_run_dir = config.runs_dir / config.benchmark / harness_result.run_id / case_id
    result_dir = config.results_dir / config.benchmark / harness_result.run_id / case_id
    report_path = result_dir / "report.json"
    summary_path = result_dir / "summary.json"
    raw_summary_path = raw_run_dir / "raw_summary.json"
    trajectory_path = raw_run_dir / "trajectory.json"
    raw_summary = dict(harness_result.raw_summary)
    runtime_metrics = dict(report.runtime_metrics)
    if runtime_metrics and not isinstance(raw_summary.get("runtime_metrics"), dict):
        raw_summary["runtime_metrics"] = runtime_metrics
    trajectory = harness_result.trajectory
    raw_summary["trajectory_output"] = {
        "path": "trajectory.json",
        "step_count": runtime_metrics.get("step_count"),
        "raw_step_count": len(trajectory.steps),
        "snapshot_count": len(trajectory.snapshots),
        "final_state_present": trajectory.final_state is not None,
    }
    raw_summary.update(
        {
            "terminated_by_policy": harness_result.termination.should_stop,
            "termination_code": harness_result.termination.termination_code,
            "termination_reason": harness_result.termination.termination_reason,
            "stage_settlements": [settlement.to_dict() for settlement in harness_result.stage_settlements],
        }
    )
    raw_run_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=4), encoding="utf-8")
    summary_path.write_text(json.dumps(report.to_summary_dict(), ensure_ascii=False, indent=4), encoding="utf-8")
    trajectory_path.write_text(json.dumps(trajectory_to_json(trajectory), ensure_ascii=False, indent=4), encoding="utf-8")
    raw_summary_path.write_text(json.dumps(raw_summary, ensure_ascii=False, indent=4), encoding="utf-8")
    return HarnessEvaluationOutput(
        run_dir=result_dir,
        raw_run_dir=raw_run_dir,
        result_dir=result_dir,
        report_path=report_path,
        summary_path=summary_path,
        raw_summary_path=raw_summary_path,
        trajectory_path=trajectory_path,
    )


def write_run_level_summaries(outputs: list[HarnessEvaluationOutput]) -> None:
    """按 run 目录写出所有场景的汇总摘要。"""
    outputs_by_run_dir: dict[Path, list[HarnessEvaluationOutput]] = {}
    for output in outputs:
        if output is None:
            raise ValueError("outputs 不能包含空输出")
        outputs_by_run_dir.setdefault(output.result_dir.parent, []).append(output)
    for run_dir, run_outputs in outputs_by_run_dir.items():
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "summary.json").write_text(
            json.dumps(_build_run_level_summary(run_dir, run_outputs), ensure_ascii=False, indent=4), encoding="utf-8"
        )


def _build_run_level_summary(run_dir: Path, outputs: list[HarnessEvaluationOutput]) -> dict[str, object]:
    """构造单个 run_id 下所有场景的汇总摘要。"""
    cases: list[dict[str, object]] = []
    coverage_counts: dict[str, int] = {}
    score_sum = 0.0
    total_step_count = 0
    total_llm_tokens = 0
    total_trajectory_tokens = 0
    elapsed_values: list[float] = []
    for output in outputs:
        summary_data = json.loads(output.summary_path.read_text(encoding="utf-8"))
        if not isinstance(summary_data, dict):
            raise ValueError(f"场景摘要必须是 JSON 对象: {output.summary_path}")
        coverage = str(summary_data.get("milestone_coverage", "unknown"))
        coverage_counts[coverage] = coverage_counts.get(coverage, 0) + 1
        score_sum += float(summary_data.get("overall_score", 0.0))
        metrics = summary_data.get("runtime_metrics")
        if isinstance(metrics, dict):
            total_step_count += int(metrics.get("step_count", 0) or 0)
            total_llm_tokens += int(metrics.get("llm_total_tokens", 0) or 0)
            total_trajectory_tokens += int(metrics.get("trajectory_total_tokens", 0) or 0)
            elapsed_values.append(float(metrics.get("elapsed_seconds", 0.0) or 0.0))
        case_summary: dict[str, object] = dict(summary_data)
        case_summary.update(
            {
                "case_id": output.result_dir.name,
                "summary_path": output.summary_path.relative_to(run_dir).as_posix(),
                "report_path": output.report_path.relative_to(run_dir).as_posix(),
            }
        )
        cases.append(case_summary)
    case_count = len(cases)
    return {
        "benchmark": run_dir.parent.name,
        "run_id": run_dir.name,
        "case_count": case_count,
        "average_overall_score": score_sum / case_count if case_count else 0.0,
        "milestone_coverage_counts": coverage_counts,
        "total_step_count": total_step_count,
        "total_llm_tokens": total_llm_tokens,
        "total_trajectory_tokens": total_trajectory_tokens,
        "average_elapsed_seconds": sum(elapsed_values) / len(elapsed_values) if elapsed_values else 0.0,
        "cases": cases,
    }
