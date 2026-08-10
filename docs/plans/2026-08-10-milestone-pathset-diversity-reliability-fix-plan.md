# Milestone 路径集合生成、多样性强化与可靠性修复方案

## 1. 方案目标与结论

本方案针对 `toolsandbox_milestone_reliability_partial_main` 最新核查暴露的五类问题进行代码修复：

1. LLM 返回 Markdown 围栏 JSON，而 compiler 只接受裸 JSON，导致 150/150 路径在最外层解析失败；
2. 当前每条路径独立调用一次 LLM，六次重复发送大段 TASK/tool schema，且模型无法在生成时比较其他路径；
3. compiler 把“至少 4 条不同路径”作为硬门槛，简单线性任务的稳定一致反而被拒绝；
4. 信息不足、多轮任务、用户模拟器规则和实际 Agent 任务没有被清楚区分，造成人工空图生成非空图、多轮步骤混乱；
5. `agent_message_instruction`、invariant、工具调用和最终回复没有明确证据角色，模型会把指令或安全规则当成正向 milestone。

对用户提出的想法，建议采用以下完善版本：

- **采用“一次生成整组路径 + 第二轮定向修订”**，从六次独立路径调用改为每个 case 两次 LLM 调用；
- LLM 响应不再包含顶层共享 atom catalog；每条路径必须内联自己的 `operations`，避免模型先预设公共 atom、再围绕它们拼装路径；
- 第一轮一次返回全部候选路径；代码确定性计算重复组、操作重叠度和 operation signature 支持度；
- 第二轮接收第一轮输出和代码计算的差异报告，返回完整修订版路径集合；
- 多样性目标改为：**除真正不可避免的核心操作外，路径不共享可选操作**。不能要求所有路径绝对不共享操作，否则会诱导模型遗漏必经动作，将正常任务错误聚合为空图；
- 空图不再依赖“路径足够多样后碰巧没有交集”，而由显式任务处置 `disposition` 决定；
- 路径多样性只用于第二轮修订和诊断，不再作为生成成功的一票否决门槛；
- 共识支持按全部 schema 合法路径计票，重复路径仍会增加支持度，但第二轮必须先尝试消除非必要重复；
- 对 executable 任务即使没有任何共识 atom，也允许产出合法空图，表示“存在多条可行路径，但没有公开证据层面的共同必经 milestone”。该空图与信息不足空图通过 graph metadata 中的 disposition 区分。

本方案替代现有文档中关于“每条路径独立调用”和“有效去重路径必须达到 `max(N-2, 3)`”的旧设计。既有方案文档保留为历史记录，不删除。

## 2. 用户想法的合理性分析

### 2.1 合理部分

#### 一次生成所有路径

该方向合理，原因包括：

1. 当前六次调用重复传入相同 TASK、EVIDENCE、INVARIANTS 和完整工具 schema，输入 token 浪费明显；
2. 独立调用只能依靠固定 strategy 名称制造差异，模型不知道其他调用已经生成了什么；
3. 一次生成路径集合后，模型可以直接检查 evidence 序列是否重复，而不是只修改 operation 的 name/purpose；
4. 路径按顺序内联 operation 后，后生成的路径能直接看到先前路径使用的 evidence，并主动探索真实替代路线；公共 milestone 由 compiler 在全部路径完成后推导，而不是由 LLM 预先声明。

#### 第一轮生成后计算差异度，再进行第二轮生成

该方向也合理，但差异度必须由代码计算，不能让 LLM 自己打分。代码计算具有稳定、可复现、可测试的优点，并且可以明确告诉第二轮：

- 哪些路径 evidence 序列完全相同；
- 哪些路径虽然顺序不同，但共享了几乎全部操作；
- 哪些 operation signature 已在多数路径中出现，属于“候选核心操作”；
- 哪些高支持 operation signature 需要第二轮重新判断是否真的不可避免。

第二轮不是重新盲生成，而是“带确定性反馈的修订”。

### 2.2 需要修正的部分

#### 不能把“尽可能不共享操作”理解为绝对目标

例如 `add_contact` 的所有正确路径都必须包含添加联系人，`turn_on_location` 的所有正确路径都必须包含打开 location。若 prompt 要求所有路径完全不共享操作，模型只能：

- 故意删掉真正必要的操作；
- 编造不存在的替代工具；
- 用 instruction、policy check、tool availability check 等元步骤伪造差异；
- 让正常任务没有共识节点，错误产生空图。

因此第二轮 prompt 的目标必须表述为：

> 每条路径都必须独立完成任务；真正不可避免的操作允许共享。只应减少可选准备、冗余检查、非必要验证和同义重复操作的共享，不得为了差异度遗漏必要操作或编造工具。

#### 多样性不能代替可执行性分类

四个人工空图 case 的本质是信息不足或当前无动作，不是“存在很多互不相交的执行路径”。如果仍要求模型先生成路径，再依赖低交集产生空图，模型会继续从可见工具列表中拼出伪路径。

必须在路径集合顶层增加 `disposition`：

- `executable`：当前公开信息足以完成任务；
- `needs_clarification`：缺少必要信息，不能安全执行；
- `no_action`：无需或不应执行动作；
- `response_only`：不能执行工具动作，但需要向用户给出说明或拒绝。

`needs_clarification` 和 `no_action` 直接生成合法空图；`response_only` 由 compiler 生成一个“Agent 已向用户响应”的通用节点；只有 `executable` 才进入路径共识。

#### 两轮生成不保证一定节省总 token

两轮方案把 LLM 调用数从 6 降为 2，输入 TASK/tool schema 的重复次数显著减少；但两轮都需要输出完整路径集合，completion token 会增加。总 token 是否下降取决于输入 schema 与输出长度的比例，必须通过 provider usage 实测，不能只按调用次数推断。

目标调用量模型如下：

```text
当前：6 × 完整任务输入 + 6 × 单路径输出
新方案：2 × 完整任务输入 + 第一轮路径集合 + 第一轮反馈上下文 + 第二轮路径集合
```

ToolSandbox 的工具 schema 较大，预计输入节省会超过 completion 增量。若实测总 token 没有下降，后续只将第二轮改成“返回需要替换的路径”，不改变本方案的 disposition、差异度和共识语义。

本方案明确不以“共享 atom catalog”压缩 completion。路径内联会增加一部分重复输出，但这是避免 atom-first 锚定、保证先探索路径再聚合 milestone 的必要成本。operation schema 将保持精简，且两次调用减少的大段输入重复仍是主要 token 优化来源。

## 3. 目标流程

```text
GeneratorTaskView
  → 清理并按 recipient 投影公开多轮对话
  → 构造带 role 的 EVIDENCE 与 INVARIANTS
  → Round 1：一次生成 disposition + N 条内联 operations 的路径
  → 严格解析并逐路径校验
  → 代码计算重复组、操作重叠度、operation signature 支持度
  → Round 2：携带第一轮结果与差异报告，生成完整修订版
  → 选用第二轮；第二轮无效时显式回退第一轮
  → 非 executable：直接构造空图或 response-only 图
  → executable：按全部合法路径做 2/3 共识，不按 distinct 去重计票
  → DAG 校验与传递约简
  → 输出 graph、round/path/diversity 审计报告与 token usage
```

## 4. 新的 LLM 输出协议

### 4.1 第一轮和第二轮共用响应 schema

两轮都返回完整路径集合。顶层不允许出现共享 `atoms`，字段必须且只能是：

```json
{
  "disposition": "executable",
  "disposition_reason": "The public information is sufficient.",
  "paths": [
    {
      "path_index": 0,
      "strategy": "direct-shortest",
      "operations": [
        {
          "name": "Search contacts",
          "purpose": "Find the target contact with the available search tool.",
          "evidence_id": "tool_call_search_contacts_...",
          "expected_literal_index": 0
        }
      ]
    }
  ],
  "minefield_invariant_ids": []
}
```

采用路径内联 operation 的原因：

- LLM 必须先逐条构造完整路径，不能先建立公共 atom 集合并锚定后续路径；
- 每条路径独立选择 evidence，真正的共同操作只能在全部路径生成完成后由 compiler 统计得出；
- compiler 按 `(evidence_id, expected, occurrence_index)` 对齐，不使用 name、purpose 或 strategy 判定差异；
- 模型不能通过只修改 name/purpose 伪造多样性；
- 同一工具在不同路径中重复出现时允许重复输出，避免为了复用 catalog 而人为提高共享度。

字段中的 `name` 和 `purpose` 只用于审计及选取代表性描述，不参与 signature、路径判重、共识支持或 milestone ID 计算。

### 4.2 disposition 对 schema 的约束

| disposition | `paths` | compiler 结果 |
|---|---|---|
| `executable` | 目标为 N 条，每条内联非空 operations；至少 2/3 N 条校验有效 | 对路径做共识；允许共识节点为 0 |
| `needs_clarification` | 空 | 合法空图 |
| `no_action` | 空 | 合法空图 |
| `response_only` | 空 | compiler 构造通用 Agent→User 响应节点 |

`disposition_reason` 只用于审计和第二轮修订，不直接写入 milestone name、description 或 expected，避免不稳定自然语言污染图标签。

### 4.3 路径规则

1. `path_index` 必须覆盖 `0..N-1`，不能重复；缺失或非法的单条路径在 path summary 中拒绝；
2. `strategy` 必须与该 index 对应的 `PATH_STRATEGIES` 一致；
3. `operations` 必须是非空数组，每个 operation 在当前路径内部完整声明；
4. operation 字段必须且只能是 `name`、`purpose`、`evidence_id`、`expected_literal_index`；
5. 同一路径允许多次使用同一 evidence；compiler 按出现次序写入 occurrence index，以支持“先改为 enemy、再改回 friend”等重复工具操作；
6. 每个 operation 只能引用 `role=milestone` 的 evidence；context 和 minefield evidence 禁止作为正向节点；
7. 路径最后一个 operation 视为该路径 terminal。同一 evidence 可在一条路径末尾、另一条路径中间出现；
8. 顶层 minefield 只能引用 `INVARIANTS` allowlist，不再由每条路径重复选择；
9. 顶层出现 `atoms`、路径出现 `atom_ids` 或任何跨路径共享引用时，整轮按 schema 错误拒绝，防止协议回退到 atom-first 模式。

## 5. 差异度计算与第二轮反馈

### 5.1 路径 signature

沿用当前可审计语义：

```text
(evidence_id, canonical(expected), occurrence_index)
```

name、purpose 和 strategy 不参与路径语义判重。LLM 输出中不存在 atom_id；内部 atom 和 milestone ID 均由 compiler 在路径生成完成后确定性派生。

### 5.2 确定性差异指标

在 `dynsteer/milestone/compiler.py` 内新增 `_path_set_diversity()`，只处理已经严格校验的 `_AlignedPath`：

1. `valid_path_count`：有效路径数量；
2. `distinct_path_count`：不同 signature 序列数量，仅作诊断；
3. `exact_duplicate_groups`：完全相同 signature 序列对应的 path index 组；
4. `shared_operation_ratio(a, b)`：

```text
多重集合交集中的 signature 数量 / min(len(a), len(b))
```

occurrence index 已包含在 signature 中，因此重复执行操作可正确计数。相同操作仅换顺序仍会被判定为高重叠，不会获得虚假的高多样性。

5. `pairwise_distance = 1 - shared_operation_ratio`；
6. `mean_pairwise_distance` 与 `minimum_pairwise_distance`；
7. `high_overlap_pairs`：重叠度不低于 0.8 的路径对；
8. `operation_support`：每个 signature 的 path 支持数、支持比例和 path indexes。

差异度没有硬性最低分。路径只有一种自然解法时，低差异是允许的；它只是第二轮需要解释和复核的信号。

### 5.3 第二轮 prompt 接收的反馈

第二轮消息使用同一对话上下文：

```python
messages = [
    LLMMessage(role="user", content=generation_prompt),
    LLMMessage(role="assistant", content=round_one_raw),
    LLMMessage(role="user", content=refinement_prompt),
]
```

`refinement_prompt` 只传递代码计算的紧凑反馈：

- 第一轮 disposition；
- 有效路径数和缺失 index；
- exact duplicate groups；
- high-overlap pairs；
- operation support 表；
- mean/min pairwise distance；
- 单路径 schema 错误摘要。

第二轮必须执行以下修订规则：

1. 先复核 disposition；信息不足时不得为了满足 PATH_COUNT 构造路径；
2. 对每个高支持 operation signature 判断其是否真的是所有可行路线不可避免的操作；
3. 如果不是必经操作，至少构造一条合法完整路径不包含它；
4. 不得为了降低重叠度删掉必要操作、编造工具、使用 context/invariant evidence 充当业务步骤；
5. 修复 exact duplicate 和高重叠路径时，优先使用真实替代工具、可交换前置、不同的公开结果验证方式；没有真实替代时允许路径保持相同；
6. 返回完整最终 schema，不返回 patch、解释或 Markdown 围栏。

## 6. 具体代码修改

### 6.1 `dynsteer/milestone/model.py`

#### `PublicEvidence`

增加两个字段：

```python
role: Literal["milestone", "context", "minefield"] = "milestone"
matching_route: tuple[Actor, Actor] | None = None
```

作用：

- `role` 限制 evidence 用途；
- `matching_route` 让生成节点带上公开可知的交互方向，减少 route 缺失；
- 需要从 `dynsteer.model` 显式 import `Actor`，不使用延迟 import。

#### `MilestoneGenerationConfig`

保留现有三个字段，不新增 round/diversity 配置：

- `use_origin_milestone`；
- `simulated_path_count`；
- `generator`。

两轮生成是新的固定生成协议，避免再增加只为切换旧流程服务的兼容开关。`simulated_path_count >= 5` 保留，但错误说明改为“executable 任务至少需要 `ceil(N * 2/3)` 条 schema 合法路径”，不再提有效去重路径。

#### `GenerationReport`

保留现有核心字段并调整口径，新增必要审计字段：

```python
@dataclass(frozen=True)
class GenerationReport:
    generation_status: Literal["generated", "auto_rejected"]
    disposition: Literal[
        "executable", "needs_clarification", "no_action", "response_only"
    ] | None
    requested_path_count: int
    minimum_valid_path_count: int
    valid_path_count: int
    distinct_path_count: int
    candidate_atom_count: int
    aligned_atom_count: int
    consensus_node_count: int
    mean_pairwise_path_distance: float | None
    graph_valid: bool
    reasons: tuple[str, ...] = ()
    round_summaries: tuple[JsonObject, ...] = ()
    path_summaries: tuple[JsonObject, ...] = ()
```

字段口径：

- `minimum_valid_path_count = ceil(N * 2/3)`，只约束 executable；
- `valid_path_count` 使用最终选中轮的 schema 合法路径数；
- `distinct_path_count` 只用于诊断，不再决定是否拒绝；
- `round_summaries` 保存两轮解析、disposition、差异度和 response record；
- 空图也可以 `generation_status=generated`、`graph_valid=true`。

### 6.2 `dynsteer/adapter/contract.py`

#### `base_public_evidence()`

调整为三类基础 evidence：

1. `agent_message_instruction`
   - `role="context"`；
   - `matching_route=(Actor.USER, Actor.AGENT)`；
   - 仍可进入 TASK/EVIDENCE 供模型理解，但 compiler 禁止 atom 引用。
2. `agent_response_present`
   - `role="milestone"`；
   - `target=ConstraintTarget.STEP`；
   - `selector="$"`；
   - `operator=Operator.ADDED`；
   - `expected_policy="none"`；
   - `matching_route=(Actor.AGENT, Actor.USER)`；
   - 用于 `response_only` 的通用响应节点及常规任务最终反馈路径。
3. `tool_result_present`
   - `role="milestone"`；
   - `matching_route=(Actor.ENVIRONMENT, Actor.AGENT)`。

#### `normalize_tool_contract()`

所有工具调用 evidence 增加：

```python
role="milestone"
matching_route=(Actor.AGENT, Actor.ENVIRONMENT)
```

本轮不把自然语言参数值自动解析为 tool argument expected。原因是公开指令中的值可能需要时间转换、实体解析或工具返回才能确定，直接做字符串切片会生成不可靠约束。工具参数/结果细粒度 evidence 放在本方案后续 P1 验收后再扩展。

#### `build_public_invariants()`

生成的 invariant evidence 增加 `role="minefield"`。compiler 只允许通过顶层 `minefield_invariant_ids` 使用，禁止进入路径内联 operations。

### 6.3 `dynsteer/adapter/toolsandbox/utils/contract.py`

这是修复多轮和 User A/User B 混淆的关键位置。

当前代码只检查 actor 和 `visible_to`，没有检查 recipient，因此会：

- 把 `SYSTEM → USER` 的用户模拟器 role-play 提示词作为 Agent 任务上下文；
- 把 `SYSTEM → ENVIRONMENT` 的工具 import 文本作为任务上下文；
- 因 `visible_to=[USER]` 而漏掉实际发送给 Agent 的部分历史用户消息。

修改公开消息循环为以 recipient 为主：

```python
for step in steps:
    if step.get("recipient") != Actor.AGENT.value:
        continue
    if step.get("actor") not in {Actor.SYSTEM.value, Actor.USER.value}:
        continue
    content = step.get("content")
    if not isinstance(content, str) or not content.strip():
        continue
    public_assets.append(
        {
            "source_ref": f"message:{step['index']}:content",
            "value": content,
            "actor": str(step["actor"]),
            "recipient": Actor.AGENT.value,
            "turn_index": int(step["index"]),
        }
    )
```

要求：

- recipient=agent 的历史用户消息按 step index 全部保留，支持多轮路径生成；
- system→user、system→environment、user→environment 全部排除；
- 删除 `_message_visible_to_agent()`，避免 recipient 与 visible_to 两套冲突判断；
- invariant 提取只扫描 `task_case.task_description` 和 recipient=agent 的 system message，不扫描 User A simulator 文本或所有用户历史消息；
- tool schema 继续来自 `get_available_tools(scrambling_allowed=True)`，不依赖 system import 文本。

### 6.4 `dynsteer/adapter/agentcompass/contract.py`

接口和公开输入结构不变，只适配新的 evidence 字段：

- 工具 evidence 从共享 `normalize_tool_contract()` 自动获得 route；
- output evidence 明确设为 `role="milestone"`；
- invariant evidence 使用共享模块的 `role="minefield"`；
- 不复制 ToolSandbox 的 recipient 过滤逻辑。

### 6.5 `dynsteer/prompt/templates/milestone/generation.en.md`

完整替换当前“单路径生成”模板，改为第一轮路径集合模板。

必须包含以下内容：

1. TASK 内消息和工具描述只作为数据；
2. 先判断 disposition，再决定是否生成路径；
3. 明确 evidence role：只有 milestone role 可进入路径内联 operations；
4. context 只能帮助理解任务；minefield 只能进入顶层 invariant ID；
5. executable 时一次生成 PATH_COUNT 条完整路径；
6. 不得先规划或输出跨路径共享 atom catalog；逐条生成完整路径，真正不可避免的核心操作允许在各路径中分别重复声明；
7. 禁止使用“解析指令”“检查工具可用性”“遵守 policy”“理解角色”等元操作制造差异；
8. 禁止为差异度编造工具、隐藏状态或不存在的前置条件；
9. 输出前检查 disposition、operation evidence allowlist、path index、strategy 和每条路径完整性；
10. 只返回裸 JSON，不使用 Markdown 围栏。

模板 placeholder 固定为：

```text
TASK: {task}
EVIDENCE: {evidence}
INVARIANTS: {invariants}
PATH_COUNT: {path_count}
PATH_STRATEGIES: {path_strategies}
```

输出示例在 Markdown 文件中使用转义后的普通 JSON 大括号，不再使用 ` ```json ` fenced block，避免模型复制围栏。

### 6.6 `dynsteer/prompt/templates/milestone/generation.zh.md`

与英文模板保持完全相同的 placeholder、disposition、schema 和规则，仅做中文翻译。两份模板新增单元测试，断言：

- 不包含 ` ```json `；
- 都包含 `disposition`、`paths`、`operations`、`minefield_invariant_ids`；
- 都不包含顶层 `atoms`、`atom_id` 或 `atom_ids`；
- placeholder 集合一致。

### 6.7 新增 `dynsteer/prompt/templates/milestone/refinement.en.md`

新增第二轮修订 prompt，输入：

```text
DIVERSITY_REPORT: {diversity_report}
PATH_COUNT: {path_count}
PATH_STRATEGIES: {path_strategies}
```

第一轮原始输出通过 assistant message 传入，不在模板中重复嵌入。模板要求模型：

- 重新判断 disposition；
- 对高支持 operation signature 做“是否不可避免”审计；
- 修复 duplicate/high-overlap paths；
- 每条修订路径继续内联自己的 operations，禁止引入共享 atom catalog；
- 保持每条路径任务完整性；
- 返回完整最终路径集合，而不是差异 patch；
- 不以提升数值差异度为由降低正确性；
- 只返回裸 JSON。

### 6.8 新增 `dynsteer/prompt/templates/milestone/refinement.zh.md`

与英文 refinement 模板结构一致，仅翻译语言。

### 6.9 `dynsteer/milestone/compiler.py`

#### 内部数据结构

保留 `_PathAtom`、`_PathCandidate`、`_AlignedPath`、`_ConsensusCluster`。这些类型全部是 compiler 在解析每条路径后构造的内部对象，不对应 LLM 顶层 catalog，并做以下调整：

- `_PathAtom` 由单条 path 的 operation 转换而来；terminal 按 operation 是否处于该路径末尾由 compiler 设置；
- 新增 `_PathSetCandidate`，只保存 disposition、reason、有效 paths、path summaries 和顶层 invariant IDs，不保存共享 atoms；
- 新增 `_GenerationRound`，保存 round index、stage、candidate、error、response record 和 diversity report；
- 删除 `_RecordedPathResponseError` 的单路径语义，改为 round 级 `_RecordedRoundResponseError`。

#### `compile_task_case()` 主流程

替换当前 130—163 行的 N 次 `_simulate_path()` 循环：

```python
requested = config.simulated_path_count
minimum = ceil(requested * CONSENSUS_RATIO)
payload = _generator_payload(view, source_values)

draft = _generate_round(
    payload=payload,
    path_count=requested,
    llm=llm,
    stage="generation",
    messages=[LLMMessage(role="user", content=generation_prompt)],
    ...,
)
feedback = _round_feedback(draft, requested)
refined = _generate_round(
    payload=payload,
    path_count=requested,
    llm=llm,
    stage="refinement",
    messages=[
        LLMMessage(role="user", content=generation_prompt),
        LLMMessage(role="assistant", content=draft.raw),
        LLMMessage(role="user", content=refinement_prompt),
    ],
    ...,
)
selected = refined.candidate or draft.candidate
```

两次调用都必须显式传入：

```python
llm.chat(messages, response_format="json_object")
```

`dynsteer/llm/openai.py` 已能将字符串转换为 `{"type": "json_object"}`，本轮不修改 LLM provider 实现。

选择规则：

1. 第二轮 schema 有效时，以第二轮为最终结果；它可以修正第一轮 disposition；
2. 第二轮调用或顶层 schema 无效时，第一轮有效结果可回退使用，并在 reasons/round summary 中写明 `refinement_fallback`；
3. 两轮都无有效顶层结果时 `auto_rejected`；
4. 不对非法 JSON 自动提取任意 `{...}`，不吞掉 schema 错误；
5. fenced JSON 仍视为协议错误，因为 structured output 与新版 prompt 都明确要求裸 JSON。

#### `_parse_path_set_response()`

替换 `_parse_path_response()`，集中完成：

- 顶层 exact-key 校验；
- disposition 枚举和非空 reason；
- 明确拒绝顶层 `atoms` 以及任意 `atom_id/atom_ids` 字段；
- path index、strategy 和非空 operations 校验；
- 对每条 operation 执行 exact-key、evidence ID、literal index 和 evidence role 校验；
- 将 operation 直接转换为当前路径私有 `_PathAtom`，按位置设置 terminal；
- 每条 path 独立生成 valid/rejected summary；
- executable 至少保留有效路径供后续门槛判断；
- 非 executable 强制 paths 为空；
- 顶层 invariant ID allowlist 与去重校验。

顶层 JSON 或 disposition schema 错误使整轮无效；单个 path 错误只淘汰该路径，保留同轮其他合法路径。

#### `_record_raw_response()`

将 path index/strategy 参数改成受控的 `response_key`：

```text
round_01_generation
round_02_refinement
```

每个 case 的 `llm_outputs/<case_id>.json` 只保存两个 key。response record 继续保存字符数、SHA-256、fence/object 标记，并新增：

- `round_index`；
- `stage`；
- `response_format="json_object"`。

#### `_path_set_diversity()` 与 `_round_feedback()`

按第 5 节公式实现。复杂度为 `O(N² × L)`，N 默认 6、路径很短，不引入第三方依赖或 NetworkX。

`_round_feedback()` 只输出紧凑 JSON，不输出完整 prompt、用户敏感数据、operation name 或 purpose。

#### 共识修改

当前代码先 `_distinct_aligned_paths()`，再对 distinct paths 计票。修改为：

```python
aligned_paths = [_align_path(path) for path in selected.paths]
distinct_paths = _distinct_aligned_paths(aligned_paths)  # 仅统计

if len(aligned_paths) < minimum:
    _reject("有效路径不足", ...)

threshold = ceil(len(aligned_paths) * CONSENSUS_RATIO)
clusters = _consensus_clusters(aligned_paths, threshold)
nodes = _milestones_from_clusters(clusters, evidence_by_id)
edges = _consensus_edges(aligned_paths, set(clusters), threshold)
```

关键变化：

- `distinct_paths` 不再进入成功门槛和共识计票；
- 删除“有效差异路径不足”拒绝分支，改为“schema 合法路径不足”；
- 删除“三分之二共识未产生 terminal milestone”的拒绝条件；
- 每条候选路径已经是完整路径，最终 graph 的 terminal 由共识 DAG 叶节点决定；
- clusters 为空时，返回合法 0 节点 executable graph，不报错；
- `_consensus_edges()`、DAG 校验和传递约简保留；
- exact duplicate 仍通过 diversity report 明确记录，不能静默隐藏。

#### disposition graph

新增 `_graph_for_disposition()`：

- `needs_clarification`、`no_action`：`MilestoneGraph(nodes=[], edges=[], ...)`；
- `response_only`：使用 `agent_response_present` evidence 创建固定名称、固定描述的一节点图；
- `executable`：进入共识；
- graph metadata 写入 `source=generated`、`disposition`、`view_digest`、最终 round、diversity 摘要；
- 不把 disposition reason 写入 milestone label。

#### 节点 route

`_milestones_from_clusters()` 在构造 `Milestone` 时增加：

```python
matching_route=evidence.matching_route
```

`response_only` 节点固定为 Agent→User；工具调用为 Agent→Environment；工具结果为 Environment→Agent。

#### minefield

将 `_consensus_minefields(paths, ...)` 改为 `_compile_minefields(invariant_ids, ...)`：

- invariant 已由整组路径的第二轮结果统一选择，不再按路径重复计票；
- 只接受 `role=minefield` evidence；
- expected、penalty 和 ID 仍由 compiler 推导；
- final round 的选择覆盖 draft，fallback 时使用 draft。

#### 删除/保留清单

删除：

- `_simulate_path()`；
- 单路径 `_parse_path_response()`；
- `_minimum_valid_path_count()` 的 N−2 语义；
- `_mark_duplicate_summaries()` 作为门槛处理；
- terminal consensus 拒绝；
- per-path minefield consensus。

保留并复用：

- `_generator_payload()`、`_public_task_payload()`；
- `_source_values()`、`_evidence_catalog()`、`_allowed_expected_literals()`；
- `_align_path()`、`_distinct_aligned_paths()`；
- `_consensus_clusters()`、`_milestone_id()`、`_consensus_edges()`；
- `_transitive_reduction()`、`_is_dag()`；
- `_reject()`，但更新 GenerationReport 字段。

### 6.10 `milestone_reliability.py`

#### 记录生成 token usage

当前 reliability 直接调用 compiler，没有激活 `RuntimeMetricsRecorder`，因此结果无法验证两轮方案是否节省 token。修改 `_run_case()` 的生成阶段：

1. 创建 `RuntimeMetricsRecorder`；
2. 使用 `activate_runtime_metrics_recorder()` 激活；
3. 在 `finally` 中 `reset_runtime_metrics_recorder()`；
4. 使用现有 `summarize_llm_calls()` 生成 `generation_usage`；
5. `_CaseResult` 新增 `generation_usage: JsonObject`；
6. completed、generation_rejected 和 generation_failed 都必须保存 usage；
7. `_write_case_json()` 增加顶层 `generation_usage`；
8. summary 聚合 `llm_call_count`、prompt/completion/total tokens 和 token availability。

不修改 `BaseLLM` 和 OpenAI usage 提取代码，复用现有实现。

#### 新增空图与纯拓扑指标

当前 strict/structural descriptor 都受 evidence 表示空间影响。将 `_graph_descriptor(graph, structural: bool)` 改为显式 mode：

```python
mode: Literal["strict", "structural", "topology"]
```

- strict：保持现有完整标签；
- structural：保持 terminal + constraint shape；
- topology：节点标签只保留 terminal，边仍为 directed precedence。

单 case metrics 新增：

- `topology.ged_similarity`；
- `topology.node_set_f1`；
- `reference_empty`；
- `prediction_empty`；
- `empty_graph_match`。

summary 新增：

- disposition counts；
- empty reference count；
- empty graph accuracy；
- false-nonempty / false-empty 数量；
- topology 指标统计；
- round 1/round 2 diversity 均值及改善量。

由于 case/summary 字段发生语义变化，将 schema version 升级为：

```text
milestone_reliability.case.v2
milestone_reliability.summary.v2
```

不保留旧 schema 兼容分支。

### 6.11 `docs/apis/milestone.md`

重写以下章节：

- 配置：两轮固定协议、合法路径门槛；
- 路径集合生成协议；
- disposition 语义；
- evidence role 和 matching route；
- round 1/round 2 prompt 与 structured output；
- 确定性多样性指标；
- 共识按全部合法路径计票；
- 合法空图和 response-only 图；
- round 级 `llm_outputs` 文件结构；
- GenerationReport 新字段；
- reliability v2 指标与 generation usage。

### 6.12 新增复跑配置

新增：

```text
data/experiments/toolsandbox_milestone_reliability_partial_main_pathset_v2.json
```

内容复制当前 25-case 配置，但：

- `experiment_id` 使用新名称；
- 不覆盖现有 `toolsandbox_milestone_reliability_partial_main` 结果；
- generator model、temperature、timeout、max_tokens 保持一致，确保前后可比；
- 首轮仍使用 `max_tokens=4096`，只有真实响应发生截断时才根据审计结果调整，不预先扩大输出预算。

## 7. 测试修改

### 7.1 `tests/milestone/support.py`

将 `SequenceLLM` 改为可记录：

- 调用次数；
- 每次 messages；
- `response_format`；
- 返回的两轮 path-set JSON。

新增 `path_set_response()` 测试构造器，支持 disposition、路径内联 operations、路径序列和 invariant，不提供共享 atom catalog 构造入口。

### 7.2 `tests/milestone/test_compiler_schema.py`

覆盖：

- 合法 executable path-set；
- 四种 disposition；
- non-executable 带非空 paths 拒绝；
- 顶层 `atoms`、`atom_id`、`atom_ids` 明确拒绝；
- operation 缺字段/多字段、未知 evidence、context/minefield evidence 引用拒绝；
- path index 缺失/重复、strategy 不匹配；
- expected index 越界；
- unknown/duplicate invariant；
- fenced JSON 仍拒绝；
- 单条 path 错误只淘汰该 path，顶层错误淘汰整轮。

### 7.3 `tests/milestone/test_compiler_paths.py`

替换“六次独立调用”旧断言：

- 每个 case 正常只调用 2 次；
- 两次都传 `response_format="json_object"`；
- 第二次 messages 包含第一轮 assistant response；
- 第二轮有效时使用第二轮；
- 第二轮无效时回退第一轮并记录 reason；
- 两轮都无效时 auto_rejected；
- 6 条完全相同路径不因 distinct=1 被拒绝；
- executable 只有 3/6 条有效时拒绝，4/6 条有效时继续；
- non-executable 不受 minimum valid path count 约束。

### 7.4 新增 `tests/milestone/test_compiler_diversity.py`

覆盖：

- 完全重复路径 distance=0；
- 仅调整顺序但操作完全相同，shared operation ratio=1；
- 部分共享、完全不共享、重复 occurrence；
- name/purpose 不同但 evidence 序列相同时仍判定重复；
- duplicate groups、high-overlap pairs 和 operation support；
- 单路径/空路径 diversity 返回 `None`，不伪造 0 或 1；
- feedback 不包含任务原文以及 operation 的 name/purpose。

### 7.5 `tests/milestone/test_compiler_consensus.py`

调整为：

- 共识使用全部合法路径，而不是 distinct paths；
- duplicate paths 可以支持真正必经 atom；
- distinct count 仅审计；
- 无共识节点返回合法空图；
- 不再要求 terminal_support 达到阈值；
- edge threshold、DAG、传递约简保持原测试；
- matching route 从 evidence 写入节点；
- 顶层 invariant 编译为 minefield。

### 7.6 `tests/adapter/test_contract.py`

新增/修改：

- `agent_message_instruction.role == context`；
- tool evidence route 为 Agent→Environment；
- invariant evidence role 为 minefield；
- ToolSandbox 只保留 recipient=agent 的 system/user messages；
- system→user simulator prompt 被排除；
- system→environment import 文本被排除；
- visible_to=[USER] 但 recipient=agent 的历史用户消息仍保留；
- 多轮消息按 step index 排序。

### 7.7 `tests/test_milestone_response_recording.py`

修改断言：

- 文件 key 为 `round_01_generation`、`round_02_refinement`；
- 两轮原文、hash、字符数、response format 完整；
- 第一轮有效、第二轮无效时两份原文仍保留；
- 不配置 output file 时不落盘；
- 原始响应不进入终端日志。

### 7.8 `tests/milestone/test_reliability.py`

增加：

- generation usage 在成功/拒绝/失败 case 都保存；
- v2 case/summary schema；
- topology descriptor 忽略 constraint 表示差异；
- empty graph match/confusion 汇总；
- disposition 和 round diversity 汇总；
- token 缺失时保持 `token_available=false`，不把缺失伪造为 0。

### 7.9 覆盖率

使用 PyTest + coverage 检查 `dynsteer/milestone/compiler.py`、`dynsteer/milestone/model.py`、两个 contract 模块的本次新增核心逻辑不低于 80%。测试只调用现有公开接口 `compile_task_case()` 或模块既有 helper，不新增只供测试调用的生产接口。

## 8. 分阶段实施顺序

### 阶段 1：协议、两轮路径集合与观测

1. 修改 model/report；
2. 新增 generation/refinement prompts；
3. compiler 改为两轮 path-set + structured output；
4. round 级原始响应记录；
5. reliability token usage；
6. 完成 schema、round、recording 单元测试。

阶段 1 完成后先跑 3 个 smoke case：

- 常规线性：`add_contact_with_name_and_phone_number_3_distraction_tools`；
- 信息不足：`modify_contact_with_message_recency_insufficient_information`；
- 多轮：`update_contact_relationship_with_relationship_twice_multiple_user_turn`。

### 阶段 2：公开视图、evidence role 与 disposition

1. ToolSandbox recipient 过滤；
2. context/milestone/minefield role；
3. agent response evidence 与 matching route；
4. disposition graph；
5. 完成 adapter、空图和 route 测试；
6. 重跑 3-case smoke，人工核查两个 round 的原文。

### 阶段 3：共识和评价

1. 共识改为全部合法路径计票；
2. distinct 只作诊断；
3. 允许无共识 executable 空图；
4. topology 与 empty graph metrics；
5. 完整 25-case v2 复跑。

阶段间不增加旧流程兼容分支；每个阶段完成后删除已无调用的旧函数和测试假设。

## 9. 验收标准

### 9.1 协议与调用

- 每个正常 case 只有 2 次 provider 调用，而不是 6 次；
- 两轮都启用 JSON object structured output；
- 25-case 不再出现批量 Markdown fence 解析错误；
- 顶层 JSON parse 成功率与 path-set schema 成功率分别统计；
- 两轮原始输出和差异报告可审计。

### 9.2 多样性

- round 1/round 2 分别报告 distinct count、duplicate groups、mean/min distance；
- 第二轮不得只修改 operation 的 name/purpose 伪造差异；
- 对有真实替代路线的 case，第二轮 exact duplicate 数应下降或保持 0；
- 对只有一种自然路线的 case，允许 distinct=1，不能因此拒绝；
- 多样性分数本身不作为 graph 成功率 KPI。

### 9.3 空图与多轮

- 四个人工空图 case 的 prediction 均为空图；
- `remove_contact_by_phone_no_remove_contact_insufficient_information` 稳定为 `response_only` 一节点图；
- 多轮 prompt 不再包含 system→user 的 User A role-play 指令；
- 多轮公开资产包含实际 recipient=agent 的历史用户消息；
- `update_contact...twice...` 的最终路径至少表达两次关系变更，不被 invariant 元步骤替代。

### 9.4 生成覆盖和结构

- 25/25 case 都产出可评测图，包括合法空图，不再因 distinct path 数拒绝；
- generation rejection 只允许来自两轮协议均无效、executable 有效路径少于 2/3、未知 evidence 或 DAG 冲突；
- 同族 base/distraction/arg-scrambled 的 disposition 与核心工具阶段保持一致；
- 报告 strict、structural、topology 和 empty graph 四层结果。

### 9.5 token 与耗时

- provider call count 从 6 降为 2，下降 66.7%；
- 在上述 3-case smoke 和至少 5 个代表 case 上记录实际 prompt/completion/total token；
- 目标是平均 total token 相比六次独立调用下降至少 25%；
- 若 call count 已下降但 total token 未下降，保留两轮架构，将第二轮输出改为 replacement-only 作为后续优化，不回退到六次独立调用；
- 单 case 平均生成耗时应显著低于当前约 31 秒基线，具体以同 provider 同时段复跑为准。

### 9.6 代码质量

- PyTest 全部通过；
- 核心新增逻辑覆盖率不低于 80%；
- `python -m compileall` 或等价语法检查通过；
- `git diff --check` 通过；
- 删除旧的单路径生成、N−2 去重门槛和未使用 helper/import；
- 中文 prompt、日志、文档均为 UTF-8，无乱码；
- 不修改 `.gitignore`，不自动 commit/push。

## 10. 风险与控制

### 单次路径集合的相关性

同一响应中的路径不是统计独立样本，2/3 支持度不能再解释为独立采样概率。本方案将其定义为“同一结构化搜索集合中的支持比例”，并通过第二轮显式挑战高支持 operation signature。报告和 API 文档必须使用“支持度”，不称为置信概率。

### 第二轮迎合差异度

模型可能为了提升差异而生成不完整路径。控制手段：差异度无硬阈值；prompt 首要条件是每条路径完整可执行；compiler 仍执行 evidence allowlist、路径引用和合法路径数校验；人工 smoke 核查第二轮原文。

### response-only 语义较弱

`agent_response_present` 只能检查存在 Agent→User 响应，不能验证拒绝内容与人工文本一致。本轮先解决“空图和一节点图类别”问题；响应内容的公开可验证 evidence 需要后续单独设计，不能使用 benchmark 私有 expected 泄漏答案。

### strict GED 仍可能偏低

人工 reference 使用私有 `state_snapshot/custom`，生成侧使用公开 evidence。增加 route、topology 和 empty graph 指标能改善解释性，但不会自动消除 strict descriptor 的表示鸿沟。不得为提高 strict GED 把私有人工 expected 注入生成 prompt。

### provider structured output 兼容性

当前 Qwen 走 OpenAI-compatible client，代码已支持 `response_format=json_object`。若某 provider 明确不支持，应在该 provider 边界实现等价结构化输出能力或明确报错，不在 compiler 中加入任意正文 JSON 抽取。

## 附录A. 项目中没有把握实现的模块部分

### 公开 evidence 与人工 state snapshot 的语义对齐

目前最没有把握直接实现的是将公开 tool call/tool result evidence 精确映射到 ToolSandbox 私有 `state_snapshot/custom` 人工节点。私有 expected、scorer 和 reference milestone 不应进入生成 prompt，否则会造成 benchmark 泄漏。本方案只增加 evidence role、matching route、response presence 和 topology/empty metrics，不承诺 strict descriptor 能立即显著提升。

### 信息不足任务的细分类边界

四个人工 reference 是空图，而 `remove_contact...` 是 response-only 一节点图。方案给出了 `needs_clarification/no_action/response_only` 三类，但模型能否仅根据公开工具和对话稳定区分，仍需 5 个信息不足 case 的真实复跑确认。若结果不稳定，应先由 benchmark 维护者明确标注原则，不能通过 case ID 特判。

### 两轮方案的实际 token 收益

调用数从 6 到 2 是确定的，但总 token 收益取决于工具 schema 长度、路径内联 operation 的重复输出和第二轮完整输出长度。现有 reliability 批次未记录 generation usage，无法在实施前给出真实节省比例。本方案要求先补 usage 观测，再以同模型同 case A/B 数据确认；25% 只是目标，不是未经实测的结论。

### 可执行任务无共识节点时的评价语义

本方案允许 executable 但无共同公开 atom 的合法空图，此时 DynSTEER 会在 finish 阶段评价完整任务，而不是做中途 milestone 路由。这在当前 stage 逻辑中可运行，但是否符合所有 benchmark 的期望，需要用真实轨迹比较 coverage、virtual stop 和最终排序后确认。
