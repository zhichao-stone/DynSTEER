# DynSTEER ready frontier 无进展 patience 终止策略代码修改方案

> 生成日期：2026-07-13  
> 当前状态：方案设计，不落地代码。  
> 本次调整：将原“逐 ready milestone 无进展 watch”调整为“ready frontier 前沿组无进展 watch”，并把默认 patience 从 3 调整为 8。

## 1. 问题判断

当前运行期逻辑允许如下路径：

1. 前序 milestone 已 matched。
2. 下一个 required milestone 进入 ready。
3. 每个新增 step 都会尝试评分，但该 milestone 一直没有达到 `PASS`。
4. 如果没有后续 blocked milestone、fatal minefield 或已结算阶段失败，主循环不会因为“ready 但未匹配”立即终止。
5. agent 可以自然执行到结束。
6. 自然结束后，`pending_required_stage_results(...)` 才生成 synthetic pending stage。

这个问题需要在运行期更早识别。但原方案中“为每个 ready milestone 单独计算 patience”的做法存在明显误伤风险。

典型反例：

```text
m0 -> m1
m0 -> m2
...
m0 -> m9
m1 -> m10
m2 -> m10
...
m9 -> m10
```

完成 m0 后，m1 到 m9 会同时进入 ready 队列。此时不能要求 m1 到 m9 都在很短窗口内同步提升。Agent 很可能先处理 m1，再处理 m2，最后处理 m9；未被当前处理的 ready milestone 分数暂时不变是正常现象。

因此，本方案不再追踪“每个 ready milestone 是否单独提升”，而是追踪 **ready frontier 前沿组整体是否还在推进**。

## 2. 核心设计原则

本方案不采用以下两类主策略：

- 固定 step budget：不同 milestone 复杂度差异大，固定步数容易误伤长流程。
- 固定 attempt budget：复杂目标可能需要多次尝试，只看次数不看质量变化不合理。

推荐策略：

```text
对当前 required ready frontier 作为一个整体观察：
只要前沿组中任意 required ready milestone 出现有效分数提升或被成功匹配，就视为前沿仍在推进；
只有整个前沿组连续若干次评分观察都没有任何有效提升，才判定 ready frontier 停滞。
```

这意味着：

- m1 到 m9 同时 ready 时，不要求它们都提升。
- 只要 m1 在提升，整个 frontier 就不算停滞。
- m1 matched 后，frontier 变为 m2 到 m9，watch 重新基准化。
- 当最终只剩 m9 或剩余一组 milestone 长时间都无提升时，才触发 no-progress 终止。

## 3. 概念定义

### 3.1 Ready Frontier

在任一运行期 step 上：

```text
ready_frontier = 当前 ready_before 中 required=True 且尚未 matched 的 milestone 集合
```

optional milestone 默认不进入 no-progress 终止判断，避免非必需分支影响主任务。

### 3.2 Frontier 观察

一次 frontier 观察来自 `analyze_milestone_step(...)` 返回的：

```text
attempt_detail["ready_before"]
attempt_detail["candidate_scores"]
```

只要本次 `candidate_scores` 中包含 ready frontier 成员的结构化 score，就可以更新 frontier watch。该过程复用已有 milestone 评分结果，不增加额外 scorer、LLM 或 benchmark 调用。

### 3.3 Frontier 变化

以下情况都视为 frontier 变化，需要重建 watch 基准，而不是累加 stale：

- 有 milestone matched，ready 集合缩小或产生新的 ready 节点。
- `ready_frontier` 的 milestone id 集合与上一次不同。
- 当前没有 required ready milestone。

这样可以自然处理并行 DAG：

```text
m0 完成后 frontier = {m1...m9}
m1 matched 后 frontier = {m2...m9}
m2 matched 后 frontier = {m3...m9}
```

每次 frontier 变化都说明任务图仍在推进，不应继承旧 frontier 的停滞计数。

## 4. 状态字段

建议将 watch 状态从“每个 milestone 一个 watch”改为“当前 frontier 一个 watch，内部记录每个成员的 best score”。

```python
@dataclass
class ReadyMilestoneProgress:
    milestone_id: str
    best_score: float
    best_status: str
    best_boundary_step_index: int | None
    last_improved_step_index: int


@dataclass
class ReadyFrontierProgressWatch:
    frontier_key: tuple[str, ...]
    ready_since_step_index: int
    last_observed_step_index: int
    last_frontier_improved_step_index: int
    stale_frontier_observation_count: int = 0
    frontier_observation_count: int = 0
    milestone_progress: dict[str, ReadyMilestoneProgress] = field(default_factory=dict)
```

字段含义：

| 字段 | 含义 |
|---|---|
| `frontier_key` | 当前 required ready frontier 的稳定 key，即排序后的 milestone id tuple。 |
| `ready_since_step_index` | 当前 frontier 首次出现的 step index。 |
| `last_observed_step_index` | 最近一次观察该 frontier 的 step index。 |
| `last_frontier_improved_step_index` | 最近一次前沿组内任一 milestone 有效提升的 step index。 |
| `stale_frontier_observation_count` | 当前 frontier 连续无任何有效提升的观察次数。 |
| `frontier_observation_count` | 当前 frontier 累计观察次数，仅用于诊断。 |
| `milestone_progress` | 各 ready milestone 的历史最高分和最近提升位置。 |

`RuntimeEvaluationState` 建议新增：

```python
ready_frontier_progress_watch: ReadyFrontierProgressWatch | None = None
```

## 5. 有效提升定义

新增配置：

```python
ready_frontier_min_delta: float = 0.02
```

对 frontier 中任一 milestone，如果本次候选分数满足：

```text
current_score >= best_score + ready_frontier_min_delta
```

则视为本次 frontier 有有效提升：

```text
更新该 milestone 的 best_score
更新 last_frontier_improved_step_index
stale_frontier_observation_count = 0
```

如果本次 frontier 内没有任何 milestone 达到有效提升：

```text
stale_frontier_observation_count += 1
```

注意：这是 **frontier 级别** 的 stale，不是每个 milestone 独立 stale。

## 6. 默认 patience 调整

原方案：

```python
milestone_progress_patience: int = 3
```

问题：

- 容易被理解成“3 步内没有提升就停”。
- 对并行 ready frontier 不合理。
- 即便改成 frontier 级别，3 也偏激进，真实 benchmark 中可能存在多个非评分推进 step。

调整后建议：

```python
ready_frontier_patience: int = 8
```

解释：

- 这是“同一个 ready frontier 连续 8 次评分观察没有任何有效提升”，不是“每个 milestone 8 步内必须提升”。
- 任一 frontier 成员提升或 matched，都会重置计数或重建 watch。
- 大型并行 DAG 或长工具链任务可以通过环境变量调大，例如 12、16 或关闭该策略。

## 7. 终止条件

新增配置：

```python
stop_on_ready_frontier_no_progress: bool = True
ready_frontier_patience: int = 8
ready_frontier_min_delta: float = 0.02
```

当同一个 required ready frontier 满足：

```text
stale_frontier_observation_count >= ready_frontier_patience
and frontier 中没有 milestone 达到 PASS
```

触发运行期策略终止。

termination code：

```text
ready_frontier_no_progress:{most_promising_milestone_id}
```

其中 `most_promising_milestone_id` 是当前 frontier 中 `best_score` 最高的 milestone。若 frontier 只有一个 milestone，也可以复用更直观的：

```text
milestone_no_progress:{milestone_id}
```

termination detail：

```json
{
  "code": "ready_frontier_no_progress:m1",
  "ready_milestone_ids": ["m1", "m2", "m3"],
  "most_promising_milestone_id": "m1",
  "ready_since_step_index": 12,
  "last_observed_step_index": 25,
  "last_frontier_improved_step_index": 17,
  "stale_frontier_observation_count": 8,
  "frontier_observation_count": 13,
  "patience": 8,
  "min_delta": 0.02,
  "milestone_progress": {
    "m1": {
      "best_score": 0.55,
      "best_status": "fail",
      "best_boundary_step_index": 17,
      "last_improved_step_index": 17
    }
  }
}
```

## 8. 并行 DAG 场景下的行为

以用户提出的图为例：

```text
m0 -> m1...m9 -> m10
```

行为应为：

1. m0 matched 后：

```text
frontier = {m1,m2,m3,m4,m5,m6,m7,m8,m9}
```

2. Agent 开始处理 m1，m1 分数提升：

```text
frontier 有进展，stale_frontier_observation_count = 0
```

3. m2 到 m9 没有提升：

```text
不单独惩罚，因为 frontier 作为整体仍在推进
```

4. m1 matched：

```text
frontier 变化为 {m2...m9}，watch 重建
```

5. Agent 继续处理 m2：

```text
只要 m2 或任一剩余 ready milestone 有提升，就不停止
```

6. 如果某一刻剩余 frontier 长期没有任何成员提升：

```text
连续 8 次 frontier 观察无提升后，触发 ready_frontier_no_progress
```

这解决了“难道指望 3 步内 m1 到 m9 都有分数提升”的问题：**不需要，提升任一成员即可表示前沿仍在推进。**

## 9. 运行期流程接入点

### 9.1 主循环顺序

建议在 `DynSTEEREvaluator.evaluate(...)` 中复用现有 milestone 分析结果。

核心顺序：

```python
analysis = analyze_milestone_step(...)

if analysis.hit is not None:
    decision = self._evaluate_checkpoint(...)
    if decision.checkpoint is not None:
        reset_ready_frontier_watch(state)
        state = decision.next_state
        ...
        continue

if analysis.attempt_detail is not None:
    no_progress_detail = update_ready_frontier_progress_watch(
        task_case=task_case,
        state=state,
        attempt_detail=analysis.attempt_detail,
        thresholds=self._thresholds,
        stop_enabled=config.stop_on_ready_frontier_no_progress,
        patience=config.ready_frontier_patience,
        min_delta=config.ready_frontier_min_delta,
    )
    state.match_attempts.append(analysis.attempt_detail)
    if no_progress_detail is not None:
        stop_case(...)
        break

if analysis.blocked_detail is not None:
    ...
```

### 9.2 与语义 WARN 复判的顺序

如果当前存在 WARN semantic review 候选，应优先完成已有 `_evaluate_checkpoint(...)` 复判逻辑。

建议规则：

```text
语义候选 accepted：
    milestone matched，frontier watch 重建或清空。

语义候选 rejected：
    本次 attempt 可进入 frontier watch；
    如果没有有效提升，算作一次 stale frontier observation。
```

这样避免把“正在复判的语义候选”提前判定为 no-progress。

## 10. 配置方案

### 10.1 HarnessRunConfig 新增字段

在 `dynsteer/harness/model.py` 的 `HarnessRunConfig` 增加运行期已解析字段：

```python
stop_on_ready_frontier_no_progress: bool = True
ready_frontier_patience: int = 8
ready_frontier_min_delta: float = 0.02
```

校验：

```python
if self.ready_frontier_patience < 1:
    raise ValueError("ready_frontier_patience 必须大于 0")
if self.ready_frontier_min_delta < 0:
    raise ValueError("ready_frontier_min_delta 不能为负数")
```

其中 `ready_frontier_patience` 的有效值由环境变量注入；保留在 `HarnessRunConfig` 中是为了让 evaluator 在运行期统一读取，不需要在主循环里直接访问 `os.environ`。

### 10.2 环境变量配置

新增环境变量：

```powershell
$env:DYNSTEER_READY_FRONTIER_PATIENCE = "8"
```

读取规则：

```text
未设置或为空：使用默认值 8
设置为非整数：启动配置加载时报错
设置为小于 1：启动配置加载时报错
```

如果需要对大型并行 DAG 更保守：

```powershell
$env:DYNSTEER_READY_FRONTIER_PATIENCE = "16"
```

建议在 `dynsteer/harness/config.py` 增加：

```python
def load_ready_frontier_patience_from_env(env: Mapping[str, str] | None = None) -> int:
    """从环境变量读取 ready frontier 无进展 patience。"""
```

并在 `load_harness_run_configs(...)` 创建每个 `HarnessRunConfig` 时传入：

```python
ready_frontier_patience=load_ready_frontier_patience_from_env()
```

这样同一批 benchmark run 使用同一个 patience，避免在 `run_configs.json` 中逐项重复配置，也便于实验脚本统一调整。

### 10.3 run_configs.json 可选字段

`run_configs.json` 不再配置 `ready_frontier_patience`。仍可保留以下两个字段作为 per-run 行为开关和灵敏度设置：

```json
{
  "stop_on_ready_frontier_no_progress": true,
  "ready_frontier_min_delta": 0.02
}
```

如果需要保持旧行为：

```json
{
  "stop_on_ready_frontier_no_progress": false
}
```

## 11. 代码修改范围

### 11.1 `dynsteer/evaluate/runtime.py`

新增：

- `ReadyMilestoneProgress` dataclass。
- `ReadyFrontierProgressWatch` dataclass。
- `RuntimeEvaluationState.ready_frontier_progress_watch` 字段。
- `update_ready_frontier_progress_watch(...)`。
- `ready_frontier_no_progress_termination_reason(...)`。

建议函数签名：

```python
def update_ready_frontier_progress_watch(
    task_case: TaskCase,
    state: RuntimeEvaluationState,
    attempt_detail: JsonObject,
    thresholds: ThresholdConfig,
    stop_enabled: bool,
    patience: int,
    min_delta: float,
) -> JsonObject | None:
    """更新 required ready frontier 的无进展追踪状态，必要时返回终止详情。"""
```

### 11.2 `dynsteer/evaluate/evaluator.py`

在主循环 `analysis` 后接入 frontier watch。

触发终止时：

- 设置 `terminated_by_policy = True`。
- 设置 `termination_code = detail["code"]`。
- 设置 `termination_reason`。
- 设置 `termination_detail = detail`。
- 调用 `harness.stop_case(session, termination_reason)`。
- 输出 `evaluator_policy_stop` 结构化日志。

当 checkpoint 成功接受 milestone 后，清空当前 frontier watch：

```python
state.ready_frontier_progress_watch = None
```

原因是 matched 会改变 ready frontier，下一次 attempt 会重新建立基准。

### 11.3 `dynsteer/evaluate/diagnostics.py`

扩展 final diagnostics：

- pending milestone 可包含 `ready_frontier_watch` 摘要。
- 若因 no-progress 提前终止，可在 raw summary 的 `termination_detail` 中保留完整 frontier 信息。

为了减少改动，第一版可以只保证 `termination_detail` 完整，不强行改 `build_final_milestone_diagnostics(...)` 结构。

### 11.4 `dynsteer/harness/model.py`

新增配置字段和校验。

### 11.5 `dynsteer/harness/config.py`

从环境变量读取 `DYNSTEER_READY_FRONTIER_PATIENCE`，从 `run_configs.json` 读取其余可选字段，并统一传入 `HarnessRunConfig`。

建议新增：

```python
def _optional_bool(data: dict[str, Any], key: str, default: bool) -> bool:
    ...

def _optional_positive_int(data: dict[str, Any], key: str, default: int) -> int:
    ...

def _optional_non_negative_float(data: dict[str, Any], key: str, default: float) -> float:
    ...

def load_ready_frontier_patience_from_env(env: Mapping[str, str] | None = None) -> int:
    ...
```

### 11.6 `docs/apis/evaluate.md`

补充：

- ready frontier no-progress 策略语义。
- 新增配置字段。
- 新增 termination code：

```text
ready_frontier_no_progress:{most_promising_milestone_id}
milestone_no_progress:{milestone_id}
```

## 12. 详细伪代码

### 12.1 更新 frontier watch

```python
def update_ready_frontier_progress_watch(
    task_case,
    state,
    attempt_detail,
    thresholds,
    stop_enabled,
    patience,
    min_delta,
):
    if not stop_enabled:
        return None

    graph = task_case.milestone_graph or MilestoneGraph()
    required_ids = {node.milestone_id for node in graph.nodes if node.required}
    matched_ids = set(state.matched_settlements)

    ready_ids = tuple(sorted(
        str(item)
        for item in attempt_detail.get("ready_before", [])
        if str(item) in required_ids and str(item) not in matched_ids
    ))

    if not ready_ids:
        state.ready_frontier_progress_watch = None
        return None

    candidate_by_id = ready_candidate_scores(attempt_detail)
    observed_candidates = {
        milestone_id: candidate_by_id[milestone_id]
        for milestone_id in ready_ids
        if milestone_id in candidate_by_id and isinstance(candidate_by_id[milestone_id].get("score"), dict)
    }
    if not observed_candidates:
        return None

    watch = state.ready_frontier_progress_watch
    if watch is None or watch.frontier_key != ready_ids:
        state.ready_frontier_progress_watch = build_new_frontier_watch(
            ready_ids=ready_ids,
            observed_candidates=observed_candidates,
            step_index=int(attempt_detail["step_index"]),
        )
        return None

    watch.frontier_observation_count += 1
    watch.last_observed_step_index = int(attempt_detail["step_index"])
    frontier_improved = False

    for milestone_id, candidate in observed_candidates.items():
        score_payload = candidate["score"]
        current_score = clamp(float(score_payload.get("score", 0.0)))
        current_status = str(score_payload.get("status") or "unknown")
        boundary = candidate.get("boundary")
        boundary_step_index = (
            int(boundary.get("step_index"))
            if isinstance(boundary, dict) and isinstance(boundary.get("step_index"), int)
            else None
        )

        progress = watch.milestone_progress.get(milestone_id)
        if progress is None:
            watch.milestone_progress[milestone_id] = ReadyMilestoneProgress(
                milestone_id=milestone_id,
                best_score=current_score,
                best_status=current_status,
                best_boundary_step_index=boundary_step_index,
                last_improved_step_index=watch.last_observed_step_index,
            )
            frontier_improved = True
            continue

        if current_score >= progress.best_score + min_delta:
            progress.best_score = current_score
            progress.best_status = current_status
            progress.best_boundary_step_index = boundary_step_index
            progress.last_improved_step_index = watch.last_observed_step_index
            frontier_improved = True

    if frontier_improved:
        watch.last_frontier_improved_step_index = watch.last_observed_step_index
        watch.stale_frontier_observation_count = 0
        return None

    watch.stale_frontier_observation_count += 1
    if watch.stale_frontier_observation_count < patience:
        return None

    if any(progress.best_score >= thresholds.pass_threshold for progress in watch.milestone_progress.values()):
        return None

    return ready_frontier_no_progress_detail(watch, patience, min_delta)
```

### 12.2 新建 frontier watch

```python
def build_new_frontier_watch(ready_ids, observed_candidates, step_index):
    milestone_progress = {}
    for milestone_id, candidate in observed_candidates.items():
        score_payload = candidate["score"]
        milestone_progress[milestone_id] = ReadyMilestoneProgress(
            milestone_id=milestone_id,
            best_score=clamp(float(score_payload.get("score", 0.0))),
            best_status=str(score_payload.get("status") or "unknown"),
            best_boundary_step_index=candidate["boundary"].get("step_index"),
            last_improved_step_index=step_index,
        )
    return ReadyFrontierProgressWatch(
        frontier_key=ready_ids,
        ready_since_step_index=step_index,
        last_observed_step_index=step_index,
        last_frontier_improved_step_index=step_index,
        stale_frontier_observation_count=0,
        frontier_observation_count=1,
        milestone_progress=milestone_progress,
    )
```

## 13. 测试计划

新增或扩展测试：

1. `test_ready_frontier_watch_resets_when_any_member_improves`
   - frontier = `{m1,m2,...,m9}`。
   - 只有 m1 分数提升。
   - 不触发 no-progress。

2. `test_ready_frontier_watch_does_not_require_all_parallel_milestones_to_improve`
   - m2 到 m9 分数长期不变。
   - 只要 m1 或任一成员有提升，frontier stale 计数保持清零。

3. `test_ready_frontier_watch_rebuilds_after_match`
   - m1 matched 后 frontier 从 `{m1...m9}` 变成 `{m2...m9}`。
   - watch 重建，不继承旧 stale。

4. `test_ready_frontier_watch_stops_when_no_member_improves`
   - 同一 frontier 连续 `ready_frontier_patience` 次没有任何成员提升。
   - 返回 `ready_frontier_no_progress` 详情。

5. `test_single_ready_milestone_no_progress_uses_milestone_code`
   - frontier 只有 m3。
   - 触发 `milestone_no_progress:m3`。

6. `test_ready_frontier_watch_ignores_optional_milestones`
   - optional ready milestone 不进入 frontier watch。

7. `test_ready_frontier_patience_loads_from_env`
   - 环境变量 `DYNSTEER_READY_FRONTIER_PATIENCE=16` 时，`HarnessRunConfig.ready_frontier_patience == 16`。

8. `test_ready_frontier_patience_env_rejects_invalid_value`
   - 环境变量为非整数或小于 1 时，配置加载报错。

9. `test_run_config_loads_ready_frontier_options`
   - run config 能读取 `stop_on_ready_frontier_no_progress` 和 `ready_frontier_min_delta`，但不读取 `ready_frontier_patience`。

## 14. 兼容性与输出影响

- 默认开启 `stop_on_ready_frontier_no_progress=True` 会改变部分 case 的运行期行为：原本自然结束后 pending fail 的 case，可能提前终止。
- 该策略不改变 milestone 分数算法、不改变 stage score、不改变动态权重公式。
- 触发提前终止时，最终报告仍可通过现有 pending required 机制体现未完成 milestone。
- raw summary 需要保留 `termination_detail`，解释是哪个 ready frontier 停滞。

## 15. 实施步骤

1. 修改 `HarnessRunConfig`，新增 ready frontier no-progress 配置字段。
2. 修改 `harness/config.py`，从环境变量读取 `DYNSTEER_READY_FRONTIER_PATIENCE`，并读取 run config 中除 patience 外的其他新字段。
3. 修改 `RuntimeEvaluationState`，新增 `ready_frontier_progress_watch`。
4. 在 `runtime.py` 实现 `update_ready_frontier_progress_watch(...)` 与 termination detail 构造。
5. 在 `evaluator.py` 主循环接入 frontier watch，checkpoint 成功后清空 watch。
6. 更新 `docs/apis/evaluate.md`。
7. 增加单元测试覆盖并行 frontier、单节点 frontier、frontier 重建、optional 忽略和配置读取。
8. 运行：

```powershell
uv run pytest tests -q
```

## 16. 验收标准

- m0 完成后 m1 到 m9 同时 ready 时，不要求 m1 到 m9 都在 patience 窗口内提升。
- 只要 m1 到 m9 中任一 required ready milestone 有有效提升，frontier 不触发 no-progress。
- m1 matched 后 watch 随 frontier 变化重建，不继承旧 stale。
- 同一 frontier 连续 8 次评分观察没有任何成员有效提升时，触发 no-progress 终止。
- 将 `DYNSTEER_READY_FRONTIER_PATIENCE` 设置为 16 后，同一 frontier 连续 16 次无有效提升才触发 no-progress。
- 单个 required ready milestone 的行为退化为原本想要的“单 milestone 无进展 patience”。
- optional milestone 不触发 no-progress 终止。
- termination detail 能解释 ready frontier、最有希望的 milestone、最高分、最后提升 step 和 stale 次数。

## 附录A. 项目中没有把握实现的模块部分

当前最需要结合真实数据校准的是默认参数：

- `ready_frontier_patience=8`
- `ready_frontier_min_delta=0.02`

没有完全把握的原因是：不同 benchmark 的 milestone score 曲线可能差异很大。ToolSandbox 的 snapshot constraint 可能出现“长时间 0 分，最后一步直接 PASS”的跳变；如果这种场景很多，过低的 patience 仍可能误判停滞。因此代码实现本身可控，但默认参数需要通过真实 run 数据回放校准。

第二个需要实测确认的是哪些 boundary 应计入“评分观察”。当前方案复用 `candidate_scores`，这通常与每个 step 的 candidate boundary 对齐；如果真实数据中存在大量对 milestone 分数没有意义的 agent/user 消息，可进一步只统计 `tool_result`、`state_update`、`artifact_update`、`final` 或与 semantic message 相关的 boundary。

第三个需要实测确认的是语义 WARN 复判与 frontier watch 的顺序。当前语义 WARN 候选存在“配置 standard judge 但 effective level 仍可能是 cheap”的细节，实际落地时需要用测试固定行为，避免把本该复判的语义候选提前判为 no-progress。
