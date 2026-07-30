from dynsteer.model import JsonObject

def state_namespace_summary(state: JsonObject) -> JsonObject:
    """生成状态 namespace 行数摘要，避免诊断摘要嵌入完整状态。

    入参：
        state: benchmark 运行期状态 JSON，期望包含 namespaces 字段。
    输出：
        每个 namespace 的行数摘要。
    """
    if state is None:
        raise ValueError("state 不能为空")
    namespaces = state.get("namespaces")
    if not isinstance(namespaces, dict):
        return {"namespace_count": 0, "row_counts": {}}
    row_counts = {str(namespace): len(rows) for namespace, rows in namespaces.items() if isinstance(rows, list)}
    return {"namespace_count": len(namespaces), "row_counts": row_counts}
