# 跨 Benchmark case 规模缩减记录

## 1. 修订理由

SWE-bench Pro 与 SkillsBench 的单 case 需要构建或拉取任务镜像、执行 agent 决策循环并运行 native verifier，主实验、消融实验和单模型 shard 叠加后墙钟时间过长。为控制计算与时间成本，本次在正式运行前缩减固定 `case_ids` 规模。缩减只改变样本量，不改变 benchmark 源码、任务定义、方法矩阵、重复次数或评估指标。

正式配置继续显式固化完整 `case_ids`，运行期不做任何二次抽样。为保留 default 轨迹和 replay 结果的复用关系，SWE-bench Pro 的消融列表严格嵌套于新主实验列表；SkillsBench 消融列表同样嵌套于新主实验列表。四个单模型 shard 配置与对应正式配置使用完全相同的 case 列表。

## 2. 抽样规则

抽样复用 `scripts/get_cases.py::stratified_sample()` 的确定性比例配额算法：

1. seed 固定为 `202608`；
2. SWE-bench Pro 按 `repo` 分层，SkillsBench 按 `metadata.category` 分层；
3. 每层配额先用整数比例分配，余量按层规模从大到小、同规模按层名字典序补齐；
4. 每层内对排序后的 case ID 使用 `random.Random(202608).sample()`；
5. 输出按 case ID 排序后写入 JSON。

SWE-bench Pro 的新主实验先限定在修订前 100 例主实验列表内，再按同一分层规则抽 39 例；消融 19 例只从这 39 例中抽取。SkillsBench 的新主实验从当前全量 87 例中抽 39 例；消融 19 例只从这 39 例中抽取。由于旧主实验与旧消融实验不是嵌套关系，若强行要求新消融同时是旧两个列表的子集将无解；本次优先保证新列表内部可复用，并保持原实验设计的分层比例。

pilot 配置保持修订前的原始列表：SWE-bench Pro 为 3 例，SkillsBench 为 6 例。ToolSandbox 配置不受本次修订影响。

## 3. 新旧规模

| Benchmark | 实验 | 修订前 | 修订后 | 比例 |
| --- | --- | ---: | ---: | ---: |
| SWE-bench Pro | 主实验 | 100 | 39 | 39% |
| SWE-bench Pro | 消融实验 | 50 | 19 | 38% |
| SWE-bench Pro | pilot | 3 | 3 | 不变 |
| SkillsBench | 主实验 | 87 | 39 | 约 44.8% |
| SkillsBench | 消融实验 | 50 | 19 | 38% |
| SkillsBench | pilot | 6 | 6 | 不变 |

主实验与消融实验的重复次数仍为 3；pilot 仍为 1。单模型 shard 的样本量与上表对应实验一致。

## 4. 固化校验

下表哈希为 `sha256(json.dumps(case_ids, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))`。

| Benchmark | 实验 | case 数 | case_ids SHA-256 |
| --- | --- | ---: | --- |
| SWE-bench Pro | main | 39 | `7d35558af81ccfb1ee222bdc7dca1d53832590e278f674f628b928129d7ed098` |
| SWE-bench Pro | ablation | 19 | `68168215b21b89a69b706c0047af3d5aa644d4197c2c0d6bca6eceb6faabef34` |
| SWE-bench Pro | pilot | 3 | `c88d3396028bdde6b3f02042b7e741e28b30f386deb9a35495b1808c39c2c24c` |
| SkillsBench | main | 39 | `2d69507749392c08ba119d95a9918642f2e9ba16ddc4903bcdca4aad9b13c037` |
| SkillsBench | ablation | 19 | `610e1964b8ff0badd85b3a504ff95fe8032d2a3dd0c338852c46b40f04306df4` |
| SkillsBench | pilot | 6 | `62c67e85fd15fb7ef33b82d5aa65232c510a8cd390fa6024f64a5ec67649f35f` |

## 5. 修订后分层分布

### SWE-bench Pro

| repo | main | ablation |
| --- | ---: | ---: |
| NodeBB/NodeBB | 2 | 0 |
| ansible/ansible | 6 | 3 |
| element-hq/element-web | 2 | 0 |
| flipt-io/flipt | 5 | 3 |
| future-architect/vuls | 3 | 2 |
| gravitational/teleport | 5 | 3 |
| internetarchive/openlibrary | 6 | 3 |
| navidrome/navidrome | 2 | 0 |
| protonmail/webclients | 3 | 2 |
| qutebrowser/qutebrowser | 5 | 3 |
| tutao/tutanota | 0 | 0 |

pilot 覆盖 `ansible/ansible`、`flipt-io/flipt` 和 `internetarchive/openlibrary` 三个 repo。

### SkillsBench

| metadata.category | main | ablation |
| --- | ---: | ---: |
| cybersecurity | 3 | 2 |
| finance-economics | 4 | 2 |
| industrial-physical-systems | 7 | 4 |
| mathematics-or-formal-reasoning | 3 | 1 |
| media-content-production | 2 | 0 |
| natural-science | 6 | 3 |
| office-white-collar | 6 | 3 |
| software-engineering | 8 | 4 |

pilot 覆盖 `finance-economics`、`industrial-physical-systems`、`natural-science`、`office-white-collar` 和 `software-engineering` 五个 category，其中 `software-engineering` 为 2 例。

## 6. 受影响文件

- `data/experiments/swebench_pro_main.json`
- `data/experiments/swebench_pro_ablation.json`
- `data/experiments/swebench_pro_pilot.json`
- `data/experiments/skillsbench_main.json`
- `data/experiments/skillsbench_ablation.json`
- `data/experiments/skillsbench_pilot.json`
- `data/experiments/model_shards/swebench_pro_{main,ablation}_{1..4}.json`
- `data/experiments/model_shards/skillsbench_{main,ablation}_{1..4}.json`

`scripts/build_experiment_configs.py` 已同步新规模和嵌套抽样逻辑，后续重建配置时不会回退到修订前规模。
