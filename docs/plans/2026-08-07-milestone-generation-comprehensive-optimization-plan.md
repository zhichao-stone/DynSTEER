# DynSTEER Milestone 生成综合优化方案

## 1. 方案目标与实施边界

本方案针对以下两类输入形成统一修复方案：

1. `results/milestone/toolsandbox_milestone_reliability_partial_main` 核查中暴露的生成失败、信息不足任务误生成、节点粒度/拓扑偏差、minefield schema 失败、扰动不稳定和严格 GED 指标失真；
2. 用户补充的路径采样要求：请求模拟 `N` 条路径时，允许实际使用 `N-2` 条，但实际使用数最少为 3 条。

本方案只制定代码落地方案，不在本次任务中直接修改 compiler、adapter 或 prompt。实现时必须保留当前工作区已有的 milestone compiler、model、prompt 和 API 未提交修改，并在其上继续收敛；不得用历史版本覆盖当前修改。

### 1.1 已确认的 N−2 语义

当前配置 `data/experiments/toolsandbox_milestone_reliability_partial_main.json` 中的：

```json
"simulated_path_count": 6
```

因此本次新规则为：

```text
N = config.simulated_path_count
M = max(N - 2, 3)
```

其中 `M` 是编译器接受并用于共识编译的最少有效**去重路径数**。当前截图中的 case 记录为 `N=6`、`actual_valid_distinct_path_count=4`，旧规则 `M=5` 时被拒绝；采用新规则后 `M=max(6-2,3)=4`，该 case 应通过路径数量门槛，随后继续进行 atom 共识、边共识和 DAG 校验。

这里必须区分三个数量：

| 名称 | 定义 | 是否用于门槛 |
|---|---|---|
| `requested_path_count` | 配置要求的 N | 否，作为生成目标和审计值 |
| `valid_path_count` | 通过 schema、atom 引用、terminal、重复 atom 等校验的路径记录数 | 否，避免重复路径虚增共识样本 |
| `actual_valid_distinct_path_count` | 对有效路径按 `atom_ids` 去重后的数量，也是当前共识算法真正消费的数量 | 是，必须 `>= M` |

本方案保留 `MilestoneGenerationConfig.simulated_path_count >= 5` 的配置下限。原因是 N−2 规则在 N=5 时已经达到 3 条有效路径，仍能形成最小三分之二共识；如果未来需要允许 N=3/4，应另行调整 prompt 路径多样性和统计置信度，不在本方案中隐式放宽配置协议。

## 2. 当前问题的根因归纳

### 2.1 路径门槛的旧规则与报告表述不一致

当前 `dynsteer/milestone/compiler.py::compile_task_case()` 第 89 行附近已经使用：

```python
minimum_valid_paths = config.simulated_path_count - 1
```

但报告只写“低于 5 条门槛”，没有同时展示 N=6 和实际值 4，容易被误认为存在硬编码 5 或忘记了 N−1 规则。更重要的是，核查结论使用了旧规则的拒绝结果，未指出 N−2 放宽后该 case 应进入后续 graph 校验。

### 2.2 公开输入被过度压缩

当前 `dynsteer/milestone/compiler.py::_public_task_payload()` 只向模型提供：

- `view.instruction`；
- 工具名称列表。

`GeneratorTaskView.public_assets` 中真正包含的有序 system/user 消息、任务上下文、澄清要求、多轮任务说明和公开不变量没有进入 `TASK`。这直接导致：

- `find_days_till_holiday_insufficient_information` 等任务丢失“没有当前日期、不能猜测、应澄清”的关键信号；
- `update_contact_relationship_with_relationship_twice_multiple_user_turn` 丢失多轮顺序信息；
- 工具参数描述/required/type 没有参与计划，distraction 和 schema scramble 会改变模型表面选择，而非只影响合理工具选择。

### 2.3 没有任务执行模式，无法表达空图

当前 compiler 把 `bool(nodes)` 作为 graph 有效性的必要条件。信息不足或“只应澄清/保持状态”的任务只能被迫生成至少一个 terminal atom，最终出现 reference 为空图而 prediction 生成 2～3 个节点、相似度为 0 的结果。

仓库已有空图运行支持：`dynsteer/graph.py::augmented_edges()`、`dynsteer/evaluate/final.py::_empty_graph_finish_verification()` 和 `dynsteer/evaluate/evaluator.py::_empty_graph_coverage()` 已经可以处理空 milestone graph。因此缺失的是生成协议和 graph metadata，而不是重新发明 evaluator。

### 2.4 模型承担了可确定推导的 schema 工作

当前模型必须同时输出 `source_refs`、`evidence_id`、`expected`、`minefield_id`、`invariant_id`，导致：

- 一个公开 literal 复制错误即可淘汰 atom；
- minefield 需要重复生成本来可以由 invariant/evidence 确定的 expected；
- 25 个 case 共出现 91 次 minefield schema 错误；
- atom 被淘汰后，路径又级联出现未知 atom。

### 2.5 共识算法对节点和边的处理过于粗糙

当前：

- 节点支持度按每条去重路径中是否出现计算；
- 边通过所有节点对的方向 bit OR 后，仅保留“所有路径方向一致”的 pair；
- 节点完全依赖模型 atom ID/文本分割，缺少重复动作、多轮轮次和阶段角色约束。

这会导致复杂任务被压成 1～2 个节点，也会因一条反向路径丢弃本来具有明显多数支持的 precedence。

### 2.6 可靠性指标把表示差距当成语义差距

`milestone_reliability.py::_graph_descriptor()` 将 name、description、route、terminal 和全部 constraints 合成一个 canonical JSON，并以完全相等作为零成本替换。人工 reference 使用 ToolSandbox 私有 `state_snapshot/custom` 约束，生成器使用公开 `step/tool_call/tool_result` 约束，严格 `node_set_f1=0` 是表示空间不一致与真实差距的叠加。

同时，逐 case 结果只保存节点数量和 GED 编辑路径，不保存足以复核的预测/参考 descriptor 摘要，导致拒绝 case 和节点替换无法人工解释。

## 3. 优化后的总体算法

### 3.1 阶段 A：构造稳定、完整、可防注入的公开任务视图

适配器仍只允许输出 Agent 可见内容，绝不引入 benchmark 私有 matcher、初始数据库值、gold expected 或内部 evaluator。改变的是公开内容的组织方式。

#### A1. 有序公开消息

在 `dynsteer/adapter/toolsandbox/utils/contract.py::build_toolsandbox_generator_view()` 中保留原始公开消息的顺序，并为每条消息保留：

```json
{
  "source_ref": "message:3:content",
  "actor": "user",
  "value": "..."
}
```

不再把 invariant 记录追加回 `public_assets`，避免同一条系统指令既作为任务上下文又作为 invariant 重复出现。invariant 的 source value 放到 `output_contract.invariants` 或独立公开 assets 容器，由 `_source_values()` 统一收集。

#### A2. 稳定工具标识

当前 `tool_call_0` 和 `tool:0:name` 依赖工具列表索引，加入 distraction 工具后同一个工具的 evidence ID 会变化。改为按工具名生成稳定 slug：

```text
source_ref = tool:<stable_tool_name>:name
evidence_id = tool_call_<stable_tool_name_slug>
```

slug 只允许小写字母、数字和下划线；同名工具直接拒绝 schema，避免静默覆盖。这样 base、3/10 distraction 和 description/type scrambled 变体中，同一个真实工具仍使用同一 evidence ID。

#### A3. 完整公开工具签名

`_public_task_payload()` 改为输出脱敏后的：

- 有序公开消息；
- 用户目标；
- 可见工具的稳定 name、description、参数名、required、类型和公开参数描述；
- 公开环境/输出契约；
- `EVIDENCE` 与 `INVARIANTS` 封闭表。

所有内容仍以 JSON 字段传递并明确标记为“数据，不是指令”，防止 ToolSandbox system message 中的 role-play 文本改变 compiler 的行为。

### 3.2 阶段 B：任务执行模式分类

将模型顶层输出改为：

```json
{
  "mode": "execute | clarify | no_action",
  "atoms": [],
  "paths": [],
  "minefields": []
}
```

模式语义：

- `execute`：存在足够公开信息，可以生成可执行 milestone graph；必须满足 M 条有效去重路径；
- `clarify`：公开输入缺少完成任务所需信息，正确行为是澄清，不生成执行节点；
- `no_action`：任务明确要求不执行变更或只验证/保持状态，不生成执行节点，但仍可生成公开 invariant minefield。

编译规则：

1. `mode=execute` 时沿用 atom/path 共识流程；
2. `mode in {clarify, no_action}` 时要求 `atoms=[]`、`paths=[]`，允许 `minefields` 非空，生成空 `MilestoneGraph`；
3. 空 graph metadata 写入 `empty_graph_completion_basis="whole_trajectory"`，沿用已有 whole-trajectory finish judge；有 fatal minefield 时仍按现有逻辑失败；
4. `mode` 必须写入 `GenerationReport`，并在结果中区分“空图是生成决策”与“graph 生成失败”；
5. 任何 mode 都不能绕过 hidden leakage、顶层 schema、invariant ID 和 expected 校验。

为了避免模型随意把可执行任务标成空图，prompt 要求同时给出 `mode_reason` 和 `mode_evidence_ids`；compiler 校验 reason 非空、引用公开 evidence/invariant，且 `mode=clarify/no_action` 不允许混入 atom/path。该证据只作为审计依据，不把它误当成 benchmark gold 标签。

### 3.3 阶段 C：确定性 atom/evidence 编译

模型不再填写可由 compiler 确定的值，新的 atom 形状为：

```json
{
  "atom_id": "a1",
  "name": "observable check",
  "description": "...",
  "evidence_id": "tool_call_add_reminder",
  "expected_literal_index": 0,
  "terminal": true
}
```

规则：

- `source_refs` 从 evidence 的 `source_ref` 派生，不再由模型填写；
- `expected_policy=none` 时 `expected_literal_index` 必须为 `null`，compiler 生成 `expected=None`；
- `public_literal` 时 index 必须落在 `_allowed_expected_literals()` 范围，compiler 生成最终 expected；
- `minefields` 只输出 `invariant_id`，minefield ID、source_ref、expected、penalty 由 `_compile_minefields()` 确定生成；
- atom 的 name/description 保留用于可读性，但不再被严格 descriptor 指标当作语义唯一依据。

这会把模型错误集中到“选错公开证据、阶段和顺序”，而不是在 JSON 中复制 literal、source_ref 和 minefield expected。

### 3.4 阶段 D：N−2 路径门槛与共识编译

新增单一策略函数：

```python
def _minimum_valid_path_count(requested_path_count: int) -> int:
    """返回 N−2 且不低于 3 的有效去重路径下限。"""
    return max(requested_path_count - 2, 3)
```

`compile_task_case()` 在解析配置后只计算一次 `minimum_valid_paths`，所有 reject/success `GenerationReport` 使用同一值。路径流程固定为：

1. 逐条校验 path schema、atom ID、terminal 和重复 atom；
2. 删除相同 `atom_ids` 序列的重复 path，保留首个 strategy 作为展示信息；
3. 当 `len(distinct_paths) < max(N-2,3)` 时拒绝，并在错误消息中包含 `requested=N, minimum=M, actual=K`；
4. `threshold = ceil(len(distinct_paths) * 2/3)`，只用去重路径进入共识；
5. 节点支持度按路径出现次数计算；必须保留至少一个 terminal atom，否则拒绝；
6. 边支持度不再要求全路径方向一致，而是分别累计 `source→target` 与 `target→source` 的支持数；某一方向达到 threshold 才保留该方向，平局或双方均未过 threshold 则不保留；
7. 对保留边执行 transitive reduction，再做 DAG 校验；
8. `mode=clarify/no_action` 跳过路径门槛和节点共识，但必须通过空 graph 规则。

这样，截图中的 4 条有效去重路径在 N=6 时达到 M=4，可以继续进行共识编译；如果后续节点共识为空、terminal 丢失或边成环，仍会以真正的 graph 原因拒绝，而不是继续归咎于路径数量。

### 3.5 阶段 E：多轮、阶段和分支约束

公开 prompt 明确要求：

- 按有序 user/system message 识别轮次和任务阶段；
- 同一工具在不同轮次执行时必须使用不同 atom ID，并在 description 中标注第几轮或阶段目的；
- 只在 evidence 支持时创建“状态检查→准备/恢复→业务动作→确认/反馈”阶段；
- optional preparation 只进入部分 path，不能因出现在一条 path 就成为共识节点；
- 并行前置条件保留 DAG 分支，不能强制压成单链；
- strategy 名称不算差异，`atom_ids` 序列必须真正不同。

compiler 的 edge majority 支持使一条反向/异常 path 不会直接抹掉明显多数 precedence；prompt 的有序消息与稳定 evidence ID 则减少多轮和 distraction 的表面漂移。

### 3.6 阶段 F：分层可靠性指标与可审计产物

保留 strict GED 作为完整 descriptor 一致性指标，但增加以下分层结果：

1. `node_count_error`、`edge_count_error`；
2. 无 name/description/expected 的 `topology_ged`；
3. `terminal_f1`、`route_f1`、`constraint_shape_f1`（target/selector/operator/namespace 的结构匹配）；
4. `strict_descriptor_ged` 与 strict `node_set_f1`，明确标记为 representation-sensitive；
5. `unobservable_reference_node_count`，统计 reference 中不能由公开 evidence 表达的 private state snapshot/custom 节点；
6. 若提供同一轨迹，则新增 behavioral alignment：逐 step milestone 命中序列、最终 coverage、terminal 命中、minefield 触发和 virtual stop 一致性。

逐 case 结果增加安全 descriptor 摘要，不保存完整私有 expected：

```json
{
  "reference_descriptor_summary": {
    "node_count": 2,
    "edge_count": 1,
    "nodes": [{"id": "m0", "terminal": false, "constraint_shape": ["state_snapshot/custom"]}],
    "label_digest": "..."
  },
  "prediction_descriptor_summary": {
    "node_count": 1,
    "edge_count": 0,
    "nodes": [{"id": "a1", "terminal": true, "constraint_shape": ["tool_call/equals"]}],
    "label_digest": "..."
  }
}
```

完整 descriptor 仅在显式 `--include-descriptors` 且输出目录为本地受控目录时写出，避免把 benchmark 私有 expected 写入默认实验产物。

## 4. 具体代码修改位置与内容

### 4.0 跨 benchmark 影响矩阵

本次不是只修改 ToolSandbox。三类 benchmark 都经过同一个 `compile_task_case()`，但公开 view 的构造分为两条路径：

| 代码层 | ToolSandbox | SWE-bench Pro | SkillsBench | 修改结论 |
|---|---|---|---|---|
| `dynsteer/milestone/compiler.py` | 使用 | 使用 | 使用 | 三者共同修改，负责 N−2/3、mode、atom/minefield schema、共识和 DAG |
| `dynsteer/milestone/model.py` | 使用 | 使用 | 使用 | 三者共同修改，负责 `GenerationReport` 和配置语义 |
| `dynsteer/prompt/templates/milestone/*` | 使用 | 使用 | 使用 | 三者共同修改，模板协议必须统一 |
| `dynsteer/adapter/agentcompass/contract.py` | 不使用 | 使用 | 使用 | 共享修改，稳定工具 ID、公开消息/工具 schema、invariant 和 source value |
| `dynsteer/adapter/toolsandbox/utils/contract.py` | 使用 | 不使用 | 不使用 | ToolSandbox 特化修改，保留 sandbox 公共消息/工具视图 |
| `dynsteer/adapter/swebench_pro/adapter.py` | 不使用 | 使用 | 不使用 | 只调整 workspace/output 的公开契约，避免 case ID/path 进入 prompt |
| `dynsteer/adapter/skillsbench/adapter.py` | 不使用 | 不使用 | 使用 | 只调整 workspace 的公开契约，公共逻辑仍由 shared contract 提供 |
| `dynsteer/adapter/base.py`、`loader.py` | 间接使用 | 间接使用 | 间接使用 | 保持统一调用链；只增加空 graph/mode 回归测试，不复制 benchmark 分支 |
| `milestone_reliability.py` | 可直接实验 | 后续复用 | 后续复用 | 指标和产物格式共同修改，不增加 benchmark 专用指标分支 |

因此，其他 benchmark 的正确做法不是分别新增 `swebench_contract.py` 或 `skillsbench_contract.py`，而是修改它们共同调用的 `agentcompass/contract.py`；只有 workspace/output 的业务公开字段保留在各自 adapter 中。

### 4.1 `dynsteer/milestone/compiler.py`

这是本方案的核心修改文件。

1. **路径下限**：在 `compile_task_case()` 前或 compiler 内部 helper 区域新增 `_minimum_valid_path_count()`，把第 89 行附近 `config.simulated_path_count - 1` 改为 `max(config.simulated_path_count - 2, 3)`；所有 `_reject()`、成功 `GenerationReport` 和日志统一复用该值。
2. **错误消息**：将模糊的“有效差异路径少于配置要求”改为带参数的结构化消息，例如 `有效差异路径不足: requested=6, minimum=4, actual_distinct=3`；`GenerationReport.reasons` 保留短文本，具体数值放入字段。
3. **顶层 schema**：扩展 `_TOP_LEVEL_KEYS`，解析 `mode`、`mode_reason`、`mode_evidence_ids`；`_parse_response()` 校验 mode 枚举和 reason/evidence 的结构。
4. **atom schema**：修改 `_parse_atoms()` 的 required keys，删除模型填写的 `source_refs` 和 `expected`，改为 `expected_literal_index`；从 `evidence_by_id` 和 `source_values` 确定 `_Atom.source_refs` 与 `_Atom.expected`。
5. **minefield schema**：修改 `_compile_minefields()`，输入只接受 `invariant_id`，按 invariant 顺序生成稳定 minefield ID 并自动解析 expected/penalty；移除模型对 minefield expected 的重复填写责任。
6. **空图模式**：在路径解析前处理 `clarify/no_action`。非 execute mode 若有 atom/path 直接 reject；否则创建空 `MilestoneGraph`，写入 `metadata.source="generated"`、`empty_graph_completion_basis="whole_trajectory"`、`generation_mode`，生成成功报告。
7. **共识节点**：在 occurrence 统计后显式检查 terminal 支持；没有达到 threshold 的 terminal 不允许通过 execute 模式。
8. **共识边**：重写 `_consistent_reduced_edges()`，按 pair 的两个方向计数，使用传入 threshold 选择多数方向，再执行 transitive reduction 和 DAG 检查。
9. **稳定 payload**：重写 `_public_task_payload()`，纳入有序 public messages、完整公开工具签名、environment/output contract；保留 benchmark/case ID 不进入 prompt 的约束。
10. **日志和审计**：生成开始/完成/拒绝日志额外输出 `requested_path_count`、`minimum_valid_path_count`、`actual_valid_distinct_path_count`、`generation_mode`，不输出完整 prompt、expected 或密钥。

### 4.2 `dynsteer/milestone/model.py`

1. 保留 `MilestoneGenerationConfig.simulated_path_count >= 5`，但错误信息改成“模拟路径 N 必须大于等于 5；有效使用下限为 max(N-2,3)”以消除概念混淆。
2. `GenerationReport` 增加 `generation_mode`、`mode_reason` 或等价的脱敏审计字段；已有 `requested_path_count`、`minimum_valid_path_count`、`actual_valid_distinct_path_count` 继续保留并改用 N−2 计算。
3. 不新增 `effective_path_count` 与现有 `actual_valid_distinct_path_count` 重复字段；文档明确后者就是共识实际消费的路径数。

### 4.3 `dynsteer/adapter/agentcompass/contract.py`（SWE-bench Pro 与 SkillsBench 共享）

该文件是两个非 ToolSandbox benchmark 的共同修改点，不能遗漏。

1. **函数 `_actf_evidence_catalog(tool_schema, output_contract)`**：将当前 `tool_call_{index}`、`tool:{index}:name` 改为基于工具名 slug 的稳定 ID；复制工具 dict 时保留 `function.name/description/parameters`，只追加内部 `source_ref/value`，不改变原始公开 schema。
2. **函数 `_tool_name(tool)`**：增加名称 slug 生成所需的规范化边界；工具名为空、重复或无法生成稳定 slug 时抛出 `ValueError`，不静默覆盖 evidence。
3. **函数 `_public_task_assets(task_case)`**：不再把 `repo`、`base_commit` 等实验/数据定位元数据直接作为 milestone TASK；改为只保留真正对 Agent 公开且与任务语义有关的文本资产。若 workspace/output 必须展示，统一放入有明确 `source_ref` 的 `environment_schema`/`output_contract`。
4. **函数 `_explicit_invariants(instruction, public_assets, evidence)`**：取消对 `public_assets.append({"source_ref": "invariant:..."})` 的副作用；返回 invariant 时同步写入一个独立的公开 source-value 容器，保证 `_source_values()` 能解析 invariant，但 TASK 不重复展示相同文本。
5. **函数 `build_agentcompass_generator_view()`**：保持 SWE-bench Pro 和 SkillsBench 共用签名，确保返回的 `GeneratorTaskView` 具备有序公开消息、稳定工具 evidence、环境/output source refs；不在两个 adapter 中复制处理逻辑。
6. **共享 payload 的字段约定**：SWE-bench Pro 与 SkillsBench 都由 compiler 统一读取 `view.public_assets`、`view.tool_schema`、`view.environment_schema`、`view.output_contract`；不新增 benchmark-specific top-level prompt 字段。

#### SWE-bench Pro 的 adapter 具体修改

在 `dynsteer/adapter/swebench_pro/adapter.py::generator_task_view()` 中：

- 当前 `environment_schema={"workspace": f"/app/{case_id}/repo"}` 改为不包含 case ID 的稳定公开路径，例如 `{"workspace": "/workspace/repo"}`；
- 当前 output contract 的 `"path": f"/app/{case_id}/patch.txt"` 改为通用公开契约，例如 `"path": "/workspace/patch.diff"`，`format` 保持 unified diff 语义；
- `case_id` 仍作为函数寻址参数存在，但禁止通过 environment/output source value 进入 milestone prompt。

#### SkillsBench 的 adapter 具体修改

在 `dynsteer/adapter/skillsbench/adapter.py::generator_task_view()` 中：

- 将 `environment_schema={"workspace": "/root"}` 改为与执行 harness 无关的公开语义路径，例如 `{"workspace": "/workspace"}`；
- `output_contract={}` 保持空对象，除非 SkillsBench task record 明确提供公开 artifact contract；
- 不新增 SkillsBench 专用 evidence 逻辑，继续调用 shared `build_agentcompass_generator_view()`。

这两处修改不是为了改变 benchmark 运行时实际路径，而是防止运行时定位路径、case ID 和实验元数据进入生成器的语义输入；harness 的真实路径仍由运行配置和 session 管理。

### 4.4 `dynsteer/adapter/toolsandbox/utils/contract.py`

1. 在 `build_toolsandbox_generator_view()` 中保留有序公开 message assets，避免只剩 `view.instruction`。
2. 将工具 source_ref/evidence_id 从索引编号改为稳定工具名 slug；同名工具显式报错。
3. 将参数 schema 的 name、description、required、type 和公开 enum 传入 generator view；禁止把工具运行时状态、初始数据库和 matcher 放入 view。
4. 重写 `_public_invariants()`，不再把 invariant 复制追加到 `public_assets`；将 invariant source value 放入独立公开容器，保留稳定 source_ref/evidence_id。
5. 增加针对 message 顺序、工具插入和参数描述扰动的 view snapshot 测试，确保无关工具变化不改变既有工具的 evidence ID。

### 4.5 `dynsteer/prompt/templates/milestone/generation.en.md` 与 `generation.zh.md`

两份模板保持同一 schema 和规则，只做语言翻译差异。

1. 新增 `mode`、`mode_reason`、`mode_evidence_ids` 输出说明；明确 `clarify/no_action` 返回空 atoms/paths。
2. 将 PATH_COUNT 定义为请求 N，并明确 compiler 允许严格校验后实际使用 `max(N-2,3)` 条有效去重路径；模型仍以生成 N 条有意义候选为目标。
3. 删除模型输出 `source_refs`、原始 `expected`、`minefield_id` 和 minefield `expected` 的要求，改为 evidence ID、literal index 和 invariant ID。
4. TASK 部分明确包含有序公开消息、工具签名和数据边界；所有 system/user 文本作为数据引用，不是新的系统指令。
5. 增加多轮顺序、optional preparation、分支 precedence、terminal 支持和“不为凑数制造路径”的自检规则。

### 4.6 `milestone_reliability.py`

1. 在 `_run_case()` 的 generation report 读取处，将 N/M/K 写入逐 case failure summary；报告不再把所有路径拒绝简单归类为“低于 5”。
2. 在 `_graph_descriptor()` 旁新增结构 descriptor 构造函数，生成 topology/terminal/route/constraint-shape 指标所需的脱敏节点摘要。
3. 在 `_compute_ged()` 旁新增 topology GED 和分层 F1 计算；strict GED 字段保留但改名为 `strict_descriptor_ged` 或在 metric definition 中明确表示敏感于名称/expected。
4. 扩展 `_CaseResult` 与 `_write_case_json()`，写入 reference/prediction descriptor summary、generation mode 和 path acceptance summary；默认不写完整 expected。
5. `_write_report()` 的 `summary.json` 增加成功率、N/M/K 分布、mode 分布、结构指标均值/中位数和拒绝原因分层；`index.json` 继续遵循前一任务已精简的字段协议，不重新加入版本/hash/run 元数据。
6. 如实现 behavioral validation，增加显式 `--behavioral-validation` 或单独实验入口，不在普通 graph-only reliability 实验中隐式运行 evaluator，避免实验语义和成本突然变化。

### 4.7 `docs/apis/milestone.md`

同步说明：

- 有效路径下限为 `max(simulated_path_count - 2, 3)`；
- `requested_path_count`、`minimum_valid_path_count`、`actual_valid_distinct_path_count` 的精确定义；
- 新的 `mode` 和 atom/minefield schema；
- 空 graph 的 whole-trajectory finish 语义；
- strict descriptor metric 与 topology/behavioral metric 的区别；
- public view 不包含 private state snapshot/matcher/初始数据库。

### 4.8 `dynsteer/adapter/loader.py`、`dynsteer/graph.py`、`dynsteer/evaluate/*`

默认不改核心逻辑，因为当前代码已经提供空图的 topology、finish verification 和 coverage 支持。只增加回归测试验证：

- generated empty graph 能经过 `enrich_milestone_graph()`、`_postprocess_task_case()` 和 stage goal/spec 生成；
- `clarify/no_action` 空图进入 whole-trajectory finish；
- fatal minefield 仍能在空图中触发失败；
- 普通 execute graph 的现有 runtime frontier 行为不变。

只有测试证明 mode metadata 无法传递到现有 finish coverage 时，才在 `dynsteer/evaluate/final.py` 或 `dynsteer/evaluate/evaluator.py` 增加读取 `empty_graph_completion_basis` 的最小修改，不重写已有空图算法。

### 4.9 修改前后关键接口与数据流

下面给出实现时必须达到的关键代码形状，避免只按概念修改而遗漏调用点。

#### 4.9.1 `compile_task_case()` 主流程

当前主流程是“读取 view → 一次 LLM → parse atoms/paths/minefields → N−1 门槛 → 共识”。修改后固定为：

```python
def compile_task_case(view, config, llm):
    requested = config.simulated_path_count
    minimum = _minimum_valid_path_count(requested)
    source_values = _source_values(view)
    evidence_by_id = _evidence_catalog(view, source_values)
    payload = _generator_payload(view, requested, source_values)
    response = _parse_response(llm.chat([_render_prompt(payload)]))
    mode = _parse_mode(response, evidence_by_id, source_values)

    if mode.name in {"clarify", "no_action"}:
        _validate_empty_mode_response(response, mode)
        graph = _empty_generated_graph(mode)
        return graph, _generated_report(
            mode=mode,
            requested=requested,
            minimum=minimum,
            valid_paths=0,
            distinct_paths=0,
            consensus_nodes=0,
        )

    atoms = _parse_atoms(response["atoms"], evidence_by_id, source_values)
    paths = _parse_paths(response["paths"], atoms)
    distinct_paths = _distinct_paths(paths)
    if len(distinct_paths) < minimum:
        raise _path_count_error(requested, minimum, len(distinct_paths))
    threshold = ceil(len(distinct_paths) * CONSENSUS_RATIO)
    retained_ids = _retained_atom_ids(atoms, distinct_paths, threshold)
    edges = _consistent_reduced_edges(distinct_paths, retained_ids, threshold)
    graph = _build_execute_graph(atoms, edges, response["minefields"], ...)
    return graph, _generated_report(...)
```

关键要求：

- `minimum` 只能通过 `_minimum_valid_path_count()` 计算，禁止在 `_reject()`、prompt、实验脚本中重复写 `N-2` 或 `3`；
- empty mode 只绕过路径门槛，不绕过 hidden leakage、mode evidence、invariant 和 schema 校验；
- execute mode 的 `retained_ids` 必须包含至少一个 terminal atom，且 `_build_execute_graph()` 最终必须通过 DAG 检查。

#### 4.9.2 atom 与 minefield 的输入输出变化

当前 atom 输入：

```json
{
  "source_refs": ["instruction"],
  "evidence_id": "agent_message_instruction",
  "expected": "..."
}
```

修改后 atom 输入：

```json
{
  "evidence_id": "agent_message_instruction",
  "expected_literal_index": 0
}
```

compiler 内部 `_Atom` 仍保留 `source_refs` 和 `expected`，但这两个字段只能由：

```python
evidence = evidence_by_id[evidence_id]
source_refs = (evidence.source_ref,)
expected = _literal_at(evidence, expected_literal_index, source_values)
```

派生。模型响应中不得再出现它们，避免模型自行拼接 public literal。

当前 minefield 输入：

```json
{"minefield_id": "mf1", "invariant_id": "invariant_0", "expected": "..."}
```

修改后 minefield 输入：

```json
{"invariant_id": "invariant_0"}
```

`_compile_minefields()` 按 invariant 顺序生成 `mf_<stable_invariant_slug>`，从关联 evidence 解析 expected，按 severity 计算 penalty，并对重复 invariant ID 报错。

#### 4.9.3 AgentCompass shared contract 的修改前后

当前两个 benchmark 的调用保持不变：

```python
return build_agentcompass_generator_view(
    config, task_case,
    environment_schema=...,
    output_contract=...,
)
```

修改后的 `build_agentcompass_generator_view()` 仍保持该签名，但内部数据流改为：

```python
public_assets = _public_task_assets(task_case)       # 只保留公开语义资产
tool_schema = _copy_public_tool_schema(task_case.tool_schema)
normalized_output = _with_source_refs(output_contract, "output")
evidence = _actf_evidence_catalog(tool_schema, normalized_output)
invariants, invariant_assets = _explicit_invariants(task_case.task_description)
public_assets.extend(invariant_assets)                # 仅供 source_values，不进入 TASK
return GeneratorTaskView(
    ...,
    public_assets=public_assets,
    tool_schema=tool_schema,
    environment_schema=...,
    output_contract=normalized_output,
    evidence_catalog=tuple(evidence),
    invariant_catalog=tuple(invariants),
)
```

实际实现中不能把 `invariant_assets` 与普通 TASK assets 共用同一个列表而不加标记；compiler 的 `_public_task_payload()` 必须过滤 `asset.get("kind") == "invariant"`，否则 invariant 仍会重复进入 prompt。

#### 4.9.4 三个 benchmark 的 adapter 修改后边界

```python
# toolsandbox/adapter.py：保持调用不变
return build_toolsandbox_generator_view(config, task_case, context, module_loader)

# swebench_pro/adapter.py：只改变公共契约字面值
environment_schema = {"workspace": "/workspace/repo"}
output_contract = {
    "path": "/workspace/patch.diff",
    "format": "Only output a unified diff that solves the task.",
}

# skillsbench/adapter.py：只改变公共 workspace 字面值
environment_schema = {"workspace": "/workspace"}
output_contract = {}
```

三个 adapter 都不直接创建 atoms、paths、minefields，也不实现自己的 N−2、mode 或 expected 解析。

#### 4.9.5 reliability 结果字段

`milestone_reliability.py::_write_case_json()` 修改后的 metrics/generation report 至少包含：

```json
{
  "generation_report": {
    "generation_mode": "execute",
    "requested_path_count": 6,
    "minimum_valid_path_count": 4,
    "valid_path_count": 4,
    "distinct_path_count": 4,
    "actual_valid_distinct_path_count": 4
  },
  "metrics": {
    "strict_descriptor_ged_similarity": 0.25,
    "topology_ged_similarity": 0.75,
    "terminal_f1": 1.0,
    "route_f1": 0.5,
    "constraint_shape_f1": 0.0
  }
}
```

字段命名必须在 `summary.json` 的 `metric_definition` 中逐项解释；旧的 `ged_similarity` 若保留，只能作为 strict descriptor 的别名并标注 representation-sensitive，不能再单独作为“语义可靠性”结论。

## 5. 测试与验收方案

当前仓库没有可直接复用的 milestone pytest 测试目录；落地时应新增测试并使用项目已有接口，不为测试创建平行实现。

### 5.1 Compiler 单元测试

建议新增 `tests/milestone/test_compiler_paths.py`：

1. `N=6` 时 `actual distinct=3` 被拒绝，报告为 `requested=6/minimum=4/actual=3`；
2. `N=6` 时 `actual distinct=4` 通过路径门槛；
3. `N=5` 时下限为 3；
4. `N=5` 时 2 条路径仍拒绝；
5. 有效路径数、去重路径数和共识实际消费数分别正确；
6. 多数方向 precedence 被保留，少数反向 path 不会直接删除该边；
7. 无 terminal 共识节点或产生环时仍拒绝。

建议新增 `tests/milestone/test_compiler_modes.py`：

1. `mode=clarify` 和 `mode=no_action` 的空 atoms/paths 生成空 graph；
2. 非 execute mode 混入 atom/path 被拒绝；
3. mode reason/evidence 缺失被拒绝；
4. 空 graph 可完成 enrich/stage/evaluator finish 流程；
5. execute mode 不允许用空 graph 绕过路径门槛。

建议新增 `tests/milestone/test_compiler_schema.py`：

1. atom 只引用 evidence ID，compiler 正确派生 source_ref/expected；
2. `expected_literal_index` 越界、none policy 非 null 被拒绝；
3. minefield 只传 invariant ID 时 compiler 正确生成 expected/penalty；
4. unknown invariant/evidence、hidden leakage 和重复 ID 仍拒绝。

### 5.2 ToolSandbox 公开视图测试

建议新增 `tests/adapter/toolsandbox/test_generator_view.py`：

1. public message 顺序、actor 和 source_ref 保持；
2. 增加 distraction 工具不改变既有工具的 evidence ID；
3. description/type scramble 只改变工具 schema 内容，不改变稳定 ID；
4. invariant 不在 TASK 中重复出现，但仍在 INVARIANTS 中可引用；
5. generator view 不包含 matcher、gold、ground_truth、初始数据库值等隐藏字段。

建议新增 `tests/adapter/agentcompass/test_contract.py`，分别以 `benchmark="swebench_pro"` 和 `benchmark="skillsbench"` 构造最小 TaskCase，验证：

1. 两个 benchmark 都使用同一 shared contract，不能出现一方仍使用 index-based tool ID；
2. 增加无关工具后，既有工具的 `evidence_id/source_ref` 不变；
3. `repo/base_commit` 等非任务语义元数据不会进入 compiler 的 TASK payload；
4. SWE-bench Pro 的 workspace/output source refs 不含 case ID；
5. SkillsBench 的 workspace source ref 稳定且 output contract 为空时不生成伪 evidence；
6. shared invariant 处理与 ToolSandbox 的 invariant 处理具有相同的“不在 TASK 重复、仍可被引用”语义。

### 5.3 Reliability 指标测试

建议新增 `tests/milestone/test_reliability_metrics.py`：

1. topology descriptor 对名称/描述不同但拓扑相同的图给出一致结果；
2. strict descriptor metric 仍能识别完整标签差异；
3. terminal/route/constraint-shape 分层指标可复算；
4. descriptor summary 不写出完整 private expected；
5. 25-case 结果汇总能同时报告 N/M/K，不再出现“固定 5 门槛”的歧义。

### 5.4 分阶段实验验收

1. 先用 mock LLM 跑 compiler 单测，不调用网络模型；
2. 用当前 25-case 配置重跑 graph-only reliability，确认截图 case 从 `generation_rejected` 进入后续 graph 校验，并观察后续 graph rejection 原因是否真实暴露；
3. 对同族 variant 比较 stable evidence ID、mode、节点数、拓扑摘要和 strict/structural 指标；
4. 通过 25-case 回归后再跑完整 509 case；
5. behavioral validation 另行跑同轨迹对比，报告 reference/generated 的命中序列和 final coverage，不把 graph GED 当作运行行为等价。

### 5.5 目标验收指标

代码修复本身不预设不真实的质量数字，但最低验收门槛必须满足：

- N=6 的 4 条有效去重路径不再因路径数量门槛拒绝；
- 任何 N>=5 的接受下限都严格等于 `max(N-2,3)`；
- 生成失败报告不再出现无法反推 N/M/K 的模糊文案；
- 公开视图对 distraction/tool schema scramble 具有稳定 evidence ID；
- minefield schema 错误不再由模型重复填写 expected 导致；
- 信息不足 case 可以生成合法空图，且 execute case 不能用空图逃避路径校验；
- strict 与 structural/behavioral 指标分开输出；
- 所有核心新增逻辑达到项目要求的 pytest 80% 以上覆盖率，且无语法错误。

## 6. 实施顺序与回滚边界

### Step 1：先落地路径策略和报告口径

只修改 `compiler.py`、`model.py`、`docs/apis/milestone.md` 和 reliability 报告字段，完成 N−2/3 下限、N/M/K 审计和单测。此步风险最低，可立即验证截图 case 的行为。

### Step 2：落地公开视图与 schema 简化

修改 ToolSandbox contract、compiler payload、英文/中文 prompt 和 schema 测试。此步可能改变生成模型输入及结果，不与 Step 1 混合判断。

### Step 3：落地 mode/空图与多轮共识

增加 mode、稳定 ID、edge majority、空图 metadata 和 evaluator 回归测试，重点重跑信息不足和 multiple_user_turn case。

### Step 4：落地分层指标和 descriptor summary

修改 reliability 计算和输出；先在 graph-only 实验验证，再开启 behavioral validation。结果 schema 的新增字段只增加诊断信息，不恢复上一任务已删除的 index 版本/hash/run 字段。

### 回滚边界

- 若公开 payload 变大导致 LLM 超时，可临时关闭完整工具 description，但不得恢复丢失有序 public messages 的旧 payload；
- 若 mode 空图误判率过高，可先保留 mode 仅作为报告字段并对空图继续拒绝，但不得删除空图回归测试；
- 若 edge majority 改变既有图过多，可通过报告同时输出旧/新 topology 指标定位，不回退 N−2 门槛；
- 不使用 `git reset --hard` 或覆盖用户现有未提交修改的方式回滚。

## 7. 附录A：项目中没有把握实现的模块部分

最没有把握的是“仅基于公开 evidence 自动判断某个信息不足任务是否应该生成空 graph，而不是生成澄清 milestone 或执行 milestone”。原因是：

1. ToolSandbox system message 中含有 role-play、任务目标和禁止假设规则，公开但复杂，不能简单用关键词判断；
2. reference 的空图语义来自 benchmark 原生 scorer 和人工设计，生成器不能读取 private matcher 作为标签；
3. 当前 `GeneratorTaskView` 没有显式的“所需参数缺失”公开结构，必须先完善工具 schema 和有序 message 视图；
4. 错误的空图会让 evaluator 退化为 whole-trajectory judge，可能掩盖本应被 milestone 精确检查的任务。

因此方案采用“mode + 公开 evidence 审计 + 空图运行回归 + behavioral validation”组合，而不是在 compiler 中加入不可解释的关键词硬编码。若 25-case 回归仍显示 mode 误判，需要用户先确认 benchmark 对“澄清、保持状态、无操作”的人工标签语义，再决定是否增加受控规则分类器。

## 8. 附录B：本方案明确不做的事情

- 不把 ToolSandbox 私有 `state_snapshot/custom` expected 注入 generator prompt；
- 不把 strict GED 删除或改写成唯一质量指标；
- 不把重复 path 数量直接当作独立共识样本；
- 不通过自动修补 unknown atom、expected 或 minefield 来掩盖模型错误；
- 不在普通 graph-only reliability 实验中隐式启动 evaluator 或额外网络调用；
- 不恢复 `index.json` 中已由前一任务删除的 `experiment_config_sha256`、`networkx_version`、`ignored_matrix_dimensions`、`run_id`、`schema_version` 字段。
