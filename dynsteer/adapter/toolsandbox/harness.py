from __future__ import annotations

import copy
import json
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
from dynsteer.adapter.toolsandbox.utils.usage import ProviderUsageRecorder
from dynsteer.adapter.utils import rows_from_dataframe, retry_call
from dynsteer.harness.model import HarnessAdvanceResult, HarnessRunConfig
from dynsteer.model import Actor, JsonObject, ToolSandboxSession, TrajectoryStep
from dynsteer.utils import clamp, enum_name, json_safe


logger = logging.getLogger(__name__)
class ToolSandboxHarness(BaseBenchmarkHarness):
    """ToolSandbox benchmark native execution"""

    benchmark = "toolsandbox"

    def constraint_scorer(self) -> ToolSandboxConstraintScorer:
        """returns the ToolSandbox special restraint scoring machine."""
        return ToolSandboxConstraintScorer(module_loader=load_toolsandbox_module)

    def list_case_ids(self, config: HarnessRunConfig) -> list[str]:
        """Lists the ToolSandbox scenes."""
        self.prepare_config(config)
        return sorted(str(case_id) for case_id in self._named_scenarios(config))

    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> ToolSandboxSession:
        """Initialize the ToolSandbox original session."""
        if config is None or raw_output_dir is None or not case_id:
            raise ValueError('Config, case_id and raw_output_dir cannot be empty')
        scenarios = self._named_scenarios(config)
        if case_id not in scenarios:
            raise KeyError(f"The ToolSandbox scene does not exist:{case_id}")
        scenario = scenarios[case_id]
        usage_recorder = ProviderUsageRecorder() if config.metadata.get("capture_agent_usage") is True else None
        roles = self._toolsandbox_roles(config, usage_recorder)
        context = copy.deepcopy(self._starting_context_from_scenario(scenario))
        self._set_current_context(context)
        initial_max = self._context_max_sandbox_message_index(context)
        max_messages = int(config.metadata.get("max_messages", 100))
        if max_messages <= 0:
            raise ValueError('ToolSandbox max_sessions must be greater than 0')
        session = ToolSandboxSession(
            scenario=scenario,
            roles=roles,
            context=context,
            initial_state=None,
            case_id=case_id,
            initial_max_sandbox_message_index=initial_max,
            last_sandbox_message_index=initial_max,
            max_messages=max_messages,
            usage_recorder=usage_recorder,
        )
        self._prepare_system_environment_messages(session)
        session.initial_state = initial_state_from_context(session.context, load_toolsandbox_module)
        return session

    def advance_case(self, session: object) -> HarnessAdvanceResult:
        """Advances a native benchmark step and returns the new step."""
        session = self._require_session(session)
        if session.finished:
            return HarnessAdvanceResult(steps=[], snapshots=[], continue_running=False)
        usage_recorder = session.usage_recorder
        self._advance_native_session(session)
        usage_delta = usage_recorder.take_delta() if isinstance(usage_recorder, ProviderUsageRecorder) else None
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
            raise ValueError(f"ToolSandbox appended SANDBOX rows contain duplicate indexes: {sorted(duplicates)}")
        steps = sandbox_rows_to_step_dicts(rows)
        snapshot_data = snapshots_from_context(session.context, steps, load_toolsandbox_module) if session.context is not None else []
        trajectory = trajectory_from_sandbox_rows(
            task_id=f"toolsandbox::{session.case_id}",
            steps=steps,
            snapshots=snapshot_data,
        )
        if not trajectory.steps and not session.finished:
            raise RuntimeError('Benchmark session not completed but not added trajectory steps')
        if isinstance(usage_recorder, ProviderUsageRecorder):
            self._assign_agent_usage(trajectory.steps, usage_delta, usage_recorder)
        if indexes:
            session.last_sandbox_message_index = max(indexes)
        return HarnessAdvanceResult(
            steps=trajectory.steps,
            snapshots=trajectory.snapshots,
            continue_running=not session.finished,
        )

    def metrics_from_session(self, session: object) -> JsonObject:
        """Returns the ToolSandbox runtime metrics."""
        self._require_session(session)
        metrics: JsonObject = {"native_evaluation_skipped": True}
        if isinstance(session.usage_recorder, ProviderUsageRecorder):
            metrics["agent_usage"] = session.usage_recorder.summary()
        return metrics

    def initial_state_from_session(self, session: object) -> JsonObject | None:
        """Returns the actual initial state of the current ToolSandbox session for solidification."""
        session = self._require_session(session)
        return session.initial_state

    def final_state_from_session(self, session: object) -> JsonObject | None:
        """Returns the current namespace state of ToolSandbox."""
        session = self._require_session(session)
        if session.context is None:
            return None
        return state_from_context(session.context, load_toolsandbox_module, include_sandbox=True)

    def raw_summary_from_session(self, session: object) -> JsonObject:
        """returns the ToolSandbox original summary."""
        session = self._require_session(session)
        return {
            "native_evaluation_skipped": True,
            "case_id": session.case_id,
            "termination_reason": session.termination_reason,
            "termination_detail": {"stop_reason": session.stop_reason},
        }

    def default_result_from_session(self, session: object) -> BenchmarkDefaultResult:
        """Extracts the Default result from the ToolSandbox original evaluation."""
        session = self._require_session(session)
        if session.scenario is None or session.context is None:
            raise RuntimeError('ToolSandbox session released and unable to extract the Default result')
        evaluation = getattr(session.scenario, "evaluation", None)
        evaluate = getattr(evaluation, "evaluate", None)
        if not callable(evaluate):
            raise NotImplementedError('ToolSandbox scenario lacks available originals.valuate')
        try:
            result = evaluate(execution_context=session.context, max_turn_count=session.max_messages)
        except Exception as exc:
            logger.exception(
                "toolsandbox_default_evaluation_failed",
                extra={'event': 'ToolSandbox native DEFAULT evaluation failed', 'case_id': session.case_id, 'error': str(exc)},
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
                "native_evaluation_token_source": "deterministic_native_verifier",
            },
        )

    def stop_case(self, session: object, reason: str) -> None:
        """Terminate ToolSandbox session using DynSTEER policy."""
        session = self._require_session(session)
        if not reason:
            raise ValueError("It's not empty, reason.")
        session.finished = True
        session.stop_reason = reason
        session.termination_reason = "evaluation_policy_stop"

    def send_guidance(self, session: object, message: str) -> None:
        """Writes a ToolSandbox guide that is visible only to the agent."""
        session = self._require_session(session)
        if not message.strip():
            raise ValueError('Message cannot be empty.')
        if session.finished or not bool(
            self._last_column_value(self._sandbox_database(session.context), "conversation_active")
        ):
            raise RuntimeError('ToolSandbox session closed, unable to write guide message')
        latest_index = int(
            self._last_column_value(self._sandbox_database(session.context), "sandbox_message_index")
        )
        if latest_index >= session.initial_max_sandbox_message_index + session.max_messages:
            raise RuntimeError('ToolSandbox has reached max_messages, unable to write lead messages')
        self._append_guidance_message(session, message)

    def _append_guidance_message(self, session: ToolSandboxSession, message: str) -> None:
        """ToolSandbox BaseRole publicly writes API additional hidden user messages."""
        if session.context is None:
            raise RuntimeError('ToolSandbox session released')
        # ToolSandbox is an optional independent dependency and only loads its role into the boundary when the implementation period is guided.
        base_role = load_toolsandbox_module("tool_sandbox.roles.base_role")
        message_conversion = load_toolsandbox_module("tool_sandbox.common.message_conversion")
        execution_context = load_toolsandbox_module("tool_sandbox.common.execution_context")
        role_type = getattr(execution_context, "RoleType")
        guidance = getattr(message_conversion, "Message")(
            sender=role_type.USER,
            recipient=role_type.AGENT,
            content=message.strip(),
            visible_to=[role_type.AGENT],
        )
        self._set_current_context(session.context)
        getattr(base_role, "BaseRole").add_messages([guidance])
        session.context = self._get_current_context()
        new_index = self._context_max_sandbox_message_index(session.context)
        if new_index <= session.last_sandbox_message_index:
            raise RuntimeError('ToolSandbox Guidance message index is not advanced')
        session.last_sandbox_message_index = new_index

    def teardown_case(self, session: object) -> None:
        """ToolSandbox rule resource is released and large object references are severed."""
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
                            'event': "ToolSandbox failed to release role resources",
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
            raise RuntimeError(f"ToolSandbox role teardown failed for {len(errors)} roles") from errors[0]

    def _require_session(self, session: object) -> ToolSandboxSession:
        if not isinstance(session, ToolSandboxSession):
            raise TypeError('ToolSandboxSession')
        return session

    def _named_scenarios(self, config: HarnessRunConfig) -> dict[str, Any]:
        """Get the ToolSandbox original scene dictionary."""
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

    def _toolsandbox_roles(self, config: HarnessRunConfig, usage_recorder: ProviderUsageRecorder | None=None) -> dict[object, object]:
        """Creates the ToolSandbox native role."""
        execution_context = load_toolsandbox_module("tool_sandbox.common.execution_context")
        execution_environment = load_toolsandbox_module("tool_sandbox.roles.execution_environment")
        cli_utils = load_toolsandbox_module("tool_sandbox.cli.utils")
        role_type = getattr(execution_context, "RoleType")
        agent_type = self._role_impl_type(config.metadata.get("agent"))
        user_type = self._role_impl_type(config.metadata.get("user"))
        agent_client_config = config.metadata.get("agent_client")
        user_client_config = config.metadata.get("user_client") or agent_client_config
        agent_factory = get_agent_factory(agent_type, agent_client_config, usage_recorder) or getattr(cli_utils, "AGENT_TYPE_TO_FACTORY").get(agent_type)
        user_factory = get_user_factory(user_type, user_client_config) or getattr(cli_utils, "USER_TYPE_TO_FACTORY").get(user_type)
        if agent_factory is None or user_factory is None:
            raise ValueError('ToolSandbox agent or user rule factory does not exist')
        return {
            role_type.USER: user_factory(),
            role_type.EXECUTION_ENVIRONMENT: execution_environment.ExecutionEnvironment(),
            role_type.AGENT: agent_factory(),
        }

    def _assign_agent_usage(self, steps: list[TrajectoryStep], delta: ProviderUsageDelta | None, recorder: ProviderUsageRecorder) -> None:
        """Only the first item is assigned to the user of a batch. The rest is clearly not."""
        agent_outbound_positions = [
            index for index, step in enumerate(steps)
            if step.actor == Actor.AGENT and step.recipient in {Actor.USER, Actor.ENVIRONMENT}
        ]
        token_position = agent_outbound_positions[0] if agent_outbound_positions else -1
        if delta is not None and delta.total_tokens and not agent_outbound_positions:
            recorder.note_missing_agent_target()
        for index, step in enumerate(steps):
            if index != token_position:
                step.cost.tokens = 0
            elif delta is None:
                step.cost.tokens = None
            else:
                step.cost.tokens = delta.total_tokens

    def _starting_context_from_scenario(self, scenario: object) -> object:
        """Reads starting context from the standard ToolSandbox scenario."""
        if scenario is None:
            raise ValueError('Scenario cannot be empty.')
        starting_context = getattr(scenario, "starting_context", None)
        if starting_context is None:
            raise ValueError('ToolSandbox scenario missing starting_content')
        return starting_context

    def _prepare_system_environment_messages(self, session: ToolSandboxSession) -> None:
        """Execute ToolSandbox system-> initialization messages."""
        if session.system_environment_messages_prepared:
            return
        if session.context is None:
            raise RuntimeError('ToolSandbox session released')
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
                raise TypeError('ToolSandbox exit environment recall must be called')
            respond(ending_index=message_index)
            session.context = self._get_current_context()
            if self._context_max_sandbox_message_index(session.context) != session.initial_max_sandbox_message_index:
                raise RuntimeError('ToolSandbox system initialisation should not add messages')
        session.context = self._get_current_context()
        session.system_environment_messages_prepared = True

    def _advance_native_session(self, session: ToolSandboxSession) -> None:
        """Restores the current context and only promotes the current recipient respond."""
        if session.finished:
            return
        if not session.system_environment_messages_prepared:
            raise RuntimeError('ToolSandbox session incomplete initialization')
        if session.context is None:
            raise RuntimeError('ToolSandbox session released')
        self._set_current_context(session.context)
        sandbox_db = self._sandbox_database(session.context)
        if not bool(self._last_column_value(sandbox_db, "conversation_active")):
            session.finished = True
            session.stop_reason = 'ToolSandbox conversion_active as false'
            session.termination_reason = "natural_end_conversation"
            return
        latest_index = int(self._last_column_value(sandbox_db, "sandbox_message_index"))
        if latest_index >= session.initial_max_sandbox_message_index + session.max_messages:
            session.finished = True
            session.stop_reason = 'ToolSandbox to max_messages'
            session.termination_reason = "max_messages"
            return
        recipient = self._last_column_value(sandbox_db, "recipient")
        role = self._role_for_recipient(session.roles, recipient)
        self._respond_with_retry(session, role, recipient)

    def _respond_with_retry(self, session: ToolSandboxSession, role: object, recipient: object) -> None:
        """Retry the respond() of the agent/ user rule."""
        if session.context is None:
            raise RuntimeError('ToolSandbox session released')
        respond = getattr(role, "respond", None)
        if not callable(respond):
            raise TypeError('ToolSandbox rule.respond must be available')
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
                warning_message='ToolSandbox rule respond failed, ready to retry',
                extra={
                    "benchmark": self.benchmark,
                    "case_id": session.case_id,
                    "role": role_name,
                    "recipient": enum_name(recipient),
                },
            )
        except json.JSONDecodeError as exc:
            session.finished = True
            session.termination_reason = "agent_invalid_tool_call"
            session.stop_reason = f"Agent tool-call arguments are invalid: {exc}"
            logger.error(
                'ToolSandbox agent returned invalid tool-call JSON; stopping with a controlled failure',
                extra={
                    "case_id": session.case_id,
                    "role": role_name,
                    "recipient": enum_name(recipient),
                    "error": str(exc),
                },
            )
        except Exception:
            session.termination_reason = "role_error"
            raise

    def _sandbox_database(self, context: object, *, drop_sandbox_message_index: bool = False, get_all_history_snapshots: bool = False) -> object:
        """Reads the ToolSandbox SANDBOX database."""
        if context is None:
            raise ValueError("Can't be empty.")
        execution_context = load_toolsandbox_module("tool_sandbox.common.execution_context")
        database_namespace = getattr(execution_context, "DatabaseNamespace")
        return context.get_database(
            database_namespace.SANDBOX,
            drop_sandbox_message_index=drop_sandbox_message_index,
            get_all_history_snapshots=get_all_history_snapshots,
        )

    def _context_max_sandbox_message_index(self, context: object | None) -> int:
        """Reads context the current maximum sandbox_session_index."""
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
        raise KeyError(f"ToolSandbox rule does not exist:{recipient}")

    def _last_column_value(self, dataframe: object, column: str) -> object:
        if dataframe is None:
            raise ValueError('SANDBOX database cannot be empty')
        if isinstance(dataframe, dict):
            values = dataframe.get(column)
            if not isinstance(values, list) or not values:
                raise ValueError(f"SANDBOX missing column:{column}")
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
