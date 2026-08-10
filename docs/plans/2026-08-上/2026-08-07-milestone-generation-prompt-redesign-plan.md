# Milestone 生成 Prompt 重构方案

## 1. 背景与问题诊断

当前 `dynsteer/prompt/templates/milestone/generation.en.md` 只给出了一个非常短的输出 schema，并把完整的 `GeneratorTaskView` 作为一个大 JSON 直接塞给模型。模型需要自己完成以下工作：

1. 从多层嵌套的 `public_assets`、`tool_schema` 和 evidence catalog 中找出可引用的值；
2. 推断 `source_ref`、`evidence_id`、`expected_policy` 和 `expected` 之间的严格对应关系；
3. 设计 atom、路径和终点；
4. 同时满足 compiler 对重复 atom、未知引用、公开 literal、终端路径和路径差异性的全部约束。

这些规则没有被拆成模型可执行的步骤，也没有提供“允许值表”。结果是模型经常输出自然语言 expected、错误 evidence_id 或不存在的 atom_id，最终所有路径被 compiler 拒绝。当前 partial 实验报告已经出现：

- `expected 不是 source_ref 的公开 literal`；
- `引用未知 evidence_id`；
- `路径引用未知 atom`；
- `有效差异路径少于 3 条`（现有固定阈值；本方案改为由请求路径数动态计算）。

本方案只重构生成 prompt 和输入 payload 的组织方式，不放宽 compiler 校验，也不把无效输出自动修正为有效 graph。可靠性实验必须继续能够区分“模型生成成功”和“模型生成被拒绝”。

## 2. 设计目标

### 2.1 模型必须明确生成的对象

模型生成的是一个“可执行 milestone graph 候选”，而不是任务总结：

- `atoms` 是可被 evaluator 检查的单个进展事实；
- `paths` 是由 atom ID 组成的候选执行顺序；
- `minefields` 是可选的禁止行为候选；
- graph 的节点语义来自 evidence，不来自模型自行编造的文本；
- path 是 synthetic alternative，不声称是唯一 gold path。

### 2.2 可靠性约束

- 所有 `evidence_id`、`source_ref`、`invariant_id` 必须来自显式 allowlist；
- `expected_policy=public_literal` 时，`expected` 只能从对应 evidence 的 `allowed_expected_literals` 中逐字复制；
- `expected_policy=none` 时，`expected` 必须为 `null`；
- 每个 atom 只绑定一个 evidence；
- 每条 path 必须引用已定义 atom，且最后一个 atom 必须 `terminal=true`；
- 请求模拟路径数 `N` 必须至少为 5；模型以生成不少于 `N` 条差异路径为目标；
- compiler 接受的有效差异路径数量范围为 `[N-1, +∞)`，允许严格校验淘汰一条候选，也不限制模型返回更多有效路径；
- 不输出工具参数、隐藏状态、API key、原始 prompt 或 compiler 解释。

## 3. 新版 Prompt

新版模板建议使用以下英文正文和中文正文。关键变化是把“先筛选 allowlist，再生成 atom，再生成 path，最后自检”写成固定步骤，并要求输入提供规范化 evidence 表，而不是让模型从大 JSON 中猜测。通用 `OUTPUT SHAPE` 只展示有实际 expected 值的 public-literal atom，避免模型把 `null` 理解为默认输出内容。

```text
You are a milestone-graph compiler. Your output is an executable candidate graph for
evaluating an agent trajectory. Do not summarize the task and do not invent facts.

You will receive:
- TASK: only the public user objective and public environment/tool information needed
  to derive observable milestones. Do not include benchmark names, case IDs, dataset
  names, file paths, or experiment metadata;
- EVIDENCE: a closed table of evidence records;
- INVARIANTS: a closed table of invariant records;
- PATH_COUNT: the requested number of alternative paths.

Each evidence record has:
- evidence_id: the only ID allowed in an atom;
- source_ref: the public source containing the value;
- target, selector, operator: the evaluator check represented by the atom;
- expected_policy: either public_literal or none;
- allowed_expected_literals: the complete set of values that may be copied into expected.

Follow these steps exactly.

STEP 1 — Build the atom catalog.
For each atom, choose exactly one evidence_id from EVIDENCE. Copy its source_ref into
source_refs. If expected_policy is public_literal, copy exactly one value from that
record's allowed_expected_literals. Do not paraphrase, translate, normalize, or derive
the value. If expected_policy is none, use expected=null. The atom name and description
must describe the observable check, not an imagined implementation detail.

STEP 2 — Mark terminal atoms.
Mark terminal=true only for an atom that represents task completion or the required
terminal tool result. Every path must end at a terminal atom. Do not create a terminal
atom merely because it sounds important.

STEP 3 — Build alternative paths.
Return at least PATH_COUNT distinct paths. You may return more when the public evidence
supports additional meaningful alternatives; there is no maximum path count. Every atom_ids entry must refer to an
atom in the same response. Paths must be different sequences, not copies with different
strategy names. Use the available evidence to vary ordering or optional preparation;
never invent a tool, state, or hidden prerequisite. Each path must end in terminal=true.
Choose applicable strategies from: direct-shortest, prerequisite-first, state-check-first,
artifact-or-result-first, alternative-tool, verification-first, and conservative. Strategy
names do not make paths distinct; atom sequences must actually differ.

STEP 4 — Add minefields only when grounded.
Use only invariant_id values from INVARIANTS. If no invariant is useful, return [].
Minefield expected follows the invariant evidence record's expected_policy and allowed
values.

STEP 5 — Perform a silent checklist before responding.
- top-level keys are exactly atoms, paths, minefields;
- every evidence_id, invariant_id and atom_id is allowlisted;
- every public_literal expected is copied from the matching allowed list;
- the response contains at least PATH_COUNT distinct paths; additional valid paths are allowed;
- every path is non-empty, unique, atom-valid, and ends at a terminal atom;
- no duplicate atom_id and no extra JSON fields.

Return JSON only. Do not include markdown, comments, explanations, or code fences.

INPUT
TASK: {task}
EVIDENCE: {evidence}
INVARIANTS: {invariants}
PATH_COUNT: {path_count}

OUTPUT SHAPE
{{
  "atoms": [
    {{
      "atom_id": "a1",
      "name": "...",
      "description": "...",
      "source_refs": ["..."],
      "evidence_id": "...",
      "expected": "copy one allowed literal exactly",
      "terminal": true
    }}
  ],
  "paths": [
    {{"strategy": "shortest", "atom_ids": ["a1"]}}
  ],
  "minefields": []
}}
```

### 3.1 中文 Prompt 正文

```text
你是 milestone graph 编译器。你的输出是用于评估 Agent 轨迹的可执行候选图，
不是任务总结。不要编造事实，不要输出实现猜想。

你将收到：
- TASK：只包含推导可观察 milestone 所必需的公开用户目标、环境和工具信息；不得包含
  benchmark 名称、case ID、数据集名称、文件路径或实验元数据；
- EVIDENCE：封闭的 evidence 表；
- INVARIANTS：封闭的 invariant 表；
- PATH_COUNT：要求生成的候选路径数量。

每条 evidence 记录包含：
- evidence_id：atom 唯一允许引用的 ID；
- source_ref：公开值所在的来源；
- target、selector、operator：该 atom 对应的 evaluator 检查；
- expected_policy：只能是 public_literal 或 none；
- allowed_expected_literals：expected 可以复制的完整值集合。

严格执行以下步骤：

步骤 1——建立 atom 目录。
每个 atom 必须选择 EVIDENCE 中存在的一个 evidence_id，并逐字复制对应 source_ref。
当 expected_policy=public_literal 时，expected 必须从该 evidence 的
allowed_expected_literals 中逐字复制一个值；禁止改写、翻译、归一化或推导。
当 expected_policy=none 时，遵循该 evidence 的明确规则，不要自行填写值。
atom 的名称和描述只说明可观察的 evaluator 检查，不描述臆造的内部实现。

步骤 2——标记 terminal atom。
只有表示任务完成或必需终止检查的 atom 才能 terminal=true。每条路径必须以
terminal=true 的 atom 结束。不要因为某个 atom 看起来重要就把它标记为 terminal。

步骤 3——建立差异路径。
至少返回 PATH_COUNT 条差异路径。公开 evidence 能支持更多有意义的替代方案时，可以返回更多路径，
路径数量不设上限。每条 atom_ids 都必须引用本次响应中定义的 atom，
并且路径序列必须真正不同，不能只修改 strategy 名称。只能利用已有 evidence 表达
顺序差异或可选准备步骤，不能添加隐藏前置条件。每条路径都必须以 terminal atom 结束。
按证据适用性从以下策略中选择：直接最短路径、前置条件优先、状态检查优先、产物或结果优先、
替代工具、结果验证优先、保守路径。strategy 名称不同不代表路径不同，atom 序列必须真实不同。

步骤 4——仅在有依据时生成 minefield。
只能使用 INVARIANTS 中存在的 invariant_id。如果没有合适的 invariant，返回空数组。
minefield 的 expected 必须遵循其 invariant evidence 的 expected_policy 和允许值规则。

步骤 5——输出前静默自检。
- 顶层 key 必须且只能是 atoms、paths、minefields；
- 每个 evidence_id、invariant_id、atom_id 都在对应 allowlist 中；
- 每个 public_literal expected 都逐字复制自匹配的允许值；
- 响应至少包含 PATH_COUNT 条差异路径，允许增加更多有效路径；
- 每条 path 非空、互不重复、atom 有效并以 terminal atom 结尾；
- atom_id 不重复，不增加额外 JSON 字段。

只返回 JSON，不要 markdown、注释、解释或代码围栏。

输入：
TASK：{task}
EVIDENCE：{evidence}
INVARIANTS：{invariants}
PATH_COUNT：{path_count}

输出形状：
{{
  "atoms": [
    {{
      "atom_id": "a1",
      "name": "...",
      "description": "...",
      "source_refs": ["..."],
      "evidence_id": "...",
      "expected": "逐字复制一个允许的公开值",
      "terminal": true
    }}
  ],
  "paths": [
    {{"strategy": "shortest", "atom_ids": ["a1"]}}
  ],
  "minefields": []
}}
```

## 4. 具体 case 的渲染 Prompt 示例

以下示例采用 `add_reminder_content_and_date_and_time`，`PATH_COUNT=5`。实际实现时，payload builder 必须把当前 `GeneratorTaskView` 转换成下列扁平结构；不再只传递一个未经解释的大型 `task` JSON。`benchmark` 和 `case_id` 不进入 prompt，因为它们不提供 milestone 语义。

```text
You are a milestone-graph compiler. Your output is an executable candidate graph for
evaluating an agent trajectory. Do not summarize the task and do not invent facts.

TASK
- user objective: Remind me to buy chocolate milk 3/22/2024 5PM
- available tool names: add_reminder, datetime_info_to_timestamp

EVIDENCE (closed allowlist)
1. evidence_id=agent_message_instruction
   source_ref=instruction
   target=step, selector=$.content, operator=fuzzy_match
   expected_policy=public_literal
   allowed_expected_literals=["Remind me to buy chocolate milk 3/22/2024 5PM"]

2. evidence_id=tool_result_present
   source_ref=instruction
   target=tool_result, selector=$, operator=added
   expected_policy=none
   expected rule: use the compiler-defined empty-value rule; do not invent a placeholder

3. evidence_id=tool_call_0
   source_ref=tool:0:name
   target=tool_call, selector=$.name, operator=equals
   expected_policy=public_literal
   allowed_expected_literals=["add_reminder"]

4. evidence_id=tool_call_1
   source_ref=tool:1:name
   target=tool_call, selector=$.name, operator=equals
   expected_policy=public_literal
   allowed_expected_literals=["datetime_info_to_timestamp"]

INVARIANTS
- invariant_id=invariant_0, evidence_id=invariant_message_0, expected_policy=public_literal
- invariant_id=invariant_1, evidence_id=invariant_message_1, expected_policy=public_literal
- invariant_id=invariant_2, evidence_id=invariant_message_2, expected_policy=public_literal
- invariant_id=invariant_3, evidence_id=invariant_message_3, expected_policy=public_literal
- invariant_id=invariant_4, evidence_id=invariant_message_4, expected_policy=public_literal

PATH_COUNT: 5

Remember: expected must be copied from the matching allowed_expected_literals list.
Return JSON only with exactly atoms, paths, and minefields.
```

## 5. 合格的样例输出

下面是上述 case 的一个合格候选输出。它只使用 allowlist 中的 ID 和 literal；五条路径不同，且都以 `a2` 这个 terminal atom 结束。示例刻意不展示 `expected_policy=none` 的 evidence，避免把空值当成常规输出模式。

```json
{
  "atoms": [
    {
      "atom_id": "a1",
      "name": "Resolve reminder time",
      "description": "The agent uses the public datetime conversion tool.",
      "source_refs": ["tool:1:name"],
      "evidence_id": "tool_call_1",
      "expected": "datetime_info_to_timestamp",
      "terminal": false
    },
    {
      "atom_id": "a2",
      "name": "Add the requested reminder",
      "description": "The agent calls the public reminder creation tool.",
      "source_refs": ["tool:0:name"],
      "evidence_id": "tool_call_0",
      "expected": "add_reminder",
      "terminal": true
    },
    {
      "atom_id": "a3",
      "name": "Preserve the user instruction",
      "description": "The agent acts on the stated reminder request.",
      "source_refs": ["instruction"],
      "evidence_id": "agent_message_instruction",
      "expected": "Remind me to buy chocolate milk 3/22/2024 5PM",
      "terminal": false
    }
  ],
  "paths": [
    {"strategy": "direct-shortest", "atom_ids": ["a2"]},
    {"strategy": "prerequisite-first", "atom_ids": ["a1", "a2"]},
    {"strategy": "instruction-first", "atom_ids": ["a3", "a2"]},
    {"strategy": "instruction-then-prerequisite", "atom_ids": ["a3", "a1", "a2"]},
    {"strategy": "prerequisite-then-instruction", "atom_ids": ["a1", "a3", "a2"]}
  ],
  "minefields": []
}
```

在 compiler 的三分之二共识规则下，5 条路径要求 atom 至少出现 4 次。上述示例中 `a2`
进入 graph；`a1` 和 `a3` 作为非共识的候选准备步骤保留在生成审计结果中，不进入最终 graph。
预期生成 graph 的核心结构为：

```text
a2 (add_reminder, terminal)
```

## 6. 实施修改范围

1. 新增 payload builder，将 `GeneratorTaskView` 投影为 `TASK/EVIDENCE/INVARIANTS/PATH_COUNT` 四段；
   `TASK` 明确排除 benchmark、case_id、数据集名称、实验 ID 和路径等非语义字段；
2. 对每个 `public_literal` evidence 预先计算 `allowed_expected_literals`，由代码提供给模型；
3. 更新 `generation.en.md` 与 `generation.zh.md`，保持模板只负责渲染，不在 prompt 内执行动态逻辑；
4. 保留现有 compiler 的严格校验，不在解析失败时自动替换 expected 或 atom_id；
5. 在生成报告中继续记录 rejection reasons，并增加 evidence allowlist 摘要，禁止记录完整 prompt/API key；
6. 保持 `milestone_reliability.py` 的 graph、GED、输出路径和实验 schema 不变。
7. 删除 `dynsteer/milestone/compiler.py` 中固定的最低有效路径阈值，改为
   `minimum_valid_paths = config.simulated_path_count - 1`；当有效差异路径数小于该值时拒绝，
   大于等于该值时继续编译，不设置上限；
8. 将 `MilestoneGenerationConfig.simulated_path_count` 的最小允许值从 3 提升到 5，默认值保持 6；
9. rejection report 同时记录 `requested_path_count`、`minimum_valid_path_count` 和
   `actual_valid_distinct_path_count`，避免只显示模糊的“路径不足”；
10. 更新所有相关单元测试：当 `N=5` 时 3 条有效路径必须拒绝，4 条及以上允许继续；
    当 `N=6` 时 4 条必须拒绝，5 条及以上允许继续；超过 N 的有效路径必须被完整接受。

## 7. 验收标准

- 使用上述具体 case 的 mock LLM、请求 `N=5` 并返回 4 条或更多有效差异路径时，
  `compile_task_case()` 返回 `generation_status=generated`；
- expected 改成任意自然语言后，必须返回 `generation_status=auto_rejected`；
- 对任意 `N>=5`，只有少于 `N-1` 条有效差异路径时才拒绝；`N-1`、`N` 和超过 `N`
  的有效差异路径都允许继续共识编译；
- 未知 evidence_id、未知 atom_id、重复 atom_id、非 terminal path、重复 path 都必须被拒绝；
- 真实 Qwen 调用时，终端只显示 tqdm 进度条和简短状态，不打印完整 prompt 或响应；
- partial 配置至少能区分 `completed`、`generation_rejected`、`generation_failed` 三类状态，而不是所有异常都归为同一类。

## 8. 未覆盖部分

本方案暂不修改 compiler 的证据语义、共识阈值或 GED 逻辑；如果新版 prompt 仍然在真实模型上大量拒绝，应先保存脱敏后的结构化 rejection statistics，再决定是否调整模型、输出 schema 或 evidence 投影，不能通过放宽校验掩盖生成质量问题。
