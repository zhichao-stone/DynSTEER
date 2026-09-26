# 2026-09-22 LLMJudge 评估失败跳过方案

## 背景

`skillsbench_pilot` 中，空 milestone graph 会进入 whole-trajectory judge。`manufacturing-equipment-maintenance` 的 109 个 step 序列化后超过 `qwen3-max-2026-01-23` 的 `258048` 输入长度限制，OpenAI-compatible provider 返回 400，当前异常重试 3 次后中断 case 执行。

## 目标

- LLMJudge 评估失败时不再让 experiment 中断，case 直接降级为已跳过评估的失败结果。
- provider 400 这类确定性客户端错误不再执行无效重试。
- 输入长度超限单独标记为 `llm_judge_input_length_exceeded`；其他 LLMJudge 响应失败标记为 `llm_judge_evaluation_failed`。
- `raw_summary.json`、`summary.json`、`report.json` 和 experiment `index.json` 都保留同一份结构化 `failure` 对象。
- replay 阶段发生评估异常时，尽量保留已加载的原轨迹，而不是只写一个合成 1-step 轨迹。
- 带 `failure` 的旧输出不会被视为完整可复用输出，后续重跑会重新评估。

## 修改点

1. `dynsteer/harness/outputs.py`
   - `write_failed_case_outputs()` 接受可选 `trajectory`。
   - 失败产物统一写入 `failure: {failure_type, error}`。
   - LLMJudge 输入超限使用独立中文原因和 `evaluation_failure:*` 终止码。
2. `dynsteer/experiment/model.py`
   - `ExperimentCaseResult` 增加可序列化 `failure` 字段，并输出到 `index.json`。
3. `dynsteer/experiment/runner.py`
   - 异常分类优先识别 `LLMJudgeResponseError` 和其中的 `Range of input length` 错误。
   - replay 加载轨迹后记录局部变量，失败写入时复用该轨迹。
   - `_case_result_from_output()` 从 summary 读取失败标记。
4. `dynsteer/judges/base.py`、`dynsteer/llm/base.py`
   - LLMJudge 异常保留 provider 具体错误。
   - provider 4xx（超时、冲突、限流除外）直接失败，不消耗剩余重试。
5. 测试
   - 覆盖失败产物、experiment index 和输入长度异常分类。

## 验收

- 相关 PyTest 通过。
- 失败 case 的 score 保持 `null`，不进入有效分数统计。
- 运行记录与 results 均能直接看到失败类型和具体 provider 错误。

## 附录A. 项目中没有把握实现的模块部分

- 无法在本机访问服务器上的真实 DashScope 服务，因此不发起真实长 prompt 请求验证；测试使用与真实异常文本一致的异常对象验证分类与落盘。
