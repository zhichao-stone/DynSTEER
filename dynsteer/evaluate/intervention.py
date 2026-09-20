from __future__ import annotations

import json

from dynsteer.model import (
    JsonObject,
    RuntimeEvaluationDecision,
    RuntimeEvaluationState,
    TaskCase,
)

INTERVENTION_TEXT_LIMIT = 1200


def build_intervention_message(
    task_case: TaskCase,
    decision: RuntimeEvaluationDecision,
    state: RuntimeEvaluationState,
) -> str:
    """用 agent 可见的公开诊断构造过程引导消息。

    入参：
        task_case: 当前已适配 case。
        decision: 触发终止的运行期决策。
        state: 当前运行期评估状态。
    输出：
        以固定前缀开头的纯文本干预提示。
    """
    if task_case is None or decision is None or state is None:
        raise ValueError("task_case、decision 和 state 不能为空")
    termination = decision.termination
    detail = termination.termination_detail if isinstance(termination.termination_detail, dict) else {}
    stage_id = trigger_stage_id(decision)
    lines = [
        "[DynSTEER intervention]",
        "这是过程诊断提示，不改变原任务目标。",
        f"任务描述：{task_case.task_description}",
    ]
    stage_goal = task_case.stage_goals.get(stage_id)
    if stage_goal:
        lines.append(f"当前阶段目标：{stage_goal}")
    if decision.stage_result is not None:
        stage = decision.stage_result
        lines.append(f"阶段状态：{stage.status.value}，阶段分数：{stage.stage_score:.3f}")
    diagnosis = _whitelisted_diagnosis(detail)
    if diagnosis:
        lines.append(f"结构化诊断：{diagnosis}")
    ready = _ready_frontier(task_case, state)
    if ready:
        lines.append(f"候选前沿：{ready}")
    return "\n".join(lines)[:INTERVENTION_TEXT_LIMIT]


## 内部函数

def trigger_stage_id(decision: RuntimeEvaluationDecision) -> str:
    """读取可公开的触发阶段标识；缺失时回退到白名单终止详情字段。"""
    if decision.stage_result is not None and decision.stage_result.stage_id.strip():
        return decision.stage_result.stage_id
    detail = decision.termination.termination_detail if isinstance(decision.termination.termination_detail, dict) else {}
    for key in ("milestone_id", "most_promising_milestone_id", "failure_basis"):
        value = detail.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return "unknown"


def _whitelisted_diagnosis(detail: JsonObject) -> str:
    """提取白名单中的候选分数、状态、证据与终止代码。"""
    allowed = {"code", "failure_basis", "candidate_scores", "best_score", "best_status", "evidence"}
    payload = {str(key): detail[key] for key in allowed if key in detail}
    candidate = next(
        (
            item
            for item in reversed(payload.get("candidate_scores", []))
            if isinstance(item, dict) and item.get("selected") is True
        ),
        None,
    ) if isinstance(payload.get("candidate_scores"), list) else None
    if candidate is not None:
        score = candidate.get("score")
        if isinstance(score, dict):
            payload["selected_score"] = {
                key: score[key] for key in ("score", "status", "evidence") if key in score
            }
        payload.pop("candidate_scores")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _ready_frontier(task_case: TaskCase, state: RuntimeEvaluationState) -> str:
    """生成 ready frontier 的 id/name 白名单摘要。"""
    names = {
        node.milestone_id: node.name
        for node in task_case.milestone_graph.nodes
    } if task_case.milestone_graph is not None else {}
    return ", ".join(
        f"{milestone_id}({names.get(milestone_id, 'unknown')})"
        for milestone_id in state.milestone_frontier.ready_ids[:6]
    )
