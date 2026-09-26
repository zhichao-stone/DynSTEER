from dynsteer.adapter.utils import rows_from_dataframe
from dynsteer.model import JsonValue
from dynsteer.utils import enum_name, json_safe

def sandbox_rows_from_context(context: object | None, module_loader: object, *, get_all_history_snapshots: bool=True) -> list[dict[str, object]]:
    if context is None:
        return []
    database_namespace = _database_namespace(module_loader)
    dataframe = context.get_database(_sandbox_namespace(database_namespace), get_all_history_snapshots=get_all_history_snapshots, drop_sandbox_message_index=False)
    return rows_from_dataframe(dataframe)

def database_namespaces(module_loader: object, include_sandbox: bool=False) -> list[object]:
    namespaces = _namespace_members(_database_namespace(module_loader))
    if include_sandbox:
        return namespaces
    return [namespace for namespace in namespaces if enum_name(namespace) != "SANDBOX"]

def state_from_context(context: object, module_loader: object, *, sandbox_message_index: int | None=None, include_sandbox: bool=True) -> dict[str, JsonValue]:
    """Extracts the unified namespace state from the ToolSandbox context. Args: context: context: ToolSandbox current implementation context. load function for mode_loader: ToolSandbox module. sandbox_message_index: optional historical message boundary; read the current state for empty hours. Include_sandbox: Whether to include the SANDBOX dialogue database. Returns: Unified JSON status, all namespaces keep sandbox_message_index columns."""
    if context is None:
        raise ValueError("Can't be empty.")
    namespaces: dict[str, JsonValue] = {}
    for namespace in database_namespaces(module_loader, include_sandbox=include_sandbox):
        kwargs: dict[str, object] = {"namespace": namespace, "drop_sandbox_message_index": False}
        if sandbox_message_index is not None:
            kwargs["sandbox_message_index"] = sandbox_message_index
        dataframe = context.get_database(**kwargs)
        namespaces[enum_name(namespace)] = [json_safe(row) for row in rows_from_dataframe(dataframe)]
    return {"namespaces": namespaces}

def initial_state_from_context(context: object, module_loader: object) -> dict[str, JsonValue]:
    """Reads ToolSandbox's initial status to avoid dynamic timestamp using expired offline JSON."""
    first_user_index = getattr(context, "first_user_sandbox_message_index", None)
    return state_from_context(context, module_loader, sandbox_message_index=first_user_index if isinstance(first_user_index, int) else None, include_sandbox=True)

def snapshots_from_context(context: object, steps: list[dict[str, JsonValue]], module_loader: object) -> list[dict[str, JsonValue]]:
    if not steps:
        return []
    sandbox_indexes: list[int] = []
    for step in steps:
        sandbox_index = step.get("raw_sandbox_message_index")
        if not isinstance(sandbox_index, int) or sandbox_index < 0:
            raise ValueError(f"ToolSandbox step missing valid raw_sandbox_message_index:{step.get('step_id')}")
        sandbox_indexes.append(sandbox_index)
    if sandbox_indexes != sorted(sandbox_indexes):
        raise ValueError('ToolSandbox steps must be arranged in raw_sandbox_message_index ascending order')
    if len(sandbox_indexes) != len(set(sandbox_indexes)):
        raise ValueError('ToolSandbox steps includes repeat')
    snapshots: list[dict[str, JsonValue]] = []
    for step, sandbox_index in zip(steps, sandbox_indexes, strict=True):
        state = state_from_context(context, module_loader, sandbox_message_index=sandbox_index, include_sandbox=True)
        snapshots.append(
            {
                "snapshot_id": f"toolsandbox:{sandbox_index}",
                "after_step_id": str(step["step_id"]),
                "after_step_index": int(step["index"]),
                "namespaces": state["namespaces"],
                "raw": {"sandbox_message_index": sandbox_index},
            }
        )
    return snapshots

def _database_namespace(module_loader: object) -> object:
    execution_context = module_loader("tool_sandbox.common.execution_context")
    return getattr(execution_context, "DatabaseNamespace")

def _namespace_members(database_namespace: object) -> list[object]:
    try:
        return list(database_namespace)
    except TypeError:
        members = []
        for name in dir(database_namespace):
            if not name.startswith("_"):
                value = getattr(database_namespace, name)
                if not callable(value):
                    members.append(value)
        return members

def _sandbox_namespace(database_namespace: object) -> object:
    for namespace in _namespace_members(database_namespace):
        if enum_name(namespace) == "SANDBOX":
            return namespace
    return getattr(database_namespace, "SANDBOX")
