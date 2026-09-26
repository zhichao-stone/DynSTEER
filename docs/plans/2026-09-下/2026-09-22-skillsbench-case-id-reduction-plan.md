# SkillsBench case 规模修订方案

## 目标

`fix-build-agentops` 的镜像构建依赖外部 registry 且当前超时，本次将其从 SkillsBench pilot、main、ablation 配置中移除，不再为该 case 构建镜像。修订后的正式规模从 39/19 降为 29/14，并保证 pilot 中其余 5 个 case 同时保留在新的 main 和 ablation 列表内。

## 抽样规则

1. main 抽样范围限定为修订前 39 个 case，移除 `fix-build-agentops` 后剩余 38 个。
2. 固定保留 `earthquake-plate-calculation`、`manufacturing-equipment-maintenance`、`parallel-tfidf-search`、`sales-pivot-analysis`、`sec-financial-report` 这 5 个 pilot case。
3. 继续使用 seed `202608` 和 `metadata.category` 分层比例；每个分层先占用强制保留 case 的配额，再在同层剩余 case 中确定性补足。
4. ablation 从新的 29 个 main case 中按同一规则嵌套抽取 14 个，并同样强制包含 5 个 pilot case。
5. 输出列表按 case ID 排序，并同步到 4 个 main shard 和 4 个 ablation shard。

## 修改内容

- `scripts/build_experiment_configs.py`：
  - 新增 SkillsBench pilot case 固化列表；
  - 将 SkillsBench main/ablation 目标规模改为 29/14；
  - 新增带强制保留 case 的确定性分层抽样 helper；
  - SkillsBench main、ablation、pilot 都使用修订后的列表。
- `data/experiments/skillsbench_pilot.json`：只保留 5 个 pilot case。
- `data/experiments/skillsbench_main.json`：移除失败 case，写入抽样的 29 个 case。
- `data/experiments/skillsbench_ablation.json`：移除失败 case，写入嵌套抽样的 14 个 case。
- `data/experiments/model_shards/skillsbench_{main,ablation}_{1..4}.json`：与正式配置保持同一列表的均分结果。

## 附录A. 项目中没有把握实现的模块部分

强制保留 case 与分层比例可能存在轻微张力：若某分层的保留 case 数超过比例配额，会破坏比例；当前 5 个 pilot case 分布在 5 个不同 category，29/14 两级配额均足够容纳它们，因此可以保证约束同时成立。实现后会用数量、嵌套关系和禁用 case 断言验收。
