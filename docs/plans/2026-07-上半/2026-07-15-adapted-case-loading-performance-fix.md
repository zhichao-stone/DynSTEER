# adapted case 加载性能修复方案

## 1. 问题定位

当前 `data/<benchmark>/adapted_cases` 中已有缓存时，`load_task_case()` 本身读取很快；主要耗时来自缓存读取前的 `harness.list_cases()`，ToolSandbox 会在该步骤构造全部原生场景。完整评估时，`start_case()` 也会重复调用 ToolSandbox 原生 `named_scenarios()`。

## 2. 修改方案

1. `select_case_ids()` 在 `config.case_ids` 已明确指定时直接返回配置中的 case 顺序，不再调用 `harness.list_cases()` 做全量校验。
2. `--only_adapt` 在已指定 case 且对应 adapted JSON 文件均存在、未强制重建时，直接走缓存加载快路径，不再调用 `harness.list_cases()`。
3. ToolSandbox harness 对 `_named_scenarios()` 做进程内缓存，避免同一 `data_root + tool_backend` 在一次运行中重复构造全部原生场景。
4. 调整 `load_task_case()` 进度条文案，减少“缓存命中仍在重新适配”的误解。
5. 增加 pytest 覆盖显式 case 快路径、only_adapt 缓存快路径和 ToolSandbox 场景缓存。

## 3. 验收方式

运行与修改相关的测试：

```bash
python -m pytest tests/test_harness_selection.py tests/test_main_adapt_only.py tests/test_toolsandbox_harness_cache.py tests/test_task_case_loader.py
```

## 附录A. 项目中没有把握实现的模块部分

当前任务中最需要谨慎的是 ToolSandbox 原生场景对象是否完全适合跨 case 复用。现有 `start_case()` 已经对 `scenario.starting_context` 执行 `copy.deepcopy()`，并且 `list_cases()` 只读 categories，因此进程内缓存 `named_scenarios()` 返回字典整体风险较低；但如果外部 ToolSandbox 未来让 scenario 本身携带可变运行期状态，则仍需要进一步收紧缓存粒度或只缓存 case 元信息。
