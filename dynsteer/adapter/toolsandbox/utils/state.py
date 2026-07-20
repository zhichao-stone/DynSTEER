from dynsteer.adapter.utils import rows_from_dataframe
from dynsteer.model import JsonValue
from dynsteer.utils import enum_name, json_safe


def sandbox_rows_from_context(
    context: object | None, module_loader: object, *, get_all_history_snapshots: bool = True
) -> list[dict[str, object]]:
    if context is None:
        return []
    database_namespace = _database_namespace(module_loader)
    dataframe = context.get_database(
        _sandbox_namespace(database_namespace),
        get_all_history_snapshots=get_all_history_snapshots,
        drop_sandbox_message_index=False,
    )
    return rows_from_dataframe(dataframe)


def database_namespaces(module_loader: object, include_sandbox: bool = False) -> list[object]:
    namespaces = _namespace_members(_database_namespace(module_loader))
    if include_sandbox:
        return namespaces
    return [namespace for namespace in namespaces if enum_name(namespace) != "SANDBOX"]


def initial_state_from_context(context: object, module_loader: object) -> dict[str, JsonValue]:
    namespaces: dict[str, JsonValue] = {}
    first_user_index = getattr(context, "first_user_sandbox_message_index", None)
    for namespace in database_namespaces(module_loader):
        dataframe = context.get_database(namespace=namespace, sandbox_message_index=first_user_index)
        namespaces[enum_name(namespace)] = [json_safe(row) for row in rows_from_dataframe(dataframe)]
    return {"namespaces": namespaces}


def snapshots_from_context(
    context: object, steps: list[dict[str, JsonValue]], module_loader: object
) -> list[dict[str, JsonValue]]:
    if not steps:
        return []
    sandbox_indexes = [
        int(step["raw_sandbox_message_index"])
        for step in steps
        if isinstance(step.get("raw_sandbox_message_index"), int)
    ]
    if not sandbox_indexes:
        return []
    step_by_sandbox_index = {
        int(step["raw_sandbox_message_index"]): step
        for step in steps
        if isinstance(step.get("raw_sandbox_message_index"), int)
    }
    snapshots: list[dict[str, JsonValue]] = []
    for sandbox_index in sorted(set(sandbox_indexes)):
        step = step_by_sandbox_index[sandbox_index]
        namespaces: dict[str, JsonValue] = {}
        for namespace in database_namespaces(module_loader, include_sandbox=True):
            dataframe = context.get_database(
                namespace=namespace, sandbox_message_index=sandbox_index, drop_sandbox_message_index=False
            )
            namespaces[enum_name(namespace)] = [json_safe(row) for row in rows_from_dataframe(dataframe)]
        snapshots.append(
            {
                "snapshot_id": f"toolsandbox:{sandbox_index}",
                "after_step_id": str(step["step_id"]),
                "after_step_index": int(step["index"]),
                "namespaces": namespaces,
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
            if name.startswith("_"):
                continue
            value = getattr(database_namespace, name)
            if callable(value):
                continue
            members.append(value)
        return members


def _sandbox_namespace(database_namespace: object) -> object:
    for namespace in _namespace_members(database_namespace):
        if enum_name(namespace) == "SANDBOX":
            return namespace
    return getattr(database_namespace, "SANDBOX")
