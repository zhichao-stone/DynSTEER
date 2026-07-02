from __future__ import annotations

from dynsteer.adapter.toolsandbox.adapter import snapshots_from_context


class FakeNamespace:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeContext:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None, bool, bool]] = []

    def get_database(
        self,
        namespace: FakeNamespace,
        sandbox_message_index: int | None = None,
        get_all_history_snapshots: bool = False,
        drop_sandbox_message_index: bool = True,
    ) -> list[dict[str, object]]:
        self.calls.append((namespace.name, sandbox_message_index, get_all_history_snapshots, drop_sandbox_message_index))
        if get_all_history_snapshots:
            return [
                {"sandbox_message_index": 28, "value": f"{namespace.name}-28"},
                {"sandbox_message_index": 29, "value": f"{namespace.name}-29"},
            ]
        return [{"sandbox_message_index": sandbox_message_index, "value": f"{namespace.name}-{sandbox_message_index}"}]


class FakeExecutionContext:
    class DatabaseNamespace:
        SANDBOX = FakeNamespace("SANDBOX")
        SETTING = FakeNamespace("SETTING")
        CONTACT = FakeNamespace("CONTACT")


def fake_module_loader(module_name: str) -> object:
    if module_name == "tool_sandbox.common.execution_context":
        return FakeExecutionContext
    raise ModuleNotFoundError(module_name)


def test_toolsandbox_snapshots_are_full_namespace_batches() -> None:
    context = FakeContext()
    steps = [
        {"step_id": "s28", "index": 28, "raw_sandbox_message_index": 28},
        {"step_id": "s29", "index": 29, "raw_sandbox_message_index": 29},
    ]

    snapshots = snapshots_from_context(context, steps, fake_module_loader)

    assert [item["snapshot_id"] for item in snapshots] == ["toolsandbox:28", "toolsandbox:29"]
    assert snapshots[0]["after_step_id"] == "s28"
    assert snapshots[0]["after_step_index"] == 28
    assert set(snapshots[0]["namespaces"]) == {"SANDBOX", "SETTING", "CONTACT"}
    assert snapshots[0]["namespaces"]["SANDBOX"] == [
        {"sandbox_message_index": 28, "value": "SANDBOX-28"}
    ]
