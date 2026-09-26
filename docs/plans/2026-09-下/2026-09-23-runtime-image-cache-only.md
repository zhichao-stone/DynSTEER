# SWE-bench-Pro 运行时镜像离线缓存约束修改方案

## 目标

1. Docker 镜像的远端拉取只允许发生在实验前 preflight 阶段。
2. 实验执行阶段禁止访问远端 registry；本地 Docker 镜像缺失时优先载入 `docker_images` 下的 case 级压缩缓存。
3. 本地 Docker 镜像与压缩缓存都不可用时，不调用 pull，立即将 case 标记为 `docker_image_pull_failed` 并写入 experiment 结果，跳过 agent 与评估执行。
4. preflight 的镜像准备失败只通过 stdout 返回 `image_failures`，不额外持久化失败结果；实验入口收到失败后再生成 case 级失败结果。

## 代码修改

### 1. `dynsteer/runtime/docker.py`

- `LocalDockerExecutor.start()` 保留 `client.images.get()` 的本地检查。
- `_ensure_image_unlocked()` 删除远端 `client.api.pull()` 分支。
- 逻辑收敛为：
  - archive 存在时载入并校验 tag，载入失败时包装为本地缓存不可用；
  - archive 不存在或不可用时抛出 `本地镜像缓存缺失` / `本地镜像缓存不可用`。
- 不再在运行时构造任何远端拉取请求。

### 2. 失败分类

- `dynsteer/adapter/swebench_pro/harness.py` 将 `本地镜像缓存缺失` 分类为既有结果类型 `docker_image_pull_failed`。
- `dynsteer/experiment/runner.py` 对统一实验外层捕获的同类错误做相同分类。
- 结果文案继续使用“镜像拉取失败/跳过执行”的既定语义，表示预取失败或本地缓存不可恢复。

### 3. preflight 语义保持

- `scripts/preflight_experiment.py::prepare_swe_images()` 继续负责实验前补齐 Docker 本地镜像和压缩缓存，允许远端 pull。
- 单个镜像准备失败继续聚合到 `DockerImagePreparationError.failures`，由 preflight JSON 的 `image_failures` 输出。
- preflight 不写镜像失败专用落盘文件，主实验也不读取预检失败缓存；是否跳过完全由运行时的本地镜像/archive 检查决定。

## 测试方案

- 修改 Docker 运行时测试：本地镜像缺失且 archive 存在时只载入 archive，不调用 `api.pull()`。
- 增加测试：本地镜像缺失且 archive 不存在时抛出 `本地镜像缓存缺失`，且 `api.pull()` 未被调用。
- 增加 SWE harness 分类测试：错误文本包含 `本地镜像缓存缺失` 时映射为 `docker_image_pull_failed`。
- 运行运行时、preflight 与 SWE 相关 pytest。

## 附录A. 项目中没有把握实现的模块部分

- 当前 Windows 会话没有可用 Docker daemon，无法端到端验证真实镜像载入与容器启动；只能通过 fake Docker client 覆盖调用路径。
- 原生 evaluator 是外部源码子进程，其 Docker 行为不在本次仓库运行时封装内；按任务目标，本次约束的是 DynSTEER 运行时启动路径不再 pull。

