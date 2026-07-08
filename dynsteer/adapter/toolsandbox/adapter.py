from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from dynsteer.adapter.base import BaseBenchmarkAdapter, BaseBenchmarkHarness
from dynsteer.adapter.loader import load_trajectory, parse_milestone_graph, parse_task_case
from dynsteer.adapter.utils import callable_spec, ensure_source_root, import_module, load_manifest, rows_from_dataframe
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import (
    Actor,
    EventType,
    JsonObject,
    JsonValue,
    MilestoneGraph,
    StageGoalSemanticKind,
    TaskCase,
    TaskType,
    Trajectory,
    ensure_json_object,
)
from dynsteer.utils import enum_name, json_safe

TOOL_SANDBOX_DEPENDENCY_ERROR = (
    "ToolSandbox harness 需要安装 ToolSandbox 及其依赖。"
    "请确认 data/toolsandbox/benchmark.json 的 source_root 可导入，"
    "或在当前 uv 环境安装 ToolSandbox。"
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _tool_backend(config: HarnessRunConfig, module_loader: object) -> object:
    if config is None:
        raise ValueError("config 不能为空")
    manifest = load_manifest(config.data_root, "toolsandbox")
    raw_backend = config.metadata.get("tool_backend", manifest.get("tool_backend", "DEFAULT"))
    if not isinstance(raw_backend, str) or not raw_backend.strip():
        raise ValueError("ToolSandbox tool_backend 不能为空")
    discovery_module = module_loader("tool_sandbox.common.tool_discovery")
    tool_backend_type = getattr(discovery_module, "ToolBackend")
    backend_name = raw_backend.strip()
    try:
        return tool_backend_type[backend_name]
    except (KeyError, TypeError):
        try:
            return tool_backend_type(backend_name)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"不支持的 ToolSandbox tool_backend: {backend_name}") from exc


def role_to_actor(sender: object, recipient: object) -> str:
    """将 ToolSandbox sender/recipient 转换为 DynSTEER actor。"""
    sender_name = enum_name(sender)
    recipient_name = enum_name(recipient)
    if sender_name == "SYSTEM":
        return Actor.SYSTEM.value
    if sender_name == "USER":
        return Actor.USER.value
    if sender_name == "AGENT":
        return Actor.AGENT.value
    if sender_name == "EXECUTION_ENVIRONMENT":
        return Actor.ENVIRONMENT.value
    if recipient_name == "AGENT":
        return Actor.ENVIRONMENT.value
    return Actor.AGENT.value


def tool_trace_from_row(row: dict[str, object]) -> dict[str, JsonValue] | None:
    """从 ToolSandbox SANDBOX 行读取 tool_trace。"""
    raw_trace = row.get("tool_trace")
    if raw_trace is None:
        return None
    trace_items = list(raw_trace) if isinstance(raw_trace, list) else [raw_trace]
    if not trace_items or trace_items[0] is None:
        return None
    first = trace_items[0]
    if isinstance(first, dict):
        return json_safe(first)  # type: ignore[return-value]
    try:
        trace = json.loads(str(first))
    except json.JSONDecodeError as exc:
        raise ValueError("ToolSandbox tool_trace 不是合法 JSON") from exc
    if not isinstance(trace, dict):
        return None
    return json_safe(trace)  # type: ignore[return-value]


def tool_arguments_from_agent_content(content: object) -> JsonObject:
    """从 ToolSandbox agent 代码片段中解析工具调用参数。

    Args:
        content: SANDBOX 行中的 agent 代码文本。

    Returns:
        JSON 可序列化的工具参数；无法解析时返回空字典。
    """
    if not isinstance(content, str) or not content.strip():
        return {}
    # ToolSandbox agent content 常以 *_parameters = {...} 形式记录工具参数。
    match = re.search(
        r"[A-Za-z_][A-Za-z0-9_]*_parameters\s*=\s*(\{.*?\})(?:\r?\n|$)",
        content,
        flags=re.DOTALL,
    )
    if match is None:
        return {}
    # 使用 literal_eval 解析 Python 字面量，避免执行 agent content 中的任意代码。
    try:
        parsed = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    safe = json_safe(parsed)
    return safe if isinstance(safe, dict) else {}


def tool_call_from_agent_row(row: dict[str, object], trace: dict[str, JsonValue] | None) -> JsonObject | None:
    """从 Agent 发给执行环境的 SANDBOX 行中提取工具调用。

    Args:
        row: ToolSandbox SANDBOX 消息行。
        trace: 从 tool_trace 字段解析出的结构化工具轨迹。

    Returns:
        DynSTEER tool_call JSON；无法识别工具调用时返回 None。
    """
    # agent content 中的参数可补足 tool_trace 或 openai function 字段缺失的问题。
    parsed_arguments = tool_arguments_from_agent_content(row.get("content"))
    # 优先使用 tool_trace 中的工具名，因为它最接近执行环境真实调用。
    if trace is not None and isinstance(trace.get("tool_name"), str) and trace.get("tool_name"):
        arguments = trace.get("arguments")
        return {
            "name": str(trace["tool_name"]),
            "arguments": arguments if isinstance(arguments, dict) else parsed_arguments,
        }
    # 其次使用 OpenAI function name，并复用从 agent content 中解析出的参数。
    if isinstance(row.get("openai_function_name"), str) and row.get("openai_function_name"):
        return {"name": str(row["openai_function_name"]), "arguments": parsed_arguments}
    content = row.get("content")
    if not isinstance(content, str):
        return None
    # 最后从代码文本中兜底提取函数名。
    match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", content)
    if match is None:
        return None
    return {"name": match.group(1), "arguments": parsed_arguments}


def sandbox_message_index(row: dict[str, object]) -> int:
    """读取 ToolSandbox 行的 sandbox_message_index。"""
    value = row.get("sandbox_message_index")
    if isinstance(value, int):
        return value
    return -1


def sandbox_rows_to_step_dicts(rows: list[dict[str, object]]) -> list[dict[str, JsonValue]]:
    """将 ToolSandbox SANDBOX 行转换为 DynSTEER trajectory step 字典。"""
    if rows is None:
        raise ValueError("rows 不能为空")
    steps: list[dict[str, JsonValue]] = []
    for row in rows:
        raw_index = sandbox_message_index(row)
        step_index = raw_index if raw_index >= 0 else len(steps)
        sender = row.get("sender")
        recipient = row.get("recipient")
        trace = tool_trace_from_row(row)
        actor = role_to_actor(sender, recipient)
        event_type = EventType.MESSAGE.value
        tool_call: JsonObject | None = None
        tool_result: JsonObject | None = None
        if enum_name(sender) == "AGENT" and enum_name(recipient) == "EXECUTION_ENVIRONMENT":
            event_type = EventType.TOOL_CALL.value
            tool_call = tool_call_from_agent_row(row, trace)
        elif enum_name(sender) == "EXECUTION_ENVIRONMENT" and enum_name(recipient) == "AGENT":
            event_type = EventType.TOOL_RESULT.value
            tool_result = {
                "success": row.get("tool_call_exception") is None,
                "content": trace.get("result") if trace is not None else json_safe(row.get("content")),
                "exception": row.get("tool_call_exception") if isinstance(row.get("tool_call_exception"), str) else None,
            }
        steps.append(
            {
                "step_id": f"s{step_index}",
                "index": step_index,
                "actor": actor,
                "event_type": event_type,
                "content": row.get("content") if isinstance(row.get("content"), str) else None,
                "tool_call": tool_call,
                "tool_result": tool_result,
                "raw_sandbox_message_index": json_safe(row.get("sandbox_message_index")),
                "sender": enum_name(sender),
                "recipient": enum_name(recipient),
                "openai_tool_call_id": json_safe(row.get("openai_tool_call_id")),
                "openai_function_name": json_safe(row.get("openai_function_name")),
                "visible_to": json_safe(row.get("visible_to")),
            }
        )
    return steps


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


def sandbox_rows_from_context(
    context: object | None,
    module_loader: object,
    *,
    get_all_history_snapshots: bool = True,
) -> list[dict[str, object]]:
    """从 context 读取 SANDBOX 行。"""
    if context is None:
        return []
    database_namespace = _database_namespace(module_loader)
    dataframe = context.get_database(
        _sandbox_namespace(database_namespace),
        get_all_history_snapshots=get_all_history_snapshots,
        drop_sandbox_message_index=False,
    )
    return rows_from_dataframe(dataframe)


def task_description_from_steps(
    steps: list[dict[str, JsonValue]],
    fallback: str,
    first_user_sandbox_message_index: int | None = None,
) -> str:
    """从首条用户消息读取 task_description。"""
    if steps is None or fallback is None:
        raise ValueError("steps 和 fallback 不能为空")
    if first_user_sandbox_message_index is not None:
        for step in steps:
            if (
                step.get("actor") == Actor.USER.value
                and step.get("raw_sandbox_message_index") == first_user_sandbox_message_index
                and isinstance(step.get("content"), str)
            ):
                return str(step["content"])
    for step in steps:
        if (
            step.get("actor") == Actor.USER.value
            and isinstance(step.get("content"), str)
            and not _visible_only_to_user_simulator(step.get("visible_to"))
        ):
            return str(step["content"])
    for step in steps:
        if step.get("actor") == Actor.USER.value and isinstance(step.get("content"), str):
            return str(step["content"])
    return fallback


def task_types_from_categories(categories: list[object]) -> list[TaskType]:
    """将 ToolSandbox categories 转换为 DynSTEER task_types。"""
    names = {enum_name(item) for item in categories}
    result: list[TaskType] = []
    if "STATE_DEPENDENCY" in names or "SINGLE_TOOL_CALL" in names or "MULTIPLE_TOOL_CALL" in names:
        result.append(TaskType.STATEFUL_TOOL)
    if "MULTIPLE_USER_TURN" in names:
        result.append(TaskType.DIALOGUE_INTERACTION)
    if "INSUFFICIENT_INFORMATION" in names:
        result.append(TaskType.SAFETY_SENSITIVE)
    if not result:
        result.append(TaskType.STATEFUL_TOOL)
    return result


def _tool_trace_stage_goal_semantics(row: dict[str, JsonValue]) -> JsonObject | None:
    """从 SANDBOX target row 中解析工具调用 stage_goal 语义。"""
    if row is None:
        raise ValueError("SANDBOX row 不能为空")
    raw_trace = row.get("tool_trace")
    if raw_trace is None:
        return None
    trace_items = _tool_trace_items(raw_trace)
    if not trace_items:
        return None
    trace_value = trace_items[0]
    tool_name = trace_value.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        return None
    arguments = trace_value.get("arguments")
    return {
        "kind": StageGoalSemanticKind.TOOL_CALL.value,
        "tool_name": tool_name.strip(),
        "arguments": arguments if isinstance(arguments, dict) else {},
        "evidence_source": "trajectory_or_structured_scorer",
        "user_visible_required": False,
    }


def _visible_only_to_user_simulator(value: JsonValue) -> bool:
    if not isinstance(value, list) or len(value) != 1:
        return False
    return str(value[0]) == "USER"


def _tool_trace_items(raw_trace: JsonValue) -> list[JsonObject]:
    trace_value = _parse_tool_trace_value(raw_trace)
    if isinstance(trace_value, dict):
        return [trace_value]
    if not isinstance(trace_value, list):
        return []
    items: list[JsonObject] = []
    for item in trace_value:
        parsed_item = _parse_tool_trace_value(item)
        if isinstance(parsed_item, dict):
            items.append(parsed_item)
        elif isinstance(parsed_item, list):
            items.extend(dict(nested) for nested in parsed_item if isinstance(nested, dict))
    return items


def _parse_tool_trace_value(value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return json_safe(parsed)
    return json_safe(value)


def constraint_from_snapshot_constraint(constraint_id: str, constraint: object) -> dict[str, JsonValue]:
    """将 ToolSandbox snapshot constraint 转换为 DynSTEER constraint JSON。"""
    namespace = enum_name(getattr(constraint, "database_namespace", None))
    target_dataframe = getattr(constraint, "target_dataframe", None)
    rows = [json_safe(row) for row in rows_from_dataframe(target_dataframe)]
    snapshot_constraint = getattr(constraint, "snapshot_constraint", None)
    base_snapshot_constraint = getattr(snapshot_constraint, "func", snapshot_constraint)
    snapshot_constraint_name = getattr(base_snapshot_constraint, "__name__", str(base_snapshot_constraint))
    snapshot_constraint_module = getattr(base_snapshot_constraint, "__module__", None)
    partial_keywords = getattr(snapshot_constraint, "keywords", None) or {}
    snapshot_constraint_kwargs = {
        str(key): getattr(value, "__name__", str(value)) if callable(value) else json_safe(value)
        for key, value in dict(partial_keywords).items()
    }
    column_measures = getattr(constraint, "column_similarity_measure", None) or {}
    reference_index = json_safe(getattr(constraint, "reference_milestone_node_index", None))
    # guardrail 表达相对参考 milestone 的状态保持，不表达目标数据库为空。
    if "guardrail" in snapshot_constraint_name:
        reference = (
            {"type": "milestone_index", "value": reference_index}
            if isinstance(reference_index, int)
            else {"type": "initial_state"}
        )
        stage_goal_semantics = {
            "kind": StageGoalSemanticKind.PRESERVE_STATE.value,
            "namespace": namespace,
            "reference": reference,
            "evidence_source": "structured_scorer",
            "user_visible_required": False,
        }
    # SANDBOX namespace 表达可见消息目标，文本匹配采用语义等价策略。
    elif namespace == "SANDBOX":
        first = rows[0] if rows and isinstance(rows[0], dict) else {}
        tool_trace_semantics = _tool_trace_stage_goal_semantics(first) if isinstance(first, dict) else None
        if tool_trace_semantics is not None:
            stage_goal_semantics = tool_trace_semantics
        else:
            sender = first.get("sender") if isinstance(first, dict) else None
            recipient = first.get("recipient") if isinstance(first, dict) else None
            content = first.get("content") if isinstance(first, dict) else None
            stage_goal_semantics = {
                "kind": StageGoalSemanticKind.EMIT_MESSAGE.value,
                "sender": str(sender or "AGENT"),
                "recipient": str(recipient or "USER"),
                "content": str(content or ""),
                "match_policy": "semantic_equivalent",
                "evidence_source": "trajectory_or_structured_scorer",
                "user_visible_required": True,
            }
    # 其他 snapshot constraint 表达目标 state namespace 的设置或校验。
    else:
        expected = dict(rows[0]) if len(rows) == 1 and isinstance(rows[0], dict) else list(rows)
        stage_goal_semantics = {
            "kind": StageGoalSemanticKind.SET_STATE.value,
            "namespace": namespace,
            "expected": expected,
            "evidence_source": "structured_scorer",
            "user_visible_required": False,
        }
    return {
        "constraint_id": constraint_id,
        "target": "state_snapshot",
        "namespace": namespace,
        "selector": "$",
        "operator": "custom",
        "expected": {"rows": rows, "columns": list(rows[0].keys()) if rows else []},
        "weight": 1.0,
        "threshold": 1.0,
        "hard": True,
        "evaluator_hint": "toolsandbox",
        "stage_goal_semantics": stage_goal_semantics,
        "metadata": {
            "toolsandbox": {
                "database_namespace": namespace,
                "snapshot_constraint": snapshot_constraint_name,
                "snapshot_constraint_module": snapshot_constraint_module,
                "snapshot_constraint_kwargs": snapshot_constraint_kwargs,
                "reference_milestone_node_index": reference_index,
                "column_similarity_measure": {
                    str(key): callable_spec(value)
                    for key, value in dict(column_measures).items()
                },
                "guardrail": "guardrail" in snapshot_constraint_name,
            }
        },
    }


def milestone_nodes(milestone_matcher: object | None) -> list[dict[str, JsonValue]]:
    """将 ToolSandbox milestone matcher 转换为 milestone JSON 节点。"""
    if milestone_matcher is None:
        return []
    nodes: list[dict[str, JsonValue]] = []
    for milestone_index, milestone in enumerate(getattr(milestone_matcher, "milestones", []) or []):
        constraints = [
            constraint_from_snapshot_constraint(f"m{milestone_index}_c{constraint_index}", constraint)
            for constraint_index, constraint in enumerate(getattr(milestone, "snapshot_constraints", []) or [])
        ]
        nodes.append(
            {
                "milestone_id": f"m{milestone_index}",
                "name": f"ToolSandbox milestone {milestone_index}",
                "description": f"ToolSandbox milestone {milestone_index}",
                "constraints": constraints,
                "required": True,
                "metadata": {"toolsandbox": {"milestone_index": milestone_index}},
            }
        )
    return nodes


def minefield_nodes(minefield_matcher: object | None) -> list[dict[str, JsonValue]]:
    """将 ToolSandbox minefield matcher 转换为 minefield JSON 节点。"""
    if minefield_matcher is None:
        return []
    minefields: list[dict[str, JsonValue]] = []
    for minefield_index, minefield in enumerate(getattr(minefield_matcher, "milestones", []) or []):
        constraints = [
            constraint_from_snapshot_constraint(f"mf{minefield_index}_c{constraint_index}", constraint)
            for constraint_index, constraint in enumerate(getattr(minefield, "snapshot_constraints", []) or [])
        ]
        minefields.append(
            {
                "minefield_id": f"mf{minefield_index}",
                "name": f"ToolSandbox minefield {minefield_index}",
                "description": f"ToolSandbox minefield {minefield_index}",
                "severity": "fatal",
                "constraints": constraints,
                "penalty": {"mode": "fixed", "value": 1.0},
                "metadata": {"toolsandbox": {"minefield_index": minefield_index}},
            }
        )
    return minefields


def edge_list(matcher: object | None, prefix: str) -> list[list[str]]:
    """读取 ToolSandbox matcher 边列表。"""
    if matcher is None:
        return []
    milestones = list(getattr(matcher, "milestones", []) or [])
    raw_edges = getattr(matcher, "edge_list", None)
    edges = raw_edges if raw_edges is not None else [(index, index + 1) for index in range(len(milestones) - 1)]
    return [[f"{prefix}{source}", f"{prefix}{target}"] for source, target in list(edges or [])]


def milestone_graph_from_scenario(scenario: object) -> MilestoneGraph:
    """将 ToolSandbox evaluation 转为 DynSTEER milestone graph。"""
    evaluation = getattr(scenario, "evaluation", None)
    if evaluation is None:
        return parse_milestone_graph({"nodes": [], "edges": [], "minefields": [], "metadata": {"benchmark": "toolsandbox"}})
    milestone_matcher = getattr(evaluation, "milestone_matcher", None)
    minefield_matcher = getattr(evaluation, "minefield_matcher", None)
    return parse_milestone_graph(
        {
            "nodes": milestone_nodes(milestone_matcher),
            "edges": edge_list(milestone_matcher, "m"),
            "minefields": minefield_nodes(minefield_matcher),
            "metadata": {
                "benchmark": "toolsandbox",
                "constraint_semantics": "toolsandbox_custom_metadata",
            },
        }
    )


def database_namespaces(module_loader: object, include_sandbox: bool = False) -> list[object]:
    """返回 ToolSandbox 数据库命名空间。"""
    namespaces = _namespace_members(_database_namespace(module_loader))
    if include_sandbox:
        return namespaces
    return [namespace for namespace in namespaces if enum_name(namespace) != "SANDBOX"]


def initial_state_from_context(context: object, module_loader: object) -> dict[str, JsonValue]:
    """从 context 读取首条用户消息前的初始数据库状态。"""
    if context is None:
        raise ValueError("context 不能为空")
    namespaces: dict[str, JsonValue] = {}
    first_user_index = getattr(context, "first_user_sandbox_message_index", None)
    for namespace in database_namespaces(module_loader):
        dataframe = context.get_database(namespace=namespace, sandbox_message_index=first_user_index)
        namespaces[enum_name(namespace)] = [json_safe(row) for row in rows_from_dataframe(dataframe)]
    return {"namespaces": namespaces}


def snapshots_from_context(
    context: object,
    steps: list[dict[str, JsonValue]],
    module_loader: object,
) -> list[dict[str, JsonValue]]:
    """按新增步骤对应 sandbox index 读取可见数据库快照。"""
    if context is None:
        raise ValueError("context 不能为空")
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
                namespace=namespace,
                sandbox_message_index=sandbox_index,
                drop_sandbox_message_index=False,
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


def trajectory_from_sandbox_rows(
    run_id: str,
    task_id: str,
    rows: list[dict[str, object]],
    snapshots: list[dict[str, JsonValue]] | None = None,
) -> Trajectory:
    """将 SANDBOX 行和快照转换为 DynSTEER Trajectory。"""
    if not run_id or not task_id or rows is None:
        raise ValueError("run_id、task_id 和 rows 不能为空")
    return load_trajectory(
        {
            "run_id": run_id,
            "task_id": task_id,
            "steps": sandbox_rows_to_step_dicts(rows),
            "snapshots": snapshots or [],
        }
    )


def load_toolsandbox_experiment(data: JsonObject) -> tuple[TaskCase, Trajectory]:
    """载入 ToolSandbox 风格离线实验数据。"""
    experiment = ensure_json_object(data)
    if experiment.get("task") is not None and experiment.get("trajectory") is not None:
        task_data = dict(ensure_json_object(experiment["task"]))
        task_data.setdefault("case_id", str(task_data.get("task_id", experiment.get("case_id", "toolsandbox-case"))))
        task_case = parse_task_case(task_data)
        trajectory = load_trajectory(ensure_json_object(experiment["trajectory"]))
    else:
        task_id = str(experiment.get("task_id", experiment.get("id", "toolsandbox-task")))
        case_id = str(experiment.get("case_id", experiment.get("id", task_id)))
        task_case = parse_task_case(
            {
                "task_id": task_id,
                "task_description": str(experiment.get("task_description", experiment.get("instruction", ""))),
                "case_id": case_id,
                "task_types": experiment.get("task_types", []),
                "tool_schema": experiment.get("tool_schema", {}),
                "environment_schema": experiment.get("environment_schema", {}),
            }
        )
        messages = experiment.get("conversation", experiment.get("messages", []))
        if not isinstance(messages, list):
            raise ValueError("ToolSandbox conversation/messages 必须是数组")
        steps = []
        for index, item in enumerate(messages):
            message = ensure_json_object(item)
            steps.append(
                {
                    "step_id": str(message.get("step_id", f"s{index}")),
                    "index": int(message.get("index", index)),
                    "actor": _actor_from_role(message.get("role", message.get("actor"))),
                    "event_type": str(message.get("event_type", _event_type_from_message(message))),
                    "content": message.get("content") if isinstance(message.get("content"), str) else None,
                    "tool_call": message.get("tool_call"),
                    "tool_result": message.get("tool_result"),
                }
            )
        trajectory = load_trajectory(
            {
                "run_id": str(experiment.get("run_id", experiment.get("id", "toolsandbox-run"))),
                "task_id": task_id,
                "steps": steps,
                "snapshots": experiment.get("snapshots", []),
                "metrics": experiment.get("metrics", {}),
                "final_state": experiment.get("final_state"),
            }
        )
    graph_data = experiment.get("milestone_graph", experiment.get("milestones"))
    if graph_data is not None:
        task_case.milestone_graph = parse_milestone_graph(ensure_json_object(graph_data))
    return task_case, trajectory


def _event_type_from_message(message: JsonObject) -> str:
    if message.get("tool_call") is not None:
        return EventType.TOOL_CALL.value
    if message.get("tool_result") is not None:
        return EventType.TOOL_RESULT.value
    if message.get("final") is True:
        return EventType.FINAL.value
    return EventType.MESSAGE.value


def _actor_from_role(role: object) -> str:
    if role in {"system", "user", "agent", "environment", "evaluator"}:
        return str(role)
    if role == "assistant":
        return Actor.AGENT.value
    if role == "tool":
        return Actor.ENVIRONMENT.value
    return Actor.AGENT.value


class ToolSandboxAdapter(BaseBenchmarkAdapter):
    """ToolSandbox 数据适配与 harness 工厂。"""

    benchmark = "toolsandbox"

    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase:
        """读取 ToolSandbox 原生 scenario 并转换为 DynSTEER TaskCase。"""
        if config is None or not case_id:
            raise ValueError("config 和 case_id 不能为空")
        ensure_source_root(config.data_root, _project_root(), self.benchmark)
        module_loader = self._module_loader
        scenarios_module = module_loader("tool_sandbox.scenarios")
        scenarios = scenarios_module.named_scenarios(preferred_tool_backend=_tool_backend(config, module_loader))
        if not isinstance(scenarios, dict):
            raise ValueError("ToolSandbox named_scenarios 必须返回字典")
        if case_id not in scenarios:
            raise KeyError(f"ToolSandbox 场景不存在: {case_id}")
        scenario = scenarios[case_id]
        context = getattr(scenario, "starting_context", None)
        if context is None:
            raise ValueError("ToolSandbox scenario 缺少 starting_context")
        rows = sandbox_rows_from_context(context, module_loader)
        steps = sandbox_rows_to_step_dicts(rows)
        graph = milestone_graph_from_scenario(scenario)
        first_user_index = getattr(context, "first_user_sandbox_message_index", None)
        return TaskCase(
            task_id=f"toolsandbox::{case_id}",
            task_description=task_description_from_steps(
                steps,
                case_id,
                first_user_sandbox_message_index=first_user_index if isinstance(first_user_index, int) else None,
            ),
            case_id=case_id,
            environment_schema={"source": "toolsandbox"},
            tool_schema={"source": "toolsandbox"},
            initial_state=initial_state_from_context(context, module_loader),
            milestone_graph=graph,
            task_types=task_types_from_categories(getattr(scenario, "categories", [])),
            metadata={
                "benchmark": "toolsandbox",
                "scenario_name": case_id,
                "categories": [enum_name(item) for item in getattr(scenario, "categories", [])],
            },
        )

    def create_harness(self) -> BaseBenchmarkHarness:
        """创建 ToolSandbox 运行期 harness。"""
        from dynsteer.adapter.toolsandbox.harness import ToolSandboxHarness

        return ToolSandboxHarness()

    def load_experiment(self, data: JsonObject) -> tuple[TaskCase, Trajectory]:
        """兼容离线 ToolSandbox 导出数据解析。"""
        return load_toolsandbox_experiment(data)

    def _module_loader(self, module_name: str) -> object:
        return import_module(module_name, TOOL_SANDBOX_DEPENDENCY_ERROR)
