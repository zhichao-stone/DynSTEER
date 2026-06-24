from __future__ import annotations

from typing import Any, TypeVar

from dynsteer.adapter.base import BaseBenchmarkAdapter, BaseBenchmarkHarness
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

EnumT = TypeVar("EnumT")


def _required(data: JsonObject, key: str) -> Any:
    if key not in data or data[key] is None:
        raise ValueError(f"缺少必要字段: {key}")
    return data[key]


def _required_str(data: JsonObject, key: str) -> str:
    value = _required(data, key)
    if not isinstance(value, str) or value == "":
        raise ValueError(f"字段 {key} 必须是非空字符串")
    return value


def _optional_object(data: JsonObject, key: str) -> JsonObject:
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"字段 {key} 必须是对象")
    return value


def _optional_object_or_none(data: JsonObject, key: str) -> JsonObject | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"字段 {key} 必须是对象")
    return value


def _enum(enum_class: type[EnumT], value: Any, field_name: str) -> EnumT:
    if value is None:
        raise ValueError(f"缺少枚举字段: {field_name}")
    try:
        return enum_class(value)  # type: ignore[call-arg]
    except ValueError as exc:
        raise ValueError(f"字段 {field_name} 的枚举值非法: {value}") from exc


def _unknown_fields(data: JsonObject, known: set[str]) -> JsonObject:
    return {key: value for key, value in data.items() if key not in known}


def _load_constraint(data: JsonObject) -> Constraint:
    return Constraint(
        constraint_id=_required_str(data, "constraint_id"),
        target=_enum(ConstraintTarget, _required(data, "target"), "target"),
        selector=_required_str(data, "selector"),
        operator=_enum(Operator, _required(data, "operator"), "operator"),
        expected=data.get("expected"),
        namespace=data.get("namespace") if isinstance(data.get("namespace"), str) else None,
        reference_milestone_id=data.get("reference_milestone_id")
        if isinstance(data.get("reference_milestone_id"), str)
        else None,
        weight=float(data.get("weight", 1.0)),
        threshold=float(data.get("threshold", 1.0)),
        hard=bool(data.get("hard", False)),
        evaluator_hint=str(data.get("evaluator_hint", "rule")),
        metadata=_optional_object(data, "metadata"),
    )


def _load_milestone(data: JsonObject) -> Milestone:
    constraints_value = data.get("constraints", [])
    if not isinstance(constraints_value, list):
        raise ValueError("milestone.constraints 必须是数组")
    return Milestone(
        milestone_id=_required_str(data, "milestone_id"),
        name=_required_str(data, "name"),
        description=_required_str(data, "description"),
        constraints=[_load_constraint(ensure_json_object(item)) for item in constraints_value],
        required=bool(data.get("required", True)),
        pass_threshold=float(data["pass_threshold"]) if data.get("pass_threshold") is not None else None,
        metadata=_optional_object(data, "metadata"),
    )


def _load_minefield(data: JsonObject) -> Minefield:
    constraints_value = data.get("constraints", [])
    if not isinstance(constraints_value, list):
        raise ValueError("minefield.constraints 必须是数组")
    penalty_data = ensure_json_object(data.get("penalty", {"mode": "fixed", "value": 0.0}))
    return Minefield(
        minefield_id=_required_str(data, "minefield_id"),
        name=_required_str(data, "name"),
        description=_required_str(data, "description"),
        severity=str(data.get("severity", "warning")),
        constraints=[_load_constraint(ensure_json_object(item)) for item in constraints_value],
        penalty=MinefieldPenalty(mode=str(penalty_data.get("mode", "fixed")), value=float(penalty_data.get("value", 0.0))),
        metadata=_optional_object(data, "metadata"),
    )


def load_milestone_graph(data: JsonObject) -> MilestoneGraph:
    """从通用 JSON 对象载入 MilestoneGraph。

    Args:
        data: milestone_graph JSON 对象。

    Returns:
        统一 milestone 图模型。
    """
    graph_data = ensure_json_object(data)
    node_values = graph_data.get("nodes", [])
    edge_values = graph_data.get("edges", [])
    minefield_values = graph_data.get("minefields", [])
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
    thresholds = graph_data.get("default_thresholds", {})
    if not isinstance(thresholds, dict):
        raise ValueError("default_thresholds 必须是对象")
    return MilestoneGraph(
        nodes=[_load_milestone(ensure_json_object(item)) for item in node_values],
        edges=edges,
        minefields=[_load_minefield(ensure_json_object(item)) for item in minefield_values],
        default_thresholds={str(key): float(value) for key, value in thresholds.items()},
        metadata=_optional_object(graph_data, "metadata"),
    )


def load_task_case(data: JsonObject) -> TaskCase:
    """从通用 JSON 对象载入任务定义。

    Args:
        data: task JSON 对象。

    Returns:
        统一任务模型。
    """
    task_data = ensure_json_object(data)
    raw_task_types = task_data.get("task_types", [])
    if not isinstance(raw_task_types, list):
        raise ValueError("task_types 必须是数组")
    policy_constraints = task_data.get("policy_constraints", [])
    if not isinstance(policy_constraints, list):
        raise ValueError("policy_constraints 必须是数组")
    milestone_graph = None
    if task_data.get("milestone_graph") is not None:
        milestone_graph = load_milestone_graph(ensure_json_object(task_data["milestone_graph"]))
    return TaskCase(
        task_id=_required_str(task_data, "task_id"),
        task_description=_required_str(task_data, "task_description"),
        environment_schema=_optional_object(task_data, "environment_schema"),
        tool_schema=_optional_object(task_data, "tool_schema"),
        policy_constraints=[ensure_json_object(item) for item in policy_constraints],
        initial_state=_optional_object_or_none(task_data, "initial_state"),
        milestone_graph=milestone_graph,
        task_types=[_enum(TaskType, item, "task_types") for item in raw_task_types],
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
    refs = data.get("state_delta_refs", [])
    if not isinstance(refs, list):
        raise ValueError("state_delta_refs 必须是数组")
    index = _required(data, "index")
    if not isinstance(index, int):
        raise ValueError("step.index 必须是整数")
    return TrajectoryStep(
        step_id=_required_str(data, "step_id"),
        index=index,
        actor=_enum(Actor, _required(data, "actor"), "actor"),
        event_type=_enum(EventType, _required(data, "event_type"), "event_type"),
        timestamp=data.get("timestamp") if isinstance(data.get("timestamp"), str) else None,
        content=data.get("content") if isinstance(data.get("content"), str) else None,
        tool_call=_load_tool_call(ensure_json_object(data["tool_call"])) if data.get("tool_call") is not None else None,
        tool_result=_load_tool_result(ensure_json_object(data["tool_result"]))
        if data.get("tool_result") is not None
        else None,
        state_delta_refs=[str(item) for item in refs],
        cost=_load_cost(ensure_json_object(data["cost"])) if data.get("cost") is not None else StepCost(),
        raw=_unknown_fields(data, known),
    )


def _load_snapshot(data: JsonObject) -> StateSnapshot:
    index = _required(data, "after_step_index")
    if not isinstance(index, int):
        raise ValueError("snapshot.after_step_index 必须是整数")
    namespaces = data.get("namespaces", {})
    if not isinstance(namespaces, dict):
        raise ValueError("snapshot.namespaces 必须是对象")
    return StateSnapshot(
        snapshot_id=_required_str(data, "snapshot_id"),
        after_step_id=_required_str(data, "after_step_id"),
        after_step_index=index,
        namespaces=namespaces,
        raw=_unknown_fields(data, {"snapshot_id", "after_step_id", "after_step_index", "namespaces"}),
    )


def load_trajectory(data: JsonObject) -> Trajectory:
    """从通用 JSON 对象载入 Agent 轨迹。

    Args:
        data: trajectory JSON 对象。

    Returns:
        统一轨迹模型。
    """
    trajectory_data = ensure_json_object(data)
    step_values = trajectory_data.get("steps")
    if not isinstance(step_values, list):
        raise ValueError("trajectory.steps 必须是数组")
    snapshot_values = trajectory_data.get("snapshots", [])
    if not isinstance(snapshot_values, list):
        raise ValueError("trajectory.snapshots 必须是数组")
    metrics = trajectory_data.get("metrics", {})
    if not isinstance(metrics, dict):
        raise ValueError("trajectory.metrics 必须是对象")
    final_state = trajectory_data.get("final_state")
    if final_state is not None and not isinstance(final_state, dict):
        raise ValueError("trajectory.final_state 必须是对象")
    known = {"run_id", "task_id", "steps", "snapshots", "final_state", "metrics"}
    return Trajectory(
        run_id=_required_str(trajectory_data, "run_id"),
        task_id=_required_str(trajectory_data, "task_id"),
        steps=[_load_step(ensure_json_object(item)) for item in step_values],
        snapshots=[_load_snapshot(ensure_json_object(item)) for item in snapshot_values],
        final_state=final_state,
        metrics=metrics,
        raw=_unknown_fields(trajectory_data, known),
    )


class GenericAdapter(BaseBenchmarkAdapter):
    """通用 JSON benchmark 适配器。"""

    benchmark = "generic"

    # override 基类的函数实现

    def load_experiment(self, data: JsonObject) -> tuple[TaskCase, Trajectory]:
        """载入通用 JSON 实验数据。

        Args:
            data: 包含 task 与 trajectory 的实验字典。

        Returns:
            DynSTEER 任务与轨迹。
        """
        experiment = ensure_json_object(data)
        if experiment.get("task") is None or experiment.get("trajectory") is None:
            raise ValueError("通用实验数据必须包含 task 和 trajectory")
        return load_task_case(ensure_json_object(experiment["task"])), load_trajectory(
            ensure_json_object(experiment["trajectory"])
        )

    def create_harness(self) -> BaseBenchmarkHarness:
        """创建通用逐步回放 harness。

        Returns:
            GenericHarness 实例。
        """
        from dynsteer.adapter.generic.harness import GenericHarness

        return GenericHarness()

    # GenericAdapter 独有函数实现
