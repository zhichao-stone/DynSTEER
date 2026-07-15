# DynSTEER ready frontier 增量化与 Evaluator 简化方案

> 生成日期：2026-07-13  
> 当前状态：方案设计，不落地代码。  
> 本次修订：更正 ready frontier 算法设计。frontier 维护不能在每次 match 后重新扫描全图；必须初始化时扫描一次，之后按直接后继增量更新。

## 0. 更正说明

上一版方案存在一个关键算法误区：为了回应“不要维护重复 graph index”的意见，我把方案改成了“只在 milestone matched 后刷新 frontier，刷新时可扫描 graph”。这仍然没有解决 `ready_milestones(...)` 的高复杂度问题。

正确方向应为：

```text
初始化 case 时允许扫描 graph 一次；
之后每次 milestone matched，只遍历该 milestone 的直接后继；
ready frontier、directly blocked frontier、remaining predecessor count 都作为运行期状态增量维护；
step 热路径和 match 更新路径都不再扫描全图。
```

这里不是恢复一个泛化的 `build_milestone_graph_index(...)`。方案不维护一套完整 graph 副本，也不反复校验 graph；只在 `MilestoneFrontierState` 中保存 frontier 增量更新必需的最小邻接信息：

- `dependents_by_id`
- `remaining_predecessor_count`
- `ready_ids`
- `blocked_candidate_ids`
- `milestone_by_id` 或等价的 milestone 引用表
- `order_by_id`，仅用于稳定输出顺序

这些字段是 frontier 运行期状态的一部分，不是额外业务模型。

## 1. 问题核查结论

### 1.1 ready 判定的真实性能问题

当前 `dynsteer/evaluate/milestone.py::ready_milestones(...)` 的复杂度来自全图扫描：

```python
for node in graph.nodes:
    if all(source in matched_ids for source in node.dependency_predecessor_ids):
        ready.append(node)
```

更严重的是，`analyze_milestone_step(...)` 每个 step 都内联了类似逻辑，遍历所有未 matched milestone，并重复计算 `missing_predecessors`。这会让运行期复杂度接近：

```text
O(step_count * milestone_count)
```

如果仅改成“每次 match 后扫描一次全图”，复杂度会变成：

```text
O(match_count * milestone_count)
```

在 milestone 较多、match 较多的 benchmark 中仍不可接受。因此本方案要求：**初始化之后不再用全图扫描维护 ready frontier**。

### 1.2 evaluator.py 的主要冗余和重复实现

当前 `dynsteer/evaluate/evaluator.py` 约 `979` 行，`evaluate(...)` 约 `370` 行。主要重复点如下：

| 类型 | 位置 | 问题 |
| --- | --- | --- |
| 策略终止副作用重复 | fatal minefield、blocked milestone、ready frontier no-progress、stage failure 分支 | 多次重复设置 termination 字段、调用 `harness.stop_case(...)`、输出 `evaluator_policy_stop` 日志。 |
| ready frontier no-progress 检查重复 | `analysis.hit is None`、WARN 无 standard judge、WARN rejected 三处 | 同样的 attempt 追加和 no-progress 决策重复。 |
| boundary 重复计算 | step loop 与 `analyze_milestone_step(...)` 内部 | 同一 step 的 boundary 可以只计算一次。 |
| minefield 历史重复扫描 | `_enrich_stage_result(...)`、`_runtime_report(...)`、`_should_stop_after_stage(...)` | 主循环已经增量记录 minefield，后续仍反复全量扫描历史 trajectory。 |
| 同类匹配流程散落 | `evaluate/milestone.py`、`evaluate/minefield.py`、新增 frontier 逻辑 | milestone、minefield、frontier 都属于运行期匹配与诊断逻辑，应集中到同一包。 |
| stage 命名潜在冲突 | 拟新增 `dynsteer/evaluate/stages.py` 与现有 `dynsteer/stage.py` | 两者都和阶段有关，容易形成概念混淆，也不利于阶段相关能力集中。 |

## 2. 修订后的设计原则

1. **frontier 初始化后绝不扫描全图维护 ready**：matched 后只更新当前 milestone 的直接后继。
2. **不新增泛化 graph index**：不设计 `build_milestone_graph_index(...)` 这类看起来和 `MilestoneGraph` 平行的全量索引 API。
3. **frontier 状态保存最小邻接信息**：为了 O(out-degree) 更新，`dependents_by_id`、`remaining_predecessor_count` 必须保存在运行期 frontier state 中。
4. **匹配类能力集中成包**：`frontier.py`、`milestone.py`、`minefield.py` 统一迁入 `dynsteer/evaluate/matching/`。
5. **不新增 `dynsteer/evaluate/stages.py`**：阶段逻辑若需迁出，应集中到 `dynsteer/stage/`，避免 evaluate 下出现相似命名模块。
6. **先消除重复副作用代码**：优先合并 stop/log/no-progress 处理，再拆分文件。
7. **不主动保留旧内部 import 兼容**：项目内部统一改新路径，不额外写只转发旧路径的 wrapper。

## 3. 目标模块结构

### 3.1 matching 包

建议新增：

```text
dynsteer/evaluate/matching/
- __init__.py
- frontier.py   # ready frontier 初始化、O(out-degree) 更新、no-progress watch 所需 ready ids
- milestone.py  # 当前 milestone step 分析、semantic WARN 候选、blocked 诊断
- minefield.py  # minefield boundary 匹配与诊断
```

迁移关系：

| 当前文件 | 新位置 |
| --- | --- |
| `dynsteer/evaluate/milestone.py` | `dynsteer/evaluate/matching/milestone.py` |
| `dynsteer/evaluate/minefield.py` | `dynsteer/evaluate/matching/minefield.py` |
| 新增 frontier 逻辑 | `dynsteer/evaluate/matching/frontier.py` |

`dynsteer/evaluate/__init__.py` 只导出 evaluator 相关入口，不承担 matching 子模块的兼容重导出。

### 3.2 不新增 evaluate/stages.py

本方案取消 `dynsteer/evaluate/stages.py`。

阶段相关逻辑的处理策略：

1. 第一阶段只在 `evaluator.py` 内收缩重复代码，不迁移 stage settlement。
2. 如果收缩后 `evaluator.py` 仍明显过大，再把现有 `dynsteer/stage.py` 升级为 `dynsteer/stage/` 包。
3. 升级后的结构建议为：

```text
dynsteer/stage/
- __init__.py      # 保留 stage_goal_key、stage_start_step_index 等导出
- interval.py      # 当前 stage key / interval 工具
- settlement.py    # 若确需迁出，放 stage settlement / stage report 构造
```

## 4. frontier 增量状态设计

### 4.1 数据结构

建议在 `dynsteer/evaluate/matching/frontier.py` 中定义：

```python
@dataclass
class MilestoneFrontierState:
    milestone_by_id: dict[str, Milestone]
    dependents_by_id: dict[str, tuple[str, ...]]
    remaining_predecessor_count: dict[str, int]
    ready_ids: set[str]
    blocked_candidate_ids: set[str]
    order_by_id: dict[str, int]
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `milestone_by_id` | id 到 milestone 对象的引用表，避免在热路径按 id 扫 graph。 |
| `dependents_by_id` | 每个 milestone 的直接后继 id，用于 matched 后 O(out-degree) 更新。 |
| `remaining_predecessor_count` | 每个未完成 milestone 还剩多少直接前驱未 matched。 |
| `ready_ids` | 当前 ready frontier。 |
| `blocked_candidate_ids` | 已靠近当前执行前沿、但仍缺前驱的候选，用于低成本 predecessor gap 诊断。 |
| `order_by_id` | graph.nodes 原始顺序，保证输出稳定。 |

注意：

- 这些字段不是为了重复表达 `MilestoneGraph`，而是为了维护 frontier 所必需的运行期状态。
- `milestone_by_id` 保存对象引用，不复制 milestone 内容。
- `dependents_by_id` 只保存直接后继，不保存额外派生图分析。

### 4.2 初始化

新增：

```python
def initialize_milestone_frontier(
    graph: MilestoneGraph,
    matched: dict[str, HarnessStageSettlement],
) -> MilestoneFrontierState:
    """初始化 milestone frontier 运行期状态。"""
```

复杂度：

```text
O(|V| + |E|)
```

这是允许的，因为每个 case 只执行一次。

初始化规则：

1. 单次遍历 `graph.nodes` 构造 `milestone_by_id`、`order_by_id`。
2. 单次遍历每个 milestone 的 `dependency_predecessor_ids` 构造 `dependents_by_id`。
3. 计算每个 milestone 的 `remaining_predecessor_count`。
4. `remaining_predecessor_count == 0` 且未 matched 的节点进入 `ready_ids`。
5. `blocked_candidate_ids` 初始为空；如果已有预置 matched，可把 matched 节点的未 ready 后继加入 blocked candidates。

### 4.3 matched 后推进

新增：

```python
def advance_milestone_frontier(
    frontier: MilestoneFrontierState,
    matched_milestone_id: str,
    matched: dict[str, HarnessStageSettlement],
) -> None:
    """在 milestone matched 后原地推进 frontier。"""
```

复杂度：

```text
O(out_degree(matched_milestone_id))
```

推进规则：

1. 从 `ready_ids` 和 `blocked_candidate_ids` 移除 `matched_milestone_id`。
2. 遍历 `dependents_by_id[matched_milestone_id]`。
3. 对每个后继：
   - 若后继已 matched，跳过。
   - `remaining_predecessor_count[successor_id] -= 1`。
   - 若计数归零：加入 `ready_ids`，并从 `blocked_candidate_ids` 移除。
   - 若计数仍大于零：加入 `blocked_candidate_ids`。

这里不会扫描 `graph.nodes`。

### 4.4 稳定输出

新增：

```python
def ready_milestones(frontier: MilestoneFrontierState) -> tuple[Milestone, ...]:
    """返回按 graph 原始顺序排列的 ready milestone。"""


def blocked_candidate_milestones(frontier: MilestoneFrontierState) -> tuple[Milestone, ...]:
    """返回按 graph 原始顺序排列的 blocked 诊断候选。"""
```

如果担心函数只是字段读取，可以不新增函数，直接在 `analyze_milestone_step(...)` 内用 `_ordered_milestones(...)` 私有 helper。

## 5. milestone step 分析改造

当前 `analyze_milestone_step(...)` 从 graph 自行推导 ready。

建议改为：

```python
def analyze_milestone_step(
    task_case: TaskCase,
    trajectory: Trajectory,
    step: TrajectoryStep,
    boundary: Boundary,
    matched: dict[str, HarnessStageSettlement],
    frontier: MilestoneFrontierState,
    scorer: GeneralScorer | None = None,
    context: ScoringContext | None = None,
) -> MilestoneStepAnalysis:
```

变化：

- `ready_before` 来自 `frontier.ready_ids`。
- ready candidate 只遍历 `ready_milestones(frontier)`。
- blocked candidate 只遍历 `blocked_candidate_milestones(frontier)`。
- boundary 由 evaluator 主循环传入，函数内部不再重复计算。
- 函数内不再构造 `node_by_id`。
- 函数内不再扫描 `graph.nodes`。

### 5.1 阶段起点计算优化

当前 `stage_start_for_milestone(...)` 通过 milestone id 从 graph 中查节点。建议替换为：

```python
def stage_start_for_ready_milestone(
    milestone: Milestone,
    matched: dict[str, HarnessStageSettlement],
    trajectory: Trajectory,
) -> tuple[str, int]:
    """基于已持有的 milestone 对象计算阶段起点。"""
```

这样 ready candidate 分析不需要再按 id 查 graph。

### 5.2 no-progress watch 调整

当前 `update_ready_frontier_progress_watch(...)` 通过 `task_case.milestone_graph` 过滤 required ready id。

建议改为：

```python
def update_ready_frontier_progress_watch(
    state: RuntimeEvaluationState,
    ready_required_ids: tuple[str, ...],
    attempt_detail: JsonObject,
    thresholds: ThresholdConfig,
    stop_enabled: bool,
    patience: int,
    min_delta: float,
) -> JsonObject | None:
    """根据当前 required ready frontier 更新无进展 watch。"""
```

`ready_required_ids` 由 `frontier.ready_ids` 中的 milestone.required 过滤得到，不再在 watch 函数内部扫描 graph。

## 6. evaluator.py 简化方案

### 6.1 集中策略终止副作用

新增：

```python
def _apply_policy_stop(
    self,
    harness: BaseBenchmarkHarness,
    session: object,
    case_id: str,
    task_case: TaskCase,
    decision: RuntimeEvaluationDecision,
    default_reason: str,
) -> RuntimeEvaluationDecision:
    """执行策略终止副作用并输出结构化日志。"""
```

替代重复代码：

```python
termination_code = ...
termination_reason = ...
termination_detail = ...
terminated_by_policy = True
harness.stop_case(...)
logger.warning(...)
```

### 6.2 集中 attempt 追加与 no-progress 检查

新增：

```python
def _record_attempt_and_check_no_progress(
    self,
    config: HarnessRunConfig,
    state: RuntimeEvaluationState,
    attempt_detail: JsonObject | None,
) -> RuntimeEvaluationDecision | None:
    """记录 milestone attempt，并返回可能的 ready frontier no-progress 终止决策。"""
```

替代当前三处重复：

- no hit 普通 attempt。
- WARN 候选但没有 standard judge。
- WARN 复判 rejected。

### 6.3 拆出单 step 处理

新增：

```python
def _evaluate_step(
    self,
    config: HarnessRunConfig,
    task_case: TaskCase,
    trajectory: Trajectory,
    state: RuntimeEvaluationState,
    step: TrajectoryStep,
    scorer: GeneralScorer,
) -> RuntimeEvaluationDecision | None:
    """处理单个新增 step，返回可能的策略终止决策。"""
```

职责：

1. 追加 step。
2. 构造 scoring context。
3. 计算 boundary。
4. 增量扫描 minefield。
5. 调用 `matching.milestone.analyze_milestone_step(...)`。
6. 处理 PASS、WARN semantic review、blocked、no-progress。
7. milestone 成功 matched 后调用 `advance_milestone_frontier(...)`。

`evaluate(...)` 保留 case 生命周期编排，不再塞入全部 step 细节。

### 6.4 复用增量 minefield 状态

主循环已经维护：

- `state.minefield_matches`
- `state.max_minefield_score`
- `state.fatal_minefield`

建议：

- `_enrich_stage_result(...)` 改用 state 中的 minefield 聚合值。
- `_should_stop_after_stage(...)` 改用 state 中的 minefield 聚合值。
- `_runtime_report(...)` 直接使用 `state.minefield_matches`。
- `evaluate_minefields(...)` 保留公开诊断能力，但不再作为主循环 stage enrich/report 的默认路径。

## 7. 冗余代码处理清单

| 代码 | 处理建议 |
| --- | --- |
| `analyze_milestone_step(...)` 每 step 扫描 `graph.nodes` | 删除，改为遍历 `frontier.ready_ids` 与 `blocked_candidate_ids`。 |
| `ready_milestones(graph, matched)` | 删除或改为基于 `MilestoneFrontierState` 返回 ready；不得扫描 graph。 |
| `build_milestone_graph_index(...)` | 不新增。 |
| `stage_start_for_milestone(...)` 按 id 查 graph | 改为基于已持有 `Milestone` 对象计算。 |
| evaluator 中三处 no-progress 处理 | 合并为 `_record_attempt_and_check_no_progress(...)`。 |
| evaluator 中多处 stop/log 处理 | 合并为 `_apply_policy_stop(...)`。 |
| evaluator 与 milestone 分析重复计算 boundary | 主循环计算一次并传入。 |
| stage enrich/report/stop 中 minefield 全量扫描 | 改为读取运行期增量 minefield state。 |
| 拟新增 `dynsteer/evaluate/stages.py` | 取消。必要时升级 `dynsteer/stage.py` 为 `dynsteer/stage/` 包。 |

## 8. 分阶段实施步骤

### P0. 行为保护测试

新增或扩展测试：

1. 初始化 frontier 与旧逻辑输出 ready 集合一致。
2. milestone matched 后只遍历直接后继即可更新 ready 集合。
3. 并行 DAG 中 m0 -> m1...m9 -> m10，m1 matched 后只更新 m1 的后继，不扫描 m2...m9 全图。
4. `analyze_milestone_step(...)` 的 `attempt_detail["ready_before"]` 与旧逻辑一致。
5. direct blocked candidate 命中时仍生成 `milestone_predecessor_gap`。
6. fatal minefield、blocked、no-progress、stage failure 都走统一 stop/log helper。
7. WARN semantic review 的 accepted/rejected/skipped 行为不变。

建议测试文件：

```text
tests/test_milestone_frontier.py
tests/test_evaluator_policy_stop.py
```

### P1. 创建 matching 包并迁移文件

修改：

- 新增 `dynsteer/evaluate/matching/__init__.py`
- 移动 `dynsteer/evaluate/milestone.py` 到 `dynsteer/evaluate/matching/milestone.py`
- 移动 `dynsteer/evaluate/minefield.py` 到 `dynsteer/evaluate/matching/minefield.py`
- 新增 `dynsteer/evaluate/matching/frontier.py`

步骤：

1. 更新 evaluator 与测试中的 import。
2. 不保留旧 `dynsteer/evaluate/milestone.py`、`dynsteer/evaluate/minefield.py` 中转文件。
3. `matching/__init__.py` 只做本包必要导出，不把所有符号全部塞进去。

### P2. 实现 frontier 增量状态

修改：

- `dynsteer/evaluate/matching/frontier.py`
- `dynsteer/evaluate/runtime.py`
- `dynsteer/evaluate/evaluator.py`

步骤：

1. 新增 `MilestoneFrontierState`。
2. 新增 `initialize_milestone_frontier(graph, matched)`，只在 case 初始化调用。
3. 新增 `advance_milestone_frontier(frontier, matched_milestone_id, matched)`，match 后 O(out-degree) 更新。
4. `RuntimeEvaluationState` 增加 `milestone_frontier` 字段。
5. evaluator 初始化 state 时初始化 frontier。
6. milestone 成功 matched 后推进 frontier。

### P3. 改造 milestone step 分析

修改：

- `dynsteer/evaluate/matching/milestone.py`
- `dynsteer/evaluate/evaluator.py`

步骤：

1. `analyze_milestone_step(...)` 接收 `boundary` 和 `frontier`。
2. 删除函数内 graph 全量 ready 判定。
3. ready candidate 遍历 `ready_milestones(frontier)`。
4. blocked candidate 遍历 `blocked_candidate_milestones(frontier)`。
5. 删除每 step 构造 `node_by_id`。
6. 用 `stage_start_for_ready_milestone(...)` 替代按 id 查 graph。

### P4. 合并 evaluator 策略终止路径

修改：

- `dynsteer/evaluate/evaluator.py`

步骤：

1. 新增 `_apply_policy_stop(...)`。
2. 新增 `_record_attempt_and_check_no_progress(...)`。
3. fatal minefield、blocked、no-progress、stage decision 都返回 `RuntimeEvaluationDecision`。
4. 主循环只在一个位置更新 `terminated_by_policy` 和 termination 字段。

### P5. 拆出 `_evaluate_step(...)`

修改：

- `dynsteer/evaluate/evaluator.py`

步骤：

1. 将 per-step 逻辑移动到 `_evaluate_step(...)`。
2. `evaluate(...)` 保留 start/advance/finalize/teardown 生命周期。
3. 所有 break/continue 行为用 `RuntimeEvaluationDecision | None` 表达。

### P6. 复用 minefield 增量状态

修改：

- `dynsteer/evaluate/evaluator.py`

步骤：

1. `_enrich_stage_result(...)` 增加 state 参数。
2. `_should_stop_after_stage(...)` 改为读取 state。
3. `_runtime_report(...)` 改为读取 `state.minefield_matches`。
4. `evaluate_minefields(...)` 保留为公开诊断工具。

### P7. 阶段逻辑是否迁出

触发条件：

- P1-P6 后 `dynsteer/evaluate/evaluator.py` 仍超过 `650` 行，或 `evaluate(...)` 仍超过 `160` 行。

处理：

1. 不新增 `dynsteer/evaluate/stages.py`。
2. 若确需迁出阶段逻辑，先将 `dynsteer/stage.py` 升级为 `dynsteer/stage/` 包。
3. `interval.py` 保存现有 stage key / interval 工具。
4. `settlement.py` 保存 stage settlement / report 构造。
5. `dynsteer/stage/__init__.py` 保留当前 `from dynsteer.stage import ...` 的导出能力，减少调用点大面积改动。

## 9. 验收标准

性能验收：

- `initialize_milestone_frontier(...)` 之外，ready frontier 维护不扫描 `graph.nodes`。
- 每次 milestone matched 后只遍历该 milestone 的直接后继。
- 每 step 不扫描 `graph.nodes` 推导 ready。
- 每 step blocked 诊断不扫描所有 blocked milestone。
- 同一 step 的 boundary 只计算一次。
- stage enrich/report/stop 不重复全量扫描历史 minefield。

代码体积验收：

- `dynsteer/evaluate/evaluator.py` 从约 `979` 行降到 `650` 行以下。
- `DynSTEEREvaluator.evaluate(...)` 从约 `370` 行降到 `160` 行以下。
- `matching/milestone.py::analyze_milestone_step(...)` 降到 `90` 行以内。
- `dynsteer/evaluate` 根目录下不再散落 `milestone.py`、`minefield.py`、`frontier.py` 三个同类文件。

行为验收：

- `uv run pytest tests -q` 全部通过。
- ready frontier no-progress 终止策略仍生效。
- WARN semantic review 的 accepted/rejected/skipped 行为不变。
- pending required stage 仍能基于 `match_attempts` 生成最终诊断。
- `raw_summary["termination_detail"]` 内容不丢失。

复杂度回归检查：

```powershell
rg -n "for .* in graph.nodes|ready_milestones\\(|candidate_boundary_for_current_step" dynsteer/evaluate
```

期望：

- `matching/frontier.py` 中仅初始化允许扫描 graph。
- final diagnostics 等非热路径允许扫描 graph。
- `matching/milestone.py::analyze_milestone_step(...)` 不应扫描 graph。
- 单 step 主流程中 `candidate_boundary_for_current_step(...)` 只出现一次。

## 10. 不做事项

- 不新增 `build_milestone_graph_index(...)`。
- 不在 match 后通过全图扫描刷新 frontier。
- 不新增 `dynsteer/evaluate/stages.py`。
- 不修改 milestone / constraint 的评分语义。
- 不删除 `evaluate_minefields(...)` 公开诊断能力。
- 不为了保留旧内部 import 写中转 wrapper。
- 不主动 git commit。

## 附录A. 项目中没有把握实现的模块部分

1. **blocked_candidate_ids 的诊断范围**

   当前实现会每 step 扫描所有 blocked milestone，因此可以发现 agent 直接完成深层未来 milestone 的情况。增量方案只把已靠近执行前沿的后继加入 `blocked_candidate_ids`，能覆盖常见前驱断裂，但可能减少深层跳跃的即时诊断。如果真实 benchmark 中深层跳跃很多，需要后续设计低频全图诊断，而不是放回每 step 热路径。

2. **frontier.ready 很大时的 scorer 成本**

   增量 frontier 能消除全图 ready 判定成本，但如果某些 case 的 ready frontier 本身非常大，scorer 调用仍然多。需要用真实 run 统计 `len(frontier.ready_ids)` 分布。

3. **minefield 增量状态与最终报告一致性**

   从当前代码看 minefield 是 boundary 级扫描，适合增量记录。但如果未来 minefield 依赖最终状态或跨 step 聚合，报告阶段完全复用增量 state 可能不够，需要测试固定。

4. **将 `dynsteer/stage.py` 升级为包的迁移成本**

   该操作能解决 stage 逻辑集中问题，但会触及所有 `from dynsteer.stage import ...` 的调用路径。可以通过 `dynsteer/stage/__init__.py` 保持导出，但文件级迁移仍需要谨慎执行。

## 附录B. 方案自检

- [x] 方案保存于 `docs/plans`。
- [x] 仅修改方案，不落地代码。
- [x] 更正 frontier 维护算法，不再 match 后全图扫描。
- [x] 保留 matching 包集中化建议。
- [x] 取消 `dynsteer/evaluate/stages.py`。
- [x] 取消 `build_milestone_graph_index(...)`，但保留 frontier 必需的最小邻接状态。
- [x] 明确列出 evaluator 冗余和重复实现。
- [x] 包含 `## 附录A. 项目中没有把握实现的模块部分`。
