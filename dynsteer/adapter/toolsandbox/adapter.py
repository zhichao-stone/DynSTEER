from copy import deepcopy

from dynsteer.adapter.base import BaseBenchmarkAdapter
from dynsteer.adapter.toolsandbox.utils.contract import (
    agent_facing_tool_schema,
    build_toolsandbox_generator_view,
)
from dynsteer.adapter.toolsandbox.utils.runtime import (
    load_named_scenarios,
    load_toolsandbox_module,
    toolsandbox_project_root,
)
from dynsteer.adapter.toolsandbox.utils.scenario import (
    milestone_graph_from_scenario,
    task_description_from_steps,
    task_types_from_categories,
)
from dynsteer.adapter.toolsandbox.utils.state import initial_state_from_context, sandbox_rows_from_context
from dynsteer.adapter.toolsandbox.utils.trace import sandbox_rows_to_step_dicts
from dynsteer.adapter.utils import ensure_source_root
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.milestone.model import GeneratorTaskView
from dynsteer.model import MilestoneGraph, TaskCase
from dynsteer.stage import generate_stage_evaluation_specs, materialize_stage_goals
from dynsteer.utils import enum_name


class ToolSandboxAdapter(BaseBenchmarkAdapter):
    """ToolSandbox data adapter."""
    benchmark = "toolsandbox"

    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase:
        """ToolSandbox native scenario to DynSTEER TaskCase."""
        if config is None or not case_id:
            raise ValueError('Config and case_id cannot be empty')
        ensure_source_root(config.data_root, toolsandbox_project_root(), self.benchmark)
        module_loader = load_toolsandbox_module
        scenarios = load_named_scenarios(config, module_loader)
        if case_id not in scenarios:
            raise KeyError(f"The ToolSandbox scene does not exist:{case_id}")
        scenario = scenarios[case_id]
        context = getattr(scenario, "starting_context", None)
        if context is None:
            raise ValueError('ToolSandbox scenario missing starting_content')
        rows = sandbox_rows_from_context(context, module_loader)
        steps = sandbox_rows_to_step_dicts(rows)
        is_default = str(config.metadata.get("method") or "").strip().lower() == "default"
        graph = None if is_default else milestone_graph_from_scenario(scenario)
        first_user_index = getattr(context, "first_user_sandbox_message_index", None)
        return TaskCase(
            task_id=f"toolsandbox::{case_id}",
            task_description=task_description_from_steps(
                steps,
                case_id,
                first_user_sandbox_message_index=first_user_index
                if isinstance(first_user_index, int)
                else None,
            ),
            case_id=case_id,
            environment_schema={"source": "toolsandbox", "stateful": True},
            tool_schema=agent_facing_tool_schema(context, module_loader),
            initial_state=initial_state_from_context(context, module_loader),
            milestone_graph=graph,
            task_types=task_types_from_categories(getattr(scenario, "categories", [])),
            metadata={
                "benchmark": "toolsandbox",
                "scenario_name": case_id,
                "categories": [
                    enum_name(item) for item in getattr(scenario, "categories", [])
                ],
            },
        )

    def generator_task_view(
        self, config: HarnessRunConfig, task_case: TaskCase, case_id: str
    ) -> GeneratorTaskView:
        """Public input is visible from the current ToolSandbox projection Agent."""
        scenarios = load_named_scenarios(config, load_toolsandbox_module)
        if case_id not in scenarios:
            raise KeyError(f"The ToolSandbox scene does not exist:{case_id}")
        context = getattr(scenarios[case_id], "starting_context", None)
        if context is None:
            raise ValueError('ToolSandbox scenario missing starting_content')
        return build_toolsandbox_generator_view(
            config, task_case, context, load_toolsandbox_module
        )

    def reliability_tool_aliases(
        self, config: HarnessRunConfig, case_id: str
    ) -> dict[str, str]:
        """Maps the application-facing tool name of the reference to the Agent visible name."""
        scenarios = load_named_scenarios(config, load_toolsandbox_module)
        if case_id not in scenarios:
            raise KeyError(f"The ToolSandbox scene does not exist:{case_id}")
        context = getattr(scenarios[case_id], "starting_context", None)
        if context is None:
            raise ValueError('ToolSandbox scenario missing starting_content')
        mapping = context.get_agent_to_execution_facing_tool_name()
        if not isinstance(mapping, dict):
            raise TypeError('ToolSandbox tool name making must be dictionary')
        return {str(actual): str(agent) for agent, actual in mapping.items()}

    def refresh_task_case_for_experiment(
        self,
        config: HarnessRunConfig,
        task_case: TaskCase,
        case_id: str,
    ) -> TaskCase:
        """Refresh dynamic binding information using this shared ToolSandbox scenario."""
        if config is None or task_case is None or not case_id:
            raise ValueError('Config, task_case and case_id cannot be empty')
        scenarios = load_named_scenarios(config, load_toolsandbox_module)
        if case_id not in scenarios:
            raise KeyError(f"The ToolSandbox scene does not exist:{case_id}")
        if task_case.milestone_graph is None:
            raise ValueError('TaskCase Missing Milestone_graph')
        source_graph = milestone_graph_from_scenario(scenarios[case_id])
        source_constraints = {
            constraint.constraint_id: constraint
            for item in [*source_graph.nodes, *source_graph.minefields]
            for constraint in item.constraints
        }
        for item in [*task_case.milestone_graph.nodes, *task_case.milestone_graph.minefields]:
            for constraint in item.constraints:
                source = source_constraints[constraint.constraint_id]
                constraint.expected = deepcopy(source.expected)
                constraint.stage_goal_semantics = deepcopy(source.stage_goal_semantics)
                constraint.metadata = deepcopy(source.metadata)
        task_case.stage_goals = materialize_stage_goals(task_case)
        task_case.stage_evaluation_specs = generate_stage_evaluation_specs(task_case)
        return task_case

    def reference_milestone_graph(
        self, config: HarnessRunConfig, case_id: str
    ) -> MilestoneGraph | None:
        """returns the ToolSandbox complete origin drag for comparison only."""
        scenarios = load_named_scenarios(config, load_toolsandbox_module)
        if case_id not in scenarios:
            raise KeyError(f"The ToolSandbox scene does not exist:{case_id}")
        return milestone_graph_from_scenario(scenarios[case_id])
