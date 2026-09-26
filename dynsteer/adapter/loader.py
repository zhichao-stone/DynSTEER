import json
import os
import re
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

from dynsteer.adapter.base import BaseBenchmarkAdapter
from dynsteer.adapter.route import enrich_milestone_routes
from dynsteer.graph import enrich_milestone_graph
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.llm import build_llm_from_config, build_llm_from_env
from dynsteer.metrics import activate_runtime_metrics_recorder, reset_runtime_metrics_recorder, summarize_llm_calls
from dynsteer.milestone import compile_task_case
from dynsteer.model import (
    Actor,
    Constraint,
    ConstraintTarget,
    Dimension,
    EventType,
    JsonObject,
    Milestone,
    MilestoneGraph,
    Minefield,
    MinefieldPenalty,
    Operator,
    StageEvaluationSpec,
    StateSnapshot,
    StepCost,
    TaskCase,
    TaskType,
    ToolCall,
    ToolResult,
    Trajectory,
    TrajectoryStep,
    RuntimeMetricsRecorder,
    ensure_json_object,
)
from dynsteer.stage import (
    generate_stage_evaluation_specs,
    generate_stage_goal_templates,
    materialize_stage_goals,
    validate_stage_evaluation_specs,
)
from dynsteer.utils import (
    enum_value,
    exclusive_file_lock,
    get_object,
    json_safe,
    normalize_actor,
    parse_int_value,
    read_json_file,
    required_str,
    stable_json_digest,
    unknown_fields,
)


def safe_case_file_name(case_id: str) -> str:
    normalized = re.sub("[^A-Za-z0-9_.-]+", "_", str(case_id)).strip("._")
    if not normalized:
        raise ValueError("case_id 不能转换为空文件名")
    return f"{normalized}.json"

def adapted_case_path(config: HarnessRunConfig, case_id: str) -> Path:
    if config is None or config.data_root is None:
        raise ValueError("config.data_root 不能为空")
    suffix = "" if config.use_milestone_graph else ".no-milestone-graph"
    normalized = re.sub("[^A-Za-z0-9_.-]+", "_", str(case_id)).strip("._")
    return config.data_root / "adapted_cases" / f"{normalized}{suffix}.json"

def load_task_case(config: HarnessRunConfig, adapter: BaseBenchmarkAdapter, force_adapt: bool=False) -> list[TaskCase]:
    """按加载阶段配置读取或重建 adapted TaskCase。

    入参：
        config: 已写入本次加载 case_ids 的 harness 配置。
        adapter: 当前 benchmark 的 TaskCase 适配器。
        force_adapt: 是否忽略已有 adapted case 并统一重建。
    输出：
        与 `config.case_ids` 顺序一致的 TaskCase 列表。
    """
    case_ids = list(config.case_ids or ())
    task_cases: list[TaskCase] = []
    for case_id in tqdm(case_ids, total=len(case_ids), unit="case", desc="加载/适配 benchmark 数据"):
        path = adapted_case_path(config, case_id)
        with exclusive_file_lock(config.data_root / ".adapted_cases.lock"):
            if force_adapt or not path.exists():
                task_case = _adapt_task_case(config, adapter, case_id)
                save_task_case(path, task_case)
            else:
                task_case = _cached_task_case(path, case_id)
        task_cases.append(task_case)
    return task_cases


def _cached_task_case(path: Path, case_id: str) -> TaskCase:
    data = read_json_file(path, f"TaskCase 文件: {path}", dict)
    task_case = parse_task_case(data)
    if task_case.case_id != case_id:
        raise ValueError(f"TaskCase.case_id 与文件对应 case_id 不一致: {case_id}")
    task_case.metadata["adaptation_usage"] = {"generated_now": False, "cache_hit": True}
    return task_case

def save_task_case(path: Path, task_case: TaskCase) -> None:
    if path is None or task_case is None:
        raise ValueError("path 和 task_case 不能为空")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json_safe(task_case)
    if isinstance(data.get("metadata"), dict):
        data["metadata"].pop("adaptation_usage", None)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)

def refresh_task_cases_for_experiment(
    config: HarnessRunConfig,
    adapter: BaseBenchmarkAdapter,
    task_cases: list[TaskCase],
) -> list[TaskCase]:
    """在一次实验执行前刷新所有当前 expected 并实例化 stage goals。"""
    if config is None or adapter is None or task_cases is None:
        raise ValueError("config、adapter 和 task_cases 不能为空")
    refreshed: list[TaskCase] = []
    for task_case in task_cases:
        graph = task_case.milestone_graph
        if graph is not None and graph.metadata.get("source") == "generated":
            current = task_case
        else:
            current = adapter.refresh_task_case_for_experiment(
                config, task_case, task_case.case_id
            )
        current.stage_goals = materialize_stage_goals(current)
        current.stage_evaluation_specs = generate_stage_evaluation_specs(current)
        refreshed.append(current)
    return refreshed

def _adapt_task_case(config: HarnessRunConfig, adapter: BaseBenchmarkAdapter, case_id: str) -> TaskCase:
    recorder = RuntimeMetricsRecorder()
    started = time.perf_counter()
    token = activate_runtime_metrics_recorder(recorder)
    try:
        task_case = adapter.adapt_task_case(config, case_id)
        if not isinstance(task_case, TaskCase):
            raise TypeError("adapter.adapt_task_case 必须返回 TaskCase")
        if task_case.case_id != case_id:
            raise ValueError(f"TaskCase.case_id 与 case_id 不一致: {case_id}")
        if str(config.metadata.get("method") or "").strip().lower() == "default":
            return task_case
        graph = task_case.milestone_graph
        has_origin_graph = graph is not None
        generation = config.milestone_generation
        if not config.use_milestone_graph:
            graph = MilestoneGraph(
                nodes=[],
                edges=[],
                minefields=[],
                metadata={
                    "source": "disabled",
                    "empty_graph_completion_basis": "whole_trajectory",
                },
            )
        elif generation.use_origin_milestone and has_origin_graph:
            graph.metadata["source"] = "origin"
        else:
            view = adapter.generator_task_view(config, task_case, case_id)
            llm = build_llm_from_config(generation.generator)
            if llm is None:
                raise ValueError(f"TaskCase 需要自动生成 milestone，但未配置 generator: {case_id}")
            graph, report = compile_task_case(view, generation, llm)
            if report.generation_status == "generation_failed":
                raise RuntimeError(f"milestone generation failed: {case_id}")
            graph.metadata["source"] = "generated"
            task_case.metadata["milestone_generation"] = report.to_dict()
        task_case.milestone_graph = (
            graph if graph.topology is not None else enrich_milestone_graph(graph)
        )
        task_case = _postprocess_task_case(task_case, str(config.metadata.get("stage_goal_generation", "auto")))
        task_case.metadata["generation_phase"] = "pre_execution"
        summary = summarize_llm_calls(recorder.llm_calls)
        canonical = json_safe(task_case)
        if isinstance(canonical.get("metadata"), dict):
            canonical["metadata"].pop("adaptation_cost", None)
            canonical["metadata"].pop("adaptation_usage", None)
        artifact_id = stable_json_digest(canonical)
        task_case.metadata["adaptation_cost"] = {
            "artifact_id": artifact_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": max(time.perf_counter() - started, 0.0), **summary,
        }
        task_case.metadata["adaptation_usage"] = {"generated_now": True, "cache_hit": False}
        return task_case
    finally:
        reset_runtime_metrics_recorder(token)

def _postprocess_task_case(task_case: TaskCase, goal_mode: str="auto") -> TaskCase:
    """对 TaskCase 执行通用适配后处理。"""
    if task_case.milestone_graph is not None:
        task_case.milestone_graph = enrich_milestone_routes(task_case.milestone_graph)
    if not task_case.stage_goal_templates:
        generation_mode = "auto" if goal_mode == "stored" else goal_mode
        task_case.stage_goal_templates = generate_stage_goal_templates(
            task_case,
            generation_mode,
            llm_provider=build_llm_from_env,
        )
    task_case.stage_goals = materialize_stage_goals(task_case)
    task_case.stage_evaluation_specs = generate_stage_evaluation_specs(task_case)
    return task_case

def _optional_object(data: JsonObject, key: str) -> JsonObject:
    value = data.get(key)
    return value if isinstance(value, dict) else {}

def _optional_json_object(data: JsonObject, key: str) -> JsonObject | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{key} 必须是 JSON 对象")
    return value

def parse_constraint(data: JsonObject) -> Constraint:
    constraint_data = ensure_json_object(data)
    expected = constraint_data.get("expected")
    expected_template = constraint_data.get("expected_template")
    if expected is not None and expected_template is not None:
        raise ValueError("Constraint.expected 与 expected_template 不能同时非空")
    return Constraint(constraint_id=required_str(constraint_data, "constraint_id", "Constraint"), target=enum_value(ConstraintTarget, constraint_data.get("target"), "target"), selector=required_str(constraint_data, "selector", "Constraint"), operator=enum_value(Operator, constraint_data.get("operator"), "operator"), expected=expected, expected_template=expected_template, namespace=constraint_data.get("namespace"), reference_milestone_id=constraint_data.get("reference_milestone_id"), weight=float(constraint_data.get("weight", 1.0)), threshold=float(constraint_data.get("threshold", 1.0)), hard=bool(constraint_data.get("hard", False)), evaluator_hint=str(constraint_data.get("evaluator_hint", "rule")), stage_goal_semantics=constraint_data.get("stage_goal_semantics"), metadata=_optional_object(constraint_data, "metadata"))

def parse_milestone(data: JsonObject) -> Milestone:
    milestone_data = ensure_json_object(data)
    return Milestone(milestone_id=required_str(milestone_data, "milestone_id", "Milestone"), name=required_str(milestone_data, "name", "Milestone"), description=required_str(milestone_data, "description", "Milestone"), constraints=[parse_constraint(ensure_json_object(item)) for item in milestone_data.get("constraints", [])], pass_threshold=float(milestone_data["pass_threshold"]) if milestone_data.get("pass_threshold") is not None else None, metadata=_optional_object(milestone_data, "metadata"))

def parse_minefield(data: JsonObject) -> Minefield:
    minefield_data = ensure_json_object(data)
    penalty_data = ensure_json_object(minefield_data.get("penalty", {"mode": "fixed", "value": 0.0}))
    return Minefield(
        minefield_id=required_str(minefield_data, "minefield_id", "Minefield"), 
        name=required_str(minefield_data, "name", "Minefield"), 
        description=required_str(minefield_data, "description", "Minefield"), 
        severity=str(minefield_data.get("severity", "warning")), 
        constraints=[parse_constraint(ensure_json_object(item)) for item in minefield_data.get("constraints", [])], 
        penalty=MinefieldPenalty(**{k: penalty_data[k] for k in ("mode", "value") if k in penalty_data}),
        metadata=_optional_object(minefield_data, "metadata")
    )

def parse_milestone_graph(data: JsonObject) -> MilestoneGraph:
    graph_data = ensure_json_object(data)
    node_values = graph_data.get("nodes", [])
    edge_values = graph_data.get("edges", [])
    minefield_values = graph_data.get("minefields", [])
    graph = MilestoneGraph(
        nodes=[parse_milestone(ensure_json_object(item)) for item in node_values],
        edges=[(str(source), str(target)) for source, target in edge_values],
        minefields=[
            parse_minefield(ensure_json_object(item)) for item in minefield_values
        ],
        metadata=_optional_object(graph_data, "metadata"),
    )
    enrich_milestone_graph(graph)
    graph = enrich_milestone_routes(graph)
    return graph

def parse_stage_evaluation_spec(data: JsonObject) -> StageEvaluationSpec:
    spec_data = ensure_json_object(data)
    raw_dimensions = spec_data.get("focus_dimensions", [])
    raw_rationale = spec_data.get("dimension_rationale", {})
    if not isinstance(raw_dimensions, list):
        raise ValueError("stage_evaluation_specs.focus_dimensions 必须是数组")
    if not isinstance(raw_rationale, dict):
        raise ValueError("stage_evaluation_specs.dimension_rationale 必须是对象")
    return StageEvaluationSpec(
        focus_dimensions=[
            enum_value(Dimension, item, "focus_dimensions") for item in raw_dimensions
        ],
        dimension_rationale={
            enum_value(Dimension, key, "dimension_rationale"): str(value)
            for key, value in raw_rationale.items()
        },
    )

def parse_task_case(data: JsonObject) -> TaskCase:
    task_data = ensure_json_object(data)
    milestone_graph = None
    if task_data.get("milestone_graph") is not None:
        milestone_graph = parse_milestone_graph(ensure_json_object(task_data["milestone_graph"]))
    task_case = TaskCase(
        task_id=required_str(task_data, "task_id", "TaskCase"),
        task_description=required_str(task_data, "task_description", "TaskCase"),
        case_id=required_str(task_data, "case_id", "TaskCase"),
        environment_schema=_optional_object(task_data, "environment_schema"),
        tool_schema=_optional_object(task_data, "tool_schema"),
        initial_state=task_data.get("initial_state"),
        milestone_graph=milestone_graph,
        stage_goal_templates={str(key): str(value) for key, value in task_data.get("stage_goal_templates", {}).items()},
        stage_goals={str(key): str(value) for key, value in task_data.get("stage_goals", {}).items()},
        stage_evaluation_specs={
            str(key): parse_stage_evaluation_spec(ensure_json_object(value))
            for key, value in task_data.get("stage_evaluation_specs", {}).items()
        },
        task_types=[
            enum_value(TaskType, item, "task_types") for item in task_data.get("task_types", [])
        ],
        metadata=_optional_object(task_data, "metadata"),
    )
    if task_case.milestone_graph is not None and task_case.stage_evaluation_specs:
        validate_stage_evaluation_specs(task_case.milestone_graph, task_case.stage_goals, task_case.stage_evaluation_specs)
    return task_case


def _load_step(data: JsonObject) -> TrajectoryStep:
    known = {"step_id", "index", "actor", "recipient", "event_type", "timestamp", "content", "tool_call", "tool_result", "state_delta_refs", "cost"}
    refs = get_object(data, "state_delta_refs", list, default=[], required=False)
    index = get_object(data, "index", int)
    tool_call_data = _optional_json_object(data, "tool_call")
    tool_result_data = _optional_json_object(data, "tool_result")
    tool_call = ToolCall(name=required_str(tool_call_data, "name", "tool_call"), arguments=_optional_object(tool_call_data, "arguments")) if tool_call_data is not None else None
    tool_result = ToolResult(success=bool(tool_result_data.get("success", False)), content=tool_result_data.get("content"), exception=tool_result_data.get("exception") if isinstance(tool_result_data.get("exception"), str) else None) if tool_result_data is not None else None
    cost_data = _optional_json_object(data, "cost")
    cost = StepCost()
    if cost_data is not None:
        cost = StepCost(
            tokens=parse_int_value(cost_data.get("tokens"), "tokens", default=None),
            latency_ms=parse_int_value(cost_data.get("latency_ms"), "latency_ms", default=None),
        )
    return TrajectoryStep(step_id=required_str(data, "step_id", "TrajectoryStep"), index=index, actor=normalize_actor(data.get("actor"), "actor", required=True) or Actor.EVALUATOR, event_type=enum_value(EventType, data.get("event_type"), "event_type"), recipient=normalize_actor(data.get("recipient"), "recipient", required=False), timestamp=data.get("timestamp"), content=data.get("content"), tool_call=tool_call, tool_result=tool_result, state_delta_refs=[str(item) for item in refs], cost=cost, raw=unknown_fields(data, known))

def _load_snapshot(data: JsonObject) -> StateSnapshot:
    return StateSnapshot(
        snapshot_id=required_str(data, "snapshot_id", "StateSnapshot"),
        after_step_id=required_str(data, "after_step_id", "StateSnapshot"),
        after_step_index=get_object(data, "after_step_index", int),
        namespaces=get_object(data, "namespaces", dict, default={}, required=False),
        raw=unknown_fields(
            data,
            {"snapshot_id", "after_step_id", "after_step_index", "namespaces"},
        ),
    )

def load_trajectory(data: JsonObject) -> Trajectory:
    trajectory_data = ensure_json_object(data)
    step_values = get_object(trajectory_data, "steps", list)
    snapshot_values = get_object(trajectory_data, "snapshots", list, default=[], required=False)
    final_state = _optional_json_object(trajectory_data, "final_state")
    metrics = _optional_json_object(trajectory_data, "metrics") or {}
    known = {"task_id", "steps", "snapshots", "final_state", "metrics"}
    return Trajectory(
        task_id=required_str(trajectory_data, "task_id", "Trajectory"),
        steps=[_load_step(ensure_json_object(item)) for item in step_values],
        snapshots=[_load_snapshot(ensure_json_object(item)) for item in snapshot_values],
        final_state=final_state,
        metrics=metrics,
        raw=unknown_fields(trajectory_data, known),
    )
