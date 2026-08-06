from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

from dynsteer.adapter.base import BaseBenchmarkHarness, BenchmarkDefaultResult
from dynsteer.adapter.toolsandbox.scorer import ToolSandboxConstraintScorer
from dynsteer.adapter.toolsandbox.utils.roles import get_agent_factory, get_user_factory, role_client_config
from dynsteer.adapter.toolsandbox.utils.runtime import load_named_scenarios, load_toolsandbox_module
from dynsteer.adapter.toolsandbox.utils.state import initial_state_from_context, snapshots_from_context, state_from_context
from dynsteer.adapter.toolsandbox.utils.trace import sandbox_message_index, sandbox_rows_to_step_dicts
from dynsteer.adapter.toolsandbox.utils.trajectory import trajectory_from_sandbox_rows
from dynsteer.adapter.utils import rows_from_dataframe, retry_call
from dynsteer.harness.model import BenchmarkCase, HarnessAdvanceResult, HarnessRunConfig
from dynsteer.model import JsonObject, ToolSandboxSession
from dynsteer.utils import clamp, enum_name, json_safe


logger = logging.getLogger(__name__)
class ToolSandboxHarness(BaseBenchmarkHarness):
    """ToolSandbox benchmark 原生执行 harness。"""

    benchmark = "toolsandbox"

    def constraint_scorer(self) -> ToolSandboxConstraintScorer:
        """返回 ToolSandbox 专用约束评分器。"""
        return ToolSandboxConstraintScorer(module_loader=load_toolsandbox_module)

    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        """列出 ToolSandbox 场景。"""
        self.prepare_config(config)
        scenarios = self._named_scenarios(config)
        cases: list[BenchmarkCase] = []
        for case_id, scenario in sorted(scenarios.items()):
            categories = [enum_name(item) for item in getattr(scenario, "categories", [])]
            cases.append(BenchmarkCase(benchmark=self.benchmark, case_id=str(case_id), categories=categories, metadata={"source": "toolsandbox"}))
        return cases

    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> ToolSandboxSession:
        """初始化 ToolSandbox 原生 session。"""
        if config is None or raw_output_dir is None or not case_id:
            raise ValueError("config、case_id 和 raw_output_dir 不能为空")
        scenarios = self._named_scenarios(config)
        if case_id not in scenarios:
            raise KeyError(f"ToolSandbox 场景不存在: {case_id}")
        scenario = scenarios[case_id]
        roles = self._toolsandbox_roles(config)
        context = copy.deepcopy(self._starting_context_from_scenario(scenario))
        self._set_current_context(context)
        initial_max = self._context_max_sandbox_message_index(context)
        max_messages = int(config.metadata.get("max_messages", 100))
        if max_messages <= 0:
            raise ValueError("ToolSandbox max_messages 必须大于 0")
        session = ToolSandboxSession(
            scenario=scenario,
            roles=roles,
            context=context,
            initial_state=None,
            case_id=case_id,
            raw_output_dir=raw_output_dir,
            initial_max_sandbox_message_index=initial_max,
            last_sandbox_message_index=initial_max,
            max_messages=max_messages,
        )
        self._prepare_system_environment_messages(session)
        session.initial_state = initial_state_from_context(session.context, load_toolsandbox_module)
        return session

    def advance_case(self, session: object) -> HarnessAdvanceResult:
        """推进一个原生 benchmark 步并返回新增步骤。"""
        session = self._require_session(session)
        if session.finished:
            return HarnessAdvanceResult(steps=[], snapshots=[], continue_running=False, reason="benchmark 已自然完成")
        self._advance_native_session(session)
        all_rows = rows_from_dataframe(
            self._sandbox_database(session.context, get_all_history_snapshots=True)
        )
        rows = [
            row
            for row in all_rows
            if sandbox_message_index(row) > session.last_sandbox_message_index
        ]
        rows.sort(key=sandbox_message_index)
        indexes = [sandbox_message_index(row) for row in rows]
        if len(indexes) != len(set(indexes)):
            seen: set[int] = set()
            duplicates: set[int] = set()
            for index in indexes:
                if index in seen:
                    duplicates.add(index)
                seen.add(index)
            raise ValueError(f"ToolSandbox 新增 SANDBOX rows 包含重复 index: {sorted(duplicates)}")
        steps = sandbox_rows_to_step_dicts(rows)
        snapshot_data = snapshots_from_context(session.context, steps, load_toolsandbox_module) if session.context is not None else []
        trajectory = trajectory_from_sandbox_rows(
            task_id=f"toolsandbox::{session.case_id}",
            steps=steps,
            snapshots=snapshot_data,
        )
        if not trajectory.steps and not session.finished:
            raise RuntimeError("benchmark session 未完成但没有新增轨迹步骤")
        if indexes:
            session.last_sandbox_message_index = max(indexes)
        return HarnessAdvanceResult(
            steps=trajectory.steps,
            snapshots=trajectory.snapshots,
            continue_running=not session.finished,
            reason=session.stop_reason if session.finished else None,
        )

    def metrics_from_session(self, session: object) -> JsonObject:
        """返回 ToolSandbox 运行期 metrics。"""
        self._require_session(session)
        return {"native_evaluation_skipped": True}

    def initial_state_from_session(self, session: object) -> JsonObject | None:
        """返回当前 ToolSandbox session 固化的真实初始状态。"""
        session = self._require_session(session)
        return session.initial_state

    def final_state_from_session(self, session: object) -> JsonObject | None:
        """返回 ToolSandbox 当前 context 的 namespace 状态。"""
        session = self._require_session(session)
        if session.context is None:
            return None
        return state_from_context(session.context, load_toolsandbox_module, include_sandbox=True)

    def raw_summary_from_session(self, session: object) -> JsonObject:
        """返回 ToolSandbox 原生摘要。"""
        session = self._require_session(session)
        return {
            "native_evaluation_skipped": True,
            "case_id": session.case_id,
            "termination_reason": session.termination_reason,
            "termination_detail": session.stop_reason,
        }

    def default_result_from_session(self, session: object) -> BenchmarkDefaultResult:
        """从 ToolSandbox 原生 evaluation 中提取 Default 结果。"""
        session = self._require_session(session)
        if session.scenario is None or session.context is None:
            raise RuntimeError("ToolSandbox session 已释放，无法提取 Default 结果")
        evaluation = getattr(session.scenario, "evaluation", None)
        evaluate = getattr(evaluation, "evaluate", None)
        if not callable(evaluate):
            raise NotImplementedError("ToolSandbox scenario 缺少可调用的原生 evaluation.evaluate")
        try:
            result = evaluate(execution_context=session.context, max_turn_count=session.max_messages)
        except Exception as exc:
            logger.exception(
                "toolsandbox_default_evaluation_failed",
                extra={"事件": "ToolSandbox原生Default评估失败", "case_id": session.case_id, "error": str(exc)},
            )
            raise
        score = clamp(float(getattr(result, "similarity", 0.0)))
        raw: JsonObject = {
            "score_source": "toolsandbox_native_evaluation",
            "case_id": session.case_id,
            "milestone_similarity": clamp(float(getattr(result, "milestone_similarity", 0.0))),
            "minefield_similarity": clamp(float(getattr(result, "minefield_similarity", 0.0))),
            "similarity": score,
            "turn_count": int(getattr(result, "turn_count", 0)),
            "milestone_mapping": {
                str(key): json_safe({"snapshot_index": item[0], "similarity": item[1]}) if isinstance(item, tuple) and len(item) == 2 else json_safe(item)
                for key, item in getattr(result, "milestone_mapping", {}).items()
            }
            if isinstance(getattr(result, "milestone_mapping", {}), dict)
            else {},
            "minefield_mapping": {
                str(key): json_safe({"snapshot_index": item[0], "similarity": item[1]}) if isinstance(item, tuple) and len(item) == 2 else json_safe(item)
                for key, item in getattr(result, "minefield_mapping", {}).items()
            }
            if isinstance(getattr(result, "minefield_mapping", {}), dict)
            else {},
        }
        return BenchmarkDefaultResult(
            score=score,
            raw=raw,
            metrics={
                "turn_count": int(getattr(result, "turn_count", 0)),
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            },
        )

    def stop_case(self, session: object, reason: str) -> None:
        """按 DynSTEER 策略终止 ToolSandbox session。"""
        session = self._require_session(session)
        if not reason:
            raise ValueError("reason 不能为空")
        session.finished = True
        session.stop_reason = reason
        session.termination_reason = "evaluation_policy_stop"

    def teardown_case(self, session: object) -> None:
        """释放 ToolSandbox role 资源并断开大对象引用。"""
        if not isinstance(session, ToolSandboxSession):
            return
        errors: list[Exception] = []
        try:
            for role_name, role in list(session.roles.items()):
                teardown = getattr(role, "teardown", None)
                if not callable(teardown):
                    continue
                try:
                    teardown()
                except Exception as exc:
                    errors.append(exc)
                    logger.exception(
                        "toolsandbox_role_teardown_failed",
                        extra={
                            "事件": "ToolSandbox role资源释放失败",
                            "case_id": session.case_id,
                            "role": str(role_name),
                            "error": str(exc),
                        },
                    )
        finally:
            session.roles.clear()
            session.context = None
            session.scenario = None
        if errors:
            raise RuntimeError(f"ToolSandbox role 资源释放失败: {len(errors)} 个 role 释放失败") from errors[0]

    def _require_session(self, session: object) -> ToolSandboxSession:
        if not isinstance(session, ToolSandboxSession):
            raise TypeError("session 必须是 ToolSandboxSession")
        return session

    def _named_scenarios(self, config: HarnessRunConfig) -> dict[str, Any]:
        """获取 ToolSandbox 原生场景字典。"""
        return load_named_scenarios(config, load_toolsandbox_module)

    def _role_impl_type(self, role_name: object) -> object:
        cli_utils = load_toolsandbox_module("tool_sandbox.cli.utils")
        role_impl_type = getattr(cli_utils, "RoleImplType")
        effective_name = str(role_name).strip()
        try:
            return role_impl_type[effective_name]
        except KeyError:
            try:
                return role_impl_type(effective_name)
            except (TypeError, ValueError):
                return effective_name

    def _toolsandbox_roles(self, config: HarnessRunConfig) -> dict[object, object]:
        """创建 ToolSandbox 原生 role。"""
        execution_context = load_toolsandbox_module("tool_sandbox.common.execution_context")
        execution_environment = load_toolsandbox_module("tool_sandbox.roles.execution_environment")
        cli_utils = load_toolsandbox_module("tool_sandbox.cli.utils")
        role_type = getattr(execution_context, "RoleType")
        agent_type = self._role_impl_type(config.metadata.get("agent"))
        user_type = self._role_impl_type(config.metadata.get("user"))
        agent_client_config = config.metadata.get("agent_client")
        user_client_config = config.metadata.get("user_client")
        agent_factory = get_agent_factory(agent_type, agent_client_config) or getattr(cli_utils, "AGENT_TYPE_TO_FACTORY").get(agent_type)
        user_factory = get_user_factory(user_type, user_client_config) or getattr(cli_utils, "USER_TYPE_TO_FACTORY").get(user_type)
        if agent_factory is None or user_factory is None:
            raise ValueError("ToolSandbox agent 或 user role 工厂不存在")
        return {
            role_type.USER: user_factory(),
            role_type.EXECUTION_ENVIRONMENT: execution_environment.ExecutionEnvironment(),
            role_type.AGENT: agent_factory(),
        }

    def _starting_context_from_scenario(self, scenario: object) -> object:
        """从标准 ToolSandbox scenario 读取起始 context。"""
        if scenario is None:
            raise ValueError("scenario 不能为空")
        starting_context = getattr(scenario, "starting_context", None)
        if starting_context is None:
            raise ValueError("ToolSandbox scenario 缺少 starting_context")
        return starting_context

    def _prepare_system_environment_messages(self, session: ToolSandboxSession) -> None:
        """执行 ToolSandbox system -> execution environment 初始化消息。"""
        if session.system_environment_messages_prepared:
            return
        if session.context is None:
            raise RuntimeError("ToolSandbox session 已释放")
        self._set_current_context(session.context)
        rows = rows_from_dataframe(self._sandbox_database(session.context, get_all_history_snapshots=True))
        execution_environment_role = self._role_for_recipient(session.roles, "EXECUTION_ENVIRONMENT")
        for row in rows:
            message_index = sandbox_message_index(row)
            if message_index < 0 or message_index > session.initial_max_sandbox_message_index:
                continue
            if enum_name(row.get("sender")) != "SYSTEM" or enum_name(row.get("recipient")) != "EXECUTION_ENVIRONMENT":
                continue
            respond = getattr(execution_environment_role, "respond", None)
            if not callable(respond):
                raise TypeError("ToolSandbox execution environment respond 必须可调用")
            respond(ending_index=message_index)
            session.context = self._get_current_context()
            if self._context_max_sandbox_message_index(session.context) != session.initial_max_sandbox_message_index:
                raise RuntimeError("ToolSandbox system environment 初始化不应新增消息")
        session.context = self._get_current_context()
        session.system_environment_messages_prepared = True

    def _advance_native_session(self, session: ToolSandboxSession) -> None:
        """恢复当前 context 并只推进当前 recipient 一次 respond。"""
        if session.finished:
            return
        if not session.system_environment_messages_prepared:
            raise RuntimeError("ToolSandbox session 尚未完成 system environment 初始化")
        if session.context is None:
            raise RuntimeError("ToolSandbox session 已释放")
        self._set_current_context(session.context)
        sandbox_db = self._sandbox_database(session.context)
        if not bool(self._last_column_value(sandbox_db, "conversation_active")):
            session.finished = True
            session.stop_reason = "ToolSandbox conversation_active 为 false"
            session.termination_reason = "natural_end_conversation"
            return
        latest_index = int(self._last_column_value(sandbox_db, "sandbox_message_index"))
        if latest_index >= session.initial_max_sandbox_message_index + session.max_messages:
            session.finished = True
            session.stop_reason = "ToolSandbox 达到 max_messages"
            session.termination_reason = "max_messages"
            return
        recipient = self._last_column_value(sandbox_db, "recipient")
        role = self._role_for_recipient(session.roles, recipient)
        self._respond_with_retry(session, role, recipient)

    def _respond_with_retry(self, session: ToolSandboxSession, role: object, recipient: object) -> None:
        """对 agent/user role 的 respond() 做统一重试。"""
        if session.context is None:
            raise RuntimeError("ToolSandbox session 已释放")
        respond = getattr(role, "respond", None)
        if not callable(respond):
            raise TypeError("ToolSandbox role.respond 必须可调用")
        client_config = role_client_config(role)
        max_retries = int(client_config.get("max_retries", 3))
        retry_base_seconds = float(client_config.get("retry_base_seconds", 1.0))
        retry_max_seconds = float(client_config.get("retry_max_seconds", 8.0))
        base_context = copy.deepcopy(session.context)
        role_name = enum_name(recipient)

        def operation() -> None:
            self._set_current_context(copy.deepcopy(base_context))
            respond()
            session.context = self._get_current_context()

        try:
            retry_call(
                operation,
                max_retries=max_retries,
                retry_base_seconds=retry_base_seconds,
                retry_max_seconds=retry_max_seconds,
                logger=logger,
                warning_message="ToolSandbox role respond 失败，准备重试",
                extra={
                    "benchmark": self.benchmark,
                    "case_id": session.case_id,
                    "role": role_name,
                    "recipient": enum_name(recipient),
                },
            )
        except Exception:
            session.termination_reason = "role_error"
            raise

    def _sandbox_database(self, context: object, *, drop_sandbox_message_index: bool = False, get_all_history_snapshots: bool = False) -> object:
        """读取 ToolSandbox SANDBOX 数据库。"""
        if context is None:
            raise ValueError("context 不能为空")
        execution_context = load_toolsandbox_module("tool_sandbox.common.execution_context")
        database_namespace = getattr(execution_context, "DatabaseNamespace")
        return context.get_database(
            database_namespace.SANDBOX,
            drop_sandbox_message_index=drop_sandbox_message_index,
            get_all_history_snapshots=get_all_history_snapshots,
        )

    def _context_max_sandbox_message_index(self, context: object | None) -> int:
        """读取 context 当前最大 sandbox_message_index。"""
        if context is None:
            return -1
        value = getattr(context, "max_sandbox_message_index", None)
        if isinstance(value, int):
            return value
        rows = rows_from_dataframe(self._sandbox_database(context, get_all_history_snapshots=True))
        return max((sandbox_message_index(row) for row in rows), default=-1)

    def _role_for_recipient(self, roles: dict[object, object], recipient: object) -> object:
        if recipient in roles:
            return roles[recipient]
        target_name = enum_name(recipient)
        for role_name, role in roles.items():
            if enum_name(role_name) == target_name:
                return role
        raise KeyError(f"ToolSandbox role 不存在: {recipient}")

    def _last_column_value(self, dataframe: object, column: str) -> object:
        if dataframe is None:
            raise ValueError("SANDBOX 数据库不能为空")
        if isinstance(dataframe, dict):
            values = dataframe.get(column)
            if not isinstance(values, list) or not values:
                raise ValueError(f"SANDBOX 缺少列: {column}")
            return values[-1]
        rows = rows_from_dataframe(dataframe)
        if rows:
            return rows[-1][column]
        values = dataframe[column]
        return values[-1]

    def _set_current_context(self, context: object) -> None:
        execution_context = load_toolsandbox_module("tool_sandbox.common.execution_context")
        set_current_context = getattr(execution_context, "set_current_context")
        set_current_context(context)

    def _get_current_context(self) -> object:
        execution_context = load_toolsandbox_module("tool_sandbox.common.execution_context")
        get_current_context = getattr(execution_context, "get_current_context")
        return get_current_context()
