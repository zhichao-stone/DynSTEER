硬性有效性规则：avoidance target 只是待检验假设，绝不是删除必需 operation 的命令。每条返回路径都必须独立完成完整 instruction；输出前淘汰只有 producer、只有 conversion 或使用编造动态参数的局部路径。

审查并重写为 1～{max_candidate_path_count} 条完整最终路径集合。输出 schema 与第一轮完全相同。

公开任务输入：{task}
第一轮原始响应：{original_response}
确定性校验错误：{validation_violations}
第一轮可模拟规范化路径：{simulated_paths}
初步共同 operation（需要寻找反例）：{common_operations}

一次完成：
1. 删除或修复结构错误、未知 evidence 和环境不可执行路径；
2. 重新核查每条路径都完整完成原 instruction，漏步路径不得进入最终集合；
3. 对每个初步共同 operation，尝试规划一条不调用它但仍完整完成任务的路径；确实无法绕过时保留它。

initial_state 已直接给出的状态不需要 getter，必须从最终集合删除仅确认该状态的 getter。公开输入明确说明信息不足或终端写操作不可完成时，不得用探索性 search 伪造 executable 路径；应返回空 operations，并一致禁止会造成错误副作用的终端写工具。

逐对审查没有数据或环境依赖的 operation；若当前集合只展示一个顺序，必须补充交换顺序后仍完整可行的路径。若可见工具中没有产生请求最终效果的终端工具，search/get-only 路径必须删除并改为 `response_only` 空路径。

disposition 只能使用 `executable`、`needs_clarification`、`no_action`、`response_only`，工具不可用时用 `response_only`，不得输出 `non_executable`。不得为绕过候选 operation 编造动态参数；计算或写入依赖的业务 ID、timestamp 等必须由同一路径 producer operation 产生，只有设备设置状态可来自 initial_state。

原始 initial_state 业务行不是 Agent 可直接读取的参数来源；联系人、消息、提醒 ID 与业务 timestamp 必须由可见 search/get producer 获取。自然语言日期写入必须保留 datetime/timestamp 转换；latest/oldest/yesterday/week delta 必须保留当前时间与完整转换链。信息 producer 与不相关 recovery 的相对顺序必须在最终集合中双向覆盖。

只返回审查后的完整最终 paths JSON，不要只返回新增反例，不要输出说明。

最终检查：每条 executable 路径都包含请求的终端效果/答案与全部所需 producer/conversion；每条 response_only 路径 operations 为空，并一致禁止会造成无效副作用的可见终端工具。
