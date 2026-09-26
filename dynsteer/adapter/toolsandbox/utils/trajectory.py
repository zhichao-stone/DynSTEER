from copy import deepcopy
from dynsteer.adapter.toolsandbox.utils.roles import actor_value_from_role_name
from dynsteer.model import Actor, EventType, JsonValue, StateSnapshot, ToolCall, ToolResult, Trajectory, TrajectoryStep

def trajectory_from_sandbox_rows(
    task_id: str,
    steps: list[dict[str, JsonValue]],
    snapshots: list[dict[str, JsonValue]] | None = None,
) -> Trajectory:
    return Trajectory(
        task_id=task_id,
        steps=[_trajectory_step_from_dict(step) for step in steps],
        snapshots=[_snapshot_from_dict(snapshot) for snapshot in snapshots or []],
    )

def _trajectory_step_from_dict(step: dict[str, JsonValue]) -> TrajectoryStep:
    step_dict = deepcopy(step)
    content, tool_call, tool_result = (step_dict.pop(k, None) for k in ["content", "tool_call", "tool_result"])
    return TrajectoryStep(
        step_id=str(step_dict.pop("step_id")),
        index=int(step_dict.pop("index")),
        event_type=EventType(str(step_dict.pop("event_type"))),
        actor=_actor_from_step(step_dict.pop("actor"), "actor"),
        recipient=_actor_from_step(step_dict.pop("recipient", None), "recipient"),
        content=content if isinstance(content, str) else None,
        tool_call=ToolCall(
            name=str(tool_call["name"]),
            arguments=dict(tool_call.get("arguments", {})),
        ) if isinstance(tool_call, dict) else None,
        tool_result=ToolResult(
            success=bool(tool_result.get("success", False)),
            content=tool_result.get("content"),
            exception=tool_result.get("exception") if isinstance(tool_result.get("exception"), str) else None,
        ) if isinstance(tool_result, dict) else None,
        raw={k: v for k, v in step_dict.items()},
    )

def _actor_from_step(value: JsonValue, field_name: str) -> Actor | None:
    actor_value = actor_value_from_role_name(str(value).upper())
    if actor_value is not None:
        return Actor(actor_value)

    try:
        return Actor(str(value))
    except ValueError as exc:
        raise ValueError(f"invalid actor for step.{field_name}: {value}") from exc

def _snapshot_from_dict(snapshot: dict[str, JsonValue]) -> StateSnapshot:
    return StateSnapshot(
        snapshot_id=str(snapshot["snapshot_id"]),
        after_step_id=str(snapshot["after_step_id"]),
        after_step_index=int(snapshot["after_step_index"]),
        namespaces=dict(snapshot.get("namespaces", {})),
        raw=dict(snapshot.get("raw", {})) if isinstance(snapshot.get("raw"), dict) else {},
    )
