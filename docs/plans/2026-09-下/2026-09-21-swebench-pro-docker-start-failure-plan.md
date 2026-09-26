# SWE-bench Pro Docker 启动失败修复方案

## 问题

服务器运行记录显示所有 DEFAULT case 的 `image_probe` 均为：

```text
TypeError: run() got an unexpected keyword argument 'workdir'
```

`docker.models.containers.ContainerCollection.run()` 的容器工作目录参数名是 `working_dir`，不是 `workdir`。由于 `SWEBenchProHarness.start_case()` 捕获启动异常后没有把 `runtime` 重置为 `None`，后续 agent 仍拿到未启动的 executor，所有 bash 命令返回 `exit_code=-1`，最终触发 `patch 收集失败: add=-1, diff=-1`。

## 修改

1. `LocalDockerExecutor.start()` 将 `workdir` 改为 Docker SDK 的 `working_dir`。
2. `SWEBenchProHarness.start_case()` 在启动失败并关闭 runtime 后将 `runtime` 置空，让 `advance_case()` 直接进入现有 infrastructure failure 分支，不再调用模型。
3. 补充针对 Docker run 参数和启动失败状态的单元测试。

## 验收

- 运行 `tests/runtime/test_docker.py` 与 `tests/adapter/swebench_pro/test_harness.py`。
- 新测试证明容器创建参数使用 `working_dir`，启动失败时不再调用模型。
- 服务器重跑时必须使用 `--force_eval`，否则现有坏 trajectory 会被输出缓存复用。

## 附录A. 项目中没有把握实现的模块部分

无。本次修改只涉及 Docker SDK 参数名和失败状态流转，不涉及跨平台 Docker 行为或 benchmark 语义重构。
