# Milestone 核心生成与评分代码优化方案

- 日期：2026-09-25
- 状态：待实施
- 前置分析：`docs/plans/2026-09-25-milestone-minefield-low-score-audit-and-optimization-plan.md`
- 结果基线：`results/milestone/toolsandbox_milestone_reliability_main`

## 1. 目标与非目标

### 1.1 目标

本方案只保留已确认有必要落地的四类修复：

1. 修正 legacy fatal minefield reason 的确定性归一。
2. 强化中英文生成 prompt 的 minefield contract 与 Tool Operation 结构约束。
3. 修复 minefield 候选投票身份被 `missing_inputs` 拆分的问题。
4. 修复 tool call 存在性投票被 `argument_intents` 拆分，导致 operation 整链丢失的问题。

### 1.2 非目标

原报告 Phase 4“低成本回归验收集”不采纳，本轮明确不做：

- 不新增 smoke 配置；
- 不新建独立 benchmark 或验收集；
- 不增加 30-case 分层生成前置实验；
- 不提高 `target_candidate_graph_count`、`max_candidate_batch_count` 或候选采样数；
- 不重跑并覆盖 509-case 主结果目录；
- 不修改已精简的 `summary.json` 指标口径。

验收只使用已有单元测试与已保存 LLM response 的 deterministic replay。真实生成实验是否发起，由用户在代码验收通过后另行决定。

## 2. 实施顺序

| 顺序 | 工作包 | 主要文件 | 依赖 |
| --- | --- | --- | --- |
| 1 | WP1 legacy reason 归一 | `dynsteer/milestone/semantics.py` | 无 |
| 2 | WP2 minefield 聚合身份 | `dynsteer/milestone/compiler.py` | 无 |
| 3 | WP3 tool operation 聚合身份 | `dynsteer/milestone/compiler.py` | 无 |
| 4 | deterministic replay 验收 | 不新增工程文件 | WP1-WP3 |
| 5 | WP4 prompt 硬约束 | 中英文 generation template | WP1-WP3 replay 通过 |
| 6 | API 与测试补齐 | `docs/apis/milestone.md`、`tests/milestone/test_generation_stability.py` | 全部代码工作包 |

Prompt 放在 compiler replay 之后，是为了先确认 deterministic 修复的收益，不把 prompt 变化、reference 归一和聚合变化混在同一轮归因里。

## 3. WP1：修正 legacy minefield reason 归一

### 3.1 修改位置

- `dynsteer/milestone/semantics.py::_minefield_identity`
- 现状：无显式 `reason_code` 的 reference 在 required arguments 不缺失时统一兜底为 `unsafe_side_effect`。

### 3.2 目标分支

保留显式 `reason_code` 原值不变；仅当 legacy reference 没有 reason code 时执行：

```python
if required_input_missing:
    reason_code = "missing_required_input"
elif tool_contract_has_writes:
    reason_code = "unsafe_side_effect"
else:
    reason_code = "unsafe_tool_call"
```

实现要求：

1. 通过 `view.tool_contracts.get(tool_name, {})` 读取 contract。
2. `writes` 或 `state_effect` 非空视为写工具。
3. contract 缺失时不猜测写集，只读 fatal 调用归一为 `unsafe_tool_call`。
4. 不根据 case ID、工具名前缀或 benchmark 名称做特判。
5. `_required_tool_arguments` 继续只负责 schema required 字段，不混入 contract 写集判断。

### 3.3 测试

在 `tests/milestone/test_generation_stability.py` 增加：

1. `test_legacy_write_minefield_defaults_to_unsafe_side_effect`
   - 使用 `add_reminder` 这类 contract 带 `writes` 的工具；
   - required arguments 完整且无显式 reason；
   - 断言 canonical minefield identity 的 `reason_code` 为 `unsafe_side_effect`。
2. `test_legacy_readonly_minefield_defaults_to_unsafe_tool_call`
   - 使用 `search_reminder` / `search_contacts` 这类无写集工具；
   - 无显式 reason 且无 required 缺失；
   - 断言 reason 为 `unsafe_tool_call`，不得再出现 `unsafe_side_effect`。
3. `test_explicit_minefield_reason_is_preserved`
   - 显式写入合法 reason；
   - 断定归一逻辑不覆盖显式值。

## 4. WP2：minefield 核心投票身份与 missing inputs 择优

### 4.1 修改位置

- `dynsteer/milestone/compiler.py::_compile_minefields`
- 现状：`Counter(_CandidateMinefield)` 把 `missing_inputs` 纳入完整身份；字段集合不同但语义同一工具、同一 reason 的候选会互相拆票。

### 4.2 聚合算法

将投票身份改为：

```python
(turn_id, evidence_id, reason_code)
```

聚合流程：

1. 每个 observation 对同一核心身份最多计 1 票；先按 observation 去重，避免同图重复候选放大支持度。
2. 仍使用严格多数阈值：`2 * support > observation_count`。
3. 核心身份未达严格多数时继续丢弃，不降低安全阈值。
4. 达到多数后，从支持该身份的合法候选中选择 `missing_inputs`：
   - 先选出现次数最多的精确 tuple；
   - 若并列，选择与 contract `required_dynamic_inputs` 交集最大者；
   - 仍并列时按排序后的 tuple 做 deterministic tie-break；
   - 非 `missing_required_input` reason 固定使用空 tuple。
5. 不回绕过 `_validate_candidate_graph`：只有已通过 contract 校验的 minefield 才能进入本聚合。
6. `minefield_terminal_conflict` 逻辑保持不变。

### 4.3 元数据要求

最终 minefield metadata 保留：

- `turn_id`
- `evidence_id`
- `reason_code`
- `missing_inputs`
- `support_count`
- `observation_count`

其中 `support_count` 表示核心身份的 observation 支持数。不得用候选条目数替代 observation 支持数。

### 4.4 测试

新增测试：

1. `test_minefield_missing_input_variants_do_not_split_vote`
   - 4 个 observation 均支持同一 `(turn_id, evidence_id, missing_required_input)`；
   - 3 个 missing inputs 分别为不同合法字段集合；
   - 断言最终 minefield 保留，且选择票数最高的合法集合。
2. `test_minefield_core_identity_requires_strict_majority`
   - 6 个 observation 中只有 3 个支持同一核心身份；
   - 断言仍不生成 minefield。
3. `test_duplicate_minefield_candidate_counts_one_vote_per_observation`
   - 同一 observation 内出现重复候选；
   - 断言支持数仍为 1，不得变成 2。
4. `test_minefield_reason_change_creates_distinct_identity`
   - 同工具、同 turn、不同 reason；
   - 断言二者不会合并。

## 5. WP3：tool operation 存在性与参数 binding 分离

### 5.1 修改 `_node_identity`

位置：`dynsteer/milestone/compiler.py::_node_identity`

现状 `tool_call` identity 为：

```python
{
    "turn_id": ...,
    "kind": ...,
    "evidence_id": ...,
    "argument_intents": ...,
}
```

目标改为：

```python
{
    "turn_id": ...,
    "kind": ...,
    "evidence_id": ...,
}
```

要求：

1. 删除 `argument_intents` 参与节点存在性投票。
2. 参数意图、selector、literal 与 producer binding 全部交给既有 `_majority_binding` / `_majority_literal` 聚合。
3. `emit_message` 与 `set_state` 的现有 identity 逻辑不改。

### 5.2 修改 observation 内投票去重

位置：`dynsteer/milestone/compiler.py::_aggregate_and_compile`

现状直接执行：

```python
for observation in observations:
    for node in observation.nodes:
        node_occurrences[node.key].append(node)
```

目标：

1. 每个 observation 内先按 `node.key` 去重。
2. 同一 key 只追加一个代表节点，保证 `support_count <= observation_count`。
3. 代表节点仅用于默认数据；字段聚合必须扫描全部 variants。
4. edge eligibility 仍按 observation 中是否存在两个端点计算，不因重复节点重复计票。

### 5.3 修改 `_compile_node` 的字段来源

位置：`dynsteer/milestone/compiler.py::_compile_node`

现状 tool call 参数来自第一个 representative：

```python
arguments = dict(node.data.get("arguments", {}))
```

目标改为扫描全部支持者的字段并集：

```python
field_names = sorted({
    name
    for value in values
    for name in dict(value.candidate.data.get("arguments", {}))
})
```

每个字段按以下规则处理：

1. 动态 binding 在全部支持者中获得严格多数：保留该 binding。
2. literal binding 在全部支持者中获得严格多数：保留该 literal。
3. 字段缺席获得严格多数且该参数在 tool schema 中非 required：不输出该参数。
4. 字段出现与缺席各占一半，但该参数非 required：选择合法缺席，让未被多数支持的只读 producer 按现有 dangling producer 规则修剪。
5. 字段为 required 但无严格多数 binding：保留 operation，并将 `argument_binding_status` 标记为 `unresolved`；不得臆造 producer 或 literal。
6. 字段名不在 tool schema 中：维持现有校验失败路径，不在聚合阶段修复。

这样处理的预期效果是：`search_messages` 在 6 个候选中全部出现时，即使 3 个无参数、3 个带 optional temporal filter，也能以 6/6 保留 operation；optional filter 无多数时合法缺席，`get_current_timestamp` 不再因被强行保留而制造 FP。

### 5.4 保持 closure 与修剪

以下逻辑不放宽：

- `_validate_aggregated_closure`
- `_prune_dangling_producers`
- `_binding_edges`
- `_add_recovery_dependencies`
- DAG 校验与传递约简

禁止为了提升 operation count 保留不可闭合的 producer、悬空查询或成环边。

### 5.5 测试

新增测试：

1. `test_tool_presence_is_not_split_by_optional_binding`
   - 6 个 observation 均包含同一 `search_messages`；
   - 3 个无参数，3 个带 `creation_timestamp_upperbound`；
   - 断言最终保留 `search_messages`，不退化成 emit-only 图。
2. `test_optional_binding_tie_uses_legal_absence`
   - optional 参数 3/6 动态、3/6 缺席；
   - 断言最终不输出该参数，且未获多数的只读 producer 被修剪。
3. `test_required_binding_tie_stays_unresolved`
   - required 参数两种 producer 各 3/6；
   - 断言 operation 保留，`argument_binding_status == "unresolved"`。
4. `test_duplicate_tool_node_has_one_vote_per_observation`
   - 同一 observation 内重复输出同一 tool evidence；
   - 断言 support count 仍为 1。
5. `test_binding_majority_survives_identity_merge`
   - 4 个 observation 中 3 个使用同一 producer binding；
   - 断言聚合后的 constraint template 指向该 producer。

## 6. WP4：中英文 prompt 硬约束

### 6.1 中文模板

文件：`dynsteer/prompt/templates/milestone/generation.zh.md`

#### 6.1.1 信息不足与 minefield 规则

替换当前“信息不足判定”与 Minefield 第 5 节中含义不完整的规则，明确写入：

1. 信息不足时只针对用户请求对应的真实终态工具或关键 producer 输出 fatal minefield。
2. `missing_required_input` 必须同时满足：
   - `missing_inputs` 为非空数组；
   - 每个元素逐字来自该工具 contract 的 `required_dynamic_inputs`；
   - 所缺字段在当前候选图中确实不可得。
3. 无法列出精确 contract 字段时，禁止使用 `missing_required_input`：
   - 只读 fatal 错误调用使用 `unsafe_tool_call`；
   - 写工具破坏性调用使用 `unsafe_side_effect`。
4. `unsafe_side_effect` 只允许用于 contract 写集或 `state_effect` 非空的工具。
5. 禁止把信息不足泛化成 `add_reminder`、`modify_contact` 等常见写操作。

#### 6.1.2 Tool Operation 结构检查

在“必经性、可行性与 goal”下新增固定检查：

1. 相对时间与 recency：先确定 latest、oldest、upcoming、yesterday、weekday delta 需要的边界，再按 contract 决定是否需要 `get_current_timestamp`、`shift_timestamp` 或 `timestamp_diff`。
2. 搜索过滤：工具 contract 支持 temporal filter 时直接绑定到搜索参数；不支持时才保留显式计算 producer。
3. 多轮任务：每个 turn 必须有自身终态判断，后续 turn 可继承先前 producer 输出，不得因最后一轮是简短追问而输出空图。
4. 环境前置：WiFi、cellular、location 只在任务语义或工具执行前提确实依赖时保留。
5. 先搜后改：按 ID、phone number、recency 修改或删除时，必须先保留 lookup producer，再接 terminal mutation。
6. 信息不足：不得输出投机工具链；使用空图 / response-only 或符合 contract 的 minefield。

同时修改现有“对抗性冗余”表述：辅助计算工具的禁止条件应是“任务语义与工具 contract 均不需要”，而不是仅看用户是否字面提及时间位移、差值或换算，避免 `find days till holiday` 这类任务被错误抑制。

#### 6.1.3 最终核对

在返回前 checklist 中新增：

- `missing_required_input` 是否有非空且逐字合法的 `missing_inputs`？
- 只读 fatal 是否使用 `unsafe_tool_call`？
- 写工具 fatal 是否使用 `unsafe_side_effect`？
- recency / relative time 链是否按 contract 保留必要 producer？
- multiple-turn 是否保留每轮终态并继承跨轮依赖？

### 6.2 英文模板

文件：`dynsteer/prompt/templates/milestone/generation.en.md`

逐条翻译并保持同等约束，不允许中文模板比英文模板多出硬规则。重点同步：

1. `missing_inputs` 非空、字段逐字来自 `required_dynamic_inputs`、当前图不可得。
2. readonly fatal 使用 `unsafe_tool_call`，destructive write 使用 `unsafe_side_effect`。
3. 只标记真实 terminal 或 critical producer，不泛化为常见写操作。
4. recency / relative time / multi-turn / environment prerequisite / lookup-before-mutation checklist。
5. final checklist 同步增加对应五项。

### 6.3 Prompt 测试

在现有 prompt builder / template 测试中断言：

1. 中文与英文模板均包含 `required_dynamic_inputs` 硬规则。
2. 两个模板均包含 readonly / write reason 三分法。
3. 两个模板均包含 recency producer checklist。
4. `_assert_no_forbidden_generation_inputs(builder.payload)` 继续通过，确保新增文本没有泄露完整私有 contract、simulation state 或 reference graph。

## 7. API 文档同步

修改 `docs/apis/milestone.md`：

1. 在 canonical semantics 段落说明 legacy reason 归一规则：
   - required 缺失 → `missing_required_input`；
   - contract 写集非空 → `unsafe_side_effect`；
   - 其余 fatal 调用 → `unsafe_tool_call`。
2. 在 aggregation 段落说明：
   - minefield 投票身份为 `(turn_id, evidence_id, reason_code)`；
   - missing inputs 在核心身份当选后择优；
   - tool call 存在性身份为 `(turn_id, evidence_id)`；
   - argument binding 独立严格多数聚合。
3. 说明 optional binding 无多数且合法缺席时选择缺席，required binding 无多数时保留 unresolved，不伪造可执行值。

## 8. Replay 与验收口径

### 8.1 Deterministic replay

使用已保存 `llm_outputs` 重编译现有 case，不调用 LLM、不生成新结果目录。分两次归因：

1. WP1-WP3 后：
   - 评估 compiler 与 reference 归一变化；
   - prompt 对旧 response 无影响，不得声称旧 response 的 `invalid_minefield` 因 prompt 下降。
2. WP4 后：
   - 只做模板渲染与禁止输入检查；
   - 真实生成效果需等用户决定发起实验后再评估。

### 8.2 必须通过的门槛

| 检查项 | 门槛 |
| --- | --- |
| 语法 | `uv run python -m py_compile dynsteer/milestone/semantics.py dynsteer/milestone/compiler.py` |
| 单元测试 | `uv run pytest tests -q` 全部通过 |
| operation-empty case | 从 126 降至不超过 65 |
| Tool Operation Micro Recall | 不低于 65% |
| Tool Operation Macro Precision | 不低于 75% |
| required binding 无多数 | 必须标记 unresolved，不得伪造 |
| valid DAG | replay 后 graph return 与 DAG 可编译率不下降 |
| Minefield exact TP | 在 WP1 重新归一 reference 后，新口径 exact TP 不下降 |
| 候选配置 | 不增加 request、batch 或 target graph 数量 |

若 replay 与门槛冲突，优先保留安全校验和正确语义，不为了指标放宽 contract、closure 或 strict majority。

## 9. 风险与回滚

| 风险 | 处理 |
| --- | --- |
| optional binding 合法缺席可能丢弃实际有用的过滤条件 | 仅在字段非 required 且无严格多数时缺席；required 字段必须 unresolved。 |
| minefield missing inputs 择优可能选到少数派字段 | 只在核心身份已严格多数后择优；候选本身仍需先通过 contract 校验。 |
| tool identity 合并可能提高 FP | 保留 closure、DAG、dangling producer 修剪；用 replay 监控 generated-only operation。 |
| prompt 规则过多导致候选多样性下降 | 先落 compiler 修复并 replay；prompt 后置，且不提高采样数。 |
| legacy reference reason 语义不可完全恢复 | 只做 contract 可证明的归一，显式 reason 不覆盖。 |

每个工作包独立提交工作区变更边界，出现回归时按 WP1 → WP2 → WP3 顺序逐个回退定位；不做混合回滚。

## 10. 附录A. 项目中没有把握实现的模块部分

1. **WP4 的真实生成收益无法仅靠旧 response replay 保证。** Prompt 修改只能通过模板测试确认约束存在，不能证明模型一定遵循；真实实验需用户另行决定。
2. **optional binding 并列策略存在语义风险。** 合法缺席并不等价于任务正确，必须依赖 replay 的 generated-only operation 与 exact TP 监控。
3. **legacy minefield reason 原始设计意图不可完全恢复。** 无显式 reason 的旧数据只能依据公开 contract 做确定性归一。
4. **指标门槛来自当前保存结果估算。** 工作区 compiler 已有其他未提交变更，replay 的绝对 TP 可能与原始 summary 存在版本差；门槛需同时检查趋势与安全校验，不能只盯单一数值。
