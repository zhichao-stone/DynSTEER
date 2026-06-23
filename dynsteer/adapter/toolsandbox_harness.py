from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from dynsteer.adapter.generic import load_task_case, load_trajectory
from dynsteer.harness.model import BenchmarkCase, HarnessRunConfig, HarnessRunResult
from dynsteer.model import JsonObject, JsonValue

TOOL_SANDBOX_DEPENDENCY_ERROR = (
    "ToolSandbox harness 需要安装 ToolSandbox 及其依赖。"
    "请确认 data/toolsandbox/benchmark.json 的 source_root 可导入，"
    "或在当前 uv 环境安装 ToolSandbox。"
)


def _enum_name(value: object) -> str:
    if value is None:
        return ""
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name.upper()
    raw = str(value)
    if "." in raw:
        raw = raw.rsplit(".", 1)[-1]
    return raw.upper()


def _json_safe(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    enum_value = getattr(value, "value", None)
    if enum_value is not None and isinstance(enum_value, (str, int, float, bool)):
        return enum_value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


class ToolSandboxHarness:
    """ToolSandbox benchmark harness 适配器。"""

    benchmark = "toolsandbox"

    def _validate_config(self, config: HarnessRunConfig) -> None:
        """校验 ToolSandbox harness 配置。

        Args:
            config: harness 运行配置。
        """
        if config is None:
            raise ValueError("config 不能为空")
        if config.benchmark != self.benchmark:
            raise ValueError("benchmark 必须是 toolsandbox")
        if config.data_root is None:
            raise ValueError("data_root 不能为空")

    def _load_manifest(self, data_root: Path) -> dict[str, object]:
        """加载 ToolSandbox 静态 manifest。

        Args:
            data_root: benchmark 静态配置目录。

        Returns:
            manifest 字典；文件不存在时返回默认配置。
        """
        if data_root is None:
            raise ValueError("data_root 不能为空")
        manifest_path = data_root / "benchmark.json"
        if not manifest_path.exists():
            return {"source_root": None}
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"ToolSandbox benchmark.json 不是合法 JSON: {manifest_path}") from exc
        if not isinstance(data, dict):
            raise ValueError("ToolSandbox benchmark.json 必须是 JSON 对象")
        return data

    def _ensure_source_root(self, data_root: Path) -> None:
        """按 manifest 将外部 ToolSandbox 源码目录加入导入路径。

        Args:
            data_root: benchmark 静态配置目录。
        """
        manifest = self._load_manifest(data_root)
        raw_source_root = manifest.get("source_root")
        if raw_source_root is None:
            return
        source_root = Path(str(raw_source_root))
        if not source_root.is_absolute():
            source_root = data_root / source_root
        if not source_root.exists():
            raise FileNotFoundError(f"ToolSandbox source_root 不存在: {source_root}")
        source_text = str(source_root.resolve())
        if source_text not in sys.path:
            sys.path.insert(0, source_text)

    def _import_module(self, module_name: str) -> Any:
        """懒加载 ToolSandbox 模块。

        Args:
            module_name: 需要导入的模块名。

        Returns:
            Python 模块对象。
        """
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            raise ImportError(TOOL_SANDBOX_DEPENDENCY_ERROR) from exc

    def _named_scenarios(self, config: HarnessRunConfig) -> dict[str, Any]:
        """获取 ToolSandbox 原生场景字典。

        Args:
            config: harness 运行配置。

        Returns:
            场景名到场景对象的映射。
        """
        self._validate_config(config)
        self._ensure_source_root(config.data_root)
        scenarios_module = self._import_module("tool_sandbox.scenarios")
        discovery_module = self._import_module("tool_sandbox.common.tool_discovery")
        tool_backend = getattr(discovery_module, "ToolBackend").DEFAULT
        scenarios = scenarios_module.named_scenarios(preferred_tool_backend=tool_backend)
        if not isinstance(scenarios, dict):
            raise ValueError("ToolSandbox named_scenarios 必须返回字典")
        return scenarios

    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        """列出 ToolSandbox 场景。

        Args:
            config: harness 运行配置。

        Returns:
            ToolSandbox 场景列表。
        """
        scenarios = self._named_scenarios(config)
        cases: list[BenchmarkCase] = []
        for case_id, scenario in sorted(scenarios.items()):
            categories = [_enum_name(item) for item in getattr(scenario, "categories", [])]
            cases.append(
                BenchmarkCase(
                    benchmark=self.benchmark,
                    case_id=str(case_id),
                    categories=categories,
                    metadata={"source": "toolsandbox"},
                )
            )
        return cases

    def _role_impl_type(self, role_name: str | None, default_name: str) -> object:
        cli_utils = self._import_module("tool_sandbox.cli.utils")
        role_impl_type = getattr(cli_utils, "RoleImplType")
        effective_name = role_name or default_name
        if effective_name is None or not str(effective_name).strip():
            raise ValueError("ToolSandbox role 名称不能为空")
        try:
            return role_impl_type[str(effective_name)]
        except KeyError:
            try:
                return role_impl_type(str(effective_name))
            except ValueError as exc:
                raise ValueError(f"不支持的 ToolSandbox role: {effective_name}") from exc

    def _toolsandbox_roles(self, config: HarnessRunConfig) -> dict[object, object]:
        """创建 ToolSandbox 原生 role。

        Args:
            config: harness 运行配置。

        Returns:
            RoleType 到 role 实例的映射。
        """
        execution_context = self._import_module("tool_sandbox.common.execution_context")
        execution_environment = self._import_module("tool_sandbox.roles.execution_environment")
        cli_utils = self._import_module("tool_sandbox.cli.utils")
        role_type = getattr(execution_context, "RoleType")
        agent_type = self._role_impl_type(config.agent, "GPT_4_o_2024_05_13")
        user_type = self._role_impl_type(config.user, "GPT_4_o_2024_05_13")
        agent_factory = getattr(cli_utils, "AGENT_TYPE_TO_FACTORY").get(agent_type)
        user_factory = getattr(cli_utils, "USER_TYPE_TO_FACTORY").get(user_type)
        if agent_factory is None or user_factory is None:
            raise ValueError("ToolSandbox agent 或 user role 工厂不存在")
        return {
            role_type.USER: user_factory(),
            role_type.EXECUTION_ENVIRONMENT: execution_environment.ExecutionEnvironment(),
            role_type.AGENT: agent_factory(),
        }

    def _run_id(self, config: HarnessRunConfig, case_id: str) -> str:
        raw_run_id = config.metadata.get("run_id")
        if isinstance(raw_run_id, str) and raw_run_id.strip():
            candidate = raw_run_id.strip()
        else:
            candidate = f"{self.benchmark}_{case_id}_run"
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", candidate).strip("._")
        if not safe:
            raise ValueError("run_id 不能为空")
        return safe

    def run_case(self, config: HarnessRunConfig, case_id: str) -> HarnessRunResult:
        """运行 ToolSandbox 场景。

        Args:
            config: harness 运行配置。
            case_id: ToolSandbox 场景 ID。

        Returns:
            转换后的 harness 运行结果。
        """
        if case_id is None or not str(case_id).strip():
            raise ValueError("case_id 不能为空")
        scenarios = self._named_scenarios(config)
        if case_id not in scenarios:
            raise KeyError(f"ToolSandbox 场景不存在: {case_id}")
        scenario = scenarios[case_id]
        roles = self._toolsandbox_roles(config)
        run_id = self._run_id(config, case_id)
        raw_output_dir = config.output_dir / config.benchmark / run_id / case_id / "raw"
        raw_output_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = scenario.play_and_evaluate(
                roles=roles,
                output_directory=raw_output_dir,
                scenario_name=case_id,
            )
            return self._result_from_toolsandbox(
                result=result,
                scenario=scenario,
                case_id=case_id,
                run_id=run_id,
                raw_output_dir=raw_output_dir,
            )
        finally:
            for role in roles.values():
                teardown = getattr(role, "teardown", None)
                if callable(teardown):
                    teardown()

    def _result_from_toolsandbox(
        self,
        result: object,
        scenario: object,
        case_id: str,
        run_id: str,
        raw_output_dir: Path,
    ) -> HarnessRunResult:
        """转换 ToolSandbox play result 为 DynSTEER 运行结果。

        Args:
            result: ToolSandbox 原生 ScenarioResult。
            scenario: ToolSandbox 场景对象。
            case_id: 场景 ID。
            run_id: 本次运行 ID。
            raw_output_dir: 原生输出目录。

        Returns:
            harness 运行结果。
        """
        if result is None:
            raise ValueError("ToolSandbox result 不能为空")
        ending_context = getattr(result, "ending_context", None)
        evaluation_result = getattr(result, "evaluation_result", None)
        if ending_context is None or evaluation_result is None:
            raise ValueError("ToolSandbox result 缺少 ending_context 或 evaluation_result")
        sandbox_rows = self._sandbox_rows_from_context(ending_context)
        steps = self.convert_sandbox_rows_to_steps(sandbox_rows)
        task_id = f"toolsandbox::{case_id}"
        graph_data = self._milestone_graph_from_scenario(scenario)
        task_case = load_task_case(
            {
                "task_id": task_id,
                "task_description": self._task_description_from_steps(steps, case_id),
                "task_types": self._task_types_from_categories(getattr(scenario, "categories", [])),
                "environment_schema": {"source": "toolsandbox"},
                "tool_schema": {"source": "toolsandbox"},
                "initial_state": self._initial_state_from_context(ending_context),
                "metadata": {
                    "benchmark": "toolsandbox",
                    "scenario_name": case_id,
                    "categories": [_enum_name(item) for item in getattr(scenario, "categories", [])],
                },
                "milestone_graph": graph_data,
            }
        )
        trajectory = load_trajectory(
            {
                "run_id": run_id,
                "task_id": task_id,
                "steps": steps,
                "snapshots": self._snapshots_from_context(ending_context, steps),
                "metrics": {
                    "toolsandbox_similarity": getattr(evaluation_result, "similarity", None),
                    "toolsandbox_milestone_similarity": getattr(evaluation_result, "milestone_similarity", None),
                    "toolsandbox_minefield_similarity": getattr(evaluation_result, "minefield_similarity", None),
                    "toolsandbox_turn_count": getattr(evaluation_result, "turn_count", None),
                },
            }
        )
        return HarnessRunResult(
            benchmark=self.benchmark,
            case_id=case_id,
            run_id=run_id,
            task_case=task_case,
            trajectory=trajectory,
            raw_output_dir=raw_output_dir,
            raw_summary={
                "similarity": getattr(evaluation_result, "similarity", None),
                "milestone_similarity": getattr(evaluation_result, "milestone_similarity", None),
                "minefield_similarity": getattr(evaluation_result, "minefield_similarity", None),
                "turn_count": getattr(evaluation_result, "turn_count", None),
                "milestone_mapping": _json_safe(getattr(evaluation_result, "milestone_mapping", {})),
                "minefield_mapping": _json_safe(getattr(evaluation_result, "minefield_mapping", {})),
            },
        )

    def _role_to_actor(self, sender: object, recipient: object) -> str:
        sender_name = _enum_name(sender)
        recipient_name = _enum_name(recipient)
        if sender_name == "SYSTEM":
            return "system"
        if sender_name == "USER":
            return "user"
        if sender_name == "AGENT":
            return "agent"
        if sender_name == "EXECUTION_ENVIRONMENT":
            return "environment"
        if recipient_name == "AGENT":
            return "environment"
        return "agent"

    def _tool_trace_from_row(self, row: dict[str, object]) -> dict[str, JsonValue] | None:
        raw_trace = row.get("tool_trace")
        if raw_trace is None:
            return None
        trace_items = list(raw_trace) if isinstance(raw_trace, list) else [raw_trace]
        if not trace_items:
            return None
        first = trace_items[0]
        if first is None:
            return None
        if isinstance(first, dict):
            return _json_safe(first)  # type: ignore[return-value]
        try:
            trace = json.loads(str(first))
        except json.JSONDecodeError as exc:
            raise ValueError("ToolSandbox tool_trace 不是合法 JSON") from exc
        if not isinstance(trace, dict):
            return None
        return _json_safe(trace)  # type: ignore[return-value]

    def _tool_call_from_agent_row(
        self,
        row: dict[str, object],
        trace: dict[str, JsonValue] | None,
    ) -> JsonObject | None:
        """从 Agent 发给执行环境的行中提取工具调用。

        Args:
            row: ToolSandbox SANDBOX 行。
            trace: 当前行已有的 tool_trace。

        Returns:
            DynSTEER tool_call 字典；无法识别名称时返回 None。
        """
        if trace is not None and isinstance(trace.get("tool_name"), str) and trace.get("tool_name"):
            return {
                "name": str(trace["tool_name"]),
                "arguments": trace.get("arguments") if isinstance(trace.get("arguments"), dict) else {},
            }
        if isinstance(row.get("openai_function_name"), str) and row.get("openai_function_name"):
            return {"name": str(row["openai_function_name"]), "arguments": {}}
        content = row.get("content")
        if not isinstance(content, str):
            return None
        match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", content)
        if match is None:
            return None
        return {"name": match.group(1), "arguments": {}}

    def convert_sandbox_rows_to_steps(self, rows: list[dict[str, object]]) -> list[dict[str, JsonValue]]:
        """将 ToolSandbox SANDBOX 行转换为 DynSTEER trajectory steps。

        Args:
            rows: ToolSandbox SANDBOX 数据库行。

        Returns:
            DynSTEER trajectory step 字典列表。
        """
        if rows is None:
            raise ValueError("rows 不能为空")
        steps: list[dict[str, JsonValue]] = []
        for row in rows:
            sender = row.get("sender")
            recipient = row.get("recipient")
            trace = self._tool_trace_from_row(row)
            actor = self._role_to_actor(sender, recipient)
            event_type = "message"
            tool_call: JsonObject | None = None
            tool_result: JsonObject | None = None
            if _enum_name(sender) == "AGENT" and _enum_name(recipient) == "EXECUTION_ENVIRONMENT":
                event_type = "tool_call"
                tool_call = self._tool_call_from_agent_row(row, trace)
            elif _enum_name(sender) == "EXECUTION_ENVIRONMENT" and _enum_name(recipient) == "AGENT":
                event_type = "tool_result"
                tool_result = {
                    "success": row.get("tool_call_exception") is None,
                    "content": trace.get("result") if trace is not None else _json_safe(row.get("content")),
                    "exception": row.get("tool_call_exception") if isinstance(row.get("tool_call_exception"), str) else None,
                }
            steps.append(
                {
                    "step_id": f"s{len(steps)}",
                    "index": len(steps),
                    "actor": actor,
                    "event_type": event_type,
                    "content": row.get("content") if isinstance(row.get("content"), str) else None,
                    "tool_call": tool_call,
                    "tool_result": tool_result,
                    "raw_sandbox_message_index": _json_safe(row.get("sandbox_message_index")),
                }
            )
        return steps

    def _sandbox_rows_from_context(self, context: object) -> list[dict[str, object]]:
        execution_context = self._import_module("tool_sandbox.common.execution_context")
        database_namespace = getattr(execution_context, "DatabaseNamespace")
        dataframe = context.get_database(
            database_namespace.SANDBOX,
            get_all_history_snapshots=True,
            drop_sandbox_message_index=False,
        )
        return self._rows_from_dataframe(dataframe)

    def _task_description_from_steps(self, steps: list[dict[str, JsonValue]], fallback: str) -> str:
        for step in steps:
            if step.get("actor") == "user" and isinstance(step.get("content"), str):
                return str(step["content"])
        return fallback

    def _task_types_from_categories(self, categories: list[object]) -> list[str]:
        names = {_enum_name(item) for item in categories}
        result: list[str] = []
        if "STATE_DEPENDENCY" in names or "SINGLE_TOOL_CALL" in names or "MULTIPLE_TOOL_CALL" in names:
            result.append("stateful_tool_task")
        if "MULTIPLE_USER_TURN" in names:
            result.append("dialogue_interaction_task")
        if "INSUFFICIENT_INFORMATION" in names:
            result.append("safety_sensitive_task")
        if not result:
            result.append("stateful_tool_task")
        return result

    def _rows_from_dataframe(self, dataframe: object | None) -> list[dict[str, object]]:
        if dataframe is None:
            return []
        if hasattr(dataframe, "to_dicts"):
            return [dict(row) for row in dataframe.to_dicts()]
        if isinstance(dataframe, list):
            return [dict(row) for row in dataframe]
        raise TypeError(f"不支持的 dataframe 类型: {type(dataframe)!r}")

    def _constraint_from_snapshot_constraint(self, constraint_id: str, constraint: object) -> dict[str, JsonValue]:
        namespace = _enum_name(getattr(constraint, "database_namespace", None))
        target_dataframe = getattr(constraint, "target_dataframe", None)
        rows = [_json_safe(row) for row in self._rows_from_dataframe(target_dataframe)]
        snapshot_constraint = getattr(constraint, "snapshot_constraint", None)
        snapshot_constraint_name = getattr(snapshot_constraint, "__name__", str(snapshot_constraint))
        column_measures = getattr(constraint, "column_similarity_measure", None) or {}
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
            "metadata": {
                "toolsandbox": {
                    "database_namespace": namespace,
                    "snapshot_constraint": snapshot_constraint_name,
                    "reference_milestone_node_index": _json_safe(
                        getattr(constraint, "reference_milestone_node_index", None)
                    ),
                    "column_similarity_measure": {
                        str(key): getattr(value, "__name__", str(value))
                        for key, value in dict(column_measures).items()
                    },
                    "guardrail": "guardrail" in snapshot_constraint_name,
                }
            },
        }

    def _milestone_nodes(self, milestone_matcher: object | None) -> list[dict[str, JsonValue]]:
        if milestone_matcher is None:
            return []
        nodes: list[dict[str, JsonValue]] = []
        for milestone_index, milestone in enumerate(getattr(milestone_matcher, "milestones", []) or []):
            constraints = [
                self._constraint_from_snapshot_constraint(f"m{milestone_index}_c{constraint_index}", constraint)
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

    def _minefield_nodes(self, minefield_matcher: object | None) -> list[dict[str, JsonValue]]:
        if minefield_matcher is None:
            return []
        minefields: list[dict[str, JsonValue]] = []
        for minefield_index, minefield in enumerate(getattr(minefield_matcher, "milestones", []) or []):
            constraints = [
                self._constraint_from_snapshot_constraint(f"mf{minefield_index}_c{constraint_index}", constraint)
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

    def _edge_list(self, matcher: object | None, prefix: str) -> list[list[str]]:
        if matcher is None:
            return []
        milestones = list(getattr(matcher, "milestones", []) or [])
        raw_edges = getattr(matcher, "edge_list", None)
        edges = raw_edges if raw_edges is not None else [(index, index + 1) for index in range(len(milestones) - 1)]
        return [[f"{prefix}{source}", f"{prefix}{target}"] for source, target in list(edges or [])]

    def _milestone_graph_from_scenario(self, scenario: object) -> dict[str, JsonValue]:
        """将 ToolSandbox evaluation 转为 DynSTEER milestone graph 字典。

        Args:
            scenario: ToolSandbox 场景对象。

        Returns:
            可被通用 adapter 载入的 milestone graph 字典。
        """
        evaluation = getattr(scenario, "evaluation", None)
        if evaluation is None:
            return {"nodes": [], "edges": [], "minefields": [], "metadata": {"benchmark": "toolsandbox"}}
        milestone_matcher = getattr(evaluation, "milestone_matcher", None)
        minefield_matcher = getattr(evaluation, "minefield_matcher", None)
        return {
            "nodes": self._milestone_nodes(milestone_matcher),
            "edges": self._edge_list(milestone_matcher, "m"),
            "minefields": self._minefield_nodes(minefield_matcher),
            "metadata": {
                "benchmark": "toolsandbox",
                "constraint_semantics": "toolsandbox_custom_metadata",
            },
        }

    def _database_namespaces(self) -> list[object]:
        execution_context = self._import_module("tool_sandbox.common.execution_context")
        database_namespace = getattr(execution_context, "DatabaseNamespace")
        return [namespace for namespace in database_namespace if _enum_name(namespace) != "SANDBOX"]

    def _initial_state_from_context(self, context: object) -> dict[str, JsonValue]:
        namespaces: dict[str, JsonValue] = {}
        first_user_index = getattr(context, "first_user_sandbox_message_index", None)
        for namespace in self._database_namespaces():
            dataframe = context.get_database(namespace=namespace, sandbox_message_index=first_user_index)
            namespaces[_enum_name(namespace)] = [_json_safe(row) for row in self._rows_from_dataframe(dataframe)]
        return {"namespaces": namespaces}

    def _snapshots_from_context(
        self,
        context: object,
        steps: list[dict[str, JsonValue]],
    ) -> list[dict[str, JsonValue]]:
        if not steps:
            return []
        snapshots: list[dict[str, JsonValue]] = []
        for namespace in self._database_namespaces():
            dataframe = context.get_database(
                namespace=namespace,
                get_all_history_snapshots=True,
                drop_sandbox_message_index=False,
            )
            by_index: dict[int, list[JsonObject]] = {}
            for row in self._rows_from_dataframe(dataframe):
                raw_index = row.get("sandbox_message_index")
                if isinstance(raw_index, int):
                    by_index.setdefault(raw_index, []).append(_json_safe(row))  # type: ignore[arg-type]
            for sandbox_index, rows in sorted(by_index.items()):
                step = self._step_for_sandbox_index(steps, sandbox_index)
                snapshots.append(
                    {
                        "snapshot_id": f"{_enum_name(namespace).lower()}:{sandbox_index}",
                        "after_step_id": str(step["step_id"]),
                        "after_step_index": int(step["index"]),
                        "namespaces": {_enum_name(namespace): rows},
                    }
                )
        return snapshots

    def _step_for_sandbox_index(self, steps: list[dict[str, JsonValue]], sandbox_index: int) -> dict[str, JsonValue]:
        selected = steps[0]
        for step in steps:
            raw_index = step.get("raw_sandbox_message_index")
            if isinstance(raw_index, int) and raw_index <= sandbox_index:
                selected = step
        return selected
