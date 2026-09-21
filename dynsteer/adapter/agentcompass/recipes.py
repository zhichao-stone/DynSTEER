from __future__ import annotations

from copy import deepcopy

from dynsteer.adapter.agentcompass.components import import_agentcompass_component
from agentcompass.runtime import RECIPES, BaseRecipe, ExecutionPlan, RunRequest, TaskSpec

_SWEBENCH_PRO_MODULE = import_agentcompass_component("agentcompass.benchmarks.swebench_pro")
SWEBenchProBenchmarkPlan = _SWEBENCH_PRO_MODULE.SWEBenchProBenchmarkPlan


@RECIPES.register()
class SWEBenchProHostProcessRecipe(BaseRecipe):
    """对齐 SWE-bench Pro 的 agent 仓库与 host-process 评估路径。"""

    id = "swebench_pro_host_process"

    def matches(self, req: RunRequest, task: TaskSpec, plan: ExecutionPlan) -> bool:
        _ = task, plan
        return req.benchmark.id == "swebench_pro" and req.environment.id == "host_process"

    def apply(self, plan: ExecutionPlan, req: RunRequest, task: TaskSpec) -> ExecutionPlan:
        _ = req, task
        updated_plan = deepcopy(plan)
        if not isinstance(updated_plan.benchmark_plan, SWEBenchProBenchmarkPlan):
            raise TypeError("swebench_pro_host_process requires SWEBenchProBenchmarkPlan")
        benchmark_plan = updated_plan.benchmark_plan
        benchmark_plan.evaluation_prepare_mode = "prebaked"
        benchmark_plan.evaluation_workspace_dir = benchmark_plan.workspace_dir
        benchmark_plan.evaluation_repo_dir = benchmark_plan.repo_dir
        return updated_plan
