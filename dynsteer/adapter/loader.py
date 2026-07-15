from __future__ import annotations

import json
import re
from pathlib import Path

from tqdm import tqdm

from dynsteer.adapter.base import BaseBenchmarkAdapter
from dynsteer.graph import enrich_milestone_graph
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.llm import build_llm_from_env
from dynsteer.model import (
    Actor,
    Constraint,
    ConstraintTarget,
    EventType,
    JsonObject,
    Milestone,
    MilestoneGraph,
    Minefield,
    MinefieldPenalty,
    Operator,
    StateSnapshot,
    StepCost,
    TaskCase,
    TaskType,
    ToolCall,
    ToolResult,
    Trajectory,
    TrajectoryStep,
    ensure_json_object,
)
from dynsteer.stage import generate_stage_goals
from dynsteer.utils import enum_value, get_object, json_safe, unknown_fields


def safe_case_file_name(case_id: str) -> str:
    if case_id is None:
        raise ValueError("case_id 不能为空")
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(case_id)).strip("._")
    if not normalized:
        raise ValueError("case_id 不能转换为空文件名")
    return f"{normalized}.json"


def adapted_case_path(data_root: Path, case_id: str) -> Path:
    # 这里保留有路径上下文的 None 校验，便于定位配置缺失。
    if data_root is None:
        raise ValueError("data_root 不能为空")
    return data_root / "adapted_cases" / safe_case_file_name(case_id)


def load_task_case(
    config: HarnessRunConfig,
    adapter: BaseBenchmarkAdapter,
    force_adapt: bool = False,
) -> list[TaskCase]:
    if config is None or adapter is None:
        raise ValueError("config 和 adapter 不能为空")
    case_ids = list(config.case_ids or ())
    if not case_ids:
        raise ValueError("config.case_ids 不能为空")

    task_cases: list[TaskCase] = []
    for case_id in tqdm(case_ids, total=len(case_ids), unit="case", desc="加载/适配 benchmark 数据"):
        path = adapted_case_path(config.data_root, case_id)
        if force_adapt or not path.exists():
            task_case = _adapt_task_case(config, adapter, case_id)
            save_task_case(path, task_case)
        else:
            ### load task case from file
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"TaskCase 文件不是合法 JSON: {path}") from exc
            if not isinstance(data, dict):
                raise ValueError(f"TaskCase 文件必须是 JSON 对象: {path}")
            task_case = parse_task_case(ensure_json_object(data))
            if task_case.case_id != case_id:
                raise ValueError(f"TaskCase.case_id 与文件对应 case_id 不一致: {case_id}")

            ### adapt task case if milestone_graph is None
            if task_case.milestone_graph is None:
                task_case = _adapt_task_case(config, adapter, case_id)
                save_task_case(path, task_case)
        task_cases.append(task_case)
    return task_cases


def save_task_case(path: Path, task_case: TaskCase) -> None:
    if path is None or task_case is None:
        raise ValueError("path 和 task_case 不能为空")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json_safe(task_case)
    _remove_legacy_required_field(data)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=4),
        encoding="utf-8",
    )


def _adapt_task_case(
    config: HarnessRunConfig,
    adapter: BaseBenchmarkAdapter,
    case_id: str,
) -> TaskCase:
    task_case = adapter.adapt_task_case(config, case_id)
    if not isinstance(task_case, TaskCase):
        raise TypeError("adapter.adapt_task_case 必须返回 TaskCase")
    if task_case.case_id != case_id:
        raise ValueError(f"TaskCase.case_id 与 case_id 不一致: {case_id}")
    if task_case.milestone_graph is None:
        raise ValueError(f"TaskCase 缺少 milestone_graph: {case_id}")

    task_case.milestone_graph = enrich_milestone_graph(task_case.milestone_graph)
    if not task_case.stage_goals:
        task_case.stage_goals = generate_stage_goals(
            task_case,
            mode=str(config.metadata.get("stage_goal_generation", "auto")),
            llm_provider=build_llm_from_env,
        )
    return task_case


def _remove_legacy_required_field(data: object) -> None:
    if not isinstance(data, dict):
        return
    graph = data.get("milestone_graph")
    if not isinstance(graph, dict):
        return
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if isinstance(node, dict):
            node.pop("required", None)


## 解析JSON对象为DynSTEER模型对象

def _required_str(data: JsonObject, key: str) -> str:
    value = get_object(data, key, str)
    if not isinstance(value, str) or not value:
        raise ValueError(f"字段 {key} 必须是非空字符串")
    return value


def _optional_object(data: JsonObject, key: str) -> JsonObject:
    value = data.get(key)
    return dict(value) if isinstance(value, dict) else {}


def parse_constraint(data: JsonObject) -> Constraint:
    constraint_data = ensure_json_object(data)
    return Constraint(
        constraint_id=_required_str(constraint_data, "constraint_id"),
        target=enum_value(ConstraintTarget, constraint_data.get("target"), "target"),  # type: ignore[arg-type]
        selector=_required_str(constraint_data, "selector"),
        operator=enum_value(Operator, constraint_data.get("operator"), "operator"),  # type: ignore[arg-type]
        expected=constraint_data.get("expected"),
        namespace=constraint_data.get("namespace") if isinstance(constraint_data.get("namespace"), str) else None,
        reference_milestone_id=(
            constraint_data.get("reference_milestone_id")
            if isinstance(constraint_data.get("reference_milestone_id"), str)
            else None
        ),
        weight=float(constraint_data.get("weight", 1.0)),
        threshold=float(constraint_data.get("threshold", 1.0)),
        hard=bool(constraint_data.get("hard", False)),
        evaluator_hint=str(constraint_data.get("evaluator_hint", "rule")),
        stage_goal_semantics=(
            ensure_json_object(constraint_data["stage_goal_semantics"])
            if constraint_data.get("stage_goal_semantics") is not None
            else None
        ),
        metadata=_optional_object(constraint_data, "metadata"),
    )


def parse_milestone(data: JsonObject) -> Milestone:
    milestone_data = ensure_json_object(data)
    if "required" in milestone_data:
        raise ValueError("Milestone 已移除 required 字段，请重新生成 adapted case")
    constraints_value = milestone_data.get("constraints", [])
    stage_anchor = milestone_data.get("stage_anchor_predecessor_id")
    return Milestone(
        milestone_id=_required_str(milestone_data, "milestone_id"),
        name=_required_str(milestone_data, "name"),
        description=_required_str(milestone_data, "description"),
        constraints=[parse_constraint(ensure_json_object(item)) for item in constraints_value],
        pass_threshold=float(milestone_data["pass_threshold"])
        if milestone_data.get("pass_threshold") is not None
        else None,
        metadata=_optional_object(milestone_data, "metadata"),
        dependency_predecessor_ids=[str(item) for item in milestone_data.get("dependency_predecessor_ids", [])],
        stage_anchor_predecessor_id=stage_anchor if isinstance(stage_anchor, str) else None,
    )


def parse_minefield(data: JsonObject) -> Minefield:
    minefield_data = ensure_json_object(data)
    constraints_value = minefield_data.get("constraints", [])
    penalty_data = ensure_json_object(minefield_data.get("penalty", {"mode": "fixed", "value": 0.0}))
    return Minefield(
        minefield_id=_required_str(minefield_data, "minefield_id"),
        name=_required_str(minefield_data, "name"),
        description=_required_str(minefield_data, "description"),
        severity=str(minefield_data.get("severity", "warning")),
        constraints=[parse_constraint(ensure_json_object(item)) for item in constraints_value],
        penalty=MinefieldPenalty(
            mode=str(penalty_data.get("mode", "fixed")),
            value=float(penalty_data.get("value", 0.0)),
        ),
        metadata=_optional_object(minefield_data, "metadata"),
    )


def parse_milestone_graph(data: JsonObject) -> MilestoneGraph:
    graph_data = ensure_json_object(data)
    node_values = graph_data.get("nodes", [])
    edge_values = graph_data.get("edges", [])
    minefield_values = graph_data.get("minefields", [])
    thresholds = graph_data.get("default_thresholds", {})
    graph = MilestoneGraph(
        nodes=[parse_milestone(ensure_json_object(item)) for item in node_values],
        edges=[(str(source), str(target)) for source, target in edge_values],
        minefields=[parse_minefield(ensure_json_object(item)) for item in minefield_values],
        default_thresholds={str(key): float(value) for key, value in thresholds.items()},
        metadata=_optional_object(graph_data, "metadata"),
    )
    return graph


def parse_task_case(data: JsonObject) -> TaskCase:
    task_data = ensure_json_object(data)
    raw_task_types = task_data.get("task_types", [])
    policy_constraints = task_data.get("policy_constraints", [])
    stage_goal_values = task_data.get("stage_goals", {})
    milestone_graph = None
    if task_data.get("milestone_graph") is not None:
        milestone_graph = parse_milestone_graph(ensure_json_object(task_data["milestone_graph"]))
    return TaskCase(
        task_id=_required_str(task_data, "task_id"),
        task_description=_required_str(task_data, "task_description"),
        case_id=_required_str(task_data, "case_id"),
        environment_schema=_optional_object(task_data, "environment_schema"),
        tool_schema=_optional_object(task_data, "tool_schema"),
        policy_constraints=[ensure_json_object(item) for item in policy_constraints],
        initial_state=ensure_json_object(task_data["initial_state"]) if task_data.get("initial_state") is not None else None,
        milestone_graph=milestone_graph,
        stage_goals={str(key): str(value) for key, value in stage_goal_values.items()},
        task_types=[enum_value(TaskType, item, "task_types") for item in raw_task_types],  # type: ignore[list-item]
        metadata=_optional_object(task_data, "metadata"),
    )


def _load_tool_call(data: JsonObject | None) -> ToolCall | None:
    if data is None:
        return None
    return ToolCall(name=_required_str(data, "name"), arguments=_optional_object(data, "arguments"))


def _load_tool_result(data: JsonObject | None) -> ToolResult | None:
    if data is None:
        return None
    return ToolResult(
        success=bool(data.get("success", False)),
        content=data.get("content"),
        exception=data.get("exception") if isinstance(data.get("exception"), str) else None,
    )


def _load_cost(data: JsonObject | None) -> StepCost:
    if data is None:
        return StepCost()
    tokens = data.get("tokens")
    latency_ms = data.get("latency_ms")
    return StepCost(
        tokens=int(tokens) if tokens is not None else None,
        latency_ms=int(latency_ms) if latency_ms is not None else None,
    )


def _load_actor(value: object, field_name: str, required: bool = True) -> Actor | None:
    if value is None:
        if required:
            raise ValueError(f"缺少枚举字段: {field_name}")
        return None
    role_aliases = {
        "SYSTEM": Actor.SYSTEM,
        "USER": Actor.USER,
        "AGENT": Actor.AGENT,
        "EXECUTION_ENVIRONMENT": Actor.ENVIRONMENT,
        "ENVIRONMENT": Actor.ENVIRONMENT,
        "EVALUATOR": Actor.EVALUATOR,
    }
    alias = role_aliases.get(str(value).upper())
    if alias is not None:
        return alias
    return enum_value(Actor, value, field_name)  # type: ignore[return-value]


def _load_step(data: JsonObject) -> TrajectoryStep:
    known = {
        "step_id",
        "index",
        "actor",
        "recipient",
        "event_type",
        "timestamp",
        "content",
        "tool_call",
        "tool_result",
        "state_delta_refs",
        "cost",
    }
    refs = get_object(data, "state_delta_refs", list, default=[], required=False)
    if not isinstance(refs, list):
        raise ValueError("state_delta_refs 必须是数组")
    index = get_object(data, "index", int)
    if not isinstance(index, int):
        raise ValueError("step.index 必须是整数")
    return TrajectoryStep(
        step_id=_required_str(data, "step_id"),
        index=index,
        actor=_load_actor(get_object(data, "actor"), "actor") or Actor.EVALUATOR,
        event_type=enum_value(EventType, get_object(data, "event_type"), "event_type"),  # type: ignore[arg-type]
        recipient=_load_actor(data.get("recipient"), "recipient", required=False),
        timestamp=data.get("timestamp") if isinstance(data.get("timestamp"), str) else None,
        content=data.get("content") if isinstance(data.get("content"), str) else None,
        tool_call=_load_tool_call(ensure_json_object(data["tool_call"])) if data.get("tool_call") is not None else None,
        tool_result=_load_tool_result(ensure_json_object(data["tool_result"]))
        if data.get("tool_result") is not None
        else None,
        state_delta_refs=[str(item) for item in refs],
        cost=_load_cost(ensure_json_object(data["cost"])) if data.get("cost") is not None else StepCost(),
        raw=unknown_fields(data, known),
    )


def _load_snapshot(data: JsonObject) -> StateSnapshot:
    index = get_object(data, "after_step_index", int)
    if not isinstance(index, int):
        raise ValueError("snapshot.after_step_index 必须是整数")
    namespaces = get_object(data, "namespaces", dict, default={}, required=False)
    if not isinstance(namespaces, dict):
        raise ValueError("snapshot.namespaces 必须是对象")
    return StateSnapshot(
        snapshot_id=_required_str(data, "snapshot_id"),
        after_step_id=_required_str(data, "after_step_id"),
        after_step_index=index,
        namespaces=namespaces,
        raw=unknown_fields(data, {"snapshot_id", "after_step_id", "after_step_index", "namespaces"}),
    )


def load_trajectory(data: JsonObject) -> Trajectory:
    trajectory_data = ensure_json_object(data)
    step_values = get_object(trajectory_data, "steps", list)
    if not isinstance(step_values, list):
        raise ValueError("trajectory.steps 必须是数组")
    snapshot_values = get_object(trajectory_data, "snapshots", list, default=[], required=False)
    if not isinstance(snapshot_values, list):
        raise ValueError("trajectory.snapshots 必须是数组")
    metrics = get_object(trajectory_data, "metrics", dict, default={}, required=False)
    if not isinstance(metrics, dict):
        raise ValueError("trajectory.metrics 必须是对象")
    final_state = get_object(trajectory_data, "final_state", (dict, type(None)), default=None, required=False)
    known = {"run_id", "task_id", "steps", "snapshots", "final_state", "metrics"}
    return Trajectory(
        run_id=_required_str(trajectory_data, "run_id"),
        task_id=_required_str(trajectory_data, "task_id"),
        steps=[_load_step(ensure_json_object(item)) for item in step_values],
        snapshots=[_load_snapshot(ensure_json_object(item)) for item in snapshot_values],
        final_state=final_state if isinstance(final_state, dict) else None,
        metrics=metrics,
        raw=unknown_fields(trajectory_data, known),
    )


## 将DynSTEER模型对象转换为JSON对象
