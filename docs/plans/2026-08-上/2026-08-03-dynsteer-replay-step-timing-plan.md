# DynSTEER-Replay 早停前缀耗时与额外评估耗时统计方案

## 1. 目标与范围

当前 `DYNSTEER_REPLAY` 的 `runtime_metrics.elapsed_seconds` 只表示 replay 评估自身的墙钟耗时；DEFAULT 轨迹没有记录生成各执行批次的耗时，因此 `trajectory_total_latency_ms` 在现有 ToolSandbox 产物中通常为 `0` 且 `trajectory_latency_available=false`。

本方案要实现以下可审计的统计口径：

1. DEFAULT 运行时为每个可观测执行批次记录耗时，并把耗时绑定到该批次产生的轨迹边界。
2. replay 按原始轨迹顺序扫描；发生虚拟早停时，只累计早停边界之前已经纳入 replay 的 DEFAULT 执行耗时，不把 DEFAULT 完整轨迹耗时带入。
3. replay 继续使用既有 `elapsed_seconds` 表示自身 Judge/本地评估墙钟耗时，并新增“DEFAULT 早停前缀耗时”和“合计有效耗时”字段，避免把不同含义的时间混成一个字段。
4. `replay_continue_after_virtual_stop=true` 时，仍然单独计算虚拟早停前缀耗时；继续扫描的剩余轨迹不会污染该指标。
5. 老的、没有耗时元数据的 DEFAULT 轨迹不能伪装成 0 秒；应明确标记不可用，并要求重新运行 DEFAULT 生成带时序信息的轨迹。

本方案只涉及运行时统计、产物 schema、实验汇总和测试，不改变 DynSTEER 的早停判定、Judge 结果、分数或 milestone 语义。

## 2. 统计口径

### 2.1 执行单位

`BaseBenchmarkHarness.advance_case()` 的契约是“推进一个可中断执行批次”，一次调用可能返回多个 raw trajectory step。当前代码没有可靠的单条 raw message 级开始/结束时间，因此本方案先测量整个 **advance batch**，再把 batch 总耗时近似平均分摊到该 batch 的每个 raw step：

- 用 `perf_counter()` 包住一次 `advance_case()` 调用，记录真实耗时，包括 role `respond()`、重试等待和 harness 内部状态整理。
- 一个 batch 返回 `n` 个 raw step 时，用整数商和余数完成毫秒级均匀分摊：`base = batch_latency_ms // n`、`remainder = batch_latency_ms % n`；每个 step 先分配 `base`，再给按轨迹顺序靠前的 `remainder` 个 step 各增加 `1ms`。
- 上述分配保证各 step 的耗时差不超过 `1ms`，并且所有 step 的分摊值之和严格等于 batch 原始总耗时，不会产生累计取整误差。
- 每个 step 的分摊结果写入其 `StepCost.latency_ms`；同时在 `trajectory.raw.execution_timing` 中保存 batch 的 step index 列表、step 数、总耗时和分摊规则，供 replay 审计。
- 该数值是 batch 内的均匀估算值，不应表述为 SDK 真实测得的单 raw message 耗时；但当早停发生在 batch 中间时，可以按已经扫描的 step 数取得成比例的执行耗时，比只绑定 batch 末尾更符合本任务的早停前缀统计目标。

### 2.2 三个核心时间字段

对 `DYNSTEER_REPLAY`，建议保留既有通用字段，并新增以下字段：

| 字段 | 含义 | 计算方式 |
| --- | --- | --- |
| `elapsed_seconds` | replay 评估墙钟耗时（兼容现有含义） | replay recorder 从开始到结束的 `perf_counter` 差值 |
| `default_prefix_execution_latency_ms` | DEFAULT 轨迹从开始到虚拟早停边界的执行耗时（毫秒） | 已扫描 DEFAULT step 的分摊 `latency_ms` 之和 |
| `default_prefix_execution_seconds` | 上述字段的秒数形式 | `default_prefix_execution_latency_ms / 1000` |
| `effective_elapsed_seconds` | 用户要求的有效总耗时 | `elapsed_seconds + default_prefix_execution_seconds` |
| `timing_available` | 是否存在可完整解释该前缀的时序数据 | 新 schema 的 timing records 可覆盖目标前缀时为 `true` |
| `virtual_stop_step_index` | 虚拟早停所在轨迹边界 | 从 replay termination detail 提取；无早停为 `null` |

当没有虚拟早停时，`default_prefix_execution_seconds` 等于 DEFAULT 完整轨迹的执行耗时；当发生早停时，只取早停边界对应的前缀。`default_reference` 仍然只能作为对照元数据，不能参与上述计算。

### 2.3 早停边界规则

replay 当前在每个 raw step 追加后执行 minefield/agent closure 评估，因此统计边界使用触发 termination 的 `step.index`：

- 按 source trajectory 顺序累计 `step.index <= virtual_stop_step_index` 的分摊 `StepCost.latency_ms`；因此早停位于 batch 中间时，会计入该 batch 中已经扫描步骤所占的近似耗时；
- `replay_continue_after_virtual_stop=true` 时，扫描结果可以包含完整轨迹，但前缀耗时仍按 `virtual_stop_step_index` 截止；
- 没有虚拟早停时，计入所有 source timing records；
- 只有缺失 timing record 时才标记不可用，绝不把缺失值当作真实 0 秒。

## 3. 代码修改设计

### 3.1 `dynsteer/harness/model.py`

修改 `HarnessAdvanceResult`，增加可选字段：

```python
execution_latency_ms: int | None = None
```

字段表示本次 `advance_case()` 批次的实际执行耗时，不表示单条 raw step 的耗时。保持 `steps`、`snapshots`、`continue_running` 的现有契约不变，并在 `__post_init__` 中校验该字段为非负整数或 `None`。

在 `BaseBenchmarkHarness` 增加统一的 `timed_advance_case(session)` 公共方法：

1. `started = time.perf_counter()`；
2. 调用既有 `advance_case(session)`；
3. 在成功和异常路径都取得结束时间；
4. 使用 `dataclasses.replace()` 返回带 `execution_latency_ms` 的 `HarnessAdvanceResult`；
5. 不吞掉原始 harness 异常。

这样 DEFAULT 和在线 `evaluate()` 可以共用同一计时边界，不在多个调用方重复实现计时逻辑。需要同步更新 `docs/apis/harness.md`，说明 `timed_advance_case()` 的批次级语义。

### 3.2 `dynsteer/metrics.py`

新增集中式的轨迹 timing helper，避免在 DEFAULT writer 和 evaluator 中复制统计逻辑：

- `append_execution_timing(trajectory, advance)`：
  - 对空 batch 不写 step timing，并累计 `unattributed_execution_seconds`；
  - 对非空 batch 按 raw step 数计算整数商和余数，将 batch 总毫秒数近似平均分配给每个 step；
  - 将各 step 的分摊值分别写入其 `StepCost.latency_ms`，并校验分摊值之和等于 `execution_latency_ms`；
  - 在 `trajectory.raw["execution_timing"]` 追加 `{batch_index, step_indices, first_step_index, last_step_index, raw_step_count, latency_ms, allocation="uniform_ms_with_leading_remainder"}`；
  - 不修改原始 step 的业务字段、actor、event_type 或 content。
- `prefix_execution_timing(trajectory, stop_step_index)`：
  - 读取并校验 `execution_timing` records；
  - 按 source step 顺序和 `stop_step_index` 累加各 step 分摊耗时，返回 `latency_ms`、秒数、已扫描 step 数、涉及 batch 数和是否完整可用；
  - 对旧轨迹返回 `timing_available=false`，而不是返回一个含义不明的 0。
- `build_replay_timing_metrics(source_trajectory, replay_trajectory, termination, elapsed_seconds)`：统一构造本节 2.2 的字段，并计算 `effective_elapsed_seconds`。

保留 `build_runtime_metrics()` 现有的通用 `trajectory_total_latency_ms`，但在其结果中增加 `timing_schema_version` 或等价标志，区分新生成的可解释 timing 与历史产物。`trajectory_latency_available` 继续表示 step cost 是否存在；新增的 `timing_available` 表示前缀是否完整可解释，避免两个概念混淆。

### 3.3 `dynsteer/harness/outputs.py`

在 `write_default_case_outputs()` 的主循环中：

1. 把 `harness.advance_case(session)` 改为 `harness.timed_advance_case(session)`；
2. 在追加 steps 前调用 `append_execution_timing(trajectory, advance)`；
3. 继续使用原有 `trajectory.extend_snapshots()`、`trajectory.append_step()`、AgentStepTracker 和 native result 流程；
4. DEFAULT `trajectory.json` 顶层 raw 写入 `execution_timing`，`raw_summary.json` 的 `runtime_metrics` 同步写入完整轨迹 timing 汇总；
5. 在 `trajectory_output_summary()` 增加 `execution_timing_available`、`execution_batch_count` 和 `unattributed_execution_seconds`。

在 `_build_method_level_summary()` 增加以下方法级聚合字段：

- `average_default_prefix_execution_seconds`
- `average_effective_elapsed_seconds`
- `timing_available_case_count`

其中 DEFAULT 方法没有 replay 前缀字段时应保持 `0`/`null` 的明确缺省，不把 DEFAULT 完整运行时间误填为 replay 前缀时间。

### 3.4 `dynsteer/evaluate/evaluator.py`

#### 在线 `evaluate()`

将主循环的 `harness.advance_case(session)` 改为 `harness.timed_advance_case(session)`，调用 `append_execution_timing()` 后再进入现有 raw step 评估。这样在线评估输出也保留真实执行成本，但不改变其终止策略。

#### `evaluate_replay()`

保持现有 replay 主循环和早停位置不变，仅在结果构造阶段增加 timing 计算：

1. `runtime_metrics = _build_runtime_metrics(...)` 仍然统计 replay evaluator 自身墙钟时间和 replay 轨迹信息；
2. 从 `virtual_termination.termination_detail["virtual_stop_step_index"]` 提取边界；
3. 调用 `build_replay_timing_metrics(source_trajectory=trajectory, replay_trajectory=replay_trajectory, ...)`；
4. 将结果合并进 `runtime_metrics`，并在 `report.metadata["replay_execution"]` 下增加 `timing` 子对象，便于 report、summary、raw_summary 三处审计；
5. `replay_continue_after_virtual_stop=true` 时，`replay_trajectory` 可以完整，但 `default_prefix_execution_seconds` 必须按虚拟停止边界重新计算；
6. `effective_elapsed_seconds` 只在 `timing_available=true` 时提供数值，否则写 `null` 并保留不可用原因。

建议 `replay_execution.timing` 至少包含：

```json
{
  "elapsed_seconds": 12.4,
  "default_prefix_execution_seconds": 8.1,
  "effective_elapsed_seconds": 20.5,
  "timing_available": true,
  "virtual_stop_step_index": 17,
  "source_execution_batch_count": 5,
  "prefix_execution_batch_count": 3,
  "source_full_execution_seconds": 14.7
}
```

`source_full_execution_seconds` 仅用于审计和对比，不参与 `effective_elapsed_seconds`，从而明确防止“Judge + DEFAULT 完整时间”的错误计算。

### 3.5 `dynsteer/model.py` 报告序列化

在 `TrajectoryEvaluationReport.to_summary_dict()` 中补充 replay timing 的常用平铺字段，至少包括：

- `default_prefix_execution_seconds`
- `effective_elapsed_seconds`
- `timing_available`
- `virtual_stop_step_index`

完整的 batch 明细只放在 `runtime_metrics`/`metadata.replay_execution.timing`，避免 summary 顶层膨胀。

### 3.6 `dynsteer/experiment/metrics.py`

`aggregate_efficiency()` 继续使用 `average_elapsed_seconds` 汇总 replay 自身评估墙钟时间，并新增：

- `average_default_prefix_execution_seconds`
- `average_effective_elapsed_seconds`
- `effective_timing_available_case_count`

新增字段仅对 `dynsteer_replay*` 结果有意义；聚合时按字段存在且 `timing_available=true` 过滤，不能把不可用的历史 case 当 0 纳入平均值。`metrics.json` 的 `efficiency` 表和 case 级结果同步保留上述字段。

### 3.7 `dynsteer/experiment/runner.py` 与缓存迁移

历史 DEFAULT `trajectory.json` 没有 `execution_timing`，无法从文件恢复已经过去的真实调用耗时。因此：

1. 在 `existing_case_output()` 或 replay 前的 default output 检查中识别 timing schema 版本；
2. 旧轨迹只允许继续用于功能 replay，但其 replay timing 必须标记 `timing_available=false`、`effective_elapsed_seconds=null`；
3. 为获得完整新统计，使用 `force_eval=True` 重新运行 DEFAULT，再运行 replay；
4. 不尝试用 DEFAULT `elapsed_seconds` 反推 step 时间，也不把 `trajectory_total_latency_ms=0` 解读为真实零耗时；
5. 新产物路径和 `default_reference` 结构保持不变，避免影响已有分数比较。

## 4. API、产物和日志调整

### 4.1 API 文档

更新：

- `docs/apis/harness.md`：说明 `timed_advance_case()`、`execution_timing` schema 和 batch 级耗时归属；
- `docs/apis/evaluate.md`：说明 replay timing、早停边界、`effective_elapsed_seconds` 与 `replay_continue_after_virtual_stop` 的关系；
- `docs/apis/experiment.md`：说明实验 `metrics.json.efficiency` 新增的平均耗时字段及历史产物不可用语义。

### 4.2 结构化日志

只在 case 完成时输出一条 INFO 级中文结构化日志，字段包括 `benchmark`、`case_id`、`method`、`timing_available`、`virtual_stop_step_index`、`elapsed_seconds`、`default_prefix_execution_seconds` 和 `effective_elapsed_seconds`。旧轨迹不可用时输出一次 WARNING，说明需要 `force_eval=True`，避免在 raw step 循环内刷屏。

## 5. 测试与验收

新增 `tests/test_replay_timing.py`（调用现有 public evaluator/output 接口，不为测试新建业务接口），至少覆盖：

1. 一个 DEFAULT batch 一个 step：该 step 获得完整 batch 耗时，replay 无早停时有效总耗时等于 prefix + `elapsed_seconds`。
2. 一个 DEFAULT batch 多个 raw step：batch 耗时按 step 数近似平均分摊，各 step 差值不超过 `1ms`，且分摊总和严格等于 batch 总耗时。
3. replay 在一个多 step batch 的中间早停：prefix 包含早停 step 及其之前 step 的分摊耗时，不包含同 batch 后续 step 的分摊耗时。
4. 三个 batch 在第二个 batch 结束处虚拟早停：prefix 只包含前两个 batch，完整 source timing 不进入有效总耗时。
5. 早停后继续扫描完整轨迹：`replay_trajectory` 是全长，但 `default_prefix_execution_seconds` 仍截止虚拟停止 index。
6. 无虚拟早停：prefix 等于完整 source timing。
7. 历史轨迹缺少 `execution_timing`：`timing_available=false`、`effective_elapsed_seconds=null`，不能输出 0 秒假数据。
8. `advance_case()` 抛出异常：计时包装不吞掉原始异常，也不写入半成品 timing record。
9. `metrics.json` 聚合：不可用 case 不参与有效总耗时平均值，`average_elapsed_seconds` 仍保持 evaluator 墙钟语义。
10. 运行一次现有 ToolSandbox case，检查 `trajectory.json`、`raw_summary.json`、case `summary.json`、method `metrics.json` 四层字段一致。

验收命令建议：

```powershell
uv run pytest tests/test_replay_timing.py -q
uv run pytest -q
```

并使用一个强制重新生成的 case 做人工核对：

```text
effective_elapsed_seconds
  = elapsed_seconds
  + default_prefix_execution_seconds
```

同时确认它不等于 `elapsed_seconds + source_full_execution_seconds`（除非虚拟早停没有发生）。

## 6. 实施顺序

1. 先扩展 `HarnessAdvanceResult` 和 `timed_advance_case()`，补充单元测试。
2. 在 DEFAULT/在线 evaluate 接入 timing annotation，并确认新生成 trajectory 的 schema。
3. 实现 replay 前缀计算和 runtime/report metadata 字段。
4. 更新实验层聚合和 API 文档。
5. 先运行 timing 定向测试，再运行全量测试和一次 `force_eval=True` 的 ToolSandbox 样例。
6. 检查旧产物兼容路径、日志、未使用 import 和中间文件；不提交 git commit，由用户决定提交。

## 7. 风险、取舍与不做的事情

- `advance_case()` 级耗时是当前 harness 能可靠提供的最小执行单位；不能声称它是单条 raw message 的精确耗时。
- `effective_elapsed_seconds` 是“复现 DEFAULT 前缀执行成本后再进行 replay 评估”的实验统计量，不是一次真实在线运行的严格 wall-clock 预测；并发、缓存和服务端排队仍可能造成差异。
- 不把原 DEFAULT native evaluation 的最终判定时间计入 replay prefix；它不是轨迹步骤成本，也不应泄漏进 replay 判定。
- 不修改 `default_reference` 对照语义，不修改早停策略、不修改 Judge cache key 和分数计算。

## 附录A. 项目中没有把握实现的模块部分

1. **ToolSandbox role.respond() 与 raw rows 的一对多关系**：当前 SDK 返回消息的边界由外部依赖控制，无法仅凭 `advance_case()` 返回值恢复每条 raw message 的真实耗时。因此本方案把 advance batch 的总耗时近似平均分摊给 batch 内各 raw step；这能支持 batch 中间早停的前缀估算，但不等于真实的单 raw message 耗时。若论文或最终报告必须要求真实的单 raw message 级耗时，需要进一步检查 ToolSandbox SDK 是否提供事件时间戳或 callback。
2. **历史运行产物的回溯**：已经完成的 DEFAULT 运行没有保存每次 `advance_case()` 的开始/结束时刻，无法无损补算。方案只能明确标记不可用并要求 `force_eval=True` 重跑，不能保证用户保留的旧产物全部自动迁移。
3. **跨 provider 的时间可比性**：`perf_counter()` 能准确测量本地进程墙钟时间，但 provider 服务端排队、网络代理和并发调度不一定能拆分。方案把 replay evaluator 墙钟、LLM 调用耗时和 DEFAULT 前缀执行耗时分栏展示，不对跨 provider 的绝对可比性作额外假设。
