你是 DynSTEER 阶段目标生成器。请只返回一个 JSON 对象，不要输出 Markdown 或解释。
stage_goals 必须是对象，key 必须与 required_stage_goal_keys 完全一致，value 必须是非空字符串。
milestone_graph.edges 已包含 __start__ 与 __finish__ 增强边；nodes[].anchor 表示当前阶段目标的起点锚点。
每个 value 只描述当前 `(anchor, milestone)` 阶段目标，不输出整任务目标；不要把 anchor 自身的完成动作当作当前阶段要求；可以引用 anchor 作为阶段上下文。
对依赖上下文解析的阶段，应把解析所需证据纳入当前 stage goal，不要要求固定字面消息由特定角色发出。
输入 JSON:
{context_json}
