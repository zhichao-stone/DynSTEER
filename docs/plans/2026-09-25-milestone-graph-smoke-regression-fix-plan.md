# Milestone graph smoke regression 修复方案

- 日期：2026-09-25
- 结果依据：`results/milestone/toolsandbox_milestone_reliability_partial_main`
- 状态：待确认，未修改生成代码
- 修复目标：先让当前 30-case smoke subset 从“后处理确定性删空”恢复为可比较的非空 milestone graph，再评估模型能力

## 1. 问题结论

本次结果不是 LLM JSON 输出失败。30 个 case 的候选图漏斗为：

| 阶段 | 数量 |
|---|---:|
| LLM 请求 | 106 |
| 返回候选图 | 210 |
| JSON 解析成功 | 210 |
| valid candidate | 51 |
| partial candidate | 100 |
| semantic fatal reject | 59 |
| accepted observation | 143 |
| 参考图非空但生成图为空 | 25 / 29 |
| `no_majority_goal` | 22 |
| `no_observation` | 3 |
| 编译异常 | 1 |

核心断点有五类：

1. **日期/时间 literal 只做子串匹配**：`5PM` 派生出的 `hour=17, minute=0` 被判定为 `public_literal_mismatch`，进而删除整个 producer，导致下游 binding 级联失败。
2. **隐藏 state 字段契约**：validator 要求 `wifi`、`location_service` 等内部字段名，但 prompt 未暴露这些字段；模型输出 `wifi_enabled`、`location_service_enabled` 后被 `state_required_input_missing` 拒绝。
3. **终态表示分裂**：同一业务状态修改有时被表示为 `set_state`，有时被表示为直接 terminal `tool_call`。二者在 6 个候选中各占 3 票，均不满足严格多数，最后共享 producer 也被 `orphan_support_chain` 删除。
4. **emit_message identity 过细**：动态答案只要措辞不同就拆成不同节点，导致工具链虽有多数支持但没有 terminal 多数。
5. **缺少异常栈**：`modify_reminder_with_recency_latest` 只记录 `KeyError: 'n4'`，无法定位 compiler 内部具体映射错误。

## 2. 修复原则

1. **确定性优先**：日期解析、字段别名、state effect 投影、operation 别名都由公开工具契约和输入文本确定性推导，不引入 LLM judge。
2. **错误分级**：字段级 provenance 错误只降级该字段，不删除整个 producer；必需动态 binding 无法闭合时才删除对应 consumer。
3. **语义 identity 与 runtime binding 分离**：先按业务 effect / answer kind 聚合节点，再对 selector、source_ref、producer 做字段级多数。
4. **不泄露评估私有信息**：prompt 只暴露工具能力、输出、state effect 和字段名，不暴露 reference graph、expected rows、final state 或 matcher。
5. **不放宽安全性**：不能为了提升指标把无法闭合的 required binding伪装成可执行；无有效观测仍返回空图，但必须给出更准确的 empty reason。

## 3. 修改范围

### 3.1 `dynsteer/adapter/toolsandbox/utils/effects.py`

为 side-effect 工具契约补充可安全暴露的 state 字段元数据：

```json
{
  "effect": {"namespace": "SETTING", "operation": "set"},
  "required_dynamic_inputs": ["location_service"],
  "executor_arguments": {"on": "location_service"},
  "state_fields": {
    "location_service": {"type": "boolean"}
  }
}
```

具体修改：

1. 为以下工具补充或核实 `state_fields`：
   - `set_wifi_status` -> `SETTING.wifi`
   - `set_cellular_service_status` -> `SETTING.cellular`
   - `set_location_service_status` -> `SETTING.location_service`
   - `set_low_battery_mode_status` -> `SETTING.low_battery_mode`
2. 为 CONTACT / REMINDER / MESSAGING 的 add/update/remove 工具声明状态字段：
   - key 字段进入 `match`：`person_id`、`reminder_id`
   - 业务字段进入 `values`：`relationship`、`content`、`reminder_timestamp`、`recipient_phone_number`
3. 为 `send_message_with_phone_number` 明确：
   - effect 是 `MESSAGING.add`
   - `recipient_phone_number`、`content` 是新增业务字段
4. `state_fields` 只描述字段名和类型，不包含初始值、reference rows 或评估器私有信息。

### 3.2 `dynsteer/prompt/template.py`

将当前 `_safe_tool_output_contracts()` 扩展为安全工具契约投影：

```json
{
  "tool_name": "set_wifi_status",
  "outputs": {},
  "state_effect": {
    "namespace": "SETTING",
    "operation": "set",
    "fields": ["wifi"]
  },
  "executor_arguments": {"on": "wifi"}
}
```

具体修改：

1. 保留现有输出 selector / type / cardinality。
2. 对具有 `state_evaluator` 的工具额外暴露：
   - namespace；
   - operation；
   - state field 名；
   - executor argument 到 state field 的映射。
3. 不暴露：
   - `state_evaluator`
   - simulation state
   - environment rules
   - reference matcher
   - expected rows。
4. 确保英文和中文 prompt 使用同一 payload 字段。

### 3.3 `dynsteer/prompt/templates/milestone/generation.en.md` 与 `generation.zh.md`

同步修改生成语义：

1. 明确规则：
   - 凡是工具契约声明 `state_effect` 的业务状态修改，必须输出 `set_state`；
   - 不得把同一终态同时输出为 terminal `tool_call`。
2. 明确 `set_state.values` / `match` 只能使用 `state_effect.fields` 中的字段名：
   - 使用 `wifi`，不使用 `wifi_enabled`
   - 使用 `location_service`，不使用 `location_service_enabled`
3. 明确日期和时间组件允许从 instruction 的结构化表达式确定性解析：
   - `3/22/2024 5PM` 可以产生 `year=2024, month=3, day=22, hour=17, minute=0`
   - 这些值仍必须使用 `public_literal` binding，来源指向 instruction。
4. 明确动态 `emit_message` 的 `content_requirement` 描述答案类别和必须传达的实体，不要求预测运行时数值。
5. 删除或改写会诱导“直接终态工具”和“批量状态目标”对抗性二选一的表述，改为：
   - runtime 表示必须服从工具契约；
   - 多样性只能来自真实业务替代方案，不能来自同一 effect 的两种语法表示。

### 3.4 `dynsteer/milestone/compiler.py`

这是主要修改文件。

#### P0-1 候选图确定性归一

在 `_repair_candidate_graph()` 前新增 `_normalize_candidate_graph()`，执行四类确定性归一。

##### A. terminal tool_call -> set_state 投影

若同时满足：

1. `tool_call` 对应 contract 含有 `state_evaluator=toolsandbox_snapshot`；
2. 该工具没有可消费输出；
3. 该节点在原图中没有下游 consumer；
4. 工具 effect 能映射到当前 namespace / operation；

则将 terminal `tool_call` 投影为 `set_state`：

- `executor_evidence_id` 保留原工具 evidence ID；
- `namespace` / `operation` 来自 `contract.effect`;
- key 输入映射到 `match`；
- 非关键业务输入映射到 `values`;
- cardinality 默认 `one`，后续如契约能声明批量能力再扩展为 `all`。

示例：

```text
search_contacts(relationship=friend)
  -> modify_contact(person_id=search_output, relationship=enemy)
```

归一为：

```text
search_contacts(relationship=friend)
  -> set_state(
      namespace=CONTACT,
      operation=update,
      match={person_id: search_output},
      values={relationship: enemy},
      executor_evidence_id=modify_contact
    )
```

这样同一 effect 的 `set_state` 与直接工具调用不再互相稀释。

##### B. state operation / 字段名归一

1. `MESSAGING.send` 归一为 `MESSAGING.add`。
2. 对每个 state effect 使用唯一字段别名修复：
   - `wifi_enabled` -> `wifi`
   - `cellular_enabled` / `cellular_service_enabled` -> `cellular`
   - `location_service_enabled` -> `location_service`
   - `low_battery_mode_enabled` -> `low_battery_mode`
3. 仅当目标 contract 中存在唯一同类型字段时执行修复；歧义时不猜测。
4. 每次 repair 写入 `repair_actions`，保留 before / after / basis。

##### C. 日期与时间 literal 派生校验

新增 `_public_literal_derived_from_instruction(value, instruction, field)`，按字段类型分级。

第一批支持：

1. `M/D/YYYY`、`D/M/YYYY` 仅在 ToolSandbox 当前 case 已明确格式或存在唯一合法解析时使用；
2. `YYYY-M-D`；
3. `H am/pm`、`H:MM am/pm`：
   - `5PM -> hour=17`
   - 缺省 `minute=0`
   - 缺省 `second=0`
4. 数字 token 与单位：
   - `2 days -> days=2`
5. 布尔语义沿用现有 near-field 逻辑，不改成 LLM judge。

约束：

- 只允许确定性 parser；
- 无法唯一解析时仍报 `public_literal_mismatch`;
- 派生成功时新增 issue 或 repair 记录 `literal_derivation`，不得静默放宽；
- 不支持猜测“明天”“上周”等需要当前时间的相对日期；这些仍必须由当前时间工具和转换工具组成。

##### D. emit_message 动态答案归一

`_node_identity()` 中 `emit_message` 不再只用整段 content_requirement 字符串。

归一为两类：

1. **static answer**：无动态 producer，继续使用规范化文本作为 identity。
2. **dynamic answer**：存在上游 tool output binding 或计算工具时，identity 使用：
   - answer kind：如 `days_until_holiday`、`tool_result_answer`
   - 必需实体：如 holiday name
   - producer tool / output selector 类别
   - 不包含“derived from / computed via / using”等措辞差异。

第一批只根据公开工具名和字段做确定性分类，避免通用语义模型：

- `timestamp_diff.days` -> `duration_answer`
- search/getter 输出 -> `tool_result_answer`
- state effect 后回复 -> `operation_confirmation`

无法确定分类时保持现有文本 identity。

#### P0-2 字段级校验与错误分级

修改 `_validate_candidate_graph()`：

1. `_validate_node()` 返回：
   - retained node；
   - node 级 fatal issues；
   - field 级 local issues。
2. 只有以下情况删除整个节点：
   - evidence 不存在；
   - node kind / turn 无效；
   - 结构无法解析；
   - required binding 无法闭合且该节点是必需 consumer。
3. `public_literal_mismatch`、schema type mismatch、unknown public source 只降级该字段：
   - 记录 `field_binding_unresolved`;
   - 不删除 producer；
   - 不级联删除其下游 consumer。
4. 对 producer 节点，即使某个 public literal provenance 有争议，只要 operation 结构和输出契约有效，仍作为 partial observation 参与聚合。
5. terminal state goal 如果缺必需 state 字段，先尝试字段别名修复；修复后仍缺失才删除该 terminal，不影响其他 producer。

#### P0-3 聚合 identity 分层

调整 `_node_identity()`：

1. **operation identity**
   - tool producer：turn + tool identity + argument intent 的粗粒度业务意图；
   - state goal：turn + effect namespace + operation + target/value field intent；
   - emit message：answer identity。
2. **binding identity**
   - source_ref；
   - producer operation identity；
   - selector；
   - cardinality；
   - literal value。
3. 节点保留先看 operation identity；
4. 字段绑定再用现有 `_majority_binding()` / `_majority_literal()` 单独多数；
5. 字段 binding 无多数时标记 unresolved，不因为措辞差异删除 operation。
6. direct terminal tool 投影为 state goal 后，与手写 state goal 使用同一 identity。

#### P0-4 empty reason 与异常观测

1. 扩展 `GenerationReport.empty_reason`：
   - `all_candidates_rejected`
   - `no_observation`
   - `empty_after_state_projection`
   - `empty_after_binding_closure`
   - `empty_after_terminal_majority`
   - `empty_after_aggregation`
2. 在 `_run_case()` 的 generation exception 分支记录：
   - `traceback.format_exc()`
   - case_id
   - stage hint：`compile_task_case`
3. 不再只输出 `KeyError: 'n4'`。
4. 针对 `n4` 增加回归测试，确保 set_state 被局部校验或归一后，所有 producer local id 查找仍限制在本候选图内。

### 3.5 `dynsteer/milestone/model.py`

为 `GenerationReport` 增加少量诊断字段：

```text
terminal_state_projected_count
state_field_repaired_count
literal_derivation_count
field_unresolved_count
empty_after_terminal_majority_count
```

字段均使用默认值，避免破坏已有结果读取；不做兼容层，不改已有接口语义。

### 3.6 `milestone_reliability.py`

summary 聚合新增诊断：

```text
generation.terminal_state_projected_count
generation.state_field_repaired_count
generation.literal_derivation_count
generation.field_unresolved_count
empty_reason_counts
```

同时保留旧指标，便于复跑前后对比。

### 3.7 测试

新增或更新 PyTest 测试，优先复用当前结果中的原始候选结构作为 fixture，不调用外部 LLM。

#### 1. 日期派生

覆盖：

- `3/22/2024 5PM`
- `2024-03-22 5:00PM`
- `2 days`
- 非唯一日期格式不猜测

#### 2. state 字段修复

覆盖：

- `wifi_enabled -> wifi`
- `location_service_enabled -> location_service`
- `MESSAGING.send -> MESSAGING.add`
- 歧义字段不修复

#### 3. terminal 投影

覆盖：

- `modify_contact` -> CONTACT update
- `set_wifi_status` -> SETTING set
- `send_message_with_phone_number` -> MESSAGING add
- 有下游 consumer 的工具不投影

#### 4. 字段级校验

覆盖：

- hour provenance 错误不删除 datetime producer；
- 下游 timestamp binding 仍可闭合；
- required binding 无法闭合时才删除 consumer。

#### 5. 聚合

覆盖：

- 3 票 direct tool + 3 票 set_state 聚合为同一 state effect；
- 不同措辞的动态答案不分裂；
- 不同 literal source_ref 不拆散同一 operation；
- binding 无多数时保留 operation 并标记 unresolved。

#### 6. 异常回归

覆盖：

- `modify_reminder_with_recency_latest` 形状的候选图不再触发 `KeyError: n4`;
- generation failed 输出包含 traceback。

## 4. 实施顺序

### Phase A：先止血观测与确定性归一

1. 增加 traceback 记录。
2. 增加 state 字段契约和安全 prompt 投影。
3. 实现 state operation / 字段名修复。
4. 实现 terminal tool -> set_state 投影。

预期改善：

- `state_required_input_missing` 显著下降；
- setting / contact / messaging case 不再因终态表示分裂而空图；
- `orphan_support_chain` 显著下降。

### Phase B：字段级校验与日期派生

1. `_validate_node()` 改为节点级 + 字段级结果。
2. public literal 错误不再删除整个 producer。
3. 实现日期、时间、数字单位确定性 parser。
4. 补充 `add_reminder` 系列回归测试。

预期改善：

- 3 个 `no_observation` date/time case 至少产生 partial observation；
- `public_literal_mismatch` 不再导致 `binding_unresolved` 级联；
- `fatal_parse_graph_count` 明显下降。

### Phase C：聚合 identity

1. operation identity 与 binding identity 拆分。
2. 动态 emit answer 归一。
3. state effect 与 direct terminal 投影共享 identity。
4. binding unresolved 不删除 operation。

预期改善：

- `no_majority_goal` 明显下降；
- `find_days_till_holiday` 保留 `get_current_timestamp -> search_holiday -> timestamp_diff -> answer` 链；
- `update_contact_relationship_with_relationship` 不再 3/3 分裂。

### Phase D：验证与复跑

1. 运行新增 PyTest。
2. 运行不访问网络的 compiler fixture 回归。
3. 检查现有 experiment 配置解析。
4. 经用户确认后，复跑同一个 30-case smoke 配置。
5. 对比复跑前后：
   - `no_observation`
   - `no_majority_goal`
   - `orphan_support_chain`
   - `state_required_input_missing`
   - `public_literal_mismatch`
   - `fatal_parse_graph_count`
   - `goal_effect_exact`

## 5. 验收标准

### 必须达成

1. 30-case 结果中没有 compiler 内部异常。
2. `add_reminder_content_and_date_and_time` 不再因 `hour=17` 被子串校验拒绝。
3. `turn_on_location_low_battery_mode` 与 `wifi_off_3_distraction_tools` 不再使用 `location_service_enabled` / `wifi_enabled` 作为最终 state 字段。
4. `update_contact_relationship_with_relationship` 中 direct `modify_contact` 与 `set_state` 不再互相稀释。
5. 字段级 literal mismatch 不再删除整个 producer。
6. generation failed 保留完整 traceback。
7. 新增测试全部通过。
8. 不向 prompt 泄露 reference graph、final state、expected rows 或 evaluator matcher。

### 定量观察目标

在同一个 30-case subset 上：

| 指标 | 当前 | 目标 |
|---|---:|---:|
| 编译异常 | 1 | 0 |
| `no_observation` | 3 | ≤ 1 |
| `no_majority_goal` | 22 | 明显下降，至少 ≤ 12 |
| 参考非空生成空图 | 25 / 29 | 至少降至 15 / 29 |
| 实际 operation graph 非空 | 1 | 明显上升 |

不把 `goal_effect_exact` 一次性大幅提升作为硬验收，因为本轮先修复确定性问题；指标提升后再处理 minefield 和 preserve 对齐。

## 6. 明确非目标

1. 不回退到旧路径 ensemble。
2. 不用 LLM judge 替代 provenance 校验。
3. 不直接放宽所有 validator。
4. 不把 ToolSandbox reference 的 expected rows 或隐藏状态暴露给生成器。
5. 不在本轮强行拟合 preserve constraints。
6. 不通过单纯增加采样次数掩盖归一问题。
7. 不主动 git commit。

## 7. 附录A. 项目中没有把握实现的模块部分

1. **direct terminal tool -> set_state 的完整参数映射**
   - 风险：不同 add/update/remove 工具的 key 参数与业务参数划分可能存在例外。
   - 缓解：第一版只处理 ToolSandbox contract 能明确推导的工具；无法唯一映射的 terminal tool 不投影，保留原有行为并记录诊断。

2. **动态 emit_message 的语义 identity**
   - 风险：答案类别过粗可能把不同答案合并，过细又继续稀释。
   - 缓解：第一版只做确定性 producer/tool-output 分类；无法确定时退回文本 identity，并用测试锁定 `timestamp_diff.days` 答案。

3. **日期格式歧义**
   - 风险：`3/4/2024` 在不同 locale 下含义不同。
   - 缓解：只在当前 ToolSandbox case 的可验证格式或唯一合法解析下接受；无法唯一解析时拒绝并记录，不猜测。

4. **`KeyError: n4` 的精确根因**
   - 风险：现有结果没有 traceback，无法保证第一轮就定位到具体行。
   - 缓解：先补完整异常栈，再用同形状 fixture 复现；若根因在其他模块，方案中的 compiler 测试仍能防止同类 local-id 越图访问。

5. **复跑指标波动**
   - 风险：LLM 采样即使 temperature=0.2 也可能有差异。
   - 缓解：验收以确定性诊断指标下降为主；完整复跑需要用户确认后再消耗 API 额度。

## 8. 确认点

请确认以下范围：

1. 是否同意先做 Phase A-C，本轮不处理 preserve 约束和完整 minefield 契约重构？
2. 是否同意在 prompt 中暴露工具 state effect 字段名和 executor argument 映射，但继续隐藏初始状态与评估器？
3. 是否同意 deterministic terminal tool -> set_state 投影作为默认行为？
4. 修复完成后是否需要立即复跑同一个 30-case 实验？