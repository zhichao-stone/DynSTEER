# Docker 预检进度显示修改方案

## 目标

为 `preflight_experiment.py` 的 Docker 镜像准备阶段补充实时、可读的进度输出，并保持预检 JSON 仍只写入 stdout，避免破坏日志采集和管道解析。

## 修改内容

1. 在 `dynsteer/utils.py` 中实现共享的 Docker 事件格式化与进度打印：
   - 将 pull/build 的字典事件转换为稳定文本。
   - TTY 下 pull 使用单行刷新，build 输出关键里程碑。
   - 非 TTY 下只输出关键里程碑，避免 CI 日志刷屏。
2. 修改 `scripts/preflight_experiment.py`：
   - SWE-bench-Pro 显示镜像总数、当前序号、锁等待、缓存命中和分层下载进度。
   - SkillsBench 将任务序号传入镜像构建函数，显示当前构建任务和 Docker build 关键进度。
3. 修改 `dynsteer/adapter/skillsbench/runtime.py`：
   - 允许预检传入进度标签。
   - 将 `client.images.build` 换成低层 `client.api.build(..., stream=True, decode=True)`，在构建发生时实时消费事件。
   - 保留现有构建锁、镜像缓存、超时和 `docker-build.log` 行为。

## 附录A. 项目中没有把握实现的模块部分

- 远程 Docker daemon 的 build 事件格式与 BuildKit 版本相关。不同版本可能只提供粗粒度 `Step`/状态事件，无法稳定得到 apt 或 pip 的字节级下载进度；实现上采用事件归一化，只展示 Docker 实际返回的粒度。
