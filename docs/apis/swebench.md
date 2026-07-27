# SWE-bench Pro Adapter API

## 当前状态

`dynsteer.adapter.swebench` 已提供继承框架并注册 `swebench_pro`。真实 dataset 加载、repo checkout、agent 运行、patch 验证、resolved rate 提取和 pseudo-stage 生成等待 SWE-bench Pro 仓库到项目同级目录后补实现。

## 占位接口

- `SwebenchProAdapter.adapt_task_case(config, case_id) -> TaskCase`
- `SwebenchProHarness.list_cases(config) -> list[BenchmarkCase]`
- `SwebenchProHarness.start_case(config, case_id, raw_output_dir) -> object`
- `SwebenchProHarness.advance_case(session) -> HarnessAdvanceResult`
- `SwebenchProHarness.case_finished(session) -> bool`
- `SwebenchProHarness.default_result_from_session(session) -> BenchmarkDefaultResult`
- `SwebenchProHarness.metrics_from_session(session) -> JsonObject`

当前这些需要真实 SWE-bench Pro 仓库的接口都会抛出明确的 `NotImplementedError`。
