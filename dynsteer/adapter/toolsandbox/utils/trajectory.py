from dynsteer.adapter.toolsandbox.utils.roles import actor_value_from_role_name
from dynsteer.model import Actor, EventType, JsonValue, StateSnapshot, ToolCall, ToolResult, Trajectory, TrajectoryStep

def trajectory_from_sandbox_rows(run_id: str, task_id: str, steps: list[dict[str, JsonValue]], snapshots: list[dict[str, JsonValue]] | None=None) -> Trajectory:
    return Trajectory(run_id=run_id, task_id=task_id, steps=[_trajectory_step_from_dict(step) for step in steps], snapshots=[_snapshot_from_dict(snapshot) for snapshot in snapshots or []])

def _trajectory_step_from_dict(step: dict[str, JsonValue]) -> TrajectoryStep:
    tool_call = step.get('tool_call')
    tool_result = step.get('tool_result')
    return TrajectoryStep(step_id=str(step['step_id']), index=int(step['index']), actor=_actor_from_step_value(step.get('actor'), 'actor'), event_type=EventType(str(step['event_type'])), recipient=_optional_actor_from_step_value(step.get('recipient'), 'recipient'), content=step.get('content') if isinstance(step.get('content'), str) else None, tool_call=ToolCall(str(tool_call['name']), dict(tool_call.get('arguments', {}))) if isinstance(tool_call, dict) else None, tool_result=ToolResult(bool(tool_result.get('success')), tool_result.get('content'), tool_result.get('exception') if isinstance(tool_result.get('exception'), str) else None) if isinstance(tool_result, dict) else None, raw={key: value for key, value in step.items() if key not in {'step_id', 'index', 'actor', 'recipient', 'event_type', 'content', 'tool_call', 'tool_result'}})

def _actor_from_step_value(value: JsonValue, field_name: str) -> Actor:
    actor = _optional_actor_from_step_value(value, field_name)
    if actor is None:
        raise ValueError(f'缺少 step.{field_name}')
    return actor

def _optional_actor_from_step_value(value: JsonValue, field_name: str) -> Actor | None:
    if value is None:
        return None
    actor_value = actor_value_from_role_name(str(value).upper())
    if actor_value is not None:
        return Actor(actor_value)
    try:
        return Actor(str(value))
    except ValueError as exc:
        raise ValueError(f'step.{field_name} 角色非法: {value}') from exc

def _snapshot_from_dict(snapshot: dict[str, JsonValue]) -> StateSnapshot:
    return StateSnapshot(snapshot_id=str(snapshot['snapshot_id']), after_step_id=str(snapshot['after_step_id']), after_step_index=int(snapshot['after_step_index']), namespaces=dict(snapshot.get('namespaces', {})), raw=dict(snapshot.get('raw', {})) if isinstance(snapshot.get('raw'), dict) else {})
