# Model Shard 按 Case 均分方案

## 1. 目标

将 `data/experiments/model_shards` 的拆分维度从“单模型 + 全量 case”改为“全模型 + 互斥 case 子集”。这样四个 shard 可以同时启动，同一个任务镜像只会被一个 shard 构建；该 shard 内的四个模型串行复用镜像，避免多个窗口等待或争抢同一个镜像构建锁。

## 2. 设计

1. 正式实验继续按 `models` 的原始顺序保留全部 4 个模型。
2. 每个实验生成 4 个 shard，编号仍为 `_1.._4`；编号不再绑定历史单模型配置。
3. `case_ids` 先保持正式配置的排序，再按索引取模分配到 4 份 shard：
   - 第 `i` 个 case 进入 shard `i % 4 + 1`；
   - 不能整除时，前几份 shard 多 1 个 case；
   - 每份 shard 内部保持升序。
4. 四份 shard 的 `case_ids` 必须互斥，且按顺序拼接后的集合与正式配置完全一致。
5. 消融 shard 的 `source_experiments.config` 继续指向同编号主 shard，保证 case 输出复用仍按同 case 子集查找。

## 3. 修改文件

- `scripts/build_experiment_configs.py`：`_model_shards()` 不再读取历史单模型配置；新增确定性 case 均分逻辑。
- `data/experiments/model_shards/*.json`：重新生成 28 份 shard。
- `tests/scripts/test_build_experiment_configs.py`：校验全模型、case 均分、互斥与并集一致，替换旧的单模型编号断言。
- `README.md` 与 `docs/apis/source_direct_benchmarks.md`：同步说明新的并行语义。

## 附录A. 项目中没有把握实现的模块部分

镜像构建锁的实际吞吐依赖当前 Docker 构建性能与本地缓存状态。本方案只能保证不同 shard 不会请求同一个 case 镜像，不能缩短单个 shard 内四个模型共享同一镜像时的执行时间；若后续需要同一 case 内四模型并行，需要引入镜像 ready 事件或集中预热进程，本次不扩展。
