from __future__ import annotations

from dataclasses import replace

from dynsteer.adapter.base import BaseBenchmarkAdapter, BaseBenchmarkHarness
from dynsteer.adapter.generic import load_milestone_graph, load_task_case, load_trajectory
from dynsteer.model import Actor, EventType, JsonObject, TaskCase, Trajectory, ensure_json_object


def _event_type_from_message(message: JsonObject) -> str:
    if message.get("tool_call") is not None:
        return EventType.TOOL_CALL.value
    if message.get("tool_result") is not None:
        return EventType.TOOL_RESULT.value
    if message.get("final") is True:
        return EventType.FINAL.value
    return EventType.MESSAGE.value


def _actor_from_role(role: object) -> str:
    if role in {"system", "user", "agent", "environment", "evaluator"}:
        return str(role)
    if role == "assistant":
        return Actor.AGENT.value
    if role == "tool":
        return Actor.ENVIRONMENT.value
    return Actor.AGENT.value


def _trajectory_from_messages(data: JsonObject, task_id: str) -> Trajectory:
    messages = data.get("conversation", data.get("messages", []))
    if not isinstance(messages, list):
        raise ValueError("ToolSandbox conversation/messages 必须是数组")
    steps = []
    for index, item in enumerate(messages):
        message = ensure_json_object(item)
        steps.append(
            {
                "step_id": str(message.get("step_id", f"s{index}")),
                "index": int(message.get("index", index)),
                "actor": _actor_from_role(message.get("role", message.get("actor"))),
                "event_type": str(message.get("event_type", _event_type_from_message(message))),
                "content": message.get("content") if isinstance(message.get("content"), str) else None,
                "tool_call": message.get("tool_call"),
                "tool_result": message.get("tool_result"),
            }
        )
    return load_trajectory(
        {
            "run_id": str(data.get("run_id", data.get("id", "toolsandbox-run"))),
            "task_id": task_id,
            "steps": steps,
            "snapshots": data.get("snapshots", []),
            "metrics": data.get("metrics", {}),
            "final_state": data.get("final_state"),
        }
    )


def load_toolsandbox_experiment(data: JsonObject) -> tuple[TaskCase, Trajectory]:
    """载入 ToolSandbox 风格实验数据。

    Args:
        data: ToolSandbox 导出的字典。

    Returns:
        统一任务与轨迹模型。
    """
    experiment = ensure_json_object(data)
    if experiment.get("task") is not None and experiment.get("trajectory") is not None:
        task_case = load_task_case(ensure_json_object(experiment["task"]))
        trajectory = load_trajectory(ensure_json_object(experiment["trajectory"]))
    else:
        task_id = str(experiment.get("task_id", experiment.get("id", "toolsandbox-task")))
        task_case = load_task_case(
            {
                "task_id": task_id,
                "task_description": str(experiment.get("task_description", experiment.get("instruction", ""))),
                "task_types": experiment.get("task_types", []),
                "tool_schema": experiment.get("tool_schema", {}),
                "environment_schema": experiment.get("environment_schema", {}),
            }
        )
        trajectory = _trajectory_from_messages(experiment, task_id)
    graph_data = experiment.get("milestone_graph", experiment.get("milestones"))
    if graph_data is not None:
        task_case = replace(task_case, milestone_graph=load_milestone_graph(ensure_json_object(graph_data)))
    return task_case, trajectory


class ToolSandboxAdapter(BaseBenchmarkAdapter):
    """ToolSandbox 离线数据适配器。"""

    benchmark = "toolsandbox"

    # override 基类的函数实现

    def load_experiment(self, data: JsonObject) -> tuple[TaskCase, Trajectory]:
        """载入 ToolSandbox 风格实验数据。

        Args:
            data: ToolSandbox 导出或转换后的字典。

        Returns:
            DynSTEER 任务与轨迹。
        """
        return load_toolsandbox_experiment(data)

    def create_harness(self) -> BaseBenchmarkHarness:
        """创建 ToolSandbox 运行期 harness。

        Returns:
            ToolSandboxHarness 实例。
        """
        from dynsteer.adapter.toolsandbox.harness import ToolSandboxHarness

        return ToolSandboxHarness()

    # ToolSandboxAdapter 独有函数实现
