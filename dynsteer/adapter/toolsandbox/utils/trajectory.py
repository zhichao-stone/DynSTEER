from copy import deepcopy
from dynsteer.adapter.toolsandbox.utils.roles import actor_value_from_role_name
from dynsteer.model import Actor, EventType, JsonValue, StateSnapshot, ToolCall, ToolResult, Trajectory, TrajectoryStep

def trajectory_from_sandbox_rows(
    run_id: str, task_id: str, 
    steps: list[dict[str, JsonValue]], 
    snapshots: list[dict[str, JsonValue]] | None=None
) -> Trajectory:
    return Trajectory(
        run_id=run_id, task_id=task_id, 
        steps=[_trajectory_step_from_dict(step) for step in steps], 
        snapshots=[_snapshot_from_dict(snapshot) for snapshot in snapshots or []]
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
        tool_call=ToolCall.from_dict(tool_call),
        tool_result=ToolResult.from_dict(tool_result),
        raw={k: v for k, v in step_dict.items()},
    )

def _actor_from_step(value: JsonValue, field_name: str) -> Actor | None:
    actor_value = actor_value_from_role_name(str(value).upper())
    if actor_value is not None:
        return Actor(actor_value)
    
    try:
        return Actor(str(value))
    except ValueError as exc:
        raise ValueError(f"step.{field_name} 角色非法: {value}") from exc

def _snapshot_from_dict(snapshot: dict[str, JsonValue]) -> StateSnapshot:
    return StateSnapshot(
        snapshot_id=str(snapshot["snapshot_id"]),
        after_step_id=str(snapshot["after_step_id"]),
        after_step_index=int(snapshot["after_step_index"]),
        namespaces=dict(snapshot.get("namespaces", {})),
        raw=dict(snapshot.get("raw", {})) if isinstance(snapshot.get("raw"), dict) else {},
    )
