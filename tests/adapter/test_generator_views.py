from pathlib import Path
from types import SimpleNamespace

from dynsteer.adapter.agentcompass.contract import build_agentcompass_generator_view
from dynsteer.adapter.skillsbench.adapter import SkillsBenchAdapter
from dynsteer.adapter.swebench_pro.adapter import SWEBenchProAdapter
from dynsteer.adapter.toolsandbox.utils.contract import (
    agent_facing_tool_schema,
    build_toolsandbox_generator_view,
)
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import TaskCase


def _config(benchmark: str) -> HarnessRunConfig:
    return HarnessRunConfig(
        benchmark=benchmark,
        data_root=Path("data"),
        metadata={"language": "en"},
    )


def test_agentcompass_views_use_public_workspace_contracts() -> None:
    swe_task = TaskCase(
        "swe::1",
        "fix issue",
        "1",
        metadata={"repo": "owner/repo", "base_commit": "abc"},
    )
    skills_task = TaskCase("skills::2", "build artifact", "2")

    swe = SWEBenchProAdapter().generator_task_view(
        _config("swebench_pro"), swe_task, "1"
    )
    skills = SkillsBenchAdapter().generator_task_view(
        _config("skillsbench"), skills_task, "2"
    )

    assert swe.environment_schema["workspace"]["value"] == "/app/1/repo"
    assert swe.output_contract["path"]["value"] == "/app/1/patch.txt"
    assert skills.environment_schema["workspace"]["value"] == "/root"
    assert skills.output_contract == {}
    assert "ground_truth" not in str(swe.__dict__).lower()


def test_shared_agentcompass_builder_has_same_evidence_catalog() -> None:
    task = TaskCase("task", "instruction", "case")
    left = build_agentcompass_generator_view(
        _config("swebench_pro"), task, environment_schema={}, output_contract={}
    )
    right = build_agentcompass_generator_view(
        _config("skillsbench"), task, environment_schema={}, output_contract={}
    )
    assert left.evidence_catalog == right.evidence_catalog


def test_toolsandbox_schema_filters_visibility_and_preserves_agent_names() -> None:
    def agent_tool() -> None:
        pass

    def user_tool() -> None:
        pass

    agent_tool.visible_to = ("AGENT",)
    user_tool.visible_to = ("USER",)

    class Context:
        def get_available_tools(self, scrambling_allowed: bool) -> dict[str, object]:
            assert scrambling_allowed is True
            return {"scrambled_0": agent_tool, "hidden": user_tool}

    def convert(tools: dict[str, object]) -> list[dict[str, object]]:
        return [
            {
                "type": "function",
                "function": {"name": name, "description": "", "parameters": {}},
            }
            for name in tools
        ]

    loader = lambda name: SimpleNamespace(convert_to_openai_tools=convert)
    schema = agent_facing_tool_schema(Context(), loader)
    assert [item["function"]["name"] for item in schema["tools"]] == ["scrambled_0"]


def test_toolsandbox_view_excludes_hidden_state_and_matchers() -> None:
    def tool() -> None:
        pass

    tool.visible_to = ("AGENT",)

    class Namespace:
        SANDBOX = "SANDBOX"

    class Context:
        def get_available_tools(self, scrambling_allowed: bool) -> dict[str, object]:
            return {"public_tool": tool}

        def get_database(
            self, namespace: object, **kwargs: object
        ) -> list[dict[str, object]]:
            return [
                {
                    "sandbox_message_index": 0,
                    "sender": "USER",
                    "recipient": "AGENT",
                    "content": "do task",
                    "visible_to": ["AGENT"],
                }
            ]

    def loader(name: str) -> object:
        if name.endswith("execution_context"):
            return SimpleNamespace(DatabaseNamespace=Namespace)
        return SimpleNamespace(
            convert_to_openai_tools=lambda tools: [
                {
                    "type": "function",
                    "function": {"name": key, "description": "", "parameters": {}},
                }
                for key in tools
            ]
        )

    task = TaskCase("task", "do task", "case")
    view = build_toolsandbox_generator_view(
        _config("toolsandbox"), task, Context(), loader
    )
    serialized = str(view.__dict__).lower()
    assert "target_dataframe" not in serialized
    assert "matcher" not in serialized
    assert view.environment_schema["stateful"]["value"] is True
