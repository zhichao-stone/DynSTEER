# 跨实验 case 输出复用方案

## 1. 目标

消融实验中与主实验完全相同的方法，在主实验对应 `benchmark/model/repeat/case/method` 产物已完整存在时，直接复制主实验 case 输出；源产物缺失的 case 继续按原有实验流程执行。这样保留完全相同的 DEFAULT 轨迹与 replay 输入，避免重新执行引入执行随机性。

## 2. 设计

1. 消融与 intervention 配置新增顶层 `source_experiments`，每项包含：
   - `config`：来源实验配置路径，相对当前消融配置解析；
   - `methods`：允许复用的方法列表，正式消融配置为 `default`、`dynsteer_replay`；intervention 只复用 `default`。`dynsteer_replay_static` 只在主实验矩阵中，不属于当前消融矩阵。
2. `run_experiment()` 在非 `--force_eval` 且非 `--force_adapt` 时先执行导入。
3. 导入前按 `benchmark/model/repeat/method/case/threshold_profile` 匹配源与目标 spec，并逐项比较 `data_root`、judge、threshold、strategy、milestone generation、repeat 与 metadata。匹配失败直接报错，不静默重跑。
4. 按目标 case 查找来源配置中的同 identity spec；源 case 还必须同时具备 `trajectory.json`、`raw_summary.json`、`summary.json`、`report.json` 才复制，否则计为缺失并留给目标实验执行。
5. 复制完整 run case 目录（含 `raw/`）与 result case 目录；重写 `raw_summary.json`、`summary.json`、`report.json` 中的实验身份，并写入 `reused_from` 溯源信息。`trajectory.json` 不做身份改写，保持轨迹内容不变。
6. 目标 case 已完整存在时不覆盖；强制评估时不导入，按现有语义重跑。

## 3. 修改文件

- `dynsteer/experiment/reuse.py`：新增跨实验输出导入模块。
- `dynsteer/experiment/runner.py`：接入导入步骤。
- `scripts/build_experiment_configs.py`：生成消融配置时写入来源主实验；单模型 shard 将来源同步改成同编号主实验。
- `data/experiments/*_ablation.json` 与 `data/experiments/model_shards/*_ablation_[1-4].json`：补齐现有配置。
- `tests/experiment/test_reuse.py`：覆盖配置解析、兼容性检查与输出复制身份重写。
- `docs/apis/experiment.md`：补充配置与复用语义。

## 附录A. 项目中没有把握实现的模块部分

唯一的不确定点是历史 case 产物中是否有零散字段引用源实验绝对路径。本方案只改写已定义的身份字段并显式保留 `reused_from` 溯源，不做递归字符串替换，避免误改轨迹证据或诊断文本。若后续发现新的结构化身份字段，应追加精确字段级重写和测试。
