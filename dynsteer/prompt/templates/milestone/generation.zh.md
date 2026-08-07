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
