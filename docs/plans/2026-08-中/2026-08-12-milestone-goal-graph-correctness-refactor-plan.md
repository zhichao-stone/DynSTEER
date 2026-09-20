# 2026-08-12 Milestone Graph 生成与聚合最终代码修改方案

## 1. 最终代码流程

将现有“多条执行路径模拟、operation 交集、共同顺序和二轮反例修复”整体替换为以下唯一流程：

```text
构造公开 GeneratorTaskView
  → 每批请求 2 张互相独立、各自完整的候选 milestone graph
  → 最多 4 批，累计目标 6 张有效候选
  → 逐图解析、结构校验、工具参数校验和 provenance 校验
  → 同批等价图只接纳一张；跨批等价图保留为重复观测
  → 对候选观测做节点、disposition、edge、minefield 严格多数聚合
  → 加入 binding、recovery 和 turn 顺序确定性依赖
  → 执行聚合后闭包校验
  → 编译 Milestone、Constraint、Minefield
  → 派生 preserve_state、传递约简并生成最终 MilestoneGraph
```

每批候选互不共享节点表，不使用 base graph、delta 或上一批候选。正常情况下 3 批得到 6 张候选；前三批不足时，第 4 批只用于补足。达到 6 张立即停止，第 4 批后仍不足则聚合已有候选。

## 2. 数据结构与配置修改

### 2.1 `dynsteer/milestone/model.py`

#### 修改 `MilestoneGenerationConfig`

删除：

```python
max_candidate_path_count: int = 6
enable_repair: bool = True
```

改为：

```python
@dataclass(frozen=True)
class MilestoneGenerationConfig:
    use_origin_milestone: bool = True
    target_candidate_graph_count: int = 6
    max_candidate_batch_count: int = 4
    generator: JsonObject = field(default_factory=dict)
```

`__post_init__()` 必须校验：

- `target_candidate_graph_count` 是非 bool 的整数，范围 2～8；
- `max_candidate_batch_count` 是非 bool 的整数，范围 1～4；
- `target_candidate_graph_count <= 2 * max_candidate_batch_count`，防止配置一个必然无法达到的目标；
- 保留现有 `use_origin_milestone` 和 `generator` 类型校验；
- 不接受旧字段，不建立兼容 fallback。

#### 修改 `GeneratorTaskView`

删除：

```python
initial_state: JsonObject
```

新增：

```python
public_state: JsonObject
simulation_state: JsonObject
tool_contracts: JsonObject
```

字段用途固定为：

- `public_state`：允许进入 LLM payload 的公开状态；
- `simulation_state`：仅供 compiler 执行 recovery/precondition 判断，禁止进入 prompt；
- `tool_contracts`：由 adapter 根据真实工具实现提供的 output、effect 和 required-binding 契约，仅供代码校验与编译，禁止进入 prompt。

`digest()` 继续对完整 view 计算摘要，使 private contract 或 simulation state 变化时缓存失效；`MilestonePromptBuilder` 只能选择性序列化公开字段。

#### 修改 `GenerationReport`

删除全部 path、round、repair 字段：

- `max_candidate_path_count`；
- `returned_path_count`、`parsed_path_count`、`simulatable_path_count`、`final_path_count`；
- `selected_round`、`repair_triggered`；
- `counterexample_removed_operations`；
- `round_summaries`、`path_summaries`。

改为：

```python
generation_status: Literal["generated", "generation_failed"]
turn_dispositions: JsonObject
target_candidate_graph_count: int
max_candidate_batch_count: int
request_count: int
request_success_count: int
returned_graph_count: int
parsed_graph_count: int
valid_graph_count: int
accepted_observation_count: int
global_unique_graph_count: int
within_batch_duplicate_count: int
rejected_graph_count: int
target_reached: bool
graph_returned: bool
graph_empty: bool
empty_reason: str | None
aggregated_node_count: int
aggregated_edge_count: int
minefield_count: int
low_sample_count: bool
low_diversity: bool
cross_request_signature_counts: JsonObject
candidate_summaries: tuple[JsonObject, ...]
aggregation_support: JsonObject
validation_issues: tuple[JsonObject, ...]
response_digests: tuple[str, ...]
reasons: tuple[str, ...]
```

`to_dict()` 继续使用 `json_safe()`，不增加单独 serializer。

### 2.2 `dynsteer/model.py`

#### 扩展 `Constraint`

在 `expected` 后新增：

```python
expected_template: JsonValue = None
```

语义：

- `expected` 保存完全静态的目标值；
- `expected_template` 保存包含运行时 binding 的模板；
- 两者不能同时非空；
- binding 叶节点固定使用：

```json
{
  "$binding": {
    "source_milestone_id": "m_xxx",
    "selector": "$.field",
    "cardinality": "one"
  }
}
```

`cardinality` 只允许 `one` 或 `all`。该字段用于动态工具参数；ToolSandbox 状态目标的复合 binding 仍保存在 `stage_goal_semantics.match/values`，避免同时维护两套状态模板。

#### 扩展 `ScoringContext`

新增：

```python
trajectory: Trajectory | None = None
```

不得把 trajectory 复制进 `metadata`。运行时 binding resolver 直接使用这一字段和已有的 `matched_step_indexes`、`matched_snapshots`。

### 2.3 `dynsteer/adapter/loader.py`

修改 `parse_constraint()`：

- 读取可选 `expected_template`；
- 如果 `expected` 与 `expected_template` 同时非空，抛出 `ValueError`；
- 其余旧 adapted case 字段保持不变；
- 不修改已有 adapted case 文件格式，不批量重写人工图。

## 3. 候选图响应 schema

候选 dataclass 只在 `dynsteer/milestone/compiler.py` 内定义，不放入公共 model，不新增 schema 模块。

### 3.1 顶层响应

每批 prompt 要求：

```json
{
  "graphs": [
    {"dispositions": {}, "nodes": [], "edges": [], "minefields": []},
    {"dispositions": {}, "nodes": [], "edges": [], "minefields": []}
  ]
}
```

解析规则：

- prompt 强制要求恰好 2 张；
- parser 接受长度 0～2，以保留部分成功；
- 长度 1：接纳该图并记录 `batch_incomplete`；
- 长度 0：该批成功解析但无候选；
- 长度大于 2：整批记为 `batch_schema_error`，不得截断或择优；
- 单张候选可以是合法空图；
- 每张图的 local ID 只在该图内有效。

### 3.2 候选节点

#### `tool_call`

```json
{
  "local_id": "n0",
  "turn_id": "turn_0",
  "kind": "tool_call",
  "evidence_id": "tool_call_xxx",
  "arguments": {
    "name": {
      "source": "public_literal",
      "source_ref": "instruction:0",
      "value": "Alice"
    },
    "person_id": {
      "source": "node_output",
      "producer_local_id": "n_prev",
      "selector": "$.person_id",
      "cardinality": "one"
    }
  }
}
```

#### `set_state`

```json
{
  "local_id": "n1",
  "turn_id": "turn_0",
  "kind": "set_state",
  "namespace": "CONTACT",
  "operation": "update",
  "cardinality": "all",
  "match": {
    "relationship": {
      "source": "public_literal",
      "source_ref": "instruction:0",
      "value": "friend"
    }
  },
  "values": {
    "relationship": {
      "source": "public_literal",
      "source_ref": "instruction:0",
      "value": "enemy"
    }
  },
  "executor_evidence_id": "tool_call_modify_contact"
}
```

约束：

- `operation` 只允许 `add/update/remove/set`；
- `cardinality` 只允许 `one/all`；
- `match` 描述目标记录选择条件，`values` 描述新增或修改字段；
- `add` 可省略 `match`，但 `values` 非空；
- `update/set` 的 `values` 非空；
- `remove` 的 `match` 非空；
- `executor_evidence_id` 是实现证据，不进入 goal identity；不同候选可用不同 terminal tool 实现同一个状态目标；
- terminal tool 不额外编译成 operation milestone，避免与人工 `set_state` 节点重复；producer、search、conversion、recovery 仍用独立 `tool_call` 节点表达。

#### `emit_message`

```json
{
  "local_id": "n2",
  "turn_id": "turn_0",
  "kind": "emit_message",
  "sender": "AGENT",
  "recipient": "USER",
  "content_requirement": "Answer how many days remain until Christmas Day"
}
```

`content_requirement` 必须非空；不允许 LLM 伪造运行时答案。回答中需要动态值时，通过前驱 `tool_call` 和 edge 表达，不把动态答案写入候选 JSON。

#### 不允许 LLM 输出 `preserve_state`

`preserve_state` 完全由 compiler 在最终图上根据 tool contracts 派生。候选 parser 遇到 `kind=preserve_state` 直接拒绝该图，避免每批重复输出大量 guardrail。

### 3.3 值来源

只保留两种来源：

```json
{"source":"public_literal","source_ref":"...","value":...}
```

```json
{
  "source":"node_output",
  "producer_local_id":"n0",
  "selector":"$.field",
  "cardinality":"one"
}
```

删除原方案中的 `derived` 来源。计算、转换和选择必须由真实 `tool_call` 节点表达，其结果再通过 `node_output` 引用，避免形成与工具 graph 并行的表达系统。

### 3.4 Minefield

```json
{
  "turn_id": "turn_0",
  "evidence_id": "tool_call_modify_contact",
  "severity": "fatal",
  "reason_code": "missing_required_input",
  "missing_inputs": ["person_id"]
}
```

只允许 `severity=fatal`。`reason_code` 只允许：

- `missing_required_input`；
- `tool_unavailable`；
- `unsafe_side_effect`。

compiler 必须用当前 tool schema 和 private tool contract 校验：该 evidence 确实是有副作用 terminal tool，且 `missing_inputs` 无公开 literal 或可达 producer。无法由 contract 核实的 minefield 候选判为非法，不通过工具名关键字猜测。

## 4. `dynsteer/milestone/compiler.py` 重写

### 4.1 删除现有 path 算法

删除以下 dataclass 和函数：

- `_OperationCandidate`；
- `_TurnCandidate`；
- `_PathCandidate`；
- `_parse_path()`；
- `_simulate_path()`；
- `_simulate_operation()`；
- `_mandatory_occurrences()`；
- `_common_precedence()`；
- `_ordered_occurrences()`；
- `_forbidden_intersection()`；
- `_deduplicate_paths()`；
- `_normalized_path()`；
- `_occurrence_positions()`；
- `_occurrence_label()`；
- `_turn_dispositions()` 的 path 版本；
- `_round_summary()`；
- preliminary common operation、refinement 和 counterexample 全部代码。

保留并复用：

- `_evidence_catalog()`；
- `_tool_name()`；
- `_evidence_for_tool()`；
- `_rule_applies()`；
- `_state_value()`、`_set_state_value()`；
- `_assert_no_forbidden_generation_inputs()`；
- `_violation()`；
- `record_raw_response()`、`stable_json_digest()`。

### 4.2 新增 compiler 内部 dataclass

按主流程到细节的顺序，在 `compile_task_case()` 后定义：

```python
_ValueSource
_CandidateNode
_CandidateMinefield
_CandidateGraph
_CanonicalNode
_CanonicalGraphObservation
```

这些类型均为 `@dataclass(frozen=True)`，字段使用 tuple 和 JSON-safe 值，避免聚合期间被修改。它们不从 `dynsteer.milestone` 包导出。

### 4.3 重写 `compile_task_case()`

主函数固定按以下步骤实现：

```python
def compile_task_case(view, config, llm, response_output_file=None):
    evidence = _evidence_catalog(view)
    prompt_builder = MilestonePromptBuilder(view, config)
    observations = []
    batch_reports = []
    raw_records = {}

    for batch_index in range(config.max_candidate_batch_count):
        if len(observations) >= config.target_candidate_graph_count:
            break

        focus = _batch_focus(batch_index)
        prompt = prompt_builder.generation(batch_index, focus)
        batch = _request_candidate_batch(...)
        batch_reports.append(batch.report)

        parsed = _parse_candidate_batch(batch.raw_response, ...)
        validated = [_validate_candidate_graph(...)]
        canonical = [_canonicalize_candidate_graph(...)]
        accepted = _deduplicate_within_batch(canonical)

        remaining = config.target_candidate_graph_count - len(observations)
        observations.extend(accepted[:remaining])
        将 accepted[remaining:] 只记为 overflow

    if 所有批次均调用失败或顶层 JSON/schema 失败:
        返回空占位图 + generation_failed report

    graph = _aggregate_and_compile(observations, ...)
    返回 graph + generated report
```

具体要求：

- 每批独立调用 `llm.chat([user_message], response_format="json_object")`；
- 不把前批响应、候选、摘要或 validation issue 放入后续 prompt；
- raw response 使用 `batch_01`、`batch_02` 等 key 增量写入同一个 `response_output_file`；
- transport retry 仍由 `BaseLLM.chat()` 内部处理，不产生新候选票；
- 达到目标后立即停止，不发起多余请求；
- 第 4 批结束仍不足 6 张时，用实际 observation 数聚合；
- 至少一个顶层响应成功解析后，即使没有合法候选，也返回 `generated` 空图；
- 只有所有批次均为调用失败或顶层 JSON/schema 失败时返回 `generation_failed`。

### 4.4 批次审查重点

新增 `_batch_focus(batch_index: int) -> str`，只返回以下三个固定常量并循环：

1. `minimality`：检查 search/getter/conversion/recovery 是否可省；
2. `alternative`：检查替代工具、直接公开信息、批量 `set_state`；
3. `dependency_safety`：检查真实依赖、不可执行和 fatal side effect。

第 4 批重新使用 `minimality`。不新增 focus 配置、随机 focus 或基于 case ID 的分支。

### 4.5 单图确定性校验

新增 `_validate_candidate_graph(candidate, view, evidence) -> tuple[_CandidateGraph | None, list[JsonObject]]`，依次检查：

1. `dispositions` 的 key 与 `view.turns` 完全一致；
2. disposition 只允许 `executable/needs_clarification/no_action/response_only`；
3. local ID 图内唯一且非空；
4. turn ID 存在；
5. node kind 合法；
6. edge 端点存在、无自环、无重复，调用现有拓扑函数确认 DAG；
7. 非 executable turn 不包含 tool_call/set_state；
8. executable turn 至少包含一个 `set_state`、`emit_message` 或能直接回答任务的 terminal tool_call；
9. evidence ID 存在；
10. tool arguments 按当前 OpenAI tool schema 校验 required、unknown property、type、enum；
11. `public_literal.source_ref` 必须精确匹配 turn source_ref 或 `visibility=agent` 的 public asset；
12. UUID、timestamp、坐标、业务 ID 不能作为无来源 literal；
13. `node_output.producer_local_id` 存在，producer kind 为 tool_call；
14. producer 在 DAG 中可达 consumer，缺失 edge 时根据 binding 确定性补 edge；
15. output selector 与 cardinality 必须存在于 `view.tool_contracts`；
16. set_state executor contract 的 namespace、operation、required inputs 与候选目标一致；
17. minefield reason 可以由 contract 核实；
18. 同一 turn 不同时包含相同 terminal side effect 的 executable goal 和 fatal minefield。

校验失败只丢弃当前图，同一批另一张图继续处理。issue 固定包含：

```json
{
  "code": "...",
  "batch_index": 0,
  "graph_index": 1,
  "turn_id": "turn_0",
  "node_id": "n0",
  "field": "arguments.person_id",
  "message": "中文错误信息"
}
```

### 4.6 工具参数 schema 校验

在 compiler 内新增 `_validate_tool_arguments()` 和递归 `_validate_schema_value()`：

- 直接读取 `view.tool_schema["tools"][].function.parameters`；
- 只实现项目实际使用的 JSON Schema 子集：`type/object/array/string/number/integer/boolean/null`、`required`、`properties`、`items`、`enum`、`additionalProperties`；
- 对 `node_output` 只校验目标参数声明类型与 contract 输出类型兼容，不要求运行时值；
- 不引入 `jsonschema` 新依赖；
- 不在 parser、validator、compiler 多处重复校验。

### 4.7 Canonical signature

#### 节点 identity

`tool_call` identity：

```text
(turn_id, tool_call, evidence_id, argument names,
 literal values, dynamic argument type/cardinality placeholders)
```

producer local ID 不进入 identity；producer binding 属于 implementation evidence。

`set_state` identity：

```text
(turn_id, set_state, namespace, operation, cardinality,
 match/value field names, literal values, dynamic placeholders)
```

`executor_evidence_id` 和 producer local ID 不进入 goal identity。

`emit_message` identity：

```text
(turn_id, emit_message, sender, recipient,
 normalized content_requirement)
```

`content_requirement` 只做 trim、空白合并和 casefold，不使用另一次 LLM 做语义归一化。

同一图存在多个相同 base identity 时，按：

```text
topological order
→ predecessor base signatures
→ successor base signatures
→ local_id 仅作为最终稳定 tie-breaker
```

分配 occurrence index。最终 canonical node key 为 `(base_identity, occurrence_index)`。

#### 完整图 signature

由以下内容生成稳定 digest：

```text
canonical dispositions
multiset(canonical node keys)
set(canonical edges)
set(canonical minefields)
```

自由文本 description、local ID、batch index 不进入 signature。

### 4.8 同批去重与跨批计票

新增 `_deduplicate_within_batch()`：

- 同批相同 graph signature 只接纳 graph index 较小的一张；
- 另一张记录 `within_batch_duplicate`；
- 不同批次相同 signature 不去重，作为独立 observation 参与投票；
- 另用 `Counter` 生成 `cross_request_signature_counts`；
- `accepted_observation_count` 是聚合分母；
- `global_unique_graph_count` 仅用于多样性报告。

### 4.9 聚合算法

设 observation 多重集为 `G`，`n=len(G)`。

#### disposition

逐 turn 计数：

```text
keep(d) iff 2 * support(d) > n
```

没有严格多数时采用 `response_only`，记录 `disposition_conflict`，删除该 turn 的 executable tool/state 节点。

#### 节点

```text
support(v) = 包含 canonical node v 的 observation 数
keep(v) iff 2 * support(v) > n
```

节点 metadata 写入：

```json
{
  "necessity_basis": "graph_ensemble_majority",
  "support_count": 4,
  "observation_count": 6,
  "support_ratio": 0.6666666667,
  "global_unique_graph_count": 3
}
```

#### implementation evidence

对保留节点内部的 executor 和 producer binding 单独统计：

```text
eligible = 包含该节点的 observation 数
keep(binding) iff 2 * support(binding) > eligible
```

- `set_state` 的 dynamic match/value binding 未获多数时，该状态目标无法形成可评分 target，聚合后闭包必须删除该节点并记录 `unresolved_state_binding`；
- `tool_call` 的动态 argument binding 未获多数时，保留工具名节点，但不生成该参数的 hard constraint，metadata 标记 `argument_binding_status=unresolved`；
- 不从少数候选中任选 executor 或 producer。

#### edge

只对两个已保留端点统计：

```text
eligible(u,v) = 同时包含 u、v 的 observation 数
support(u,v) = 包含 u→v 的 observation 数
keep(u,v) iff 2 * support(u,v) > eligible(u,v)
```

随后按 support ratio 降序、canonical key 升序逐边加入；产生环的边跳过并记录 `edge_cycle_conflict`。

#### minefield

```text
keep(m) iff 2 * support(m) > n
```

只有已经通过 contract 校验的 minefield 才参与计数。最终 minefield 不再由 forbidden intersection 生成。

### 4.10 确定性依赖和 recovery

在多数聚合后新增 `_add_deterministic_dependencies()`：

1. 所有获多数的 `node_output` binding 强制添加 producer→consumer edge；
2. 根据 `view.environment_rules` 和 `simulation_state`，为被阻塞节点插入 recovery tool_call；
3. recovery 节点 ID 由 `(turn_id, recovery evidence, arguments, blocked node key)` 的 digest 生成；
4. recovery→blocked node edge 强制添加；
5. 已有相同 recovery 节点时复用，不重复插入；
6. 相邻用户 turn 之间，把前一 turn 的 terminal nodes 连接到后一 turn 的 root nodes；
7. 确定性 edge 不参与多数投票，但写入 `dependency_basis=binding/recovery/turn_order`；
8. 最终再次校验 DAG。

不得根据候选节点在 JSON 数组中的排列生成 edge。

### 4.11 聚合后闭包校验

新增 `_validate_aggregated_closure()`：

- 删除引用未保留 producer 的 hard binding；
- 删除包含 unresolved dynamic state binding 的 set_state；
- tool_call 可以退化为 name-only hard milestone，但必须记录被删除的 argument binding；
- executable turn 若只剩 producer/support node而没有 goal/effect/emit，删除这些 support nodes并记录 `orphan_support_chain`；
- 删除端点已删除的 edge；
- fatal minefield 与同 turn 同 effect executable node 冲突时，以已经通过 contract 的 fatal minefield 为准，turn 改为 response_only；
- 重新校验所有 edge、turn 顺序和 DAG；
- 闭包清理后允许返回空图，不为避免空图恢复少数节点。

## 5. 图与 constraint 编译

### 5.1 `tool_call` milestone

每个保留 tool_call 节点编译成一个 `Milestone`：

1. 主 constraint：

```python
target=ConstraintTarget.TOOL_CALL
selector="$.name"
operator=Operator.EQUALS
expected=<tool_name>
hard=True
matching_route=(Actor.AGENT, Actor.ENVIRONMENT)
```

2. 每个 public literal argument 增加一个 hard constraint：

```python
target=ConstraintTarget.TOOL_CALL
selector="$.arguments.<arg>"
operator=Operator.EQUALS
expected=<literal>
```

3. 每个获多数的 node_output argument 增加一个 hard constraint：

```python
target=ConstraintTarget.TOOL_CALL
selector="$.arguments.<arg>"
operator=Operator.EQUALS
expected=None
expected_template={"$binding": {...}}
```

`stage_goal_semantics` 保存 `kind=tool_call`、tool name、literal arguments 和 binding 摘要。

### 5.2 `set_state` milestone

#### ToolSandbox

编译一个 `ConstraintTarget.STATE_SNAPSHOT + Operator.CUSTOM` constraint：

- `namespace` 使用候选 namespace；
- `expected` 为 `{"rows": [], "columns": []}` 序列化占位，不作为真实 target；
- `reference_milestone_id` 指向该节点 stage anchor，root 使用 `initial`；
- `stage_goal_semantics` 保存：

```json
{
  "kind": "set_state",
  "namespace": "REMINDER",
  "operation": "remove",
  "cardinality": "one",
  "match": {"reminder_id": {"source":"node_output", "source_milestone_id":"m0", "selector":"$.reminder_id", "cardinality":"one"}},
  "values": {},
  "executor_tool_name": "remove_reminder",
  "evidence_source": "structured_scorer",
  "user_visible_required": false
}
```

- `metadata.toolsandbox.snapshot_constraint` 根据 operation 固定映射：
  - `add → addition_similarity`；
  - `update/set → update_similarity`；
  - `remove → removal_similarity`；
- column similarity 和 namespace schema 从现有 ToolSandbox scorer 恢复，不复制 adapted-case matcher metadata。

#### 非 ToolSandbox

只有 adapter `tool_contracts` 明确提供可评分 state contract 时才允许编译 set_state；否则候选校验阶段拒绝该节点，不生成永远无法评分的 constraint。

### 5.3 `emit_message` milestone

编译 route-aware constraint：

```python
target=ConstraintTarget.STEP
selector="$.content"
operator=Operator.FUZZY_MATCH
expected=<content_requirement>
hard=True
matching_route=(Actor.AGENT, Actor.USER)
stage_goal_semantics={
    "kind": "emit_message",
    "sender": "AGENT",
    "recipient": "USER",
    "content": <content_requirement>,
    "match_policy": "semantic_equivalent",
    "user_visible_required": True,
}
```

沿用现有 semantic message review，不新增消息评分模块。

### 5.4 Minefield

每个保留 minefield 编译成现有 `Minefield`：

- `severity="fatal"`；
- constraint 匹配 tool name；
- `penalty=MinefieldPenalty(mode="fixed", value=1.0)`；
- metadata 保存 turn、evidence、reason_code、missing_inputs、support_count 和 observation_count。

### 5.5 Milestone ID 与顺序

- milestone ID 由 `(turn_id, canonical node identity, occurrence_index)` 生成；
- constraint ID 由 milestone ID 和 constraint role 生成；
- 不沿用 LLM local ID；
- 节点输出顺序为 turn 顺序、DAG topological order、canonical key；
- 最终调用 `transitive_reduction()`。

## 6. ToolSandbox contract 与状态隔离

### 6.1 `dynsteer/adapter/toolsandbox/utils/contract.py`

修改 `build_toolsandbox_generator_view()`：

- `public_state={}`，ToolSandbox 原始 CONTACT/MESSAGING/REMINDER/SETTING/SANDBOX 行均不进入 LLM payload；
- `simulation_state=initial_state_from_context(...)`，只供 compiler recovery/precondition；
- `tool_contracts=toolsandbox_tool_contracts()`；
- `public_assets` 只保留 actor/recipient 对 Agent 可见的消息；
- `visibility=evaluator_only` asset 不进入 prompt payload；
- 日志只记录 namespace 数和工具数，不输出行内容、UUID 或 timestamp。

删除 prompt 对 ToolSandbox `initial_state` 的依赖。

### 6.2 `dynsteer/adapter/agentcompass/contract.py`

迁移 `GeneratorTaskView` 构造：

```python
public_state=task_case.initial_state or {}
simulation_state={}
tool_contracts={}
```

现有 environment/output public assets 保持。没有 contract 的 benchmark 只能生成可由通用 matcher评分的 tool_call/emit_message，不允许 set_state。

### 6.3 `dynsteer/adapter/toolsandbox/utils/effects.py`

保留 `toolsandbox_environment_rules()`，新增唯一 contract 入口：

```python
def toolsandbox_tool_contracts() -> JsonObject:
    ...
```

contract 按 Agent-facing tool name 建索引，每个工具只定义实际需要的字段：

```json
{
  "search_contacts": {
    "outputs": {
      "person_id": {"selector":"$.person_id", "type":"string", "cardinality":["one","all"]}
    },
    "writes": []
  },
  "modify_contact": {
    "required_dynamic_inputs": ["person_id"],
    "writes": ["CONTACT"],
    "effect": {"namespace":"CONTACT", "operation":"update"}
  }
}
```

实施时逐个核实当前 30 case 使用的工具，至少覆盖：

- current time、datetime/timestamp conversion、timestamp diff/shift/unit conversion；
- contact/message/reminder search；
- holiday search；
- contact/reminder add/modify/remove；
- message send；
- wifi/cellular/location/low-battery setting。

未核实工具不猜 selector、cardinality 或 effect，contract 中不登记；相关 dynamic candidate 被 validator 拒绝并留下 `contract_incomplete`。

## 7. Prompt 修改

### 7.1 `dynsteer/prompt/template.py`

修改 `MilestonePromptBuilder`：

- payload 只包含 benchmark、task ID、case ID、language、turns、Agent-visible public assets、`public_state`、tool schema 和 evidence catalog；
- 不包含 `simulation_state`、`tool_contracts`、environment rules、人工 graph、matcher、trajectory、final state；
- `generation()` 签名改为：

```python
def generation(self, batch_index: int, focus: str) -> str:
```

- 模板正文直接固定要求 2 张图，不再传入可变 `graph_count`；
- 删除 `refinement()`；
- payload 构造后调用现有 forbidden-key 递归检查；
- 不新增 prompt history 或 cache wrapper。

在该文件内定义中英文 focus 文案常量，不新建 prompt 参数模块：

```python
_MILESTONE_FOCUS_INSTRUCTIONS: dict[TaskLanguage, dict[str, str]] = {
    TaskLanguage.ENGLISH: {
        "minimality": (
            "Challenge every search/getter/conversion/recovery node: retain it only if "
            "the task cannot be completed correctly without its output or effect. Prefer "
            "public literals and direct goals when sufficient."
        ),
        "alternative": (
            "Actively seek a genuinely different complete realization: alternative visible "
            "tools, direct use of public information, or a correct bulk set_state. Do not "
            "create variation by omitting prerequisites."
        ),
        "dependency_safety": (
            "Audit producer-consumer dependencies, executability, and fatal side effects. "
            "Keep independent producers unordered; use empty/non-executable graphs and "
            "minefields when the visible inputs or tools cannot safely complete the task."
        ),
    },
    TaskLanguage.CHINESE: {
        "minimality": (
            "逐一质疑 search、getter、conversion、recovery 节点：只有缺少其输出或效果时任务"
            "确实无法正确完成，才保留该节点；公开 literal 或直接 goal 已足够时优先采用。"
        ),
        "alternative": (
            "主动寻找真正不同且完整的实现：替代的可见工具、直接利用公开信息，或正确的批量 "
            "set_state；不得通过省略前置条件制造差异。"
        ),
        "dependency_safety": (
            "核查 producer-consumer 的真实依赖、任务可执行性和 fatal 副作用；独立 producer "
            "之间不排序；公开输入或工具不能安全完成任务时，使用空的不可执行图和 minefield。"
        ),
    },
}
```

`generation()` 内先将 `view.language` 归一化为 `language`，校验 `focus` 存在于当前语言映射，再且仅向模板传入以下四个占位符：

```python
return load_prompt_template("milestone", "generation").render(
    language,
    batch_index=batch_index + 1,
    focus_code=focus,
    focus_instruction=_MILESTONE_FOCUS_INSTRUCTIONS[language][focus],
    task=json.dumps(self.payload, ensure_ascii=False, sort_keys=True),
)
```

其中 `batch_index + 1` 只用于向 LLM 展示自然数批次编号，不参与候选 identity 或聚合。模板不接收上一批响应、上一批图、历史摘要、validation issue 或已累计数量。

### 7.2 `dynsteer/prompt/templates/milestone/generation.en.md` 与 `.zh.md`

完全替换旧 path schema。以下两个代码块分别是目标文件的**完整内容**，实现时直接复制，不再临时改写措辞或 JSON schema。模板内 JSON 的双大括号是 `_safe_format()` 所需的转义形式：渲染后会恢复成普通 JSON 大括号；只有 `{batch_index}`、`{focus_code}`、`{focus_instruction}`、`{task}` 是真实占位符。

few-shot 只固化任务、compact nodes、edges 和空 minefield，不复制 adapted case 的 matcher、UUID、snapshot rows、reference metadata。样例中的 evidence ID 和 selector 仅用于讲解结构，不能被复制到当前响应。两个样例及其同源变体在 reliability primary 集合中标记为 `few_shot_contaminated`。

#### `generation.en.md` 完整内容

~~~~text
You generate candidate milestone goal graphs, not step-by-step trajectories. A node belongs in one candidate graph only when, under that candidate's complete and feasible interpretation, every valid completion must reach that operation or goal. One candidate is a hypothesis about necessity; necessity across candidates is decided later by code aggregation.

This is candidate batch {batch_index}. Focus code: `{focus_code}`.
Batch focus: {focus_instruction}

Return exactly 2 complete, standalone candidate graphs for the current task.

Independence and diversity rules:
- Each graph must define its own dispositions, nodes, edges, and minefields. Local IDs have graph-local scope only.
- Do not share a node table, refer to a node in the other graph, or encode graph 2 as a base/delta/patch of graph 1.
- Seek genuine diversity between two complete judgments: a different visible tool, direct use of public information, a valid bulk state goal, a different necessary dependency, or a justified executability judgment.
- Never create diversity by omitting a required producer, conversion, recovery, terminal goal, or user-facing answer; by adding irrelevant tools; or by inventing arguments.
- If no genuine alternative is supported, the two graphs may be equivalent. Completeness and correctness take priority over artificial difference.

Compact structural examples

The example evidence IDs, selectors, and source references below are illustrative only. For the current task, use only evidence IDs and public source references present in the current task JSON.

Example 1
Task: "What's my relationship with +10000000000"
Meaning: the contact search produces the dynamic answer, then the Agent must answer the user.

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
      "content_requirement": "Answer with the relationship returned for the requested phone number"
    }}
  ],
  "edges": [["n0", "n1"]],
  "minefields": []
}}

Example 2
Task: "Remove my upcoming reminder."
Meaning: current time and reminder search are independent producers needed to identify the upcoming reminder; both support the removal state goal. There is no edge between the two producers.

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

In this example, current time is indispensable to target selection but is not itself a REMINDER record field, so it must not be invented as a `match` key. Its edge to the state goal records the target-selection dependency; the selected reminder ID is dynamically bound from the search output. The shown selector remains illustrative and must be replaced by one supported by the current producer contract.

Current public task JSON

{task}

Required semantics

1. Turns and dispositions
- Every graph's `dispositions` object must contain exactly every turn ID from `turns`, with no missing or extra key.
- Each value must be exactly one of `executable`, `needs_clarification`, `no_action`, or `response_only`.
- Cover all turns in the task's original order. Assign every node and minefield to an existing turn ID.
- A non-`executable` turn must not contain `tool_call` or `set_state`. It may contain an `emit_message` only when a response to the user is itself required.
- Every `executable` turn must contain a final goal: a `set_state`, an `emit_message`, or a terminal `tool_call` that directly and completely performs the requested result when neither state-goal nor message-goal representation applies.
- A graph containing only search/getter/producer/conversion/recovery nodes is incomplete and invalid.

2. Necessity, feasibility, and goals
- Keep only milestones that are necessary under this graph's complete judgment. Do not include a useful, conventional, defensive, or confirmatory operation unless the task cannot be completed correctly without its output or effect.
- A producer is necessary only when a retained consumer needs its dynamic output or when its observed value is indispensable to the final answer or target selection.
- Represent a requested business-state change as one `set_state` goal. Put the real terminal tool's evidence ID in `executor_evidence_id`; do not also emit a duplicate terminal `tool_call` for the same effect.
- Keep search, getter, conversion, and recovery calls as separate `tool_call` nodes only when they are genuinely necessary producers or prerequisites.
- Represent a required user-facing answer with `emit_message`. `content_requirement` states what the answer must communicate; it must not fabricate the unknown runtime answer.
- Use visible public data directly when sufficient. Do not add a getter merely to reconfirm the same public value.
- If visible tools or public inputs cannot safely complete the task, do not invent a completion. Use an appropriate non-executable disposition and a legal empty or response-only graph.

3. Node schemas and provenance
- The only allowed node kinds are `tool_call`, `set_state`, and `emit_message`. Never output `preserve_state`; it is derived by code.
- A `tool_call` node has exactly `local_id`, `turn_id`, `kind`, `evidence_id`, and `arguments`.
- `evidence_id` must be a TOOL_CALL evidence ID from the current `evidence_catalog`. Never use example IDs or invent an ID.
- `arguments` contains only argument names accepted by that tool. Every argument value must use exactly one of the two binding forms below; do not put a raw scalar directly in `arguments`.

Public literal binding:
{{"source":"public_literal","source_ref":"an exact current public source reference","value":"the literal value from that source"}}

Node-output binding:
{{"source":"node_output","producer_local_id":"a producer in this same graph","selector":"$.field","cardinality":"one"}}

- `public_literal.source_ref` must point to the exact current instruction/turn or Agent-visible public asset that exposes the value. Public state is usable only when the current task JSON exposes it to the Agent.
- A `node_output` producer must be an earlier reachable `tool_call` in the same graph. Its selector and `one`/`all` cardinality must match the producer's real output.
- Every node-output binding requires a producer-to-consumer edge.
- Do not use a `derived` source. Any required lookup, calculation, date/time conversion, coordinate conversion, or recovery must be represented by a real visible `tool_call`, then referenced through `node_output`.
- Never hard-code or copy a dynamic UUID, row ID, person/message/reminder ID, timestamp, coordinate, token, or tool-produced value as `public_literal` unless that exact value is explicitly visible in a permitted current source.

A `set_state` node has exactly:
{{
  "local_id": "n_goal",
  "turn_id": "turn_0",
  "kind": "set_state",
  "namespace": "CONTACT|MESSAGING|REMINDER|SETTING or another current supported namespace",
  "operation": "add|update|remove|set",
  "cardinality": "one|all",
  "match": {{}},
  "values": {{}},
  "executor_evidence_id": "a current terminal TOOL_CALL evidence ID"
}}

- Each value inside `match` and `values` must use one of the same two binding forms.
- `match` selects target records; `values` describes fields to add or change.
- For `add`, `values` is non-empty and `match` may be empty. For `update` or `set`, `values` is non-empty. For `remove`, `match` is non-empty. Use only `one` or `all` for cardinality.
- `executor_evidence_id` must identify a current visible terminal tool that can actually perform this namespace and operation with the represented inputs.

An `emit_message` node has exactly:
{{
  "local_id": "n_answer",
  "turn_id": "turn_0",
  "kind": "emit_message",
  "sender": "AGENT",
  "recipient": "USER",
  "content_requirement": "a non-empty requirement for the answer"
}}

4. Edges
- `edges` is an array of `[source_local_id, target_local_id]` pairs whose endpoints are nodes in the same graph.
- Add an edge only for a true non-commutable data, prerequisite, or goal dependency. Array order is not an edge.
- Do not order two independent producers merely because one is commonly called first. If both independently feed one consumer, connect each producer to the consumer and do not connect the producers to each other.
- Do not add cross-turn edges only to restate conversation order; code adds deterministic turn-order dependencies.
- No self-loop, duplicate edge, missing endpoint, or cycle is allowed.

5. Minefields and legal empty graphs
- A minefield marks a fatal side-effecting tool call that must not occur for the stated turn. It has exactly:
{{
  "turn_id": "turn_0",
  "evidence_id": "a current terminal TOOL_CALL evidence ID",
  "severity": "fatal",
  "reason_code": "missing_required_input|tool_unavailable|unsafe_side_effect",
  "missing_inputs": []
}}
- Use only severity `fatal` and the three listed reason codes. Fill `missing_inputs` only for truly unavailable required inputs; otherwise use an empty array.
- The evidence must identify a real current side-effecting terminal tool, and the reason must be supported by the current public task. Do not infer danger from a tool name alone.
- Do not put the same terminal effect in both an executable goal and a fatal minefield for the same turn.
- A candidate may legally have empty `nodes` and `edges`. If no safe tool action or answer milestone is required, `minefields` may also be empty. If the task is blocked and attempting a terminal side effect would be fatal, include the justified minefield.

6. Exact response shape
- Return one JSON object with exactly one top-level key, `graphs`.
- `graphs` must contain exactly 2 graph objects.
- Every graph object has exactly `dispositions`, `nodes`, `edges`, and `minefields`.
- Do not add description, rationale, reasoning, confidence, quality, strategy, shared nodes, metadata, comments, or any other field.
- Return raw valid JSON only. Do not use Markdown fences and do not write any prose before or after the JSON.

Final checklist before returning
- Exactly two standalone complete graphs?
- Exact disposition keys for all current turns?
- Every executable turn has its final state/action/answer goal?
- No producer-only or search-only executable graph?
- All evidence IDs and public source references come from the current task?
- Every dynamic value has a reachable in-graph producer and edge?
- Only true dependencies are edges, with independent producers unordered?
- No invented ID, timestamp, coordinate, selector, argument, tool capability, or extra field?
- Empty/non-executable graph used instead of a fabricated completion when necessary?

Return only this shape, populated for the current turns and task:
{{"graphs":[{{"dispositions":{{}},"nodes":[],"edges":[],"minefields":[]}},{{"dispositions":{{}},"nodes":[],"edges":[],"minefields":[]}}]}}
~~~~

#### `generation.zh.md` 完整内容

~~~~text
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
~~~~

### 7.3 删除 refinement 模板

删除：

- `dynsteer/prompt/templates/milestone/refinement.en.md`；
- `dynsteer/prompt/templates/milestone/refinement.zh.md`。

同步删除所有加载引用和测试，避免失去用途的 prompt 继续存在。

## 8. 通用 graph helper 清理

### `dynsteer/graph.py`

删除仅服务旧算法的：

- `milestones_from_occurrences()`；
- `minefields_from_forbidden()`；
- `_evidence_tool_name()`。

保留：

- `transitive_reduction()`；
- `build_adjacency()`；
- `topological_order()`；
- `enrich_milestone_graph()`；
- dominator/stage anchor 相关 helper。

候选聚合逻辑不放入 `graph.py`，避免通用图模块依赖 milestone generation schema。

## 9. preserve_state 派生

### 9.1 compiler 内新增 `_attach_preserve_constraints()`

constraint 主体编译和 edge 完成后：

1. 构造临时 `MilestoneGraph`；
2. 调用现有 `enrich_milestone_graph()` 得到每个节点 stage anchor；
3. 对每个 milestone 计算从 stage anchor 到该 milestone 的预期写 namespace：
   - tool_call 使用 `tool_contracts[tool].writes`；
   - set_state 使用自身 namespace；
   - recovery 使用 recovery tool writes；
   - emit_message 默认无业务 namespace write；
4. 在已知 ToolSandbox 业务 namespace `CONTACT/MESSAGING/REMINDER/SETTING` 中，对不在 write set 的 namespace追加 preserve constraint；
5. root milestone 的 reference 为 `initial`；其他节点 reference 为 `graph.topology.stage_anchor_by_id[milestone_id]`；
6. constraint 使用 `reference_milestone_id`，并在 semantics 保存：

```json
{
  "kind":"preserve_state",
  "namespace":"CONTACT",
  "reference":{"type":"milestone_id", "value":"m_prev"}
}
```

7. 不对 SANDBOX 对话 namespace 生成 preserve；
8. effect contract 不完整时，不猜 unaffected namespace：该节点不派生 preserve，并记录 `preserve_contract_incomplete`。

### 9.2 `dynsteer/adapter/loader.py`

自动生成 graph 已在 compiler 中 enrich。修改 `_adapt_task_case()` 的后处理：

- `graph.topology is None` 时调用 `enrich_milestone_graph()`；
- 已 enrich 时直接复用；
- 从 JSON 重载后的 graph 因 topology 不序列化，仍会正常 enrich；
- 不重复构造 preserve constraints。

## 10. 动态 binding 运行时评分

### 10.1 `dynsteer/evaluate/runtime.py`

修改 `scoring_context()` 返回值：

```python
ScoringContext(
    task_case=task_case,
    trajectory=trajectory,
    matched_step_indexes=matched_step_indexes,
    matched_snapshots=matched_snapshots,
)
```

`state_scoring_context()` 缓存逻辑保持不变；trajectory 为同一运行对象引用，不复制 steps。

### 10.2 `dynsteer/evaluate/scoring.py`

新增以下 protected helper，供通用 argument binding 与 ToolSandbox scorer复用：

```python
_resolve_expected_template(template, context)
_resolve_binding(binding, context)
_producer_tool_result(source_milestone_id, context)
```

`_producer_tool_result()` 固定算法：

1. 从 `context.matched_step_indexes[source_milestone_id]` 获得 producer 已匹配 boundary；
2. 从 `context.task_case.milestone_graph` 读取 producer tool name；
3. 在 producer stage anchor 之后、当前 trajectory 范围内找到与 tool name 和 boundary 对应的最近 tool call；
4. 优先用 `openai_tool_call_id` 关联后续成功 tool result；
5. 没有 call ID 时，只允许使用紧邻且唯一的成功 result；存在多个可能结果则返回 missing，不猜测；
6. 对 result content 应用现有 selector；
7. cardinality=one 要求正好一个值，all 返回列表；
8. 失败、selector 缺失或 cardinality 不符时返回结构化 missing evidence，不抛出未处理异常。

修改 `score_constraint()`：

- `expected_template is not None` 时，先解析模板得到运行时 expected；
- binding 未解析成功时返回 `missing=True, score=0`；
- 静态 expected 路径保持现有行为；
- 不改 Operator 语义。

### 10.3 `dynsteer/adapter/toolsandbox/scorer.py`

修改调用链签名，显式传递通用 scorer 已解析出的 reference source：

```python
score_custom_constraint(..., reference_source, ..., context)
  → _score_toolsandbox_snapshot_constraint(
        ..., reference_source=reference_source, context=context
    )
  → _resolved_target_dataframe(
        ..., reference_snapshot=reference_source, context=context
    )
```

`reference_source` 必须是 `StateSnapshot | None`；不得在 generated constraint 路径中丢弃后再从 ToolSandbox metadata 反查。修改 `_score_toolsandbox_snapshot_constraint()` 和 `_resolved_target_dataframe()`，让 target 来源分三种：

1. `preserve_state`：使用 reference snapshot；
2. generated `set_state`：调用新增 `_state_goal_target_dataframe()`；
3. origin adapted constraint：继续使用 `constraint.expected`。

新增 `_state_goal_target_dataframe(constraint, reference_snapshot, context)`：

- 从 semantics 读取 namespace、operation、match、values、cardinality；
- public literal 直接取 value；
- node_output 通过通用 `_resolve_binding()` 获取；
- `add`：由 values 构造 target rows；
- `update/set`：从 reference snapshot 选择 match rows并覆盖 values；
- `remove`：从 reference snapshot 选择 match rows作为 removal target；
- cardinality=one 要求恰好一行，all 允许多行；
- 未匹配或多匹配冲突返回评分失败，不任选第一行；
- 用现有 `_restore_namespace_schema()` 恢复 dataframe 类型；
- reference summary 写明 `target_source=generated_state_goal_binding` 和绑定到的 producer milestone IDs，不记录完整敏感值。

修改 reference 解析：

- 优先使用 `constraint.reference_milestone_id` 和传入的 `reference_source`；
- 仅 origin adapted case继续 fallback 到 `metadata.toolsandbox.reference_milestone_node_index`；
- generated preserve/set_state 不写人工 milestone index metadata。

不新建 ToolSandbox binding scorer 文件。

## 11. Stage goal 生成修改

### `dynsteer/stage/goal.py`

修改 `_constraint_goal_text_from_semantics()`：

- origin 静态 set_state 继续使用 `[[constraint.expected]]` placeholder；
- generated set_state 若包含 `operation/match/values`，直接把 public literal 和“来自前序 milestone 输出”的 symbolic requirement 渲染到 stage goal，不使用空 expected placeholder；
- tool_call 的 node_output argument 渲染为“use the value produced by milestone X”，不把 binding JSON 直接展示给 Agent；
- emit_message 和 preserve_state 继续复用现有模板；
- 不新增一套 generated-stage-goal 模块。

修改 `materialize_stage_goals()`：

- 只替换模板实际包含的 expected placeholder；
- generated dynamic state goal 没有 placeholder 时原样保留；
- 不把 `expected={rows:[]}` 的序列化占位输出成任务目标。

补充 stage goal 单测，确保动态 removal/update 的 goal 文本包含语义要求而不是空 rows。

### `dynsteer/prompt/stage.py`

修改 `_constraint_prompt_json()`：

- origin 静态 set_state 继续把 `semantics.expected` 和 `expected_summary` 写成 expected placeholder；
- generated set_state（semantics 含 `operation/match/values`）直接输出 symbolic semantics，`expected_summary` 使用 `"generated_state_goal"`，不覆盖为 placeholder；
- tool_call 含 `expected_template` 时，prompt 只暴露 source milestone ID、参数名和“derived from predecessor”标签，不暴露已解析运行值；
- preserve 和 emit_message 路径保持；
- `_validate_required_placeholders()` 只要求静态 set_state 保留 placeholder，generated dynamic constraint 不纳入 missing 集合。

## 12. 配置、缓存和调用方迁移

### 12.1 `dynsteer/harness/config.py`

修改 `milestone_generation_from_mapping()`：

- allowed keys 改为 `use_origin_milestone`、`target_candidate_graph_count`、`max_candidate_batch_count`、`generator`；
- 删除 `max_candidate_path_count`、`enable_repair`；
- 默认值 6 和 4；
- 旧字段出现时直接报 unsupported field。

### 12.2 `dynsteer/experiment/runner.py`

修改 `_static_adaptation_key()`：

- 删除 path count 和 repair flag；
- 加入 target graph count、max batch count；
- generator 配置 digest 保持；
- prompt 模板或 tool contract 变动仍通过 view/config digest 使缓存失效。

### 12.3 `dynsteer/adapter/loader.py`

在 `_adapt_task_case()` 调用 `compile_task_case()` 后：

- `generation_status == generation_failed` 时抛出明确的 milestone generation exception，不保存空 adapted case；
- `generated` 空图正常保存；
- report 完整写入 `task_case.metadata["milestone_generation"]`；
- generated graph 已 enrich 时不重复 enrich。

### 12.4 `milestone_reliability.py`

修改 `_run_case()` 和 case JSON 输出：

- generation_failed 记为生成失败，不作为成功空图；
- 保存完整 generated graph、generation report、每批 response digest、candidate summary、validation issue 和 aggregation support；
- 不把 raw response重复嵌入 case JSON，raw 仍保存在 `llm_outputs` 文件。

修改 summary：

- 增加 request count、target reach rate、accepted observation、global unique graph、within-batch duplicate、cross-request signature 分布；
- 增加第 4 补偿批触发率和触发原因；
- token 同时报告单批 completion、每 case total、实验 total；
- minefield 增加 positive-case recall、per-tool recall 和 spurious fatal；
- empty-empty 不计入 positive minefield recall。

## 13. Canonical semantics 与指标修改

### `dynsteer/milestone/semantics.py`

重写 `canonical_graph_semantics()`，不要只抽取具体工具 occurrence。输出：

```json
{
  "dispositions": [],
  "goals": [],
  "operations": [],
  "topology": [],
  "minefields": [],
  "preserves": []
}
```

规则：

- tool_call → operations；
- set_state → goals，identity 包含 namespace/operation/cardinality/match/value 公共语义，不包含动态运行值；
- emit_message → goals；
- preserve_state → preserves；
- edge 用 canonical node identity 表示；
- minefield 包含 tool、severity、reason code；
- ToolSandbox tool aliases 继续只映射工具名，不改 goal identity。

`milestone_reliability.py` primary 输出分别保留：

- goal/effect exact 与 F1；
- operation exact 与 F1；
- topology exact；
- preserve exact；
- fatal positive recall；
- 原始 GED 仅作为诊断，不再用 operation 映射代替完整语义结论。

## 14. 实验配置修改

### `data/experiments/toolsandbox_milestone_reliability_partial_main.json`

实施复验时复制为新 experiment ID，不覆盖当前基线。新 `milestone_generation`：

```json
{
  "use_origin_milestone": false,
  "target_candidate_graph_count": 6,
  "max_candidate_batch_count": 4,
  "generator": {
    "provider": "...",
    "model": "...",
    "temperature": 0.2,
    "timeout_seconds": 120,
    "max_retries": 3
  }
}
```

temperature 使用低非零值以允许跨请求差异；具体模型对照必须使用独立 experiment ID。transport `max_retries` 不计候选批次。

## 15. 测试代码修改

### 15.1 `tests/milestone/support.py`

删除 path/response helper，新增：

- `candidate_tool_node()`；
- `candidate_state_node()`；
- `candidate_message_node()`；
- `candidate_graph()`；
- `batch_response(graph1, graph2)`；
- `SequenceLLM` 保留并支持按批返回异常/JSON；
- `make_view()` 改用 public/simulation state 和 tool contracts。

### 15.2 重写 `tests/milestone/test_compiler.py`

覆盖：

- 正常 3 批累计 6 张后停止；
- 前三批不足时第 4 批补偿；
- 第 4 批后不足仍聚合已有候选；
- 达到目标后不请求第 4 批；
- transport retry 不产生多票；
- 单图部分成功、空数组、超过两图、malformed JSON；
- 所有批失败 → generation_failed；
- 至少一批顶层成功但候选全拒绝 → generated 空图；
- 同批等价图一票，跨批等价图多票；
- 严格多数 `n=1..6` 边界；
- disposition 平票安全降级；
- goal identity 不因 executor/producer不同拆票；
- unresolved state binding 删除，tool argument binding 可退化；
- edge eligible denominator、反向 edge 冲突、DAG 和传递约简；
- binding/recovery/turn-order 确定性 edge；
- batch contact 聚合为一个 all-cardinality set_state；
- empty graph、fatal minefield、recovery graph；
- graph/constraint ID 稳定且不使用 local ID。

### 15.3 新增或重写 ToolSandbox adapter/scorer 测试

在现有 adapter 测试目录增加：

- prompt payload 不含 business rows、UUID、timestamp、private contracts、simulation state；
- AgentCompass view 正确迁移新字段；
- tool contracts selector/cardinality/effect 与真实实现样例一致；
- node_output 从 tool result 解析 one/all；
- call ID 配对优先，无 ID 时仅接受唯一相邻 result；
- ambiguous/missing producer result 评分失败；
- add/update/remove/set target dataframe；
- batch all update；
- preserve reference 使用 initial 或 milestone ID；
- origin adapted constraint 仍走旧 expected/index fallback；
- generated constraint 不依赖人工 index metadata。

### 15.4 `tests/milestone/test_semantics.py`

覆盖四类语义、动态 binding canonicalization、batch state goal、preserve、minefield reason、随机 ID 无关性和 topology identity。

### 15.5 `tests/milestone/test_reliability.py`

覆盖：

- generation_failed 与 generated empty 分开统计；
- goal/operation/topology/preserve 指标；
- fatal positive recall 不含 empty-empty；
- observation/unique/duplicate/request/补偿批统计；
- few-shot contaminated case 不进入 primary aggregate。

### 15.6 `tests/test_milestone_response_recording.py`

改为验证：

- 每个实际成功批次写入 `batch_01...batch_04`；
- 达到目标后没有额外 batch key；
- response digest 与原文一致；
- output file 为空时不写文件；
- benchmark/case 输出路径保持隔离。

### 15.7 Stage goal 测试

在现有 stage goal 测试中增加：

- generated dynamic set_state 不渲染空 expected rows；
- public literal 正确显示；
- node output 显示为前序结果引用；
- preserve reference 使用 milestone ID；
- origin adapted placeholder 行为不变。

## 16. API 文档同步

修改 `docs/apis/milestone.md`：

- 替换 path ensemble、repair、intersection 说明；
- 记录新 config、GeneratorTaskView、GenerationReport；
- 给出双图 batch schema；
- 说明同批去重、跨批计票、严格多数和 edge eligible 公式；
- 说明 expected_template/binding；
- 说明 generated empty 与 generation_failed；
- 说明该结果是经验性必经估计，不是形式化证明。

## 17. 明确删除与不新增内容

实施完成后必须确认不存在：

- path candidate、path simulation、path intersection；
- common precedence；
- forbidden intersection；
- preliminary common operation；
- refinement/counterexample prompt 和第二轮修复；
- 旧 config/report/cache 字段；
- 旧 schema fallback；
- 共享节点表、base graph、delta graph；
- 根据 case ID、工具名关键字或 prompt 文本猜 capability/effect；
- 未使用 import、旧 helper 和只转调一层的 wrapper。

不新增：

- `planner.py`；
- `aggregator.py`；
- `simulator.py`；
- `validation.py`；
- effects/capability package；
- 本地 prompt cache；
- 新第三方 schema/solver 依赖。

## 18. 实施顺序

1. 修改 model、constraint parser、config 和所有 GeneratorTaskView 构造方，使项目先能构造新接口；
2. 修改 ToolSandbox state boundary 和 tool contracts；
3. 替换 prompt builder、generation 模板并删除 refinement；
4. 重写 compiler parser、validator、canonicalization、批次循环和聚合；
5. 编译 graph、确定性 edge、recovery 和 preserve；
6. 修改 ScoringContext、通用 binding resolver 和 ToolSandbox state scorer；
7. 修改 stage goal 生成；
8. 迁移 loader、cache key、reliability 和 canonical semantics；
9. 重写单元测试并运行局部覆盖率；
10. 运行全量 `uv run pytest`；
11. 使用新 experiment ID 运行 30 case、至少 3 个实验 seed；
12. 清理运行产生的 `.pytest_cache`、`__pycache__` 和临时 coverage 文件，不删除正式 tests 源码。

## 19. 代码验收条件

- 默认每批 2 图、目标 6 图、最多 4 批；
- 正常 3 批停止，第 4 批仅在候选不足时触发；
- 同批等价图一票，跨批等价图分别计票；
- 除所有批次调用/顶层解析均失败外均返回 MilestoneGraph，包括空图；
- generation_failed 不保存为成功 adapted case；
- prompt 对人工 graph、matcher、trajectory、final state、业务 initial rows 和 private contracts 零泄漏；
- 动态参数和动态 state goal 能从真实 producer result 绑定并评分；
- preserve 使用真实 effect contract 和 stage anchor；
- 图无悬空 hard binding、无环、无传递冗余；
- 4 个 fatal 正例逐例回归，单独报告 positive recall；
- 新增/重写的 compiler、binding 和 ToolSandbox scorer 核心逻辑行覆盖率不低于 80%；
- 全量测试通过；
- 不修改 `.gitignore`，不主动 commit/push。

## 附录A. 项目中没有把握实现的模块部分

### A.1 ToolSandbox output contract 的完整覆盖

当前 OpenAI tool schema主要描述输入，多数工具没有机器可读 output schema。方案要求从真实 ToolSandbox 实现和实际 trajectory 样例核实当前 30 case 的 selector、cardinality 和 effect；无法保证一次覆盖 509 个 adapted case 的全部工具。未核实工具必须 `contract_incomplete` 失败关闭，不能猜测。

### A.2 无 call ID 时 producer result 的唯一归属

连续同名工具调用且环境结果缺少 call ID 时，仅凭顺序可能无法安全归属。实现只接受“阶段区间内唯一相邻成功 result”，其他情况返回 missing。这样可能漏匹配，但不会把错误结果绑定到 hard milestone。

### A.3 有限候选不能形式化证明必经性

多批候选与严格多数只能形成经验性必经估计。即使 6 个 observation 一致，也不能排除模型未提出的合法替代策略。当前不新增依赖残缺 tool contract 的 SAT/AND-OR 求解器；所有节点必须保留 support metadata，指标分别报告 goal 和具体 operation。

### A.4 多批请求的统计独立性

三种审查重点和低非零 temperature 可以增加差异，但不能证明同一模型多次请求相互独立。代码必须同时记录 observation 数、global unique graph 数、同批重复和跨批 signature 分布，不能把多次相同输出解释为覆盖了多种策略。
