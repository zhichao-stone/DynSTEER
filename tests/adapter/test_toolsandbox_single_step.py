from __future__ import annotations

from pathlib import Path

import pytest

import dynsteer.adapter.toolsandbox.harness as harness_module
from dynsteer.adapter.toolsandbox.harness import ToolSandboxHarness, ToolSandboxSession


class FakeExecutionContextModule:
    current_context: "FakeContext | None" = None

    class DatabaseNamespace:
        SANDBOX = "SANDBOX"

    @staticmethod
    def set_current_context(context: "FakeContext") -> None:
        FakeExecutionContextModule.current_context = context

    @staticmethod
    def get_current_context() -> "FakeContext":
        if FakeExecutionContextModule.current_context is None:
            raise RuntimeError("current context 缺失")
        return FakeExecutionContextModule.current_context


class FakeContext:
    def __init__(self) -> None:
        self.counter = 0
        self.sandbox_message_index = 0
        self.recipient = "agent"

    def get_database(self, namespace: object, **kwargs: object) -> dict[str, list[object]]:
        return {
            "conversation_active": [True],
            "sandbox_message_index": [self.sandbox_message_index],
            "recipient": [self.recipient],
        }


class FakeRole:
    def __init__(self) -> None:
        self.seen_counters: list[int] = []

    def respond(self) -> None:
        context = FakeExecutionContextModule.get_current_context()
        self.seen_counters.append(context.counter)
        context.counter += 1
        context.sandbox_message_index += 1
        FakeExecutionContextModule.set_current_context(context)


def test_advance_native_session_reuses_previous_context_without_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_import_module(module_name: str, dependency_error_message: str | None = None) -> object:
        if module_name == "tool_sandbox.common.execution_context":
            return FakeExecutionContextModule
        raise ModuleNotFoundError(module_name)

    monkeypatch.setattr(harness_module, "import_module", fake_import_module)
    context = FakeContext()
    role = FakeRole()
    session = ToolSandboxSession(
        scenario=object(),
        roles={"agent": role},
        context=context,
        case_id="case-1",
        run_id="run-1",
        raw_output_dir=tmp_path,
        initial_max_sandbox_message_index=0,
        last_sandbox_message_index=0,
        max_messages=5,
        system_environment_messages_prepared=True,
    )
    harness = ToolSandboxHarness()

    harness._advance_native_session(session)
    FakeExecutionContextModule.current_context = None
    harness._advance_native_session(session)

    assert role.seen_counters == [0, 1]
    assert session.context is context
    assert context.counter == 2
