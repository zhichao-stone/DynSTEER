# Judges API

## 目标

`dynsteer.judges` 包负责轨迹阶段评估器，定义统一接口和 cheap/standard/expensive 三档 Judge。LLM provider 的构建与交互不在本包，见 `docs/apis/llm.md`。

## 包结构

```text
dynsteer/judges/
- __init__.py    # 导出 BaseJudge、LLMJudge、CheapJudge、StandardJudge、ExpensiveJudge 及相关错误
- base.py        # BaseJudge、LLMJudge、LLMJudgeConfig、LLMJudgeConfigurationError、LLMJudgeResponseError
- prompt.py      # PromptTemplate、系统 prompt 与 standard/expensive 多语言 prompt 构造函数
- cheap.py       # CheapJudge
- standard.py    # StandardJudge
- expensive.py   # ExpensiveJudge
```

`dynsteer/judge.py` 与 `dynsteer/judges/llm.py` 已删除，`from dynsteer.judge import ...` 和 `from dynsteer.judges.llm import ...` 不再可用。

## 统一接口

```python
class BaseJudge(ABC):
    @abstractmethod
    def evaluate_stage(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        weights: dict[Dimension, float],
    ) -> StageEvaluationResult:
        """评估单个阶段。"""
```

## CheapJudge

`CheapJudge(BaseJudge)` 是本地结构化评估器，只用于 cheap 层，不访问网络。它根据 `StageInterval.milestone_score` 与 `status` 生成确定性阶段评估结果。

## LLMJudge

`LLMJudge(BaseJudge)` 是 LLM-as-a-Judge 抽象基类。它只封装入参检查、prompt 构造、JSON 调用、响应解析、结果转换和通用异常，依赖注入的 `BaseLLM.chat(...)`，不直接导入 `openai.OpenAI`。

`LLMJudge` 不实现 `evaluate_stage(...)`，也不保留 `_evaluate_standard(...)`、`_evaluate_expensive(...)`，因此不能直接实例化。共用 helper 包括 `_call_json(...)`、`_result_from_payload(...)`、`_dimension_scores(...)`、`_float_in_unit(...)`、`_string_list(...)`。

```python
judge = StandardJudge(llm=llm)
judge = ExpensiveJudge(llm=llm, expensive_passes=3)
```

## PromptTemplate

`dynsteer.language` 提供 `TaskLanguage`、`normalize_task_language(...)`、`language_from_metadata(...)` 和 `language_from_task(...)`。外部配置中的 `en`、`english` 会归一为 `TaskLanguage.ENGLISH`；`zh`、`ch`、`chinese`、`zhongwen`、`中文` 会归一为 `TaskLanguage.CHINESE`。未知语言会抛出 `ValueError`，避免静默使用错误语言。

`dynsteer.judges.prompt` 负责维护多语言 prompt。`PromptTemplate.render(language=TaskLanguage.ENGLISH, **kwargs)` 默认使用英文模板；模板渲染使用 `_safe_format(...)`，只替换 `{key}` 占位符，保留 JSON 示例中的 `{{` / `}}` 字面花括号。`build_judge_system_prompt(...)` 负责生成 LLMJudge 系统 prompt，避免在 `base.py` 中写死中文系统消息。

benchmark 语言由 `data/{benchmark}/benchmark.json` 的 `language` 字段配置，并在 `load_harness_run_configs(...)` 中写入 `HarnessRunConfig.metadata["language"]`；`DynSTEEREvaluator.evaluate(...)` 会合并到 `TaskCase.metadata`，judge 通过 `dynsteer.language.language_from_task(...)` 读取并归一为 `TaskLanguage`。

LLM judge prompt context 会额外包含 `stage_goal` 和 `rubric_dimension_focus`：

- `stage_goal`: 当前阶段的权威成功条件，由 `StageInterval` 与 `TaskCase.milestone_graph` 生成。standard / expensive judge 必须优先判断该字段，而不是要求阶段片段完成整个 `task.task_description`。
- `rubric_dimension_focus`: 当前阶段最应关注的维度，例如状态更新阶段关注 `progress`、`state_consistency`、`tool_quality`、`safety`。

`stage_goal` 由 `dynsteer.stage.build_stage_goal(...)` 生成，只使用 `Milestone`、`Constraint`、`StageInterval` 等通用字段，不解析 benchmark 私有 metadata。

## StandardJudge

`StandardJudge(LLMJudge)` 在 `evaluate_stage(...)` 内完成单轮 prompt 构造、LLM JSON 调用和结果转换，固定返回 `EvaluationLevel.STANDARD` 结果。standard prompt 包含任务上下文、阶段轨迹、维度权重、证据规则、评分 rubric 和严格 JSON 输出 schema。

`StandardJudge` 会在 `StageEvaluationResult.metadata` 中记录轻量观测字段，便于排查 LLM judge 任务目标错位：

- `task_description`: 调用 LLM 前的 `TaskCase.task_description`。
- `stage_id` / `milestone_id` / `start_step_index` / `end_step_index`: 当前阶段标识与范围。
- `prompt_context_digest`: 已渲染 standard prompt 的 SHA-256 digest，用于关联输入快照与输出诊断。
- `prompt_task_description_excerpt`: 任务描述摘要。
- `stage_goal_digest` / `stage_goal_objective_excerpt`: 当前阶段目标摘要，用于定位 LLM judge 是否按阶段目标判分。
- `stage_step_count` / `first_stage_step_excerpt` / `last_stage_step_excerpt`: 阶段轨迹摘要。
- `judge_status` / `judge_stage_score` / `judge_confidence`: LLM 输出转换后的阶段结果摘要。
- `judge_first_diagnosis` / `judge_first_evidence`: LLM 输出的首条诊断和证据摘要。

这些字段只用于审计与日志关联，不会向 prompt context 添加 `task_id`、`scenario_name` 等额外任务语义字段；prompt 语义以 `stage_goal` 和轨迹证据为准。

## ExpensiveJudge

`ExpensiveJudge(LLMJudge)` 在 `evaluate_stage(...)` 内完成多轮聚焦评估、一次风险复核和一次汇总裁决，记录 `metadata["judge_passes"]`，固定返回 `EvaluationLevel.EXPENSIVE` 结果。`expensive_passes` 控制聚焦评估轮数，必须大于 0。聚焦模板用于分维度深审，风险模板用于 fatal/minefield/约束风险复核，裁决模板基于前序 pass 形成最终 JSON 结果。

`ExpensiveJudge` 与 `StandardJudge` 使用同一套轻量输入/输出快照机制：

- 最终 `StageEvaluationResult.metadata` 会记录 adjudication prompt 的 `task_description`、`stage_id`、`milestone_id`、`prompt_type="adjudication"`、`prompt_context_digest`、阶段步骤摘要、`judge_status`、`judge_stage_score`、`judge_confidence`、首条诊断和首条证据。
- `metadata["judge_passes"]` 中每个 focus/risk 中间轮次也会记录各自的 `prompt_context_digest`、`task_description`、`stage_goal_digest`、`stage_goal_objective_excerpt`、阶段步骤摘要、`judge_first_diagnosis` 和 `judge_first_evidence`。
- 这些字段只用于审计和日志关联，不会写入 expensive adjudication 的 `previous_passes` prompt context。

## 分发约定

评估等级分发由 `DynSTEEREvaluator` 根据 `EvaluationDecision` 完成：cheap 不足时调用 `StandardJudge`，standard 不足时调用 `ExpensiveJudge`。`LLMJudge` 基类不承担分发，避免出现只调用基类方法的一行中转函数。

## 异常

- `LLMJudgeConfigurationError`: judge 配置缺失或不合法。
- `LLMJudgeResponseError`: LLM 返回内容无法解析为合法 JSON 或字段非法。
