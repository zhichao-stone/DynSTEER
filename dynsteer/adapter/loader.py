from __future__ import annotations

import json
import re
from pathlib import Path

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
from dynsteer.stage_goal import generate_stage_goals_with_llm
from dynsteer.utils import enum_value, get_object, json_safe, unknown_fields


def safe_case_file_name(case_id: str) -> str:
    """将 case_id 转换为安全 JSON 文件名。"""
    if case_id is None:
        raise ValueError("case_id 不能为空")
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(case_id)).strip("._")
    if not normalized:
        raise ValueError("case_id 不能转换为空文件名")
    return f"{normalized}.json"


def adapterd_case_path(data_root: Path, case_id: str) -> Path:
    """返回 adapted 单 case JSON 路径。"""
    if data_root is None:
        raise ValueError("data_root 不能为空")
    return data_root / "adapterd_cases" / safe_case_file_name(case_id)


def load_task_case(config: HarnessRunConfig, adapter: BaseBenchmarkAdapter) -> list[TaskCase]:
    """按 config.case_ids 逐 case 读取或生成已适配 TaskCase。"""
    if config is None or adapter is None:
        raise ValueError("config 和 adapter 不能为空")
    case_ids = list(config.case_ids or ())
    if not case_ids:
        raise ValueError("config.case_ids 不能为空")

    task_cases: list[TaskCase] = []
    for case_id in case_ids:
        path = adapterd_case_path(config.data_root, case_id)
        if not path.exists():
            task_case = adapter.adapt_task_case(config, case_id)
            if not isinstance(task_case, TaskCase):
                raise TypeError("adapter.adapt_task_case 必须返回 TaskCase")
            if task_case.case_id != case_id:
                raise ValueError(f"TaskCase.case_id 与 case_id 不一致: {case_id}")
            if task_case.milestone_graph is not None:
                task_case.milestone_graph = enrich_milestone_graph(task_case.milestone_graph)
                if not task_case.stage_goals:
                    task_case.stage_goals = generate_stage_goals(task_case, config)
            save_task_case(path, task_case)
        else:
            task_case = load_task_case_file(path, expected_case_id=case_id)
        task_cases.append(task_case)
    return task_cases


def generate_stage_goals(task_case: TaskCase, config: HarnessRunConfig) -> dict[str, str]:
    """为首次适配的 TaskCase 生成 stage goal 映射。"""
    if task_case is None or config is None:
        raise ValueError("task_case 和 config 不能为空")
    mode = config.metadata.get("stage_goal_generation", "llm")
    if mode == "stored":
        raise ValueError("TaskCase 缺少 stage_goals，请先执行 LLM 预生成")
    if mode != "llm":
        raise ValueError(f"未知 stage_goal_generation 模式: {mode}")
    llm = build_llm_from_env()
    if llm is None:
        raise ValueError("stage_goal_generation=llm 需要配置 DYNSTEER_JUDGE_PROVIDER")
    return generate_stage_goals_with_llm(task_case, llm)


def load_task_case_file(path: Path, expected_case_id: str) -> TaskCase:
    """读取单个已适配 TaskCase JSON 文件。"""
    if path is None or expected_case_id is None:
        raise ValueError("path 和 expected_case_id 不能为空")
    if not path.exists():
        raise FileNotFoundError(f"缺少已适配 TaskCase 文件: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"TaskCase 文件不是合法 JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"TaskCase 文件必须是 JSON 对象: {path}")
    task_case = parse_task_case(ensure_json_object(data))
    if task_case.case_id != expected_case_id:
        raise ValueError(f"TaskCase.case_id 与文件对应 case_id 不一致: {expected_case_id}")
    return task_case


def save_task_case(path: Path, task_case: TaskCase) -> None:
    """保存单个 TaskCase JSON 文件。"""
    if path is None or task_case is None:
        raise ValueError("path 和 task_case 不能为空")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(task_case_to_json(task_case), ensure_ascii=False, indent=4),
        encoding="utf-8",
    )


## 解析JSON对象为DynSTEER模型对象

def _required_str(data: JsonObject, key: str) -> str:
    value = get_object(data, key, str)
    if not isinstance(value, str) or not value:
        raise ValueError(f"字段 {key} 必须是非空字符串")
    return value


def _optional_object(data: JsonObject, key: str) -> JsonObject:
    value = get_object(data, key, dict, default={}, required=False)
    return dict(value) if isinstance(value, dict) else {}


def _optional_object_or_none(data: JsonObject, key: str) -> JsonObject | None:
    value = get_object(data, key, (dict, type(None)), default=None, required=False)
    return dict(value) if isinstance(value, dict) else None


def _string_list(data: JsonObject, key: str) -> list[str]:
    values = get_object(data, key, list, default=[], required=False)
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ValueError(f"字段 {key} 必须是 list[str] 类型")
    return list(values)


def _string_dict(data: dict[object, object], key: str) -> dict[str, str]:
    if data is None or not isinstance(data, dict):
        raise ValueError(f"字段 {key} 必须是 dict[str, str] 类型")
    result: dict[str, str] = {}
    for item_key, item_value in data.items():
        if not isinstance(item_key, str) or not item_key:
            raise ValueError(f"字段 {key} 的 key 必须是非空字符串")
        if not isinstance(item_value, str) or not item_value.strip():
            raise ValueError(f"字段 {key} 的 value 必须是非空字符串")
        result[item_key] = item_value
    return result


def parse_constraint(data: JsonObject) -> Constraint:
    """从 adapted JSON 对象载入 Constraint。"""
    constraint_data = ensure_json_object(data)
    return Constraint(
        constraint_id=_required_str(constraint_data, "constraint_id"),
        target=enum_value(ConstraintTarget, get_object(constraint_data, "target"), "target"),  # type: ignore[arg-type]
        selector=_required_str(constraint_data, "selector"),
        operator=enum_value(Operator, get_object(constraint_data, "operator"), "operator"),  # type: ignore[arg-type]
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
        metadata=_optional_object(constraint_data, "metadata"),
    )


def parse_milestone(data: JsonObject) -> Milestone:
    """从 adapted JSON 对象载入 Milestone。"""
    milestone_data = ensure_json_object(data)
    constraints_value = get_object(milestone_data, "constraints", list, default=[], required=False)
    if not isinstance(constraints_value, list):
        raise ValueError("milestone.constraints 必须是数组")
    stage_anchor = get_object(
        milestone_data,
        "stage_anchor_predecessor_id",
        (str, type(None)),
        default=None,
        required=False,
    )
    return Milestone(
        milestone_id=_required_str(milestone_data, "milestone_id"),
        name=_required_str(milestone_data, "name"),
        description=_required_str(milestone_data, "description"),
        constraints=[parse_constraint(ensure_json_object(item)) for item in constraints_value],
        required=bool(milestone_data.get("required", True)),
        pass_threshold=float(milestone_data["pass_threshold"])
        if milestone_data.get("pass_threshold") is not None
        else None,
        metadata=_optional_object(milestone_data, "metadata"),
        dependency_predecessor_ids=_string_list(milestone_data, "dependency_predecessor_ids"),
        stage_anchor_predecessor_id=stage_anchor if isinstance(stage_anchor, str) else None,
    )


def parse_minefield(data: JsonObject) -> Minefield:
    """从 adapted JSON 对象载入 Minefield。"""
    minefield_data = ensure_json_object(data)
    constraints_value = get_object(minefield_data, "constraints", list, default=[], required=False)
    if not isinstance(constraints_value, list):
        raise ValueError("minefield.constraints 必须是数组")
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
    """从 adapted JSON 对象载入 MilestoneGraph。"""
    graph_data = ensure_json_object(data)
    node_values = get_object(graph_data, "nodes", list, default=[], required=False)
    edge_values = get_object(graph_data, "edges", list, default=[], required=False)
    minefield_values = get_object(graph_data, "minefields", list, default=[], required=False)
    if not isinstance(node_values, list) or not isinstance(edge_values, list) or not isinstance(minefield_values, list):
        raise ValueError("nodes、edges、minefields 必须是数组")
    edges: list[tuple[str, str]] = []
    for edge in edge_values:
        if not isinstance(edge, list | tuple) or len(edge) != 2:
            raise ValueError("edges 中每条边必须包含两个 milestone_id")
        source, target = edge
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError("edge milestone_id 必须是字符串")
        edges.append((source, target))
    thresholds = get_object(graph_data, "default_thresholds", dict, default={}, required=False)
    if not isinstance(thresholds, dict):
        raise ValueError("default_thresholds 必须是对象")
    graph = MilestoneGraph(
        nodes=[parse_milestone(ensure_json_object(item)) for item in node_values],
        edges=edges,
        minefields=[parse_minefield(ensure_json_object(item)) for item in minefield_values],
        default_thresholds={str(key): float(value) for key, value in thresholds.items()},
        metadata=_optional_object(graph_data, "metadata"),
    )
    return graph


def parse_task_case(data: JsonObject) -> TaskCase:
    """从 adapted JSON 对象载入 TaskCase。"""
    task_data = ensure_json_object(data)
    raw_task_types = get_object(task_data, "task_types", list, default=[], required=False)
    if not isinstance(raw_task_types, list):
        raise ValueError("task_types 必须是数组")
    policy_constraints = get_object(task_data, "policy_constraints", list, default=[], required=False)
    if not isinstance(policy_constraints, list):
        raise ValueError("policy_constraints 必须是数组")
    stage_goal_values = get_object(task_data, "stage_goals", dict, default={}, required=False)
    if not isinstance(stage_goal_values, dict):
        raise ValueError("stage_goals 必须是对象")
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
        initial_state=_optional_object_or_none(task_data, "initial_state"),
        milestone_graph=milestone_graph,
        stage_goals=_string_dict(stage_goal_values, "stage_goals"),
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


def _load_step(data: JsonObject) -> TrajectoryStep:
    known = {
        "step_id",
        "index",
        "actor",
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
        actor=enum_value(Actor, get_object(data, "actor"), "actor"),  # type: ignore[arg-type]
        event_type=enum_value(EventType, get_object(data, "event_type"), "event_type"),  # type: ignore[arg-type]
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
    """从 adapted JSON 对象载入 Trajectory。"""
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

def constraint_to_json(constraint: Constraint) -> JsonObject:
    """将 Constraint 转换为 JSON 对象。"""
    return {
        "constraint_id": constraint.constraint_id,
        "target": constraint.target.value,
        "selector": constraint.selector,
        "operator": constraint.operator.value,
        "expected": json_safe(constraint.expected),
        "namespace": constraint.namespace,
        "reference_milestone_id": constraint.reference_milestone_id,
        "weight": constraint.weight,
        "threshold": constraint.threshold,
        "hard": constraint.hard,
        "evaluator_hint": constraint.evaluator_hint,
        "metadata": json_safe(constraint.metadata),
    }


def milestone_to_json(milestone: Milestone) -> JsonObject:
    """将 Milestone 转换为 JSON 对象。"""
    return {
        "milestone_id": milestone.milestone_id,
        "name": milestone.name,
        "description": milestone.description,
        "constraints": [constraint_to_json(item) for item in milestone.constraints],
        "required": milestone.required,
        "pass_threshold": milestone.pass_threshold,
        "metadata": json_safe(milestone.metadata),
        "dependency_predecessor_ids": list(milestone.dependency_predecessor_ids),
        "stage_anchor_predecessor_id": milestone.stage_anchor_predecessor_id,
    }


def minefield_to_json(minefield: Minefield) -> JsonObject:
    """将 Minefield 转换为 JSON 对象。"""
    return {
        "minefield_id": minefield.minefield_id,
        "name": minefield.name,
        "description": minefield.description,
        "severity": minefield.severity,
        "constraints": [constraint_to_json(item) for item in minefield.constraints],
        "penalty": {"mode": minefield.penalty.mode, "value": minefield.penalty.value},
        "metadata": json_safe(minefield.metadata),
    }


def milestone_graph_to_json(graph: MilestoneGraph) -> JsonObject:
    """将 MilestoneGraph 转换为 JSON 对象。"""
    return {
        "nodes": [milestone_to_json(item) for item in graph.nodes],
        "edges": [[source, target] for source, target in graph.edges],
        "minefields": [minefield_to_json(item) for item in graph.minefields],
        "default_thresholds": dict(graph.default_thresholds),
        "metadata": json_safe(graph.metadata),
    }


def task_case_to_json(task_case: TaskCase) -> JsonObject:
    """将 TaskCase 转换为 adapted JSON 对象。"""
    return {
        "task_id": task_case.task_id,
        "task_description": task_case.task_description,
        "case_id": task_case.case_id,
        "environment_schema": json_safe(task_case.environment_schema),
        "tool_schema": json_safe(task_case.tool_schema),
        "policy_constraints": json_safe(task_case.policy_constraints),
        "initial_state": json_safe(task_case.initial_state),
        "milestone_graph": (
            milestone_graph_to_json(task_case.milestone_graph)
            if task_case.milestone_graph is not None
            else None
        ),
        "stage_goals": json_safe(task_case.stage_goals),
        "task_types": [item.value for item in task_case.task_types],
        "metadata": json_safe(task_case.metadata),
    }


def trajectory_to_json(trajectory: Trajectory) -> JsonObject:
    """将 Trajectory 转换为 JSON 对象。"""
    if trajectory is None:
        raise ValueError("trajectory 不能为空")
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
    return {
        "step_id": step.step_id,
        "index": step.index,
        "actor": step.actor.value,
        "event_type": step.event_type.value,
        "timestamp": step.timestamp,
        "content": step.content,
        "tool_call": tool_call,
        "tool_result": tool_result,
        "state_delta_refs": list(step.state_delta_refs),
        "cost": {"tokens": step.cost.tokens, "latency_ms": step.cost.latency_ms},
        **{str(key): json_safe(value) for key, value in step.raw.items()},
    }


def snapshot_to_json(snapshot: StateSnapshot) -> JsonObject:
    """将 StateSnapshot 转换为 JSON 对象。"""
    return {
        "snapshot_id": snapshot.snapshot_id,
        "after_step_id": snapshot.after_step_id,
        "after_step_index": snapshot.after_step_index,
        "namespaces": json_safe(snapshot.namespaces),
        **{str(key): json_safe(value) for key, value in snapshot.raw.items()},
    }
