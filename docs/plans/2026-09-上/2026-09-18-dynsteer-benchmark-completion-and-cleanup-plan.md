# DynSTEER Benchmark 收尾代码修改方案

本文只描述代码、实验配置和测试修改。文档文案不在本方案范围内。

## 1. SkillsBench 缺失 score

### `dynsteer/adapter/base.py`

修改 `BenchmarkDefaultResult`：

```python
@dataclass(frozen=True)
class BenchmarkDefaultResult:
    score: float | None
    raw: JsonObject = field(default_factory=dict)
    metrics: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.score is not None and (self.score < 0.0 or self.score > 1.0):
            raise ValueError("Default score 必须位于 [0, 1]")
```

`to_dict()` 结构不变，`score` 允许输出 `null`。不新增 `has_score`、`score_available` 之类的派生字段。

### `dynsteer/adapter/skillsbench/utils/result.py`

`native_result_summary()` 中 score 缺失分支改为：

```python
score: float | None
score_value = attempt.get("score")
if score_value is None:
    if status == "completed":
        raise ValueError("completed SkillsBench 结果必须包含 score")
    score = None
    reward_available = False
else:
    if isinstance(score_value, bool) or not isinstance(score_value, (int, float)):
        raise ValueError("SkillsBench score 必须是数字且不能是 bool")
    score = float(score_value)
    if score < 0.0 or score > 1.0:
        raise ValueError("SkillsBench score 必须位于 [0, 1]")
    reward_available = True
```

返回对象的 `"score"` 直接使用 `score`。其余 `correct`、status flags、reward 一致性校验保持不变。

### `dynsteer/adapter/skillsbench/harness.py`

`SkillsBenchHarness._build_run_data()` 删除：

```python
score = float(summary["score"]) if summary["reward_available"] else 0.0
```

改为：

```python
score = summary["score"] if summary["reward_available"] else None
```

并把 `BenchmarkDefaultResult(score=score, ...)` 直接传入结果。`raw.reward_available` 已表达可用性，不重复新增字段。

## 2. ToolSandbox usage 缺失

### `dynsteer/adapter/toolsandbox/utils/usage.py`

`ProviderUsageRecorder.take_delta()` 无 pending records 时返回 `None`：

```python
records, self._pending_records = self._pending_records, []
if not records:
    return None
```

其余任一 record 不可用时继续返回 `None`。`summary()` 保持现有 request、usage response、missing response、missing agent target 和 total 字段。

### `dynsteer/adapter/toolsandbox/harness.py`

`ToolSandboxHarness._assign_agent_usage()` 改为三分支：

```python
for index, step in enumerate(steps):
    if index != token_position:
        step.cost.tokens = 0
    elif delta is None:
        step.cost.tokens = None
    else:
        step.cost.tokens = delta.total_tokens
```

保留现有 `delta is not None and delta.total_tokens and not agent_outbound_positions` 时调用 `note_missing_agent_target()` 的逻辑。语义固定为：非 agent outbound 是 `0`，缺失批次的首条 agent outbound 是 `None`，有效批次写真实 total。`build_runtime_metrics()` 不改。

## 3. Judge endpoint 配置

### 五份实验配置

修改以下文件的 `judge_profiles.fixed-judge`：

- `data/experiments/2026-09-15-main.json`
- `data/experiments/2026-09-15-ablation.json`
- `data/experiments/2026-09-15-intervention.json`
- `data/experiments/swe-bench-pro_partial_main.json`
- `data/experiments/skillsbench_partial_main.json`

删除：

```json
"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"
```

新增：

```json
"base_url_env": "QWEN_BASE_URL"
```

`provider`、`model`、`api_key_env` 保持不变。

### `dynsteer/experiment/config.py`

`_judge_config()` 改为统一校验：

```python
def _judge_config(profiles: dict[str, JsonObject], profile_name: str | None) -> JsonObject:
    if profile_name is None:
        return {}
    if profile_name not in profiles:
        raise ValueError(f"judge profile 不存在: {profile_name}")
    profile = dict(profiles[profile_name])
    for forbidden in ("api_key", "base_url"):
        if forbidden in profile:
            raise ValueError(f"judge profile 禁止配置 {forbidden}")
    api_key_env = optional_str(profile.get("api_key_env"))
    if api_key_env is None:
        raise ValueError("judge profile 必须提供非空 api_key_env")
    profile["api_key_env"] = api_key_env
    base_url_env = optional_str(profile.get("base_url_env"))
    if "base_url_env" in profile:
        if base_url_env is None:
            raise ValueError("judge profile 的 base_url_env 不能为空")
        profile["base_url_env"] = base_url_env
    return profile
```

`optional_str` 已在该文件导入。`dynsteer/llm/factory.py::build_llm_from_config()` 不改，它已经支持通过 `base_url_env` 读取环境变量。

## 4. 介入结果与后置归因

### `dynsteer/experiment/model.py`

`ExperimentCaseResult` 在 `termination_detail` 后新增两个字段：

```python
interventions: tuple[JsonObject, ...] = ()
strata: JsonObject = field(default_factory=dict)
```

`to_index_dict()` 新增：

```python
"interventions": [dict(item) for item in self.interventions],
"strata": dict(self.strata),
```

不改 `ExperimentCaseResult` 的相等语义和数据类顺序之外的既有字段。

### `dynsteer/experiment/runner.py`

`_case_result_from_output()` 读取 raw summary 并构造分层：

```python
raw_interventions = summary.get("interventions")
if raw_interventions is not None and not isinstance(raw_interventions, list):
    raise ValueError("summary.interventions 必须是数组")
if any(not isinstance(item, dict) for item in raw_interventions or []):
    raise ValueError("summary.interventions 项必须是对象")
raw_categories = task_case.metadata.get("categories", [])
strata = {
    "task_types": sorted({item.value for item in task_case.task_types}),
    "safety_categories": (
        sorted({str(item) for item in raw_categories})
        if isinstance(raw_categories, list) else []
    ),
}
```

构造 `ExperimentCaseResult` 时新增：

```python
interventions=tuple(dict(item) for item in raw_interventions or []),
strata=strata,
```

`default`、replay 和 online case 都写入 `strata`；`interventions` 在非 guided case 中自然为空 tuple。

### `dynsteer/evaluate/evaluator.py`

`_send_guidance()` 重构为一次 append。新增 helper：

```python
def _trigger_milestone_id(self, decision: RuntimeEvaluationDecision) -> str | None:
    if decision.stage_result is not None and decision.stage_result.milestone_id:
        return decision.stage_result.milestone_id
    detail = decision.termination.termination_detail or {}
    if isinstance(detail, dict):
        for key in ("milestone_id", "most_promising_milestone_id"):
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None

def _intervention_record(
    self,
    decision: RuntimeEvaluationDecision,
    state: RuntimeEvaluationState,
    trigger_stage_id: str,
    trigger_milestone_id: str | None,
    outcome: str,
    message: str | None = None,
) -> JsonObject:
    return {
        "intervention_index": len(state.interventions),
        "trigger_step_index": self._trigger_step_index(decision),
        "trigger_stage_id": trigger_stage_id,
        "trigger_milestone_id": trigger_milestone_id,
        "termination_code": decision.termination.termination_code,
        "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest() if message is not None else None,
        "message_preview": message[:160] if message is not None else None,
        "outcome": outcome,
    }
```

`_send_guidance()` 的分支和 outcome 固定为：

- 达到 `max_interventions`：`limit_reached`；
- 同一 `trigger_stage_id` 已出现：`repeat_stage_suppressed`；
- 消息构造成功但发送成功/失败：`sent` / `send_failed`；
- 消息构造失败：`build_failed`。

所有分支都 append `_intervention_record(...)` 后返回 `outcome == "sent"`。这样 same-stage 重复触发才可审计，而不是只留在局部变量里。

在 `DynSTEEREvaluator.evaluate()` 中，`_finalize_state_reports()` 之后、`_build_runtime_metrics()` 之前调用：

```python
self._enrich_interventions(state)
```

不把该调用加入 `evaluate_replay()`，replay 不产生 guided intervention。

新增私有 enrichment 函数：

```python
def _enrich_interventions(self, state: RuntimeEvaluationState) -> None:
    for item in state.interventions:
        if item.get("outcome") != "sent":
            item.update({
                "post_guidance_stage_id": None,
                "post_guidance_stage_status": None,
                "post_guidance_extra_agent_steps": None,
            })
            continue
        stage = self._post_guidance_stage(item, state)
        trigger_step = item.get("trigger_step_index")
        item.update({
            "post_guidance_stage_id": stage.stage_id if stage is not None else None,
            "post_guidance_stage_status": stage.status.value if stage is not None else None,
            "post_guidance_extra_agent_steps": (
                state.agent_step_tracker.completed_count - int(trigger_step)
                if isinstance(trigger_step, int) else None
            ),
        })
```

新增私有定位函数：

```python
def _post_guidance_stage(
    self,
    intervention: JsonObject,
    state: RuntimeEvaluationState,
) -> StageEvaluationResult | None:
    trigger_stage_id = intervention.get("trigger_stage_id")
    trigger_milestone_id = intervention.get("trigger_milestone_id")
    trigger_step_index = intervention.get("trigger_step_index")
    if not isinstance(trigger_stage_id, str):
        return None
    candidates = [
        settlement for settlement in state.settlements
        if settlement.kind == "milestone"
        and settlement.end_step_index > trigger_step_index
        and (
            settlement.stage_id == trigger_stage_id
            or settlement.milestone_id == trigger_milestone_id
        )
    ] if isinstance(trigger_step_index, int) else []
    for settlement in sorted(candidates, key=lambda item: (item.end_step_index, item.settlement_id)):
        stage = next(
            (
                item for item in state.stage_reports
                if item.stage_id == settlement.stage_id
                and item.metadata.get("synthetic_pending_milestone") is not True
            ),
            None,
        )
        if stage is not None:
            return stage
    return None
```

选择 settlement 而不是只比较 `StageEvaluationResult.stage_id`，是因为触发时的失败 checkpoint 不写入 `stage_reports`，只有后续被接受的 checkpoint 才有 `end_step_index` 可判定“在 trigger 之后”。

## 5. 介入聚合与检验

### `dynsteer/experiment/metrics.py` 常量与字段

在 `DISCRIMINABILITY_THRESHOLDS` 后新增：

```python
INTERVENTION_METHODS = {
    ExperimentMethod.DEFAULT,
    ExperimentMethod.DYNSTEER_EVALUATE,
    ExperimentMethod.DYNSTEER_EVALUATE_GUIDED,
}
STRATUM_MIN_PAIRS = 3
```

介入连续指标只使用三个现有 runtime 字段：

```python
agent_steps <- runtime_metrics["step_count"]
wall_time_seconds <- runtime_metrics["elapsed_seconds"]
agent_tokens <- runtime_metrics["trajectory_total_tokens"]
```

`agent_tokens` 只在 `runtime_metrics["trajectory_cost_available"] is True` 时读取；否则视为缺失。不重复实现成本分摊逻辑。

### 新增 `intervention_audit()`

函数签名：

```python
def intervention_audit(results: Sequence[ExperimentCaseResult]) -> JsonObject:
```

只取 `method == DYNSTEER_EVALUATE_GUIDED` 的结果，按 `(benchmark, model_id, repeat_index)` 分组。每个 group 输出：

```json
{
  "case_count": 0,
  "guidance_fired_case_count": 0,
  "guidance_count": 0,
  "mean_guidance_count": null,
  "guidance_attempt_count": 0,
  "same_stage_repeat_trigger_count": 0,
  "post_guidance_stage_pass_count": 0,
  "post_guidance_stage_pass_rate": null,
  "fatal_minefield_stop_count": 0,
  "fatal_minefield_stop_rate": null,
  "extra_cost_vs_default": {
    "agent_steps": {},
    "wall_time_seconds": {},
    "agent_tokens": {}
  },
  "extra_cost_vs_stop": {
    "agent_steps": {},
    "wall_time_seconds": {},
    "agent_tokens": {}
  }
}
```

计算规则：

1. `guidance_count` 只统计 `outcome == "sent"`；`guidance_attempt_count` 统计全部 intervention record。
2. `same_stage_repeat_trigger_count` 统计 `outcome == "repeat_stage_suppressed"`。
3. `post_guidance_stage_pass_count` 统计 sent intervention 的 `post_guidance_stage_status == "pass"`，分母是 sent intervention 数。
4. fatal minefield 按 guided case 的 `termination_code.startswith("minefield")` 统计。
5. cost delta 使用 guided case 减去同 `_pair_key()` 的 default 或 stop case；缺失配对或缺失 metric 不进入 `_distribution()`。

### 新增 `intervention_tests()`

函数签名：

```python
def intervention_tests(results: Sequence[ExperimentCaseResult]) -> JsonObject:
```

按 `(benchmark, model_id, repeat_index)` 分组，且只处理同时包含 `default`、`stop`、`guided` 的 group。三组比较固定为：

1. `guided_vs_default`: left=`DYNSTEER_EVALUATE_GUIDED`, right=`DEFAULT`；
2. `guided_vs_stop`: left=`DYNSTEER_EVALUATE_GUIDED`, right=`DYNSTEER_EVALUATE`；
3. `stop_vs_default`: left=`DYNSTEER_EVALUATE`, right=`DEFAULT`。

每组的 schema：

```json
{
  "completion": {},
  "agent_steps": {},
  "wall_time_seconds": {},
  "agent_tokens": {}
}
```

实现复用：

- completion 使用现有 `exact_mcnemar(results, left_method=..., right_method=...)`；
- 连续指标使用现有 `_paired_differences()`；
- 显著性使用现有 `_wilcoxon_payload()`；
- bootstrap 使用现有 `_bootstrap_paired_delta_payload()`，参数保持 `seed=202608`、`samples=10000`。

连续指标项 schema：

```json
{
  "paired_case_count": 0,
  "missing_pair_count": 0,
  "statistic": null,
  "p_value": null,
  "all_zero_differences": true,
  "mean": null,
  "ci_lower": null,
  "ci_upper": null
}
```

### 新增 `stratified_intervention_audit()`

函数签名：

```python
def stratified_intervention_audit(results: Sequence[ExperimentCaseResult]) -> JsonObject:
```

分层维度来自 `ExperimentCaseResult.strata`：

- `task_types` 展开到 `task_type` 层；
- `safety_categories` 展开到 `safety_category` 层。

对每个层值过滤 results 后调用 `intervention_tests()`。输出：

```json
{
  "task_type": {
    "stateful_tool_task": {
      "case_count_by_method": {},
      "insufficient_sample": false,
      "tests": {}
    }
  },
  "safety_category": {}
}
```

当任一 completion 比较的 `paired_valid_case_count < STRATUM_MIN_PAIRS` 时，`insufficient_sample=true`，`tests=null`。不合并层，不输出人工 p 值。

### 删除死代码

删除 `bootstrap_paired_delta()` 公共函数。原因：当前无调用方；介入检验直接复用内部 `_bootstrap_paired_delta_payload()`，不需要再保留一层左右序列包装。

### `write_metric_tables()` 接入

在构造 `metrics` 前计算：

```python
methods = {result.method for result in results}
```

只有以下条件成立时，才把三个介入键加入 `metrics`：

```python
if INTERVENTION_METHODS.issubset(methods):
    metrics.update({
        "intervention_audit": intervention_audit(results),
        "intervention_tests": intervention_tests(results),
        "stratified_intervention_audit": stratified_intervention_audit(results),
    })
```

主实验和消融实验不含 online 三臂，因此不出现空的介入固定字段。

## 6. 冗余逻辑清理

### `dynsteer/experiment/metrics.py`

1. 新增共享 progress helper，放在 `_saved_progress()` 附近：

```python
def _stop_progress(default: ExperimentCaseResult, replay: ExperimentCaseResult) -> float | None:
    stop_step = _finite_number(replay.runtime_metrics.get("step_count"))
    total_step = _finite_number(default.runtime_metrics.get("step_count"))
    if stop_step is None or total_step is None or total_step <= 0 or not 0 <= stop_step <= total_step:
        return None
    return stop_step / total_step
```

2. `_saved_progress()` 改为：

```python
progress = _stop_progress(default, replay)
return None if progress is None else 1.0 - progress
```

3. `_stop_detail()` 删除本地 step 校验和除法，改为：

```python
progress = _stop_progress(default, item)
if progress is None and code:
    quality["invalid_stop_progress"] += 1
```

返回对象中的 `progress` 使用该共享值。

4. `task_completion_rates()` 删除重复的 `full_score = sum(...)`，改为：

```python
completed_count = sum(value >= 1.0 for value in scores)
output[...] = {
    "completed_count": completed_count,
    "completion_rate": completed_count / len(scores) if scores else None,
    "full_score_count": completed_count,
    "full_score_rate": completed_count / len(scores) if scores else None,
}
```

输出字段不减少。

5. 删除 `_number()`，把该文件中所有 `_number(...)` 调用替换为 `_finite_number(...)`。两个函数当前都是“布尔除外、有限 float 才返回值”的同一实现，只保留一个。

## 7. 回归测试

新增 `tests/test_benchmark_cleanup.py`，只使用构造对象和临时映射，不访问真实模型、Docker 或外部 benchmark source。

测试项：

1. `test_skillsbench_missing_reward_stays_null`
   - 构造非 completed 且 `attempt.score is None` 的 detail；
   - 断言 `native_result_summary()["score"] is None`；
   - 断言 `SkillsBenchHarness()._build_run_data(detail).default_result.score is None`；
   - 断言 completed 且 score 缺失时抛出 `ValueError`。
2. `test_toolsandbox_usage_missing_batch_stays_null`
   - 构造空 `ProviderUsageRecorder`，断言 `take_delta() is None`；
   - 构造一条 agent outbound 与一条非 agent outbound 的 `TrajectoryStep`；
   - 调用 `ToolSandboxHarness()._assign_agent_usage(steps, None, recorder)`；
   - 断言 agent step `cost.tokens is None`，非 agent step `cost.tokens == 0`。
3. `test_judge_profile_requires_env_endpoint`
   - 构造最小 experiment mapping 和 `judge_profiles`；
   - `base_url` 存在时 `expand_experiment_matrix()` 抛出 `ValueError`；
   - `api_key_env` 缺失时抛出 `ValueError`；
   - `api_key_env` 与 `base_url_env` 均为非空环境变量名时展开成功。
4. `test_intervention_result_and_metrics`
   - 构造同一 `(benchmark, model_id, repeat_index, case_id)` 的 default、stop、guided 结果；
   - guided 的 `strata` 使用 `task_types=["stateful_tool_task"]`、`safety_categories=["SAFETY"]`；
   - guided 的 `interventions` 分别构造 sent、repeat suppressed、post PASS；
   - 断言 index 投影、`intervention_audit()`、`intervention_tests()` 和分层 `insufficient_sample` 行为。
5. `test_shared_progress_and_completion_cleanup`
   - 用有效、越界、缺失 step metrics 构造 default/replay；
   - 断言 `_saved_progress()` 与 `_stop_detail()` 使用同一进度口径；
   - 构造 score `1.0`、`0.5`、`None`，断言 `task_completion_rates()` 的 valid/completed/full count。

不为了测试新增生产代码接口；前两项中的现有私有函数通过所属 harness/recorder 的公共行为路径构造。

## 8. 实施顺序与验证

实施顺序：

1. SkillsBench、ToolSandbox usage、judge 配置校验。
2. `ExperimentCaseResult`/runner 投影与 evaluator intervention enrichment。
3. metrics 介入聚合、检验、分层和接入。
4. 冗余清理与回归测试。

验证命令：

```bash
./.venv-agentcompass/Scripts/python.exe -m compileall dynsteer main.py milestone_reliability.py
./.venv-agentcompass/Scripts/python.exe -m pytest tests/test_benchmark_cleanup.py -q
./.venv-agentcompass/Scripts/python.exe -c "from pathlib import Path; from dynsteer.experiment.config import load_experiment_config, expand_experiment_matrix; paths=['data/experiments/swe-bench-pro_partial_main.json','data/experiments/skillsbench_partial_main.json','data/experiments/2026-09-15-main.json','data/experiments/2026-09-15-ablation.json','data/experiments/2026-09-15-intervention.json']; [expand_experiment_matrix(load_experiment_config(Path(path))) for path in paths]; print('configs_ok')"
```

验收重点：

1. 缺失 SkillsBench reward 在 raw、summary、index 中均为 `null`，不进入完成率分母。
2. 缺失 ToolSandbox usage 的首条 agent outbound 为 `null`，runtime token 也为 `null`。
3. 五份配置展开成功，judge profile 不含明文 endpoint。
4. 介入配置输出三组 McNemar、三组连续指标 Wilcoxon/bootstrap、质量审计和分层敏感性；主/消融 metrics 不含介入字段。
5. `rg "def bootstrap_paired_delta|def _number\\(" dynsteer/experiment/metrics.py` 无结果。
6. 不覆盖旧结果；需要重跑缓存 case 时使用 `--force_eval` 或新 `experiment_id`。

## 附录A. 项目中没有把握实现的模块部分

1. intervention 后置归因：guidance 后可能自然结束、二次 stop、fatal minefield 或没有再次结算。本方案采用同 trigger stage/milestone 的首个后续 accepted settlement 和最终 agent step 差值，只作为可追溯诊断，不声称因果效应。
2. ToolSandbox usage 覆盖率：provider 网关可能不返回 usage，或特殊 role 不经过可注入 HTTP client。代码只能保证缺失保持 `null`，不能保证所有模型矩阵 token 完整。
3. 分层样本阈值：200 case、3 repeats 下部分 task type/safety category 可能样本很少。方案采用 `STRATUM_MIN_PAIRS=3` 显式标记不足并输出 `tests=null`，避免用低功效检验给出结论。
4. 历史产物：已有结果中的伪 0 不会被代码自动重写；新增 index 字段只有在重新生成 case output 或重算 experiment result 后完整出现。
