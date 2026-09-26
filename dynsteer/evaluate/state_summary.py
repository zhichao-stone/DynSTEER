from dynsteer.model import JsonObject, TaskCase

def state_namespace_summary(state: JsonObject) -> JsonObject:
    """Generates a summary of the namespace line number, avoiding the diagnostic summary from embedding the full state. Args: state: benchmark active date JSON, expected to contain namespaces fields. Returns: summary of the number of lines per namespace."""
    if state is None:
        raise ValueError('State cannot be empty.')
    namespaces = state.get("namespaces")
    if not isinstance(namespaces, dict):
        return {"namespace_count": 0, "row_counts": {}}
    row_counts = {str(namespace): len(rows) for namespace, rows in namespaces.items() if isinstance(rows, list)}
    return {"namespace_count": len(namespaces), "row_counts": row_counts}


def apply_runtime_initial_state(
    task_case: TaskCase,
    state: JsonObject | None,
    source: str,
) -> JsonObject | None:
    """Writes the initial state of the runtime and returns a light summary."""
    if state is None:
        task_case.metadata.setdefault("runtime_initial_state_source", "adapted_case")
        return None
    task_case.initial_state = state
    task_case.metadata["runtime_initial_state_source"] = source
    summary = state_namespace_summary(state)
    task_case.metadata["runtime_initial_state_summary"] = summary
    return summary
