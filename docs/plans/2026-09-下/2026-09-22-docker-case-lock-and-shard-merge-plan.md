# Docker Case 级并发与 Shard 结果归并方案

## 1. 目标

1. SWE-bench Pro 镜像拉取与 SkillsBench 镜像构建只按同一 case/tag 互斥；不同 case/tag 可并发交给 Docker daemon 调度。
2. 镜像准备或运行失败只影响对应 case，实验继续执行其余 case，并在 runs/results 中记录结构化失败原因。
3. `model_shards` 中 `_1..4` 的输出继续归入基础 `experiment_id`，最后可用原始配置读取全部 case 产物并写汇总。

## 2. 设计

1. SWE preflight 与运行期 `LocalDockerExecutor` 共用每个镜像引用的独立锁文件；等待者获得锁后先复查本地镜像和压缩包，命中则直接复用，只有仍缺失才拉取。
2. SkillsBench `build_task_image()` 按镜像 tag 建立独立锁；等待者获得锁后同样先复查本地镜像与压缩包。
3. preflight 的 Docker daemon 连接或 ping 失败仍是硬失败；单个镜像准备失败汇总到 `image_failures`，预检返回 0，让运行期生成逐 case 失败记录。
4. runner 捕获单个 case 的执行异常，写出包含错误轨迹、raw summary、summary 与 report 的失败产物，并继续后续 case。default 失败时，同 identity 的 replay/evaluate 臂直接写“default 基础设施失败”的跳过记录，不再尝试启动同一镜像。
5. Docker 启动失败在 default 产物中使用 `docker_image_pull_failed` 或 `docker_image_build_failed` 作为结构化 `failure_type`，termination_detail 同步保留错误。
6. shard 文件名保持 `_1..4`，但内部 `experiment_id` 保持基础 ID；配置加载允许 shard 文件名映射到基础 ID。同 ID 来源复用视为读取当前输出目录的 existing 产物。
7. 分批执行用现有 `--no_sum` 跳过实验级汇总；四个 shard 完成后运行原始配置，已有 case 输出会被直接读取，缺失的才执行，最后统一写 `index/scores/metrics/costs`。

## 3. 修改文件

- `scripts/preflight_experiment.py`：SWE case 级锁与逐镜像失败聚合。
- `dynsteer/adapter/skillsbench/runtime.py`：Skills tag 级构建锁。
- `dynsteer/adapter/swebench_pro/runtime.py`、`dynsteer/runtime/docker.py`：运行期镜像 fallback 使用同一 case 级锁。
- `dynsteer/harness/outputs.py`、`dynsteer/experiment/runner.py`：失败产物写出、单 case 降级与 replay/evaluate 后续方法跳过。
- `dynsteer/adapter/swebench_pro/harness.py`、`dynsteer/adapter/skillsbench/harness.py`：结构化 Docker 失败类型。
- `dynsteer/experiment/config.py`、`dynsteer/experiment/reuse.py`：shard 文件名映射与同 ID 复用。
- `scripts/build_experiment_configs.py`、`data/experiments/model_shards/*.json`：保留基础 experiment_id。
- `docs/apis/experiment.md`、`docs/apis/source_direct_benchmarks.md`：同步行为说明。

## 附录A. 项目中没有把握实现的模块部分

Docker daemon 的实际并发吞吐、Registry 限流和磁盘 IO 仍可能让多个不同 case 拉取/构建互相拖慢；本方案只消除人为的全局串行化，不保证线性加速。运行期失败降级覆盖 case 执行异常，但进程级崩溃、断电或用户中断仍会留下不完整输出，后续重跑会按现有完整性检查补齐。
