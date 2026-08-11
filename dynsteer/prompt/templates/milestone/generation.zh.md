硬性有效性规则：每条返回路径都必须独立完成完整原始 instruction。禁止返回只有 producer、只有 conversion，或用编造参数驱动终端工具的局部路径；多样性只能存在于完整路径之间。

请基于以下全部公开输入，模拟规划 1～{max_candidate_path_count} 条有差异且完整可行的执行路径：

{task}

要求：
- 每条路径必须以相同顺序覆盖全部 turns；每个 executable turn 必须包含完成原 instruction 的完整工具链。
- disposition 只能是 `executable`、`needs_clarification`、`no_action`、`response_only`；工具不可用且只能向用户解释时使用 `response_only`，绝不能输出 `non_executable` 这个非法值。
- 尽量采用不同工具、不同公开信息来源，或交换相互独立操作的顺序；不得通过漏掉必需步骤制造多样性。
- initial_state 已直接公开状态时，路径必须直接使用该状态，不得再加入仅确认同一状态的 getter；这类 getter 不是必需步骤。环境阻塞可显式加入 recovery，compiler 也会按 environment_rules 补入。
- instruction 或公开系统消息明确说明信息不足、工具不可用或不能完成终端写操作时，使用 `response_only` 或 `needs_clarification`、空 operations，并在 forbidden_evidence_ids 中列出会造成错误副作用的终端写工具；不得用探索性 search 假装任务已经可执行。
- executable 路径必须包含真正产生请求最终状态或答案的终端工具；如果可见 tool_schema 中没有能完成该最终效果的工具，search/get 单独存在仍是不完整路径，必须改为 `response_only` 且 operations 为空。
- 不得用编造的动态 ID、timestamp、坐标或其他中间值绕过其 producer operation。计算、转换或终端写入所需的每个动态业务输入，都必须来自同一路径中更早的 producer operation；只有明确设备设置状态可直接来自 initial_state。反例路径不得通过漏掉 producer 伪造绕过。
- initial_state 中的原始业务记录只用于 compiler 的环境模拟，不代表 Agent 能直接读取隐藏的联系人、消息、提醒 ID 或 timestamp，也不得直接复制为工具参数。只有明确的设备设置状态可用于跳过 status getter；业务数据必须通过可见 search/get 工具取得。若缺少读取所需 namespace 的工具，任务不可执行。
- 自然语言日期/时间写入必须先调用可见的 datetime/timestamp 转换工具；latest、oldest、yesterday、week delta 等相对时间或 recency 条件必须先调用当前时间工具并沿完整转换链计算，不能由模型心算或硬编码 timestamp。
- 对没有数据依赖或环境依赖的两个 operation，最终路径集合必须尽量同时包含 A→B 与 B→A 两种顺序；不得让任意采样顺序变成虚假的共同拓扑。recovery 只需先于它实际解锁的工具，不必先于与该状态无关的 search/get。
- 信息 producer 与不相关的环境 recovery 如果都只需要先于同一个终端工具，它们彼此独立；最终集合必须至少包含一条 producer→recovery 和一条 recovery→producer 路径。
- 只能引用 evidence_catalog 中的 TOOL_CALL evidence_id；arguments 是普通 JSON 对象。
- 只有无需工具，或可见工具/信息确实无法执行任务时，operations 才能为空。
- executable turn 的 forbidden_evidence_ids 必须为空；其他 disposition 可列出明确禁止调用的工具 evidence。
- 不得输出 milestone、edge、minefield、quality、binding、slot、effect、strategy 或自由说明。

返回前逐条淘汰：缺少请求的终端效果/答案工具，或缺少动态参数所需 producer/conversion 的任何 executable 路径。最终保留的所有 executable 路径必须包含相同的完整必需效果链。

只返回：
{{"paths":[{{"turns":[{{"turn_id":"turn_0","disposition":"executable","operations":[{{"evidence_id":"tool_call_id","arguments":{{}}}}],"forbidden_evidence_ids":[]}}]}}]}}
