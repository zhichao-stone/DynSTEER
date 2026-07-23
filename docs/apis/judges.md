# Judges API

## 目标

`dynsteer.judges` 提供 cheap / standard / expensive 三档阶段评估器。Judge 现在支持 partial dimensions：standard 和 expensive 可以只输出本轮目标维度，最终由阶段结算合并回七维报告。

## 包结构

```text
dynsteer/judges/
- base.py          # BaseJudge、LLMJudge、响应校验与结果转换
- cheap.py         # CheapJudge，本地结构化 baseline
- standard.py      # StandardJudge，维度组 LLM judge
- expensive.py     # ExpensiveJudge，逐维专项 LLM judge
- confidence.py    # 逐维 confidence / uncertainty 与多 pass agreement
```

Prompt 相关定义位于：

```text
dynsteer/prompt/
- judge.py         # prompt context 构造
- rubrics.py       # 七维详细 rubric
- templates/judge/standard.{zh,en}.md
- templates/judge/semantic_message_equivalence.{zh,en}.md
- templates/judge/expensive/{dimension}.{zh,en}.md
```

## BaseJudge

```python
class BaseJudge(ABC):
    def evaluate_stage(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        weights: dict[Dimension, float],
        dimensions: Iterable[Dimension] | None = None,
    ) -> StageEvaluationResult:
        ...
```

- `dimensions=None` 表示评估全部七维。
- partial judge 只需要返回 `dimensions` 中出现的分数。
- `StageEvaluationResult.dimension_levels` 记录每个维度实际使用的评估级别，不再维护单一 `evaluator_level`。
- `StageEvaluationResult.dimension_confidence` 与 `dimension_uncertainty` 是一等字段。
- 全局 `judge_confidence` 和全局 `uncertainty` 已移除。

## CheapJudge

`CheapJudge` 不访问网络，使用 milestone score 与阶段质量诊断生成完整七维 baseline。

定位：

- deterministic triage
- 缺 LLM 时的降级报告
- standard/expensive 覆盖前的 baseline

cheap confidence 由结构化规则来源估计。hard fail / missing 是确定性失败信号，不会被解释为高不确定度。

## StandardJudge

`StandardJudge` 对目标维度组进行 standard LLM 评估。

- prompt context 中 `rubrics` 只包含目标维度。
- `required_output.dimension_scores` 只允许目标维度。
- 不向 LLM 暴露 `requested_dimensions` 字段，目标维度由 rubric keys 和 output schema 共同约束。
- 当前 BaseLLM 不提供 token logprobs，因此默认使用 `DYNSTEER_STANDARD_JUDGE_PASSES=3` 的多 pass agreement/entropy 估计逐维 confidence。

输出合并规则：

- 多 pass 分数按维度取均值。
- 多 pass status 取更严重状态。
- confidence 使用离散 score anchor 的一致性估计。

`StandardJudge.review_message_equivalence(target, task_case=...)` 是专用于 `emit_message + semantic_equivalent` 的窄域复判接口。它只要求 LLM 返回：

- `equivalent: bool`
- `confidence: float`
- `reason: str`

该接口固定只调用一次 LLM，不跟随 `DYNSTEER_STANDARD_JUDGE_PASSES`。它不输出阶段维度分，也不参与动态权重更新；运行期只用它决定单条消息约束是否可覆写为通过。prompt 模板位于 `templates/judge/semantic_message_equivalence.{zh,en}.md`，与其他 judge prompt 采用同一套 `PromptTemplate` 多语言渲染机制。

## ExpensiveJudge

`ExpensiveJudge` 对每个目标维度独立执行专项 prompt。

模板位置：

```text
dynsteer/prompt/templates/judge/expensive/
- progress.{zh,en}.md
- state_consistency.{zh,en}.md
- tool_quality.{zh,en}.md
- efficiency.{zh,en}.md
- safety.{zh,en}.md
- interaction_quality.{zh,en}.md
- recovery.{zh,en}.md
```

每个维度固定执行 `DYNSTEER_EXPENSIVE_JUDGE_PASSES=3` 次，使用 agreement/entropy 估计该维 confidence。某一维 expensive 不会导致其他维度也使用 expensive。

## Prompt Context

`build_judge_prompt(...)` 生成的 context 包含：

- `task.task_description`
- `stage_goal`
- `rubrics`
- `interval`
- `constraint_checks`
- `steps`
- `required_output`

`constraint_checks` 是 scorer 辅助证据，字段包含：

- `constraint_id`
- `constraint_goal`
- `satisfied`
- `score`
- `missing`
- `hard`
- `expected_excerpt`
- `actual_excerpt`
- `short_evidence`

默认不暴露 operator、namespace、raw actual 或完整 snapshot，避免 LLM judge 被底层 scorer 字段牵引。`steps` 是主要行为证据。

## Payload Schema

LLM 只返回 JSON 对象：

```json
{
  "status": "pass|warn|fail|missing|ambiguous|invalid",
  "dimension_scores": {
    "tool_quality": 0.75
  },
  "evidence": ["step 3 tool_call=..."],
  "diagnosis": ["tool_quality: ..."],
  "metadata": {}
}
```

`dimension_scores` 必须且只能覆盖当前目标维度。不要输出 `stage_score`、`judge_confidence` 或全局 `uncertainty`。

## 观测字段

Judge metadata 会记录：

- `prompt_context_digest`
- `target_dimensions`
- `constraint_check_count`
- `stage_step_count`
- `first_stage_step_excerpt`
- `last_stage_step_excerpt`
- `judge_status`
- `judge_stage_score`
- `judge_dimension_confidence_avg`
- `judge_first_diagnosis`
- `judge_first_evidence`

这些字段仅用于审计和日志关联，不参与评分。
