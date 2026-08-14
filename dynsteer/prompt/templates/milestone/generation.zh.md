您负责生成候选 milestone goal graph，而不是逐步执行轨迹。在一张候选图的完整、可行任务解释下，只有每种有效完成方式都必须经过的操作或目标，才能成为该图的节点。单张候选图只是对必经性的一个判断；跨候选的稳定必经性由后续代码聚合决定。

当前是第 {batch_index} 批候选。审查重点代码：`{focus_code}`。
本批审查重点：{focus_instruction}

请为当前任务返回恰好 2 张完整、独立的候选图。

独立性与多样性规则：
- 每张图必须独立定义 dispositions、nodes、edges、minefields；local ID 仅在本图内有效。
- 禁止共享节点表、引用另一张图的节点，也禁止把第 2 张图写成第 1 张图的 base/delta/patch。
- 两张完整判断之间应寻找真实差异：不同的可见工具、直接使用公开信息、合法的批量状态目标、不同的必要依赖，或有依据的可执行性判断。
- 禁止通过遗漏必需 producer、conversion、recovery、终态 goal 或用户答案，通过加入无关工具，或者通过编造参数来制造多样性。
- 如果当前证据不支持真实替代方案，两张图可以等价；完整性和正确性优先于人为差异。

紧凑结构样例

以下样例中的 evidence ID、selector 和 source reference 只用于说明结构。处理当前任务时，只能使用当前任务 JSON 中存在的 evidence ID 和公开 source reference。

样例 1
任务："What's my relationship with +10000000000"
含义：联系人搜索产生动态答案，随后 Agent 必须回答用户。

{{
  "dispositions": {{"turn_0": "executable"}},
  "nodes": [
    {{
      "local_id": "n0",
      "turn_id": "turn_0",
      "kind": "tool_call",
      "evidence_id": "example_search_contacts",
      "arguments": {{
        "phone_number": {{
          "source": "public_literal",
          "source_ref": "instruction:0",
          "value": "+10000000000"
        }}
      }}
    }},
    {{
      "local_id": "n1",
      "turn_id": "turn_0",
      "kind": "emit_message",
      "sender": "AGENT",
      "recipient": "USER",
      "content_requirement": "使用搜索返回结果回答所请求电话号码对应的关系"
    }}
  ],
  "edges": [["n0", "n1"]],
  "minefields": []
}}

样例 2
任务："Remove my upcoming reminder."
含义：当前时间与提醒搜索是识别 upcoming reminder 所需的两个独立 producer，二者共同支持删除状态目标；两个 producer 之间没有边。

{{
  "dispositions": {{"turn_0": "executable"}},
  "nodes": [
    {{
      "local_id": "n0",
      "turn_id": "turn_0",
      "kind": "tool_call",
      "evidence_id": "example_get_current_timestamp",
      "arguments": {{}}
    }},
    {{
      "local_id": "n1",
      "turn_id": "turn_0",
      "kind": "tool_call",
      "evidence_id": "example_search_reminder",
      "arguments": {{}}
    }},
    {{
      "local_id": "n2",
      "turn_id": "turn_0",
      "kind": "set_state",
      "namespace": "REMINDER",
      "operation": "remove",
      "cardinality": "one",
      "match": {{
        "reminder_id": {{
          "source": "node_output",
          "producer_local_id": "n1",
          "selector": "$.reminders[0].reminder_id",
          "cardinality": "one"
        }}
      }},
      "values": {{}},
      "executor_evidence_id": "example_remove_reminder"
    }}
  ],
  "edges": [["n0", "n2"], ["n1", "n2"]],
  "minefields": []
}}

在该样例中，当前时间对目标选择不可缺少，但它不是 REMINDER 记录字段，因此不得编造成 `match` key；它指向状态 goal 的 edge 表达目标选择依赖，选中的 reminder ID 则从搜索输出动态绑定。样例 selector 仍只是示意，当前任务必须替换为当前 producer contract 支持的 selector。

当前公开任务 JSON

{task}

必须遵循的语义

1. Turn 与 disposition
- 每张图的 `dispositions` 对象必须恰好包含 `turns` 中的全部 turn ID，不得缺少或增加 key。
- 每个值只能是 `executable`、`needs_clarification`、`no_action`、`response_only` 之一。
- 按任务原始顺序覆盖所有 turn；每个节点和 minefield 必须归属一个真实存在的 turn ID。
- 非 `executable` turn 不得含有 `tool_call` 或 `set_state`；只有确实需要回复用户时，才可包含 `emit_message`。
- 每个 `executable` turn 必须包含最终 goal：`set_state`、`emit_message`，或者在状态目标和消息目标都不适用时，能够直接、完整产生请求结果的 terminal `tool_call`。
- 只有 search/getter/producer/conversion/recovery 节点而没有最终 goal 的图不完整且非法。

2. 必经性、可行性与 goal
- 只保留在本图完整判断下确实必经的 milestone。某操作仅仅有用、常见、防御性强或用于确认，并不足以保留；缺少其输出或效果会导致任务无法正确完成时才保留。
- 只有保留的 consumer 需要某动态输出，或者该观测值对于最终答案或目标选择不可缺少时，producer 才是必需的。
- 请求的业务状态变化表示为一个 `set_state` goal。把真实终态工具的 evidence ID 写入 `executor_evidence_id`，不得再为相同效果输出重复的 terminal `tool_call`。
- search、getter、conversion、recovery 只有作为真正必需的 producer 或前置条件时，才保留为独立 `tool_call` 节点。
- 必须向用户作答时使用 `emit_message`；`content_requirement` 只描述答案必须传达什么，不得编造运行时才知道的答案。
- 公开数据已经足够时直接使用，不要添加只为再次确认相同公开值的 getter。
- 可见工具或公开输入无法安全完成任务时，不得编造完成方式；应使用适当的不可执行 disposition，以及合法的空图或仅回复图。

3. 节点 schema 与 provenance
- node kind 只允许 `tool_call`、`set_state`、`emit_message`。禁止输出 `preserve_state`，它由代码派生。
- `tool_call` 节点恰好包含 `local_id`、`turn_id`、`kind`、`evidence_id`、`arguments`。
- `evidence_id` 必须是当前 `evidence_catalog` 中的 TOOL_CALL evidence ID；禁止使用样例 ID 或编造 ID。
- `arguments` 只能包含该工具接受的参数名。每个参数值必须严格采用下面两种 binding 之一，不得在 `arguments` 中直接写原始 scalar。

公开 literal binding：
{{"source":"public_literal","source_ref":"当前公开来源中的精确 reference","value":"该来源中出现的 literal 值"}}

节点输出 binding：
{{"source":"node_output","producer_local_id":"本图内的 producer","selector":"$.field","cardinality":"one"}}

- `public_literal.source_ref` 必须精确指向公开该值的当前 instruction/turn 或 Agent-visible public asset；只有当前任务 JSON 明确向 Agent 暴露的 public state 才能使用。
- `node_output` 的 producer 必须是本图内更早且可达的 `tool_call`；selector 与 `one`/`all` cardinality 必须符合 producer 的真实输出。
- 每个 node-output binding 都必须有 producer 指向 consumer 的 edge。
- 禁止使用 `derived` source。所需的查询、计算、日期时间转换、坐标转换或 recovery 必须表示为真实可见的 `tool_call`，其输出再通过 `node_output` 引用。
- 禁止把动态 UUID、row ID、person/message/reminder ID、timestamp、坐标、token 或工具产出值硬编码或复制成 `public_literal`，除非允许使用的当前公开来源明确给出了该精确值。

`set_state` 节点恰好具有以下结构：
{{
  "local_id": "n_goal",
  "turn_id": "turn_0",
  "kind": "set_state",
  "namespace": "CONTACT|MESSAGING|REMINDER|SETTING 或当前支持的其他 namespace",
  "operation": "add|update|remove|set",
  "cardinality": "one|all",
  "match": {{}},
  "values": {{}},
  "executor_evidence_id": "当前终态 TOOL_CALL evidence ID"
}}

- `match` 和 `values` 中的每个值都必须使用相同的两种 binding 之一。
- `match` 用于选择目标记录，`values` 描述新增或修改字段。
- `add` 的 `values` 非空且 `match` 可以为空；`update` 或 `set` 的 `values` 非空；`remove` 的 `match` 非空；cardinality 只能使用 `one` 或 `all`。
- `executor_evidence_id` 必须指向当前可见且确实能以所表达输入完成该 namespace 和 operation 的终态工具。

`emit_message` 节点恰好具有以下结构：
{{
  "local_id": "n_answer",
  "turn_id": "turn_0",
  "kind": "emit_message",
  "sender": "AGENT",
  "recipient": "USER",
  "content_requirement": "非空的答案要求"
}}

4. Edge
- `edges` 是 `[source_local_id, target_local_id]` 二元数组的数组，端点必须是同一张图中的节点。
- 只有真实且不可交换的数据依赖、前置条件依赖或 goal 依赖才添加 edge；节点数组顺序不表示 edge。
- 不得仅因为习惯上先调用其中一个，就为两个独立 producer 排序。如果二者独立地输入同一 consumer，应分别连接到 consumer，两个 producer 之间不连边。
- 不要仅为重复对话顺序而添加跨 turn edge；代码会确定性加入 turn 顺序依赖。
- 禁止自环、重复边、缺失端点和环。

5. Minefield 与合法空图
- minefield 表示某个 turn 中绝不能发生的 fatal 副作用工具调用，其结构恰好为：
{{
  "turn_id": "turn_0",
  "evidence_id": "当前终态 TOOL_CALL evidence ID",
  "severity": "fatal",
  "reason_code": "missing_required_input|tool_unavailable|unsafe_side_effect",
  "missing_inputs": []
}}
- severity 只能是 `fatal`，reason code 只能是列出的三种。只有确实缺少必需输入时才填写 `missing_inputs`，否则使用空数组。
- evidence 必须指向当前真实存在且有副作用的终态工具，reason 必须有当前公开任务依据；不得仅根据工具名称猜测危险。
- 同一 turn 的相同终态效果不得同时出现在 executable goal 和 fatal minefield 中。
- 候选图可以合法地具有空 `nodes` 和空 `edges`。如果不需要安全工具操作或答案 milestone，`minefields` 也可以为空；如果任务被阻塞且尝试终态副作用会造成 fatal 错误，则加入有依据的 minefield。

6. 精确响应结构
- 只返回一个 JSON 对象，顶层只能有 `graphs` 这一个 key。
- `graphs` 必须恰好包含 2 个 graph 对象。
- 每个 graph 对象恰好包含 `dispositions`、`nodes`、`edges`、`minefields`。
- 禁止增加 description、rationale、reasoning、confidence、quality、strategy、shared nodes、metadata、comment 或任何其他字段。
- 只返回原始有效 JSON；禁止 Markdown fence，禁止在 JSON 前后输出说明。

返回前最终核对
- 是否恰好两张独立且完整的图？
- dispositions 是否恰好覆盖当前全部 turn？
- 每个 executable turn 是否具有最终状态、动作或答案 goal？
- 是否不存在 producer-only 或 search-only 的 executable 图？
- 所有 evidence ID 和公开 source reference 是否都来自当前任务？
- 每个动态值是否都有本图内可达 producer 和 edge？
- edge 是否只有真实依赖，独立 producer 是否保持无序？
- 是否没有编造 ID、timestamp、坐标、selector、参数、工具能力或额外字段？
- 必要时是否使用空的不可执行图，而不是编造完成方式？

只按以下形状返回，并填入当前 turns 和任务内容：
{{"graphs":[{{"dispositions":{{}},"nodes":[],"edges":[],"minefields":[]}},{{"dispositions":{{}},"nodes":[],"edges":[],"minefields":[]}}]}}
