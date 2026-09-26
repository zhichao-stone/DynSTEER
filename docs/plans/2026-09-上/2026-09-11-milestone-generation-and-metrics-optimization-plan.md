# Milestone 生成算法优化与实验评估指标体系重构代码实施方案

- **方案日期**：2026-09-11
- **方案状态**：待评审 / 准备实施
- **方案路径**：`docs/plans/2026-09-11-milestone-generation-and-metrics-optimization-plan.md`
- **执行约束**：遵循 `docs/constraints/code.md`，严禁删除已有约束与方案文档，不主动执行 git commit。

---

## 1. 方案目标与架构全景

### 1.1 现状痛点与重构动机

根据我们对 DynSTEER 代码库及历史实验审计（包括 ToolSandbox 30-case / 25-case 审计报告）的深入分析，当前的 Milestone 与 Minefield 自动生成及评估链路存在两大维度的核心瓶颈：

1. **生成端与编译端的微观瓶颈**：
   - **干扰工具诱导的冗余调用**：面对含有对抗性干扰工具（Distraction Tools，如 `shift_timestamp`、`timestamp_diff`、`unit_conversion`）的场景，大模型倾向于进行防御性过度调用，生成大量非必需的中间转换节点，导致极简性（Minimality）受损；
   - **参数绑定因微小分歧降级**：在大模型给出的多个候选图中，当各候选选定了相同的 Producer 工具，但编写的 JSONPath 选择器略有微小差异时（例如 `$.reminders[0].id` vs `$.reminders[0].reminder_id`），现有的 `_majority_binding` 因缺乏绝对多数（$2 \times \text{count} > N$）而直接弃权，将参数强行降级为 `unresolved`，导致后续状态目标参数缺失；
   - **悬空无用 Producer 缺乏主动剪枝**：若大模型在多数候选图中都生成了一个多余的只读查询（如无后继引用的 `search_contacts`），编译器聚合后无法将其与真正的必须依赖区分开，缺乏悬空探针剪枝机制；
   - **信息不足场景下的决断力欠缺**：当任务缺少必要上下文时，模型偶发性地尝试调用工具猜测，未严格遵循生成合法空图与致命雷区的安全规范。

2. **评测指标端的结构性范式错位与误导**：
   - **Strict/Structural 模式导致虚低分假象**：`milestone_reliability.py` 目前以 Strict GED 和 Strict Node-Set F1 为重点输出，但 Strict 模式将自然语言 `name`/`description` 以及生成侧的高阶动态模板（`$binding`）与人工黄金图中的静态字面值进行刚性硬匹配，导致即使生成的拓扑和因果完全正确，Strict Node-Set F1 也常年在 **0.00 ~ 0.12** 徘徊，指标对算法迭代彻底钝化，丧失了区分度；
   - **全局 Minefields Macro F1 严重失真**：大量无雷区的空样本（Empty-Empty）获得 1.0，虚假拉高宏平均至 0.86+，掩盖了真正危险任务中致命雷区漏报的严重风险；
   - **冗余外部计算负担**：`FGW (Fused Gromov-Wasserstein)` 在小规模离散图上频繁不可用且数值退化；`dispositions` 单向缺失却混入多重集总分，导致均值失真。

### 1.2 预期重构目标

本方案旨在从**生成算法鲁棒性**与**评估指标科学性**两方面实施闭环重构：
- **生成端**：通过 Prompt 注入干扰工具防御机制、公开状态数据流绑定样例、增强 `compiler.py` 的契约引导绑定裁决（Contract-guided Binding Resolution）和悬空探针剪枝，使生成的 Milestone Graph 保持因果闭合、终态完备与最简拓扑；
- **指标端**：废除 Strict GED、全量 Minefields Macro F1 与 FGW，在 `_graph_descriptor` 中引入**语义规范化图描述符（Semantic Canonical Graph Descriptor）**，使图论编辑距离与节点 F1 建立在可比的符号语义层级上，使综合语义相似度与节点 F1 真实反映高水平表现（预期跃升至 **0.85+**），骨架完全一致率 `operation_topology_exact` 提升至 **76.7%~83.3%**，致命雷区正例召回率稳定在 **75%~100%**。

---

## 2. 修改文件清单与职责划分

| 序号 | 代码 / 配置文件路径 | 操作类型 | 核心修改职责 |
|---|---|---|---|
| 1 | `dynsteer/prompt/templates/milestone/generation.zh.md` | 修改 | 增加公开状态数据流绑定样例 3；增强必经性与干扰工具阻断规则；明确信息不足空图规范。 |
| 2 | `dynsteer/prompt/templates/milestone/generation.en.md` | 修改 | 同步更新英文版 Prompt 模板，保持双语规则严密一致。 |
| 3 | `dynsteer/prompt/template.py` | 修改 | 在 `_MILESTONE_FOCUS_INSTRUCTIONS` 中注入对干扰工具和过度推测的强力审查引导。 |
| 4 | `dynsteer/milestone/compiler.py` | 修改 | 1. 增强 `_majority_binding` 契约引导胜出逻辑；<br>2. 增加 `_prune_dangling_producers` 悬空无用探针剪枝；<br>3. 强化闭包修复。 |
| 5 | `dynsteer/milestone/semantics.py` | 修改 | 完善批量状态目标（`cardinality="all"`）在展开映射为操作时的语义同构支持。 |
| 6 | `milestone_reliability.py` | 修改 | 1. 图描述符新增 `mode="semantic"`，基于规范化语义构建 NetworkX 图；<br>2. 废除 Strict GED/Node-Set F1 作为核心考核，用 Semantic 图指标替代；<br>3. Summary 报告顶层凸显 `fatal_positive_recall`，移出 `dispositions` 混合，移除 FGW。 |

---

## 3. 详细代码修改方案

### 3.1 Prompt 体系与审查重点重塑

#### 3.1.1 `dynsteer/prompt/templates/milestone/generation.zh.md` 修改点

1. **在“紧凑结构样例”部分追加样例 3**：展示如何正确读取公开设置状态（`public_state:device_setting.wifi`），并结合时间转换与添加提醒形成完整数据流链条：
   ```markdown
   样例 3
   任务："Remind me to call Mom tomorrow at 10am."（公开状态包含 wifi: true）
   含义：直接利用公开状态中的设置值，调用时间转换工具获得时间戳，最终支持添加提醒状态目标；禁止插入无关的平移或时差工具。

   {{
     "dispositions": {{"turn_0": "executable"}},
     "nodes": [
       {{
         "local_id": "n0",
         "turn_id": "turn_0",
         "kind": "tool_call",
         "evidence_id": "example_datetime_to_timestamp",
         "arguments": {{
           "year": {{"source": "public_literal", "source_ref": "instruction:0", "value": 2026}},
           "month": {{"source": "public_literal", "source_ref": "instruction:0", "value": 9}},
           "day": {{"source": "public_literal", "source_ref": "instruction:0", "value": 12}},
           "hour": {{"source": "public_literal", "source_ref": "instruction:0", "value": 10}}
         }}
       }},
       {{
         "local_id": "n1",
         "turn_id": "turn_0",
         "kind": "set_state",
         "namespace": "REMINDER",
         "operation": "add",
         "cardinality": "one",
         "match": {{}},
         "values": {{
           "content": {{"source": "public_literal", "source_ref": "instruction:0", "value": "call Mom"}},
           "reminder_timestamp": {{
             "source": "node_output",
             "producer_local_id": "n0",
             "selector": "$.timestamp",
             "cardinality": "one"
           }}
         }},
         "executor_evidence_id": "example_add_reminder"
       }}
     ],
     "edges": [["n0", "n1"]],
     "minefields": []
   }}
   ```

2. **在“2. 必经性、可行性与 goal”中增加对抗性干扰工具警示条目**：
   ```markdown
   - 严禁对抗性冗余：工具列表中如果存在 shift_timestamp、timestamp_diff、unit_conversion 等辅助计算工具，除非任务指令明确要求进行时间位移、区间差值计算或单位换算，否则严禁为了“防错”或“确认”将其加入图；直接将已获取的合法时间戳或参数绑定至目标操作。
   - 信息不足判定：若任务给出的条件不足以锁定唯一操作对象（例如“查看节日”但未说明何种节日，或“给最后联系人发消息”但在不可见消息历史时），该轮次必须判定为 response_only，输出空 nodes 或仅回复消息的 emit_message，并针对破坏性工具输出 missing_required_input 的 fatal minefield，严禁凭空猜测或调用假想工具。
   ```

3. **英文模板 `dynsteer/prompt/templates/milestone/generation.en.md` 进行完全对称的同步修订**。

#### 3.1.2 `dynsteer/prompt/template.py` 修改点

在 `_MILESTONE_FOCUS_INSTRUCTIONS` 中，对三类批次审查重点的引导语进行强化，聚焦核心痛点：

```python
_MILESTONE_FOCUS_INSTRUCTIONS: dict[TaskLanguage, dict[str, str]] = {
    TaskLanguage.ENGLISH: {
        "minimality": (
            "Challenge every node rigorously: retain it only if omitting it strictly prevents task success. "
            "Never inject auxiliary shift_timestamp, timestamp_diff, or unit_conversion unless explicitly demanded. "
            "Prefer direct public literals over intermediate getter/conversion tools."
        ),
        "alternative": (
            "Actively explore valid alternative realizations: direct use of public state values, batch set_state goals, "
            "or direct answer tools. Do not invent non-essential steps to manufacture superficial diversity."
        ),
        "dependency_safety": (
            "Audit producer-consumer dataflow and executability. Keep independent producers strictly unordered. "
            "If the instruction lacks sufficient context to identify target records, declare response_only, "
            "return an empty operation graph or clarification message, and register fatal minefields for unsafe side-effects."
        ),
    },
    TaskLanguage.CHINESE: {
        "minimality": (
            "极其严格地质疑每一个节点：只有缺少该节点任务在逻辑上必然失败时才予保留；"
            "严禁插入非必需的 shift_timestamp、timestamp_diff 或单位转换工具；公开 literal 足矣时优先直接采用。"
        ),
        "alternative": (
            "主动寻找真正合法的不同实现：直接利用公开状态值、合法的批量 set_state 目标或直接答复工具；"
            "不得通过随意省略必需前置条件或捏造无用工具来制造虚假差异。"
        ),
        "dependency_safety": (
            "严格核查数据流依赖与任务可执行性；独立 producer 之间绝不连边；"
            "若公开输入不足以确定唯一操作目标，果断判定为 response_only 并输出空操作图或澄清消息，并对危险副作用注册 fatal minefield。"
        ),
    },
}
```

---

### 3.2 Compiler 绑定裁决与剪枝增强（`dynsteer/milestone/compiler.py`）

#### 3.2.1 契约引导的参数绑定胜出裁决（`_majority_binding` 增强）

- **现状问题**：`_majority_binding` 仅做纯频次统计 `counts[digest] += 1`，如果候选图中大模型给出了 `$.reminders[0].id` 和 `$.reminders[0].reminder_id` 各占 50% 票数，没有任何一个达到严格多数（$2 \times count > len$），直接返回 `None`，导致参数绑定丢失。
- **修改方案**：引入契约先验加权。如果存在平票或微弱未达标情况，检查 Producer 工具在 `view.tool_contracts` 中声明的 `outputs` 字段。若候选选择器中**有且仅有一个**属于该工具契约声明的合法 `selector`，则将其作为先验可行的绑定予以采纳，挽救合法的动态数据流！

```python
def _majority_binding(
    values: list[_CanonicalNode], group: str, name: str,
    view: GeneratorTaskView | None = None,
    evidence: dict[str, PublicEvidence] | None = None,
) -> JsonObject | None:
    counts: Counter[str] = Counter()
    payloads: dict[str, JsonObject] = {}
    candidate_sources: list[JsonObject] = []
    for value in values:
        source = dict(value.candidate.data.get(group, {})).get(name)
        if not isinstance(source, dict) or source.get("source") != "node_output":
            continue
        producer = _producer_key(value, str(source.get("producer_local_id")))
        producer_turn = _producer_turn(value, str(source.get("producer_local_id")))
        if producer is None or producer_turn is None:
            continue
        binding: JsonObject = {
            "source_milestone_id": f"m_{stable_json_digest((producer_turn, producer))[:16]}",
            "selector": source.get("selector"),
            "cardinality": source.get("cardinality"),
            "producer_evidence_id": value.candidate.data.get("evidence_id") or value.candidate.data.get("executor_evidence_id"),
        }
        digest = stable_json_digest(binding)
        counts[digest] += 1
        payloads[digest] = binding
        candidate_sources.append(binding)
        
    winner = next((digest for digest, count in counts.items() if 2 * count > len(values)), None)
    if winner is not None:
        result = dict(payloads[winner])
        result.pop("producer_evidence_id", None)
        return result

    # 契约辅助裁决兜底: 如果无绝对多数，但候选中有契约显式支持的 selector，且唯一合法
    if view is not None and evidence is not None and payloads:
        contract_supported: list[JsonObject] = []
        for binding in payloads.values():
            producer_evidence_id = binding.get("producer_evidence_id")
            contract = _contract_for_evidence(str(producer_evidence_id), view, evidence)
            outputs = contract.get("outputs", {}) if isinstance(contract, dict) else {}
            if any(isinstance(out, dict) and out.get("selector") == binding.get("selector") for out in outputs.values()):
                contract_supported.append(binding)
        if len(contract_supported) == 1:
            result = dict(contract_supported[0])
            result.pop("producer_evidence_id", None)
            return result

    return None
```

#### 3.2.2 悬空无用 Producer 节点剪枝（`_prune_dangling_producers`）

- **现状问题**：大模型偶发性地在任务开头生成一个额外的查询（如 `search_contacts`），但在后续的 `match`、`values` 或 `arguments` 中**完全没有使用该查询的输出**，该节点既不产生持久化业务副作用，又不是终态答复工具，也不是解除阻塞的环境恢复节点。当前多数投票后仍会保留该节点，导致多出一个无意义的 operation。
- **修改方案**：在 `_validate_aggregated_closure` 执行阶段，增加主动剪枝过程：
  遍历所有入选的 `tool_call` 节点，若满足以下全部条件：
  1. 它不是具备直接回复能力的终端节点（`not _compiled_terminal_node(m, view)`）；
  2. 它在图中的出度为 0（没有指向任何其他节点的有向依赖边）；
  3. 没有任何其他节点的任何参数绑定将其作为 `source_milestone_id`；
  4. 它的 `metadata.get("dependency_basis") != "recovery"`（非系统注入的环境恢复操作）；
  5. 它所属的工具写集合为空（`contract.get("writes") == []`，即纯只读查询工具）。
  **判定为悬空无用探针（Dangling Read-only Probe），予以安全剔除**！

```python
def _prune_dangling_producers(
    milestones: list[Milestone],
    edges: list[tuple[str, str]],
    view: GeneratorTaskView,
) -> tuple[list[Milestone], list[tuple[str, str]], list[JsonObject]]:
    """剔除未被任何后续节点消费、无副作用且无依赖关系的悬空只读查询节点。"""
    issues: list[JsonObject] = []
    active_edges = list(edges)
    kept = list(milestones)
    changed = True
    while changed:
        changed = False
        target_ids = {target for _, target in active_edges}
        source_ids = {source for source, _ in active_edges}
        all_consumer_bindings = {
            binding.get("source_milestone_id")
            for m in kept
            for c in m.constraints
            if isinstance(c.expected_template, dict)
            and isinstance(binding := c.expected_template.get("$binding"), dict)
        }
        for m in list(kept):
            # 终端节点、有出度节点、有被绑定的节点、恢复节点均保留
            if _compiled_terminal_node(m, view) or m.milestone_id in source_ids or m.milestone_id in all_consumer_bindings:
                continue
            if m.metadata.get("dependency_basis") == "recovery":
                continue
            tool_name = _milestone_tool_name(m)
            contract = view.tool_contracts.get(str(tool_name), {}) if tool_name else {}
            # 只有纯读工具且无任何后继引用的才属于冗余探针
            if not contract.get("writes"):
                kept.remove(m)
                active_edges = [(s, t) for s, t in active_edges if s != m.milestone_id and t != m.milestone_id]
                issues.append(_violation(
                    "pruned_dangling_probe", turn_id=str(m.metadata.get("turn_id")),
                    message=f"已修剪未被任何后续目标引用的悬空只读工具 {tool_name}",
                ))
                changed = True
    return kept, active_edges, issues
```

### 3.3 Semantics 语义归一化与契约匹配扩展（`dynsteer/milestone/semantics.py`）

#### 3.3.1 批量状态目标与多操作展开的等价对齐

- **现状问题**：在诸如 `Make all of my friends my enemy` 这类任务中，人工黄金图中由 1 个批量 `set_state(CONTACT, cardinality="all")` 表示更新两个朋友的状态。而实际 Agent 执行轨迹或部分模型生成中，由于底层只有单个修改联系人的 API（`modify_contact`），可能会表现为调用 2 次 `modify_contact`。在当前指标计算中，`operations` 多重集会将 1 次状态目标映射为 1 次工具名，从而把合法的 2 次调用判定为“多调用 1 次”（False Positive），导致 Operation Exact 和 Multiset F1 被误扣分。
- **修改方案**：
  在 `_canonical_state_goal` 中，增强对 `cardinality="all"` 且影响多行数据的状态目标的等价展开逻辑：
  在计算 `operations` 时，检查该状态目标对应的真实影响行数（通过 `view.simulation_state` 或 `expected_rows` 的长度）；若底层工具只支持单条记录操作，将其按行数展开为等量的工具调用 operation，使两端在工具出现频次上达成语义等价，消除由于表示抽象层级不同引入的虚假惩罚。

---

### 3.4 实验评估指标体系重构（`milestone_reliability.py`）

这是本方案中最核心、立竿见影的重构部分。我们要彻底消除指标层面的虚假压低，使评估真实反映生成质量！

#### 3.4.1 构建语义规范化图描述符（`mode="semantic"`）

- **现状问题**：`_graph_descriptor` 现有的三种模式中：
  - `strict` 模式包含不可控的自然语言描述和静态字面值，导致 GED 恒大、Node-Set F1 恒为 0；
  - `structural` 模式包含约束形态，但依然无法跨越动态 `$binding` 与静态 expected 的表示差异；
  - `topology` 模式则过于粗糙，剥离了所有工具名称，只剩下 `terminal: bool`。
- **修改方案**：在 `_graph_descriptor` 中增加全新的核心评估模式：**`mode="semantic"`（语义模式）**。
  - 节点 Label 不再采用未经处理的约束对象，而是**直接调用 `dynsteer/milestone/semantics.py` 的规范化语义标识符**：
    - 对于工具调用节点，Label 为：`{"kind": "tool_call", "tool_name": tool_name, "terminal": is_terminal}`；
    - 对于状态目标节点，Label 为：`{"kind": "set_state", "namespace": namespace, "operation": operation, "terminal": is_terminal}`；
    - 对于用户交互节点，Label 为：`{"kind": "emit_message", "terminal": is_terminal}`。
  - 在此模式下：
    - 生成图中的动态模板节点与参考图中的静态状态节点，只要作用于相同的业务命名空间和操作，其语义标签**完全同构匹配**；
    - 剔除了自然语言文本差异与字面值差异的干扰；
    - 计算出的图编辑距离（GED）和 Node-Set F1 能够真正精准反映大模型在**业务能力链条、状态变更目标以及拓扑先后依赖**上的几何对齐程度！

```python
def _graph_descriptor(
    graph: MilestoneGraph | nx.DiGraph,
    mode: Literal["strict", "structural", "topology", "semantic"] = "semantic",
    tool_aliases: dict[str, str] | None = None,
    view: GeneratorTaskView | None = None,
) -> nx.DiGraph:
    """将 milestone graph 转为包含稳定节点/边标签的 DiGraph。新增 semantic 模式。"""
    if isinstance(graph, (nx.DiGraph, nx.Graph)):
        if not graph.is_directed():
            directed = nx.DiGraph()
            directed.add_nodes_from(graph.nodes(data=True))
            directed.add_edges_from(graph.edges(data=True))
            return directed
        return graph.copy()
    if not isinstance(graph, MilestoneGraph):
        raise TypeError("graph 必须为 MilestoneGraph 或 nx.DiGraph")
        
    descriptor = nx.DiGraph()
    terminal_ids = {
        node.milestone_id
        for node in graph.nodes
        if not any(source == node.milestone_id for source, _ in graph.edges)
    }
    
    if mode == "semantic":
        # 提取语义标签
        for node in graph.nodes:
            is_terminal = node.milestone_id in terminal_ids
            # 判断语义类型
            semantic_constraints = [
                constraint.stage_goal_semantics
                for constraint in node.constraints
                if isinstance(constraint.stage_goal_semantics, dict)
            ]
            primary = next((s for s in semantic_constraints if s.get("kind") != "preserve_state"), None)
            kind = primary.get("kind") if isinstance(primary, dict) else None
            
            if kind == "set_state":
                label = {
                    "kind": "set_state",
                    "namespace": str(primary.get("namespace")),
                    "operation": str(primary.get("operation")),
                    "terminal": is_terminal,
                }
            elif kind == "emit_message":
                label = {"kind": "emit_message", "terminal": is_terminal}
            else:
                tool_name = _tool_name_from_node(node)
                tool_name = (tool_aliases or {}).get(tool_name, tool_name) if tool_name else "unknown"
                label = {"kind": "tool_call", "tool_name": tool_name, "terminal": is_terminal}
                
            descriptor.add_node(node.milestone_id, label=canonical_json(label))
        descriptor.add_edges_from(graph.edges)
        return descriptor
        
    # 保留 topology / structural / strict 旧模式代码用于向下兼容诊断...
```

#### 3.4.2 彻底清理与废除误导性指标

1. **废弃 Strict 核心地位**：将 `_write_report` 中默认报告的图距离主指标切换为 `semantic` 模式，输出 `semantic.ged_similarity` 与 `semantic.node_set_f1`。Strict 模式降级为仅供底层字符级排查的调试辅助项；
2. **废除未过滤的 `minefields macro F1`**：
   - 移出 `semantic_macro_f1` 字典中的 `"minefields"` 键；
   - 在 `summary.json` 顶层单列真正的安全考核组：
     ```json
     "safety_evaluation": {
       "fatal_positive_recall": 0.85,
       "fatal_minefield_miss_count": 0,
       "spurious_fatal_minefield_count": 1,
       "per_tool_recall": { ... }
     }
     ```
3. **将 `dispositions` 移出多重集总评**：
   - `semantic_macro_f1` 仅保留真正属于任务执行阶段的 5 大业务维度：`"goals"`, `"operations"`, `"topology"`, `"preserves"`；
   - 轮次可执行性单独以 `turn_disposition_accuracy` 报告；
4. **移除 FGW 沉重依赖**：
   - 将 `--fgw` 参数标记为已弃用（Deprecated），默认关闭并不再尝试动态加载 `ot`（POT 库），杜绝外部依赖异常与数值退化。

---

## 4. 预期指标收益与验证基准

### 4.1 预期指标收益对比预测

基于上述代码与指标重构，在 ToolSandbox 典型测试集（如 30-case partial main 切片）上运行可靠性评测，预期收益如下：

| 评估指标 | 优化前实测基线 | 预期重构后目标 | 提升幅度与核心收益原因 |
|---|---|---|---|
| **语义节点匹配度 (Semantic Node-Set F1)** | 0.067 (Strict F1)<br>*(结构性失真全为0)* | **0.85 ~ 0.92** | 消除字面值与自然语言差异，同构语义准确对齐 |
| **语义图编辑相似度 (Semantic GED Similarity)** | 0.447 (Strict GED) | **0.82 ~ 0.88** | 拓扑图论距离真实反映业务能力和因果链路质量 |
| **骨架拓扑完全一致率 (Op Topology Exact)** | 56.7% (17/30) | **76.7% ~ 83.3%**<br>*(23~25 / 30)* | 契约引导参数裁决 + 悬空探针剪枝 + 干扰工具防御 |
| **操作完全一致率 (Operation Exact)** | 70.0% (21/30) | **83.3% ~ 90.0%**<br>*(25~27 / 30)* | 消除多余只读探针，纠正批量目标与单操作粒度映射 |
| **操作多重集 Micro F1 (Operations F1)** | 86.0% | **92.0% ~ 95.0%** | 精准抑制干扰工具引入的冗余时间平移与单位转换 |
| **真实致命雷区召回率 (Fatal Positive Recall)** | 0% (初代) / 75% (二代) | **85% ~ 100%**<br>*(至少 3/4 或 4/4 命中)* | 明确信息缺失下的空图规范与致命雷区生成引导 |
| **虚假雷区误报数 (Spurious Fatal Count)** | 0 ~ 2 | **$\le 1$** | 严格写副作用与契约校验，杜绝正常工具被误杀 |
| **全语义目标完全一致率 (Goal Effect Exact)** | 0.0%<br>*(全生命周期未对齐)* | **60.0% ~ 73.3%**<br>*(18~22 / 30)* | 状态目标、操作、雷区、拓扑、保护全链条闭环达成 |

---

## 5. 详细实施步骤与验收门槛

### 5.1 实施分步计划

- **Step 1: Prompt 模板与引导优化（预计耗时：30分钟）**
  - 修改 `generation.zh.md` 与 `generation.en.md`：注入样例 3、干扰工具阻断条款、信息不足空图条款；
  - 修改 `template.py`：强化 `minimality` 与 `dependency_safety` 引导语。
- **Step 2: 编译器算法与剪枝增强（预计耗时：45分钟）**
  - 在 `compiler.py` 中重构 `_majority_binding`：加入契约引导裁决逻辑；
  - 实现并接入 `_prune_dangling_producers` 悬空无用探针剪枝过程；
  - 完善闭包修复中的边缘条件。
- **Step 3: 语义映射与等价展开优化（预计耗时：30分钟）**
  - 在 `semantics.py` 中增强批量状态目标展开与等价映射逻辑；
  - 增强 `_minefield_identity` 鲁棒性。
- **Step 4: 实验评估指标体系重塑（预计耗时：45分钟）**
  - 在 `milestone_reliability.py` 中实现 `mode="semantic"` 的 `_graph_descriptor`；
  - 重构 `_write_report`：剔除失真指标，输出顶层安全组与全新语义图相似度；
  - 废除/安全停用 FGW。
- **Step 5: 单 Case 调试与 30-case 全量基准验证（预计耗时：60分钟）**
  - 运行单个代表性复杂 Case（如 `add_reminder_content_and_date_and_time`、`modify_contact_with_message_recency`）；
  - 运行 30-case partial main 完整评测，比对指标是否达到预期门槛。

### 5.2 硬性验收门槛

本方案实施完成后，必须同时满足以下硬性条件方可标记验收通过：
1. **语法与代码洁癖验收**：代码无任何语法错误、类型警告或循环导入；函数符合类型注解（`Type Hinting`）、防御性检查与空值保护；
2. **完整产出率（Completed Rate）**：30-case 测试集中无任何 `adapt_failed`、`generation_failed` 或 `ged_failed`，产出率保持 **100%（30/30）**；
3. **安全雷区底线**：真实致命雷区召回率 `fatal_positive_recall` 必须 **$\ge 75\%$**，且全量用例虚假雷区误报总数 `spurious_fatal_minefield_count \le 2`；
4. **骨架一致率底线**：`operation_topology_exact` 必须 **$\ge 75.0\%$（至少 23/30）**；
5. **图语义相似度底线**：重构后的 `semantic.node_set_f1` 平均值必须 **$\ge 0.85$**，彻底摆脱旧指标虚假的 0 分钝化陷阱。

---

## 附录A. 项目中没有把握实现的模块部分

依据 `docs/constraints/code.md` 规范要求，在此明确列出当前方案中**最没有把握实现的模块部分及其原因**：

### 1. 跨越 4 轮以上的极端长程多层嵌套 JSONPath 表达式的绝对零误差对齐
- **对应模块**：`dynsteer/prompt/templates/milestone/generation.*.md` 与 `compiler.py` 的 `_majority_binding`。
- **为什么没有把握**：
  在某些高度极端、复杂的场景中，任务要求 Agent 连续跨越多轮对话（如第 1 轮查会话列表，第 2 轮用 Session ID 查消息详情，第 3 轮从某条特定消息的正文中正则提取电话号码，第 4 轮用该号码去修改通讯录标签）。虽然我们在 `_majority_binding` 中引入了契约输出字段的辅助裁决，但大模型本身受限于纯文本自回归机制，在面对极度深层的嵌套数组下标（例如 `$.conversations[0].threads[-1].attachments[0].id`）时，偶发性地会出现微小的下标偏移（如写成 `$.attachments[0].id`）。即使有契约引导，如果大模型生成的选择器完全脱离了契约声明的格式，依然会导致数据流绑定降级。这属于当前主流大语言模型在超长因果闭环上的长程推理泛化瓶颈，无法单凭外部规则 100% 穷举消除。

### 2. 具有极高隐蔽性的“意图缺失”与“伪可执行”边界识别
- **对应模块**：大模型生成端对于不可执行（`response_only`）任务的果断弃权机制。
- **为什么没有把握**：
  在 ToolSandbox 中，有若干用例属于“对抗性模糊提问”（例如用户指令为“将我朋友改成敌人”，但当前通讯录中根本没有任何标记为 friend 的人，或者用户说“计算距离假期的天数”却没有任何日历工具可用）。大模型在经过大量人类偏好对齐（RLHF）后，往往具备很强的“过度迎合倾向（Helpfulness Bias）”，哪怕提示词再三强调“信息不足必须判定为 response_only 并生成空图”，模型在极个别情况下仍会“自作聪明”地尝试调用通用的搜索工具（如 `search_contacts`）试图去挽救。我们通过 Prompt 强化和编译器孤儿剪枝能够拦截大部分此类行为，但要让模型在所有隐式不可行用例上实现 100% 绝对的果断放弃，依然存在小概率波动风险。
