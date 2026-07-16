# DynSTEER dataclass 模型集中整理方案

## 1. 修改目标

将 `dynsteer` 包内除 `dynsteer/harness/model.py` 以外的所有 `@dataclass` 数据模型集中到 `dynsteer/model.py`，并按功能模块用 `##` 注释划分模型区域。其他模块只保留业务逻辑、流程控制、序列化、评分、调度等行为代码。

## 2. 迁移范围

- `dynsteer/config.py`：`ThresholdConfig`、`DynamicWeightConfig`。
- `dynsteer/progress.py`：`CaseProgressEvent`、`CaseProgressState`、`CaseProgressBars`。
- `dynsteer/metrics.py`：`LLMCallMetrics`、`RuntimeMetricsRecorder`。
- `dynsteer/evaluate/policy.py`：`EvaluationPolicyState`、`EvaluationPolicyUpdate`。
- `dynsteer/evaluate/runtime.py`：`ReadyMilestoneProgress`、`ReadyFrontierProgressWatch`、`RuntimeEvaluationState`、`RuntimeEvaluationDecision`。
- `dynsteer/evaluate/scoring.py`：`ScoringContext`。
- `dynsteer/evaluate/matching/milestone.py`：`MilestoneStepAnalysis`。
- `dynsteer/llm/base.py`：`LLMMessage`、`LLMConfig`。
- `dynsteer/judges/base.py`：`LLMJudgeConfig`、`_ValidatedJudgePayload`。
- `dynsteer/harness/outputs.py`：`HarnessEvaluationOutput`。
- `dynsteer/harness/scheduler.py`：`HarnessCaseTask`。
- `dynsteer/adapter/toolsandbox/harness.py`：`ToolSandboxSession`。

## 3. 实施方案

1. 在 `dynsteer/model.py` 中按功能整理模型区块：JSON 与枚举、配置、轨迹与状态、milestone 与约束、任务与轨迹、评分与评估、运行期状态、进度、LLM 与 Judge、Harness 与适配器。
2. 对涉及 `dynsteer/harness/model.py` 的类型引用使用 `TYPE_CHECKING` 和延迟注解，避免 `model.py` 与 harness 模型互相运行期 import。
3. 将原模块中的 dataclass 定义删除，改为从 `dynsteer.model` 导入对应模型。
4. 保留现有模块对外函数和主要调用路径，不主动改动业务语义。
5. 使用 `rg "@dataclass"` 验证迁移后仅 `dynsteer/model.py` 与 `dynsteer/harness/model.py` 存在 dataclass 定义，并运行可用测试或编译检查。

## 4. 验收标准

- `dynsteer` 包内除 `dynsteer/model.py` 与 `dynsteer/harness/model.py` 外无 `@dataclass` 定义。
- 迁移后的 import 不产生循环依赖。
- `dynsteer/model.py` 的模型按功能区块组织，并通过 `##` 注释标识区块。
- Python 编译检查通过；如测试环境可用，运行相关 PyTest。

## 附录A. 项目中没有把握实现的模块部分

当前任务没有完全没有把握的模块。相对需要谨慎的是 `RuntimeEvaluationState` 与 `HarnessCaseTask` 这类模型引用 `dynsteer/harness/model.py` 中保留的 harness 模型，如果直接运行期 import 会形成循环依赖，因此需要使用延迟注解处理。
