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
    """从 ToolSandbox context 提取统一 namespace 状态。

    入参：
        context: ToolSandbox 当前执行上下文。
        module_loader: ToolSandbox 模块加载函数。
        sandbox_message_index: 可选历史消息边界；为空时读取当前状态。
        include_sandbox: 是否包含 SANDBOX 对话数据库。
    输出：
        统一 JSON 状态，所有 namespace 都保留 sandbox_message_index 列。
    """
    if context is None:
        raise ValueError("context 不能为空")
    namespaces: dict[str, JsonValue] = {}
    for namespace in database_namespaces(module_loader, include_sandbox=include_sandbox):
        kwargs: dict[str, object] = {"namespace": namespace, "drop_sandbox_message_index": False}
        if sandbox_message_index is not None:
            kwargs["sandbox_message_index"] = sandbox_message_index
        dataframe = context.get_database(**kwargs)
        namespaces[enum_name(namespace)] = [json_safe(row) for row in rows_from_dataframe(dataframe)]
    return {"namespaces": namespaces}

def initial_state_from_context(context: object, module_loader: object) -> dict[str, JsonValue]:
    """读取 ToolSandbox 初始状态，避免动态 timestamp 使用过期离线 JSON。"""
    first_user_index = getattr(context, "first_user_sandbox_message_index", None)
    return state_from_context(context, module_loader, sandbox_message_index=first_user_index if isinstance(first_user_index, int) else None, include_sandbox=True)

def snapshots_from_context(context: object, steps: list[dict[str, JsonValue]], module_loader: object) -> list[dict[str, JsonValue]]:
    if not steps:
        return []
    sandbox_indexes = [int(step["raw_sandbox_message_index"]) for step in steps if isinstance(step.get("raw_sandbox_message_index"), int)]
    if not sandbox_indexes:
        return []
    step_by_sandbox_index = {int(step["raw_sandbox_message_index"]): step for step in steps if isinstance(step.get("raw_sandbox_message_index"), int)}
    snapshots: list[dict[str, JsonValue]] = []
    for sandbox_index in sorted(set(sandbox_indexes)):
        step = step_by_sandbox_index[sandbox_index]
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
