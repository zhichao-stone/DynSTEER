from dynsteer.graph import FINISH_NODE_ID
from dynsteer.language import TaskLanguage, language_from_task
from dynsteer.model import MilestoneGraph, StageInterval, TaskCase

DEFAULT_FINISH_STAGE_GOAL = "完成收尾检查：确认已达成的阶段目标没有被后续证据推翻。"

_FINISH_STAGE_GOALS: dict[TaskLanguage, str] = {
    TaskLanguage.ENGLISH: (
        "Complete the final review: confirm that no later evidence has overturned the achieved stage goals."
    ),
    TaskLanguage.CHINESE: DEFAULT_FINISH_STAGE_GOAL,
}


def stage_goal_key(anchor_milestone_id: str, milestone_id: str) -> str:
    """生成阶段目标映射 key。"""
    if not anchor_milestone_id or not milestone_id:
        raise ValueError("anchor_milestone_id 和 milestone_id 不能为空")
    return f"{anchor_milestone_id}->{milestone_id}"


def required_stage_goal_keys(graph: MilestoneGraph) -> list[str]:
    """返回当前 graph 需要生成 stage_goal 的稳定 key 列表。"""
    if graph is None:
        raise ValueError("graph 不能为空")
    keys: list[str] = []
    for milestone in graph.nodes:
        if milestone is None or not milestone.milestone_id:
            raise ValueError("milestone 不能为空")
        anchor_id = milestone.stage_anchor_predecessor_id
        if not isinstance(anchor_id, str) or not anchor_id:
            raise ValueError(f"milestone 缺少 stage_anchor_predecessor_id: {milestone.milestone_id}")
        keys.append(stage_goal_key(anchor_id, milestone.milestone_id))
    return keys


def resolve_stage_goal(interval: StageInterval, task_case: TaskCase) -> str:
    """从 TaskCase.stage_goals 读取当前阶段目标。"""
    if interval is None:
        raise ValueError("interval 不能为空")
    if task_case is None:
        raise ValueError("task_case 不能为空")
    language = language_from_task(task_case)
    milestone_id = interval.milestone_id
    if milestone_id is None:
        return _finish_stage_goal(language)

    anchor_id = interval.stage_anchor_milestone_id
    if isinstance(anchor_id, str) and anchor_id.strip():
        key = stage_goal_key(anchor_id, milestone_id)
        stage_goal = task_case.stage_goals.get(key)
        if isinstance(stage_goal, str) and stage_goal.strip():
            return stage_goal.strip()
        if milestone_id != FINISH_NODE_ID:
            raise ValueError(f"TaskCase 缺少预生成 stage_goal: {key}")

    if milestone_id == FINISH_NODE_ID:
        return _finish_stage_goal(language)
    raise ValueError(f"milestone 阶段缺少 stage_anchor_milestone_id: {milestone_id}")


def _finish_stage_goal(language: TaskLanguage) -> str:
    """返回指定语言的 finish 阶段默认目标。"""
    return _FINISH_STAGE_GOALS.get(language, _FINISH_STAGE_GOALS[TaskLanguage.ENGLISH])
