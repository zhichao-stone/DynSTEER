# 基于 Agent 轨迹的 Milestone 阶段式动态评估算法方案

## 1. 第一阶段目标

本方案只采用“通用轨迹数据模型 + Benchmark Adapter”的路线：先定义 DynaSTEER 自己的 Agent 轨迹、状态快照、milestone 有向无环图和阶段评估结果模型，再通过 adapter 将 ToolSandbox 等 benchmark 数据转换到该模型上。

第一阶段不处理 milestone 自动生成，也不处理在线执行中 review。当前阶段只解决四件事：

1. 如何表示 Agent 轨迹与状态快照。
2. 如何表示用于阶段划分的 milestone 有向无环图。
3. 如何将 ToolSandbox 等 benchmark 适配到统一模型。
4. 如何完成阶段切换、阶段式动态评估、动态评估粒度调度。

Milestone 初始可以为空。空 milestone 只表示“当前任务没有阶段标注”，不表示任务满分完成。后续不论 milestone 来自已有 benchmark、人工标注还是自动方法，都直接覆盖当前任务的 milestone 图；如需保留历史版本，应由单独的标注历史结构维护，不放进 MilestoneGraph。

## 2. ToolSandbox 可复用思想

ToolSandbox 中值得迁移的是评估抽象，不是它的场景代码结构：

1. milestone 是轨迹中必须达成的关键状态或关键行为证据。
2. milestone 之间可以用 DAG 表示先后依赖，而不是强制为一条线性路径。
3. minefield 表示不应发生的行为或状态，一旦匹配可触发强惩罚。
4. snapshot constraint 可用于判断某个轨迹位置是否满足 milestone。
5. 轨迹中的消息、工具调用和状态快照应通过统一索引串联，便于定位阶段边界。

需要避免直接继承 ToolSandbox 的部分：

1. 不把核心模型绑定到 `ExecutionContext`、Polars 数据库或具体手机工具域。
2. 不把无 milestone 的任务默认评为 1 分。
3. 不让 milestone 数据结构承担标注版本管理、覆盖历史管理等职责。

## 3. 总体架构

```text
Benchmark / Agent Raw Logs
        |
        v
Adapter
  ToolSandboxAdapter
  GenericJsonAdapter
  FutureBenchmarkAdapter
        |
        v
Canonical Model
  TaskCase
  Trajectory
  StateSnapshot
  MilestoneGraph
        |
        v
Stage Alignment
  candidate boundary generation
  milestone constraint scoring
  DAG-aware milestone matching
        |
        v
Stage-wise Dynamic Evaluation
  cheap evaluation
  standard LLM-as-a-Judge
  expensive multi-pass judge
  stage report
```

## 4. 通用输入数据模型

### 4.1 TaskCase

`TaskCase` 表示一个待评估任务。

```yaml
task_id: string
task_description: string
environment_schema: object
tool_schema: object
policy_constraints: list
initial_state: object | null
milestone_graph: MilestoneGraph | null
metadata: object
```

字段说明：

1. `environment_schema` 描述可观察状态空间，例如 ToolSandbox 的 setting、contact、messaging、reminder，或代码 Agent 的文件、测试、git diff。
2. `tool_schema` 描述工具名、参数、返回、异常和副作用。
3. `policy_constraints` 描述任务级约束，可转换为 minefield 或普通约束。
4. `milestone_graph` 可以为 `null` 或空图。

### 4.2 Trajectory

`Trajectory` 表示一次 Agent 执行轨迹。

```yaml
run_id: string
task_id: string
steps: list[TrajectoryStep]
snapshots: list[StateSnapshot]
final_state: object | null
metrics:
  tokens: int | null
  wall_time_ms: int | null
  tool_call_count: int | null
  error_count: int | null
raw: object
```

### 4.3 TrajectoryStep

`TrajectoryStep` 是轨迹中的最小可索引事件。

```yaml
step_id: string
index: int
timestamp: string | null
actor: system | user | agent | environment | evaluator
event_type: message | tool_call | tool_result | state_update | artifact_update | final | error
content: string | null
tool_call:
  name: string
  arguments: object
tool_result:
  success: bool
  content: object | string | null
  exception: string | null
state_delta_refs: list[string]
cost:
  tokens: int | null
  latency_ms: int | null
raw: object
```

说明：

1. ToolSandbox 中 user/agent/environment 消息转为 `message`、`tool_call` 或 `tool_result`。
2. 对代码 Agent，文件修改、测试运行、命令执行也可转成对应事件。
3. `raw` 保留原始字段，adapter 之外的核心算法不依赖 raw。

### 4.4 StateSnapshot

`StateSnapshot` 表示某个 step 后可恢复的观察状态。

```yaml
snapshot_id: string
after_step_id: string
after_step_index: int
namespaces:
  sandbox: object | null
  setting: object | null
  contact: object | null
  messaging: object | null
  reminder: object | null
  filesystem: object | null
  test_result: object | null
  artifact: object | null
raw: object
```

`namespaces` 是开放结构。ToolSandbox adapter 只填充 ToolSandbox 相关 namespace；其他 benchmark 可以填充自己的领域状态。

## 5. Milestone 图数据模型

### 5.1 MilestoneGraph

`MilestoneGraph` 只负责描述当前任务的阶段标注图。

```yaml
nodes: list[Milestone]
edges: list[[from_milestone_id, to_milestone_id]]
minefields: list[Minefield]
default_thresholds:
  pass: 0.8
  warn: 0.6
  fail: 0.4
metadata: object
```

不放入 `case_id`、`version`、`overrides` 的原因：

1. `case_id` 已由 `TaskCase.task_id` 承担，避免重复。
2. `version` 属于标注历史管理，不属于当前评估所需的图结构。
3. `overrides` 属于“如何得到这张图”的过程信息，不属于“这张图如何参与评估”的结构信息。

如果后续需要保留多版 milestone，可单独设计：

```yaml
MilestoneAnnotationRecord:
  task_id: string
  source: toolsandbox | manual | auto | merged
  created_at: string
  graph: MilestoneGraph
  parent_record_id: string | null
  note: string | null
```

第一阶段算法只读取最终生效的 `MilestoneGraph`。

### 5.2 edges

`edges` 是 milestone DAG 中的单向边列表，每条边只表示“前置 milestone 应先于后置 milestone 完成”。

第一阶段不单独封装 `MilestoneEdge`，也不引入 `relation` 字段。原因是阶段划分只需要偏序关系；所谓 enables 或 joins 都可以由普通单向边表达：

```text
# 线性流程
identify_intent -> execute_tool -> reply_user

# 并行前置条件后汇合
collect_contact_info -> send_message
confirm_message_content -> send_message
```

第二个例子中，`send_message` 有两个前驱，表示必须同时完成联系人识别和内容确认后，才能进入发送消息阶段。这个汇合关系不需要单独的 `joins` 类型，DAG 入边已经表达了语义。

### 5.3 Milestone

```yaml
milestone_id: string
name: string
description: string
required: bool
constraints: list[Constraint]
pass_threshold: float | null
metadata: object
```

字段说明：

1. `milestone_id` 在当前图内唯一。
2. `description` 描述该阶段必须完成的状态或行为证据。
3. `required = true` 表示该 milestone 未匹配时会影响阶段完成度。
4. `constraints` 是该 milestone 的判定条件。
5. `pass_threshold` 为空时使用图级默认阈值。

### 5.4 Milestone 与路径分支的关系

Milestone 应表示任务执行中的语义阶段节点，而不是具体实现路径。例如在发送消息任务中，合理的 milestone 是：

```text
resolve_recipient -> confirm_content -> send_message -> reply_user
```

其中 `resolve_recipient` 表示“收件人识别完成”。至于是通过联系人姓名查询、用户直接提供手机号、还是上下文中已有 `person_id`，都属于达成该 milestone 的证据来源或路径细节，不应拆成多个 milestone。

第一阶段不保留 milestone-level `alternatives` 字段。若一个 milestone 可由多种证据证明，应在 Constraint 层表达，例如：

```text
milestone: resolve_recipient
constraints:
  - target: semantic
    operator: custom
    expected: recipient_identity_resolved
  - target: tool_call
    selector: $.name
    operator: one_of
    expected:
      - search_contacts
      - search_messages
      - none_if_user_provided_phone
```

如果确实存在两条不交汇的高层任务路线，应在 DAG 上用不同的后续 milestone 子图表达；但只要二者最终都汇合到同一个语义阶段，就应把汇合点作为 milestone，而不是把每个路径分支都标成 milestone。这样可以避免把 benchmark 写成“标准解法轨迹”，保留 Agent 采用不同合理路径的空间。

### 5.5 Constraint

```yaml
constraint_id: string
target: step | tool_call | tool_result | state_snapshot | state_delta | artifact | metric | semantic
namespace: string | null
selector: string
operator: equals | contains | one_of | fuzzy_match | json_subsumes | added | updated | removed | unchanged_since | ast_match | custom
expected: object | string | number | bool | list | null
reference_milestone_id: string | null
weight: float
threshold: float
hard: bool
evaluator_hint: rule | metric | llm | custom
metadata: object
```

约束语义：

1. 同一个 milestone 内的 constraints 默认加权合取。
2. `hard = true` 的约束低于阈值时，该 milestone 不能通过。
3. `reference_milestone_id` 用于表达相对变化，例如相较某个前置 milestone 新增、更新、删除或保持不变。
4. `unchanged_since` 可表达 ToolSandbox 中 guardrail 的主要含义，即某个 namespace 或 selector 自某阶段以来不应被改变。

### 5.6 guardrail 的作用与第一阶段处理方式

ToolSandbox 中 guardrail 的作用是检查“不该变化的状态没有变化”。例如 Agent 只需要关闭蜂窝网络时，不应顺手改联系人、短信或提醒事项。

第一阶段不把 guardrail 作为 Milestone 的独立字段，而是用两种更简单的方式表达：

1. 如果某个状态在阶段内必须保持不变，用普通 `Constraint` 表达：

```yaml
target: state_delta
namespace: contact
selector: $
operator: unchanged_since
reference_milestone_id: previous_stage
hard: true
```

2. 如果某种变化一旦发生就应强惩罚，用 `Minefield` 表达。

这样可以保留 guardrail 的能力，但不增加 Milestone 模型的字段复杂度。

### 5.7 Minefield

```yaml
minefield_id: string
name: string
description: string
severity: fatal | major | minor
constraints: list[Constraint]
penalty:
  mode: zero_stage | zero_trajectory | subtract | flag_only
  value: float
metadata: object
```

Minefield 与 Milestone 使用同一种 Constraint 机制，但语义相反：匹配越高，风险越高。

## 6. Adapter 设计

### 6.1 Adapter 职责

Adapter 只负责数据转换，不负责评估决策：

1. 将原始任务转换为 `TaskCase`。
2. 将原始轨迹转换为 `Trajectory`。
3. 将原始 milestone/minefield 标注转换为 `MilestoneGraph`。
4. 保留无法规范化的原始字段到 `raw`。

### 6.2 ToolSandboxAdapter

ToolSandbox 输入：

1. `ScenarioExtension.messages`
2. `tool_allow_list` / `tool_deny_list`
3. `milestones` / `minefields`
4. `milestone_edge_list` / `minefield_edge_list`
5. `execution_context.json` 或 `conversation.json`

转换关系：

```text
ScenarioExtension.messages -> TaskCase.task_description / initial messages
tool_allow_list / tool_deny_list -> TaskCase.tool_schema / policy_constraints
ExecutionContext SANDBOX rows -> Trajectory.steps
ExecutionContext non-SANDBOX snapshots -> StateSnapshot.namespaces
Milestone(snapshot_constraints) -> Milestone.constraints
Minefield(snapshot_constraints) -> Minefield.constraints
milestone_edge_list -> MilestoneGraph.edges
```

### 6.3 GenericJsonAdapter

为了后续接其他 benchmark，第一阶段同时定义一个通用 JSON 输入格式：

```yaml
task: TaskCase
trajectory: Trajectory
milestone_graph: MilestoneGraph | null
```

只要其他 benchmark 能导出该结构，就可以直接进入评估算法。

## 7. 阶段切换算法

### 7.1 候选边界生成

候选边界是可能完成 milestone 的轨迹位置。

默认候选边界包括：

1. 每个 `tool_result` 之后。
2. 每个 `state_update` 或状态快照变化之后。
3. 每个 Agent 面向用户的阶段性答复之后。
4. 每个 error、exception、retry 之后。
5. 最终答复之后。

输出：

```text
B = [b0, b1, ..., bn]
```

每个边界包含：

```yaml
boundary_id: string
step_index: int
snapshot_id: string | null
reason: tool_result | state_update | user_reply | error | final
```

### 7.2 单个 milestone 的匹配评分

对 milestone `m` 和边界 `b`：

```text
constraint_score(c, b) in [0, 1]

hard_pass(m, b) =
  all(constraint_score(c, b) >= c.threshold for c in m.constraints if c.hard)

soft_score(m, b) =
  weighted_mean(constraint_score(c, b), weight=c.weight)

milestone_score(m, b) =
  0 if not hard_pass(m, b)
  else soft_score(m, b)
```

如果 `m.constraints` 为空，则该 milestone 不能自动匹配，得分为 0，并在报告中标记为 invalid milestone。

### 7.3 DAG-aware 匹配

输入：

```text
G = MilestoneGraph
B = candidate boundaries
S[m][b] = milestone_score(m, b)
```

目标是在满足 DAG 先后关系的前提下找到最佳映射：

```text
mapping: milestone_id -> boundary_id | null
```

第一阶段推荐用 beam search，兼顾可解释性和长轨迹效率：

```text
initialize beam with empty mapping
for each expansion step:
    ready = milestones whose predecessors are already assigned
    for state in beam:
        for m in ready:
            for b in boundaries after max(predecessor boundaries):
                if S[m][b] >= candidate_min_score:
                    create new state with m -> b
    keep top_k states by objective score
return best state
```

目标函数：

```text
objective =
  mean(matched required milestone scores)
  - missing_required_penalty
  - order_violation_penalty
  - excessive_delay_penalty
```

输出：

```yaml
milestone_mapping:
  milestone_id:
    boundary_id: string | null
    step_index: int | null
    score: float
    status: matched | missing | invalid | ambiguous
    evidence: list
```

### 7.4 阶段区间构造

对已匹配 milestone `m`：

```text
stage_start(m) = max(boundary(pred(m))) if pred(m) exists else first_user_step
stage_end(m) = boundary(m)
stage(m) = (stage_start(m), stage_end(m)]
```

若多个前驱汇合，阶段起点取前驱中最晚完成的边界。若 milestone 未匹配，则创建 missing stage，用于报告缺失阶段，但不产生正常区间。

## 8. 阶段式动态评估

### 8.1 阶段评分维度

每个阶段输出七个维度：

1. `progress`：是否达成本阶段 milestone。
2. `state_consistency`：状态快照或产物是否符合约束。
3. `tool_quality`：工具选择、参数、调用顺序、异常处理是否合理。
4. `efficiency`：步骤数、工具调用数、token、耗时和重试是否合理。
5. `safety`：是否触发 minefield 或违反 policy。
6. `interaction_quality`：是否保持高质量对话协作，包括意图确认、信息不足时澄清、轮次衔接和用户回应质量。
7. `recovery`：遇到异常后是否恢复，而不是重复无效操作。

阶段总分：

```text
stage_score =
  safety_gate * sum(weight[d] * score[d] for d in dimensions)

safety_gate =
  0 if fatal minefield matched
  1 otherwise
```

### 8.2 初始权重分配依据

维度权重不应固定为唯一默认值，而应由任务类型决定。第一阶段可采用规则映射：先根据任务描述、工具 schema、benchmark 类别或人工配置识别任务类型，再选择对应初始权重。

任务类型识别优先级：

1. 若 benchmark 或任务元数据显式给出类型，优先使用显式类型。
2. 若任务核心是通过工具读取或修改外部环境状态，归为 `stateful_tool_task`。
3. 若任务主要通过多轮自然语言交互推进，且评价重点是沟通、澄清、确认、解释或用户回应质量，归为 `dialogue_interaction_task`。
4. 若任务产物是代码、文件、报告或研究结论，归为 `artifact_task`。
5. 若任务包含安全、权限、隐私、拒答等约束，叠加 `safety_sensitive_task`。
6. 无法识别时使用 `general_task`。

初始权重表：

```yaml
general_task:
  progress: 0.25
  state_consistency: 0.20
  tool_quality: 0.20
  efficiency: 0.10
  safety: 0.15
  interaction_quality: 0.05
  recovery: 0.05

stateful_tool_task:
  progress: 0.25
  state_consistency: 0.25
  tool_quality: 0.22
  efficiency: 0.08
  safety: 0.12
  interaction_quality: 0.03
  recovery: 0.05

dialogue_interaction_task:
  progress: 0.20
  state_consistency: 0.15
  tool_quality: 0.15
  efficiency: 0.08
  safety: 0.17
  interaction_quality: 0.20
  recovery: 0.05

artifact_task:
  progress: 0.28
  state_consistency: 0.22
  tool_quality: 0.12
  efficiency: 0.08
  safety: 0.10
  interaction_quality: 0.05
  recovery: 0.15

safety_sensitive_task:
  progress: 0.20
  state_consistency: 0.18
  tool_quality: 0.15
  efficiency: 0.05
  safety: 0.30
  interaction_quality: 0.07
  recovery: 0.05
```

`stateful_tool_task` 的典型例子是 ToolSandbox 中“关闭蜂窝网络”“修改联系人手机号”“创建提醒事项”等任务。此类任务的关键不只是调用了工具，而是外部环境状态是否被正确改变，且不应产生无关状态副作用，所以 `state_consistency` 和 `tool_quality` 权重较高。

混合任务可做加权组合。例如一个 ToolSandbox 信息不足场景同时属于 `stateful_tool_task` 和 `dialogue_interaction_task`：

```text
initial_weight =
  normalize(
    0.6 * weight[stateful_tool_task]
  + 0.4 * weight[dialogue_interaction_task]
  )
```

权重设计依据：

1. `progress` 是所有任务的基础项，表示阶段目标是否达成。
2. `state_consistency` 在有环境状态或产物状态的任务中更重要。
3. `tool_quality` 在工具调用 benchmark 中更重要。
4. `interaction_quality` 在对话式任务、多轮协作、用户确认、解释说明和信息不足澄清任务中更重要。
5. `safety` 在存在 minefield、权限、隐私、拒答要求时显著提高。
6. `recovery` 在长程代码、研究、文件编辑任务中更重要，因为错误恢复能力会明显影响最终质量。

## 9. uncertainty 计算

`uncertainty_i` 表示第 `i` 个阶段评估结果的不确定性，取值 `[0, 1]`。它不只来自 LLM，也来自匹配歧义、字段缺失和阈值边界。

### 9.1 组成项

1. `u_margin`：milestone 匹配歧义。

```text
u_margin = 1 - clamp(top1_score - top2_score, 0, 1)
```

若最佳边界和次佳边界分数接近，说明阶段边界不稳定，不确定性高。

2. `u_missing`：约束所需字段缺失比例。

```text
u_missing = missing_constraint_inputs / total_constraint_inputs
```

3. `u_threshold`：阶段分数靠近关键阈值的程度。

```text
nearest = min(abs(stage_score - pass_threshold), abs(stage_score - warn_threshold), abs(stage_score - fail_threshold))
u_threshold = max(0, 1 - nearest / threshold_margin)
```

推荐 `threshold_margin = 0.1`。如果分数离阈值超过 0.1，该项为 0。

4. `u_conflict`：不同证据之间是否冲突。

```text
u_conflict = 1 if progress high but state_consistency low
           = 1 if milestone matched but minefield also near matched
           = 0 otherwise
```

5. `u_judge`：LLM judge 自身不确定性，仅在 standard 或 expensive 评估后存在。

```text
u_judge = 1 - judge_confidence
```

如果多轮 judge，则使用：

```text
u_judge = normalized_variance(judge_scores)
```

### 9.2 合成公式

```text
uncertainty =
  clamp(
    0.30 * u_margin
  + 0.25 * u_missing
  + 0.20 * u_threshold
  + 0.15 * u_conflict
  + 0.10 * u_judge,
    0,
    1
  )
```

若当前阶段没有运行 LLM judge，则 `u_judge = 0`。

## 10. 动态权重更新

阶段结束后，根据低分维度和不确定性调整下一阶段权重：

```text
deficit[d] = max(0, target[d] - score[d])

weight_next[d] =
  normalize(
    weight_current[d] * exp(alpha * deficit[d] + beta * uncertainty * focus[d])
  )
```

推荐初始参数：

```yaml
alpha: 0.8
beta: 0.4
target:
  progress: 0.8
  state_consistency: 0.8
  tool_quality: 0.8
  efficiency: 0.7
  safety: 0.9
  interaction_quality: 0.7
  recovery: 0.7
focus:
  progress: 1.0
  state_consistency: 1.0
  tool_quality: 1.0
  efficiency: 0.5
  safety: 1.0
  interaction_quality: 0.5
  recovery: 0.7
```

解释：

1. 低于目标越多，下一阶段越关注该维度。
2. 不确定性越高，且该维度越关键，权重提升越明显。
3. `safety` 的目标值更高，避免低估 minefield 风险。
4. 所有权重更新后归一化，保证总和为 1。

## 11. 动态粒度调度

第一阶段使用三级评估粒度。standard 和 expensive 都可以使用 LLM-as-a-Judge，但评估框架不同。

### 11.1 cheap

cheap 是默认必跑评估层，尽量不用 LLM。

包括：

1. milestone constraint 结构化匹配。
2. minefield 结构化匹配。
3. 工具名、参数、异常、状态 diff 检查。
4. 步数、工具调用数、重试次数、耗时等统计指标。

### 11.2 standard

standard 是单轮 LLM-as-a-Judge 阶段评估。

输入：

1. 任务描述。
2. 当前阶段轨迹片段。
3. 当前 milestone 描述与约束摘要。
4. cheap 评估结果。
5. 一个紧凑 rubric。

输出：

1. 七个维度分数。
2. `judge_confidence`。
3. 简短证据。
4. 是否需要升级 expensive。

### 11.3 expensive

expensive 是多轮或拆维度 LLM-as-a-Judge。

可包含：

1. 按维度分别评估 progress、tool_quality、state_consistency、safety。
2. step-level 标注：`+1 / 0 / -1`。
3. 多次采样 judge，计算分歧。
4. 最后一轮 adjudicator 汇总。
5. 对首个错误阶段或关键失败原因做定位。

### 11.4 调度规则

先运行 cheap，再根据 cheap 结果决定是否升级。

推荐阈值：

```yaml
pass_threshold: 0.8
warn_threshold: 0.6
fail_threshold: 0.4
low_uncertainty: 0.2
high_uncertainty: 0.45
safe_minefield_threshold: 0.2
risky_minefield_threshold: 0.5
```

调度伪代码：

```text
cheap_result = run_cheap(stage)

if cheap_result.fatal_minefield_score >= 0.95
   and cheap_result.minefield_evidence_is_structural:
    return cheap_result with decision = fail

if cheap_result.stage_score >= pass_threshold
   and cheap_result.uncertainty <= low_uncertainty
   and cheap_result.minefield_score <= safe_minefield_threshold
   and cheap_result.hard_constraints_all_pass
   and cheap_result.required_fields_missing_ratio == 0:
    return cheap_result

standard_result = run_standard_llm_judge(stage, cheap_result)

if standard_result.stage_score >= pass_threshold
   and standard_result.uncertainty <= low_uncertainty
   and standard_result.judge_confidence >= 0.75
   and standard_result.minefield_score <= safe_minefield_threshold:
    return standard_result

if standard_result.stage_score < fail_threshold
   or standard_result.uncertainty >= high_uncertainty
   or standard_result.judge_confidence < 0.6
   or standard_result.minefield_score >= risky_minefield_threshold
   or standard_result.first_error_location_required
   or previous_stage.status in {warn, fail}:
    return run_expensive_llm_judge(stage, standard_result)

return standard_result
```

上面的规则可以简化为：

1. **cheap 通过条件**：结构化证据充足、硬约束全过、minefield 低、分数高于通过线、不确定性低。
2. **standard 使用条件**：cheap 不能直接确认通过，或阶段含有语义判断、自然语言回复质量、工具调用合理性等需要 judge 的内容。
3. **expensive 使用条件**：stage 可能失败、minefield 风险较高、judge 低信心、证据冲突、连续低质量阶段、或者需要定位首个错误。

## 12. 总体评估流程伪代码

```text
evaluate(task_case, trajectory):
    graph = task_case.milestone_graph

    if graph is null or graph.nodes is empty:
        return report_without_stage_alignment(
            milestone_coverage="none",
            trajectory_level_metrics=cheap_trajectory_metrics(trajectory)
        )

    boundaries = generate_candidate_boundaries(trajectory)
    score_matrix = score_all_milestones(graph.nodes, boundaries, trajectory)
    mapping = match_milestones_with_dag(graph, boundaries, score_matrix)
    stages = build_stage_intervals(graph, mapping, trajectory)

    weights = select_initial_weights(task_case)
    stage_reports = []

    for stage in topological_stage_order(stages):
        result = evaluate_stage_with_scheduler(stage, weights)
        stage_reports.append(result)

        if result.status == "fail" and result.fatal:
            break

        uncertainty = compute_uncertainty(result)
        weights = update_weights(weights, result.dimension_scores, uncertainty)

    return build_final_report(mapping, stage_reports)
```

## 13. 输出报告格式

```yaml
run_id: string
task_id: string
milestone_coverage: none | partial | full
overall_score: float
stage_reports:
  - stage_id: string
    milestone_id: string
    interval:
      start_step_index: int
      end_step_index: int
    evaluator_level: cheap | standard | expensive
    status: pass | warn | fail | missing | ambiguous
    stage_score: float
    uncertainty: float
    dimension_scores:
      progress: float
      state_consistency: float
      tool_quality: float
      efficiency: float
      safety: float
      interaction_quality: float
      recovery: float
    evidence: list
    diagnosis: list
    next_weights: object
minefield_matches: list
first_failure_stage_id: string | null
```

## 14. 第一阶段实验建议

### 14.1 实验输入

优先使用 ToolSandbox：

1. 读取 ToolSandbox 人工 milestone 和 minefield。
2. 读取 ToolSandbox execution context 或 conversation。
3. 通过 ToolSandboxAdapter 转换为 DynaSTEER 模型。
4. 运行阶段匹配和阶段式动态评估。

### 14.2 验证目标

1. Adapter 能正确还原 ToolSandbox 轨迹、状态快照、milestone DAG。
2. DynaSTEER milestone 匹配结果与 ToolSandbox 原始匹配结果一致或高度相关。
3. 阶段式评估能比终态评估更清楚定位失败阶段。
4. 动态粒度调度能减少不必要的 expensive judge 调用。
5. 动态权重能把后续评估关注点转移到前一阶段暴露出的薄弱维度。

### 14.3 指标

1. `milestone_match_agreement`：DynaSTEER 与 ToolSandbox milestone 映射一致率。
2. `score_correlation`：DynaSTEER 总分与 ToolSandbox similarity 的相关性。
3. `first_failure_localization`：首个失败阶段定位是否符合人工复核。
4. `judge_cost`：standard / expensive judge 调用次数、token、耗时。
5. `diagnostic_precision`：阶段诊断中被人工确认有效的问题占比。

## 15. 第一阶段边界

第一阶段暂不做：

1. milestone 自动生成算法。
2. 在线执行中 review 或 harness controller。
3. 多版本 milestone 历史管理。
4. 跨 benchmark 的复杂领域特化 evaluator。

第一阶段应完成：

1. 通用数据模型。
2. ToolSandbox adapter。
3. Milestone DAG 匹配。
4. 阶段切分。
5. cheap / standard / expensive 动态调度。
6. 阶段报告与实验指标。

## 16. 小结

修订后的第一阶段方案将路线约束为通用模型 + benchmark adapter。MilestoneGraph 只描述当前任务的阶段图，Milestone 只描述单个必须达成的阶段节点，复杂的标注来源、覆盖历史和多版本管理都移出核心模型。

在算法上，第一阶段重点是把 ToolSandbox 的 milestone 标注转成统一图结构，完成 DAG-aware 阶段匹配，并在每个阶段通过 cheap、standard、expensive 三档评估粒度做动态评估。这样能先验证阶段式评估本身是否有效，再进入后续 milestone 自动生成和在线 review。
