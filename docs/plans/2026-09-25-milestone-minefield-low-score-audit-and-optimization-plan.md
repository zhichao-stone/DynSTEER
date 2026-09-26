# Milestone Minefield 与 Tool Operation 低分核查与优化方案

- 日期：2026-09-25
- 范围：`results/milestone/toolsandbox_milestone_reliability_main`
- 目标：解释 Fatal Minefield P/R/F1 与 Tool Operation Micro Recall / Macro Precision 偏低的原因，并给出可分阶段落地的修正方案；同时记录本轮 summary 指标精简改造。

## 1. 核查结论概览

当前主实验共有 509 个 case，其中 507 个 completed；排除 24 个 few-shot 污染 case 后，报告口径为 483 个 primary completed case。

Fatal Minefield 的 primary micro 混淆量为：

| Metric | 计算 | 数值 |
| --- | ---: | ---: |
| Precision | 25 / 46 | 54.3% |
| Recall | 25 / 97 | 25.8% |
| F1 | 2 × 25 / (46 + 97) | 35.0% |

使用已保存的 LLM response 做 deterministic replay 后，reference 与 generated 的最终 fatal minefield 精确身份分布如下：

- reference：97 个，其中 `missing_required_input` 56 个、`unsafe_side_effect` 41 个。
- generated：46 个，其中 `missing_required_input` 45 个、`unsafe_side_effect` 1 个。
- exact true positive：25 个；漏报 72 个；误报 21 个。

漏报集中项：

| Reference fatal identity | 数量 |
| --- | ---: |
| `search_reminder + unsafe_side_effect` | 36 |
| `timestamp_diff + missing_required_input` | 10 |
| `send_message_with_phone_number + missing_required_input` | 10 |
| `modify_reminder + missing_required_input` | 5 |
| `reminder_3 + unsafe_side_effect` | 5 |
| 其他 4 类 | 6 |

误报集中项：

| Generated fatal identity | 数量 |
| --- | ---: |
| `add_reminder + missing_required_input` | 16 |
| `reminder_0 + missing_required_input` | 3 |
| `remove_contact + missing_required_input` | 1 |
| `remove_contact + unsafe_side_effect` | 1 |

只修复最大的 `search_reminder` 36 个漏报，理论 recall 可从 25.8% 提升至 62.9%；如果同时消除 `add_reminder` 的 16 个主要误报，precision 可从 54.3% 提升至约 76.7%。因此优化应优先处理 reason 归一和候选契约约束，而不是盲目增加采样次数。

## 2. 低分根因

### 2.1 Reference reason 归一与生成协议不一致

`dynsteer/milestone/semantics.py::_minefield_identity` 在旧 reference 没有显式 `reason_code` 时，只依据 tool schema 判断 required arguments：

- 若参数缺失，则归一为 `missing_required_input`。
- 否则统一兜底为 `unsafe_side_effect`。

这使只读的 `search_reminder` reference 被标为 `unsafe_side_effect`。但生成 prompt 与 compiler 契约中，`unsafe_side_effect` 要求工具 contract 有写集；只读 fatal 错误调用应使用 `unsafe_tool_call`。因此同一任务族在 reference 与 generated 两侧采用了不同语义协议，形成系统性身份错配。

### 2.2 `missing_required_input` 的生成约束过严且提示不完整

LLM 原始 response 中共出现 965 个候选 minefield，覆盖 216 个 case：

| Raw reason_code | 数量 |
| --- | ---: |
| `missing_required_input` | 796 |
| `unsafe_tool_call` | 115 |
| `unsafe_side_effect` | 37 |
| 空值/非法值 | 17 |

其中 298 个候选的 `missing_inputs` 为空，另有许多自然语言字段或非 contract 字段。compiler 当前要求：

1. `reason_code` 必须在四个枚举值内；
2. `missing_inputs` 必须非空；
3. `missing_inputs` 必须是工具 contract `required_dynamic_inputs` 的子集；
4. 所缺输入在当前候选图中确实不可得。

这本身能防幻觉，但 prompt 没有逐工具列出可用的 `required_dynamic_inputs`，也没有明确“无法列出 contract 字段时禁止使用 missing_required_input”。结果模型经常把“任务信息不足”直接写成 `missing_required_input: []`，随后被 validator 删除。

最终 case 诊断中：

- `invalid_minefield`：439 次；
- `minefield_terminal_conflict`：3 次；
- 受影响 case：139 个。

这说明大量模型实际上尝试生成 minefield，但在 contract 校验阶段被系统性丢弃。

### 2.3 候选投票身份与评分身份不一致

compiler 的 `_CandidateMinefield` 参与严格多数聚合时包含：

- `turn_id`
- `evidence_id`
- `reason_code`
- `missing_inputs`

而最终 semantic metric 的 minefield identity 只包含：

- `tool_name`
- `severity`
- `reason_code`

因此两个语义上会得分的候选可能因为 `evidence_id` 或 `missing_inputs` 细节不同而拆分票数，无法达到 `2 * support > observation_count`。这是召回率偏低的结构性原因之一。

### 2.4 非可执行任务的 minefield 目标不明确

生成 prompt 要求信息不足时输出 fatal minefield，但没有足够明确地限定“只能针对当前任务中真实终态失败工具”。模型因此偏好生成显眼的写操作（如 `add_reminder`），而 reference 更常标记导致错误答案的只读工具或任务关键 producer（如 `search_reminder`、`timestamp_diff`）。

## 3. Tool Operation Micro Recall 与 Macro Precision 偏低分析

本节全部 headline 计数来自已保存 case JSON 的 primary completed 口径；raw 候选分解来自同目录 `llm_outputs`，未重跑 LLM，也未覆盖主结果。当前工作区 compiler 后续有未提交修改，因此 deterministic replay 的 operation identity 分布只用于定位错类，不用于替换原始 TP 口径。

### 3.1 Micro Recall 偏低：漏整链多于局部参数错配

Tool Operation 的 primary micro 混淆量为：

| Metric | 计算 | 数值 |
| --- | ---: | ---: |
| Micro Precision | 466 / 575 | 81.0% |
| Micro Recall | 466 / 821 | 56.8% |
| Micro F1 | 2 × 466 / (575 + 821) | 66.8% |
| FN | 821 - 466 | 355 |
| FP | 575 - 466 | 109 |

其中 126 个 case 的 reference operations 非空，但最终 generated operations 为 0。这 126 个 case 共有 269 个 reference operations，贡献 269 / 355 = 75.8% 的 FN。因此 Micro Recall 偏低首先不是“工具选对但参数身份略有差异”的局部问题，而是大量任务最终没有保留任何可计分工具操作。

按 reference operation 数分层后，漏报集中在多步链：

| Reference operations | Case 数 | FN | Macro Recall 均值 |
| ---: | ---: | ---: | ---: |
| 1 | 86 | 35 | 0.593 |
| 2 | 178 | 147 | 0.587 |
| 3 | 61 | 52 | 0.716 |
| 4 | 49 | 121 | 0.383 |

2-operation 与 4-operation 任务合计贡献 268 / 355 = 75.5% 的 FN。Micro 按 operation 计数，而 Macro 按 case 平均，因此 operation 数较多的失败任务会在 Micro Recall 中获得更高权重；这解释了 Micro Recall 56.8% 明显低于 Macro Recall 65.6% 的现象。

进一步分解 126 个 operation-empty case 的 raw 候选：

| Raw 候选特征 | Case 数 | 说明 |
| --- | ---: | --- |
| 所有候选均无工具调用 | 33 | 模型倾向 response-only / empty，真实漏生成整链。 |
| 至少一个 reference 工具在 raw 候选中已获严格多数 | 61 | 工具名层面本可保留，但最终被聚合、closure 或修剪丢弃。 |
| reference 工具出现过，但未达严格多数 | 22 | 候选结构摇摆，例如无参数版与动态绑定版各占一半。 |
| raw 候选有工具，但 reference 工具完全未出现 | 10 | 模型选择了其他工具或错误 producer。 |

这 126 个 case 的 269 个 reference operation occurrences 中，raw 候选层面有 117 个完全 absent、79 个出现但低于严格多数、73 个达到严格多数。换言之，超过一半 occurrence 在 raw response 中至少出现过，说明相当一部分 FN可以在不增加采样的情况下通过聚合身份与结构闭合修正挽回。

典型例子是 `search_message_with_recency_latest`：6 个 raw 候选都包含 `search_messages`，但其中 3 个使用无参数搜索、3 个使用 `creation_timestamp_upperbound` 动态绑定。`_node_identity` 当前把 `argument_intents` 纳入节点投票身份，导致同一工具被拆成 3/6 与 3/6，均不满足严格多数；`get_current_timestamp` 与 emit 节点反而达到 6/6。随后搜索节点缺失，当前时间 producer 变成悬空只读节点被修剪，最终只剩 emit 类节点，Tool Operation 计数为 0。

对已保存 response 做当前代码 deterministic replay 时，最大 reference-only operation identity 为 `get_current_timestamp`，约 112 次；`shift_timestamp`、部分 `timestamp_diff` 也集中在 reference-only 侧。generated-only 侧则集中在 `search_messages`、`search_contacts`、直接写操作以及 scrambling 后的 `utilities_*`、`reminder_*`、`contact_*`、`messaging_*`。这说明模型经常把相对时间、recency、先搜后改任务简化成直接搜索或直接写操作，漏掉必要 producer / conversion；在 scrambled tool name 场景下，别名与 evidence 选择也不够稳定。

### 3.2 Macro Precision 偏低：主要是 operation-empty case 的 0 分惩罚

Macro Precision 为 0.659，Macro Recall 为 0.656。483 个 case 中有 136 个 precision 为 0，直接贡献 136 / 483 = 28.2 个百分点的 Macro Precision 惩罚；其余 347 个 case 的平均 precision 为 0.918。因此 Macro Precision 偏低的核心不是普遍性小幅 FP，而是 all-or-nothing 失败。

136 个 zero-precision case 的构成为：

| 构成 | Case 数 | 影响 |
| --- | ---: | --- |
| reference operations > 0，generated operations = 0 | 126 | 同时产生 P=0 与 R=0，是主因。 |
| reference operations = 0，generated operations > 0 | 10 | 信息不足场景过度生成工具，误报。 |

分层验证进一步确认：

| 分层 | Case 数 | Macro Precision | Macro Recall |
| --- | ---: | ---: | ---: |
| 全部 primary completed | 483 | 0.659 | 0.656 |
| reference operations > 0 | 374 | 0.587 | 0.583 |
| reference 与 generated operations 均 > 0 | 248 | 0.885 | 0.879 |
| generated operations > 0 | 258 | 0.851 | 0.845 |

一旦最终保留了至少一个 operation，case 级 precision 上升到 0.885；因此优先修复整链丢失和过度生成，比继续微调局部参数匹配更有效。另需注意，109 个 reference-empty case 中有 99 个 generated 也为空并按空集约定得到 P=1，只有 10 个过度生成得到 P=0；空 reference case 整体实际上抬高了 Macro Precision。排除全部 reference-empty case 后 Macro Precision 降至 0.587，说明真实可执行任务上的 precision 问题更严重。

## 4. 汇总指标精简改造（本轮已执行）

`milestone_reliability.py::_write_report` 的 `summary.json` 只保留以下内容：

```json
{
  "status_counts": {},
  "graph_return_count": 0,
  "valid_dag_compilation_rate": 0.0,
  "evaluation": {
    "tool_operation_micro": {"precision": 0.0, "recall": 0.0, "f1": 0.0},
    "tool_operation_macro": {"precision": 0.0, "recall": 0.0, "f1": 0.0},
    "fatal_minefield": {"precision": 0.0, "recall": 0.0, "f1": 0.0}
  },
  "topology": {"ged_similarity": {"mean": 0.0}}
}
```

实现要点：

1. `tool_operation_micro` 由 primary completed case 的逐 case `true_positive/generated_count/reference_count` 聚合。
2. `tool_operation_macro` 由 primary completed case 的逐 case precision/recall/F1 求均值。
3. `fatal_minefield` 使用同一 primary 口径的 micro 聚合，避免污染 case 混入。
4. `topology.ged_similarity.mean` 使用 completed case 的 topology GED。
5. `valid_dag_compilation_rate = graph_return_count / sum(status_counts.*)`，同时保留分子分母。
6. 不修改逐 case JSON、LLM response、validation issues 和 diagnostics，保证问题可回溯。

## 5. 优化方案

### Phase 1：统一 reason 语义与 reference 归一

修改位置：`dynsteer/milestone/semantics.py::_minefield_identity`

目标：

1. 读取 `view.tool_contracts` 中该工具的 `writes`。
2. 旧 reference 无显式 reason 时：
   - required arguments 不可得 → `missing_required_input`；
   - contract 有写集 → `unsafe_side_effect`；
   - contract 无写集 → `unsafe_tool_call`。
3. 不再默认把只读工具兜底成 `unsafe_side_effect`。

验收：

- `search_reminder` reference 从 36 个 `unsafe_side_effect` 改为 `unsafe_tool_call`；
- reference reason 分布与 compiler 可验证枚举语义一致；
- replay 后 exact identity TP 不下降。

### Phase 2：强化生成 prompt 的 minefield contract

修改位置：

- `dynsteer/prompt/templates/milestone/generation.zh.md`
- `dynsteer/prompt/templates/milestone/generation.en.md`

修改内容：

1. 为 `missing_required_input` 增加硬规则：`missing_inputs` 只能填写当前工具 contract 的 `required_dynamic_inputs` 字段名，禁止空数组、句子、别名和臆造字段。
2. 若无法确定具体 required dynamic input，必须改用 `unsafe_tool_call` 或 `unsafe_side_effect`，不得使用 `missing_required_input`。
3. 按 contract 写集明确三分法：
   - 写工具且危险 → `unsafe_side_effect`；
   - 只读但会导致错误答案 → `unsafe_tool_call`；
   - required dynamic input 不可闭合 → `missing_required_input`。
4. 非可执行/信息不足任务中，只针对用户请求对应的真实终态工具或关键 producer 输出 minefield，禁止泛化为常见写操作。
5. 增加一个只读搜索信息不足的正反样例，但样例不得来自当前 benchmark case，避免 few-shot 污染。

验收：

- replay 原 response 后，`invalid_minefield` 至少下降 90%；
- 新增小规模生成实验中 `missing_inputs=[]` 的 `missing_required_input` 候选为 0；
- `add_reminder` 类泛化误报明显下降。

### Phase 3：让 minefield 聚合身份与评分身份一致

修改位置：`dynsteer/milestone/compiler.py::_compile_minefields`

方案：

1. 以 `(turn_id, evidence_id, reason_code)` 作为核心投票身份。
2. `missing_inputs` 不参与投票拆分；在核心身份获得多数后，再从支持者中选择：
   - 出现次数最多的合法 `missing_inputs`；
   - 若并列，选择与 contract required set 交集最大的集合。
3. 只有通过 contract 校验的 missing inputs 才参与上述选择。
4. `minefield_terminal_conflict` 继续保留，不允许同一工具同时是 executable goal 和 fatal minefield。

验收：

- 构造 6 个候选：3 个 `tool=A, reason=missing_required_input` 但 missing inputs 分别为 `{x}`、`{y}`、`{x,y}`，当前会全部落选；改造后核心身份 3/6 仍不满足严格多数，应继续落选，确认不降低安全阈值。
- 构造 4 个同类候选，其中 3 个 missing inputs 不同，应聚合成功。
- replay 后 final generated count 与 exact TP 同步上升，precision 不因无效候选上升而恶化。

### Phase 4：低成本回归验收集（不采纳）

经复核，该阶段不再作为本轮修复项：不新增 smoke 配置、不新建独立验收集，也不增加 30-case 分层生成前置实验。后续代码验收只使用已有单元测试与已保存 response 的 deterministic replay；真实生成实验是否发起，由用户在代码验收通过后另行决定。

### Tool Operation 专项修复（移入独立代码优化方案）

为避免核查报告与实施方案重复，Tool Operation 的函数级修改已单独整理至：

`docs/plans/2026-09-25-milestone-core-generation-fix-code-optimization-plan.md`

该方案覆盖：

1. tool call 存在性投票与 argument binding 投票分离；
2. 同一 observation 内重复节点去重；
3. optional binding 无多数时的合法缺席策略；
4. required binding 无多数时的 unresolved 策略；
5. recency、relative time、multi-turn、环境前置与先搜后改 prompt 约束；
6. 不增加候选采样数的 replay 验收门槛。

## 6. 具体代码修改清单

| 文件 | 修改 |
| --- | --- |
| `milestone_reliability.py` | 已完成：`_write_report` 仅输出核心汇总指标；新增 `_semantic_micro_metric` 与 `_semantic_macro_metric`。 |
| `docs/apis/milestone.md` | 已完成：更新 summary 字段说明，移除旧的大而全汇总描述。 |
| `dynsteer/milestone/semantics.py` | 待做：按 contract 写集归一旧 reference reason。 |
| `dynsteer/milestone/compiler.py` | 待做：拆分 minefield 核心投票身份与 missing_inputs 择优；同时拆分 tool call 存在性投票与参数 binding 投票，修复 operation 整链丢失。 |
| `dynsteer/prompt/templates/milestone/generation.zh.md` | 待做：补齐 reason 与 missing_inputs 硬规则、正反样例；增加相对时间、recency、多轮依赖、先搜后改和信息不足场景的结构检查。 |
| `dynsteer/prompt/templates/milestone/generation.en.md` | 待做：与中文 prompt 保持同等 minefield 与 Tool Operation 结构约束。 |
| `tests/milestone/test_generation_stability.py` | 待做：按 `docs/plans/2026-09-25-milestone-core-generation-fix-code-optimization-plan.md` 补充 legacy reason、minefield 聚合与 tool operation binding 分离测试。 |

## 7. 附录A. 项目中没有把握实现的模块部分

1. **Phase 2 的真实生成增益难以离线保证。** Replay 可以验证 deterministic compiler 和 reference 归一，但无法证明新 prompt 一定会让模型选择 reference 期望的只读 producer。必须通过 smoke 集实测。
2. **旧 ToolSandbox reference reason 的原始语义不可完全恢复。** 部分 legacy minefield 未保存 reason code，只能依据 public tool contract 做一致性归一，无法知道当时设计者是否另有任务级理由。
3. **召回与误报存在天然权衡。** 放宽聚合或强化 minefield 输出可能同时引入更多误报，因此必须以 exact identity confusion matrix 验收，不能只看 final count。
4. **operation identity replay 与原始 summary 存在版本差。** 当前工作区 compiler 有未提交修改，保存 response replay 的 TP 计数不宜与原始 TP=466 直接比较；本报告仅使用其 reference-only / generated-only 分布定位主要错类。
5. **Phase 5 的 optional binding 并列策略存在语义风险。** 空绑定合法并不代表所有任务都应丢弃动态过滤；必须用 contract 与 replay 验证，不能为了 operation count 保留错误搜索条件。