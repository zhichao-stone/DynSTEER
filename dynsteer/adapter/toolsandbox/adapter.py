from copy import deepcopy

from dynsteer.adapter.base import BaseBenchmarkAdapter
from dynsteer.adapter.toolsandbox.utils.scenario import milestone_graph_from_scenario, task_description_from_steps, task_types_from_categories
from dynsteer.adapter.toolsandbox.utils.state import sandbox_rows_from_context
from dynsteer.adapter.toolsandbox.utils.trace import sandbox_rows_to_step_dicts
from dynsteer.adapter.toolsandbox.utils.runtime import load_named_scenarios, load_toolsandbox_module, toolsandbox_project_root
from dynsteer.adapter.utils import ensure_source_root
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import TaskCase
from dynsteer.stage import generate_stage_evaluation_specs, materialize_stage_goals
from dynsteer.utils import enum_name

class ToolSandboxAdapter(BaseBenchmarkAdapter):
    """ToolSandbox 数据适配器。"""
    benchmark = "toolsandbox"

    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase:
        """将 ToolSandbox 原生 scenario 适配为 DynSTEER TaskCase。"""
        if config is None or not case_id:
            raise ValueError("config 和 case_id 不能为空")
        ensure_source_root(config.data_root, toolsandbox_project_root(), self.benchmark)
        module_loader = load_toolsandbox_module
        scenarios = load_named_scenarios(config, module_loader)
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
                first_user_sandbox_message_index=first_user_index
                if isinstance(first_user_index, int)
                else None,
            ),
            case_id=case_id,
            environment_schema={"source": "toolsandbox"},
            tool_schema={"source": "toolsandbox"},
            initial_state=None,
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

    def refresh_task_case_for_experiment(
        self,
        config: HarnessRunConfig,
        task_case: TaskCase,
        case_id: str,
    ) -> TaskCase:
        """使用本次共享 ToolSandbox scenario 刷新动态约束信息。"""
        if config is None or task_case is None or not case_id:
            raise ValueError("config、task_case 和 case_id 不能为空")
        scenarios = load_named_scenarios(config, load_toolsandbox_module)
        if case_id not in scenarios:
            raise KeyError(f"ToolSandbox 场景不存在: {case_id}")
        if task_case.milestone_graph is None:
            raise ValueError("TaskCase 缺少 milestone_graph")
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
        task_case.initial_state = None
        task_case.stage_goals = materialize_stage_goals(task_case)
        task_case.stage_evaluation_specs = generate_stage_evaluation_specs(task_case)
        return task_case
