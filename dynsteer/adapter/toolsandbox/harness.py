from __future__ import annotations

import importlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.adapter.generic import load_task_case, load_trajectory
from dynsteer.harness.model import BenchmarkCase, HarnessRunConfig, HarnessRunResult
from dynsteer.model import JsonObject, JsonValue, StateSnapshot, TaskCase, TrajectoryStep

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


@dataclass
class ToolSandboxSession:
    """ToolSandbox 原生执行 session。"""

    scenario: object
    roles: dict[object, object]
    context: object | None
    case_id: str
    run_id: str
    raw_output_dir: Path
    last_sandbox_message_index: int = -1
    finished: bool = False
    stop_reason: str | None = None


class ToolSandboxHarness(BaseBenchmarkHarness):
    """ToolSandbox benchmark harness 适配器。"""

    benchmark = "toolsandbox"

    # override 基类的函数实现

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

    def _start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> ToolSandboxSession:
        """初始化 ToolSandbox 原生 session。

        Args:
            config: harness 运行配置。
            case_id: ToolSandbox 场景 ID。
            raw_output_dir: 原生输出目录。

        Returns:
            ToolSandbox session。
        """
        if raw_output_dir is None:
            raise ValueError("raw_output_dir 不能为空")
        scenarios = self._named_scenarios(config)
        if case_id not in scenarios:
            raise KeyError(f"ToolSandbox 场景不存在: {case_id}")
        scenario = scenarios[case_id]
        roles = self._toolsandbox_roles(config)
        run_id = raw_output_dir.parent.parent.name
        context = self._starting_context_from_scenario(scenario, roles, raw_output_dir, case_id)
        last_index = self._max_sandbox_message_index(context)
        return ToolSandboxSession(
            scenario=scenario,
            roles=roles,
            context=context,
            case_id=case_id,
            run_id=run_id,
            raw_output_dir=raw_output_dir,
            last_sandbox_message_index=last_index,
        )

    def _task_case_from_session(self, session: object) -> TaskCase:
        """从 ToolSandbox session 构造 DynSTEER 任务定义。"""
        if not isinstance(session, ToolSandboxSession):
            raise TypeError("session 必须是 ToolSandboxSession")
        rows = self._sandbox_rows_from_context(session.context)
        steps = self.convert_sandbox_rows_to_steps(rows)
        task_id = f"toolsandbox::{session.case_id}"
        return load_task_case(
            {
                "task_id": task_id,
                "task_description": self._task_description_from_steps(steps, session.case_id),
                "task_types": self._task_types_from_categories(getattr(session.scenario, "categories", [])),
                "environment_schema": {"source": "toolsandbox"},
                "tool_schema": {"source": "toolsandbox"},
                "initial_state": self._initial_state_from_context(session.context) if session.context is not None else {},
                "metadata": {
                    "benchmark": "toolsandbox",
                    "scenario_name": session.case_id,
                    "categories": [_enum_name(item) for item in getattr(session.scenario, "categories", [])],
                },
                "milestone_graph": self._milestone_graph_from_scenario(session.scenario),
            }
        )

    def _advance_case(self, session: object) -> list[TrajectoryStep]:
        """推进 ToolSandbox 一个可中断执行批次并返回新增步骤。"""
        if not isinstance(session, ToolSandboxSession):
            raise TypeError("session 必须是 ToolSandboxSession")
        if session.finished:
            return []
        self._advance_native_session(session)
        rows = [
            row
            for row in self._sandbox_rows_from_context(session.context)
            if self._sandbox_message_index(row) > session.last_sandbox_message_index
        ]
        if rows:
            session.last_sandbox_message_index = max(self._sandbox_message_index(row) for row in rows)
        steps = self.convert_sandbox_rows_to_steps(rows)
        if self._native_session_finished(session):
            session.finished = True
        return load_trajectory(
            {
                "run_id": session.run_id,
                "task_id": f"toolsandbox::{session.case_id}",
                "steps": steps,
            }
        ).steps

    def _case_finished(self, session: object) -> bool:
        """判断 ToolSandbox session 是否自然完成。"""
        if not isinstance(session, ToolSandboxSession):
            raise TypeError("session 必须是 ToolSandboxSession")
        return session.finished

    def _snapshots_from_session(self, session: object) -> list[StateSnapshot]:
        """提取 ToolSandbox 当前状态快照。"""
        if not isinstance(session, ToolSandboxSession):
            raise TypeError("session 必须是 ToolSandboxSession")
        if session.context is None:
            return []
        rows = self._sandbox_rows_from_context(session.context)
        steps = self.convert_sandbox_rows_to_steps(rows)
        snapshot_data = self._snapshots_from_context(session.context, steps)
        return load_trajectory(
            {
                "run_id": session.run_id,
                "task_id": f"toolsandbox::{session.case_id}",
                "steps": steps,
                "snapshots": snapshot_data,
            }
        ).snapshots

    def _metrics_from_session(self, session: object) -> JsonObject:
        """返回 ToolSandbox 运行期 metrics。"""
        if not isinstance(session, ToolSandboxSession):
            raise TypeError("session 必须是 ToolSandboxSession")
        return {"native_evaluation_skipped": True}

    def _raw_summary_from_session(self, session: object) -> JsonObject:
        """返回 ToolSandbox 原生摘要。"""
        if not isinstance(session, ToolSandboxSession):
            raise TypeError("session 必须是 ToolSandboxSession")
        return {"native_evaluation_skipped": True, "case_id": session.case_id}

    def _stop_case(self, session: object, reason: str) -> None:
        """按 DynSTEER 策略终止 ToolSandbox session。"""
        if not isinstance(session, ToolSandboxSession):
            raise TypeError("session 必须是 ToolSandboxSession")
        if not reason:
            raise ValueError("reason 不能为空")
        session.finished = True
        session.stop_reason = reason

    def _teardown_case(self, session: object) -> None:
        """释放 ToolSandbox role 资源。"""
        if not isinstance(session, ToolSandboxSession):
            return
        for role in session.roles.values():
            teardown = getattr(role, "teardown", None)
            if callable(teardown):
                teardown()


    # ToolSandboxHarness 独有函数实现

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
            return {"benchmark": self.benchmark, "source_root": None, "tool_backend": "DEFAULT"}
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
        tool_backend = self._tool_backend(config)
        scenarios = scenarios_module.named_scenarios(preferred_tool_backend=tool_backend)
        if not isinstance(scenarios, dict):
            raise ValueError("ToolSandbox named_scenarios 必须返回字典")
        return scenarios

    def _tool_backend(self, config: HarnessRunConfig) -> object:
        """读取 ToolSandbox 工具后端配置。"""
        if config is None:
            raise ValueError("config 不能为空")
        manifest = self._load_manifest(config.data_root)
        raw_backend = config.metadata.get("tool_backend", manifest.get("tool_backend", "DEFAULT"))
        if not isinstance(raw_backend, str) or not raw_backend.strip():
            raise ValueError("ToolSandbox tool_backend 不能为空")
        discovery_module = self._import_module("tool_sandbox.common.tool_discovery")
        tool_backend_type = getattr(discovery_module, "ToolBackend")
        backend_name = raw_backend.strip()
        try:
            return tool_backend_type[backend_name]
        except KeyError:
            try:
                return tool_backend_type(backend_name)
            except ValueError as exc:
                raise ValueError(f"不支持的 ToolSandbox tool_backend: {backend_name}") from exc

    def _role_impl_type(self, role_name: object, role_label: str) -> object:
        cli_utils = self._import_module("tool_sandbox.cli.utils")
        role_impl_type = getattr(cli_utils, "RoleImplType")
        if not isinstance(role_name, str) or not role_name.strip():
            raise ValueError(f"ToolSandbox run_config.json 必须提供 {role_label}")
        effective_name = role_name.strip()
        try:
            return role_impl_type[effective_name]
        except KeyError:
            try:
                return role_impl_type(effective_name)
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
        agent_type = self._role_impl_type(config.metadata.get("agent"), "agent")
        user_type = self._role_impl_type(config.metadata.get("user"), "user")
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

    def _starting_context_from_scenario(
        self,
        scenario: object,
        roles: dict[object, object],
        raw_output_dir: Path,
        case_id: str,
    ) -> object | None:
        """从 ToolSandbox scenario 获取起始 context。"""
        for name in ("starting_context", "get_starting_context", "create_context"):
            candidate = getattr(scenario, name, None)
            if callable(candidate):
                return self._call_native(candidate, roles=roles, output_directory=raw_output_dir, scenario_name=case_id)
            if candidate is not None:
                return candidate
        return None

    def _advance_native_session(self, session: ToolSandboxSession) -> None:
        """调用 ToolSandbox 原生单步执行入口。"""
        for name in ("advance", "step", "play"):
            candidate = getattr(session.scenario, name, None)
            if not callable(candidate):
                continue
            result = self._call_native(
                candidate,
                roles=session.roles,
                context=session.context,
                output_directory=session.raw_output_dir,
                scenario_name=session.case_id,
            )
            self._apply_native_result(session, result)
            return
        if not self._respond_roles_once(session):
            session.finished = True

    def _respond_roles_once(self, session: ToolSandboxSession) -> bool:
        """按 role respond 接口推进一次 ToolSandbox 对话。

        Args:
            session: ToolSandbox session。

        Returns:
            至少调用过一个 role respond 时返回 True。
        """
        if session is None:
            raise ValueError("session 不能为空")
        responded = False
        for role in session.roles.values():
            respond = getattr(role, "respond", None)
            if not callable(respond):
                continue
            result = self._call_native(
                respond,
                roles=session.roles,
                context=session.context,
                output_directory=session.raw_output_dir,
                scenario_name=session.case_id,
            )
            self._apply_native_result(session, result)
            responded = True
            if self._native_session_finished(session):
                break
        return responded

    def _apply_native_result(self, session: ToolSandboxSession, result: object) -> None:
        """将原生 advance/play 返回值合并回 session。"""
        if result is None:
            return
        ending_context = getattr(result, "ending_context", None)
        context = getattr(result, "context", ending_context)
        if context is None and not isinstance(result, (str, int, float, bool, list, tuple, dict)):
            context = result
        if context is not None:
            session.context = context
        finished = getattr(result, "finished", None)
        if isinstance(finished, bool):
            session.finished = finished
        if ending_context is not None:
            session.finished = True

    def _call_native(self, func: object, **kwargs: object) -> object:
        """按常见 ToolSandbox 参数名调用原生函数。"""
        if not callable(func):
            raise TypeError("func 必须可调用")
        attempts = [
            kwargs,
            {key: value for key, value in kwargs.items() if key != "context"},
            {key: value for key, value in kwargs.items() if key in {"roles", "context"}},
            {key: value for key, value in kwargs.items() if key == "roles"},
            {},
        ]
        last_error: TypeError | None = None
        for candidate_kwargs in attempts:
            try:
                return func(**candidate_kwargs)
            except TypeError as exc:
                last_error = exc
        raise last_error if last_error is not None else TypeError("无法调用 ToolSandbox 原生函数")

    def _native_session_finished(self, session: ToolSandboxSession) -> bool:
        """读取原生 scenario/session 的完成标记。"""
        if session.finished:
            return True
        for source in (session.scenario, session.context):
            if source is None:
                continue
            for name in ("finished", "is_finished", "done"):
                value = getattr(source, name, None)
                if callable(value):
                    value = value()
                if isinstance(value, bool) and value:
                    return True
        return False

    def _sandbox_message_index(self, row: dict[str, object]) -> int:
        """读取 ToolSandbox 行的 sandbox_message_index。"""
        value = row.get("sandbox_message_index")
        if isinstance(value, int):
            return value
        return -1

    def _max_sandbox_message_index(self, context: object | None) -> int:
        """读取当前 context 中最大的 sandbox_message_index。"""
        rows = self._sandbox_rows_from_context(context)
        return max((self._sandbox_message_index(row) for row in rows), default=-1)

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

    def _sandbox_rows_from_context(self, context: object | None) -> list[dict[str, object]]:
        if context is None:
            return []
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
