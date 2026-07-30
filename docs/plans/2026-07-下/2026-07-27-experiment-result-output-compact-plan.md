# 2026-07-27 实验结果输出紧凑化修改方案

## 1. 背景

现有 `docs/plans/2026-07-27-experiment-reliability-and-redundancy-cleanup-plan.md` 第 5.2 节里的 `index.json` 样例，把 `experiment_id`、`run_id`、`benchmark`、`case_id`、`model_id` 逐项平铺在每条结果里。这个写法信息很全，但重复太多，阅读时不够利落，也不方便快速看出同一组 run 下的 case 归属。

与此同时，`scores.json` 和 `metrics.json` 虽然体量不大，但当前 API 文档对字段口径的说明还不够直观，第一次接触结果文件的人需要来回翻代码才能看懂每个字段的意义。

## 2. 修改目标

1. 将 `results/experiments/{exp_id}/index.json` 改成分层字典，不再按列表重复写基础身份字段。
2. 保留 `scores.json` 和 `metrics.json` 的数值结构与统计口径，只补充说明，不改统计逻辑。
3. 让输出文件同时满足“人能扫懂”和“脚本易解析”。
4. 同步更新实验 API 文档和测试，确保新结构稳定可回归。

## 3. 目标输出结构

```json
{
    "experiment_id": "double_benchmark_initial",
    "case_count": 4,
    "results": {
        "toolsandbox": {
            "default": {
                "toolsandbox_gpt4o": {
                    "repeats": {
                        "0": {
                            "run_id": "double_benchmark_initial_toolsandbox_default_toolsandbox_gpt4o_default_r0",
                            "cases": {
                                "add_contact_with_birthday": {
                                    "score": 1.0,
                                    "default_score": 1.0,
                                    "dynsteer_score": null,
                                    "resolved": true,
                                    "runtime_metrics": {...},
                                    "output_paths": {...},
                                    "raw": {...}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
```

说明：

1. `experiment_id` 只保留一次。
2. `benchmark`、`method`、`model_id`、`repeat_index`、`case_id` 都变成字典层级或字典 key。
3. `run_id` 只在 repeat 节点保留一次。
4. case 节点只保留 case 结果本身，不再重复身份字段。

## 4. 具体修改

### 4.1 `dynsteer/experiment/runner.py`

1. `write_experiment_index()` 不再直接写 `results: [result.to_dict() ...]`。
2. 新增一个分组构建逻辑，按 `benchmark -> method -> model_id -> repeat_index -> case_id` 聚合结果。
3. 分组时校验同一组内 `run_id` 一致，否则直接抛错，避免把脏数据悄悄写进索引。
4. 保持输出排序稳定，保证相同输入下 JSON 顺序可回归。

### 4.2 `dynsteer/experiment/model.py`

1. 保留 `ExperimentCaseResult` 现有语义，不把新的树状结构塞进 `to_dict()` 里。
2. 如有需要，补一个只面向 `index.json` 的轻量序列化辅助方法，避免 runner 里手工拼太多字段。
3. `ExperimentCaseResult` 继续服务 case 级逻辑，不扩散成“索引聚合对象”。

### 4.3 `docs/apis/experiment.md`

1. 把 `index.json` 的描述从“case 级结果索引”改成“分层 case 索引”。
2. 用短示例说明新的树状结构。
3. 在 `scores.json` 和 `metrics.json` 下方增加字段说明：
   - `scores.json`：说明每个叶子值是同一 `method / benchmark / model_id` 下的 case 平均分。
   - `metrics.json`：说明 `efficiency`、`cost`、`psep`、`rank_tau` 的含义。
4. 明确 `metrics.json.rank_tau` 和 `psep` 在模型数不足时返回 0.0 是预期，不是异常。

### 4.4 `tests`

1. 更新 `tests/experiment/test_runner.py`，断言 `index.json` 变成分层字典。
2. 增加一个最小 fixture，确认同一组结果只写一次 `run_id`，case 名称由字典 key 承载。
3. 保留 `tests/experiment/test_metrics.py` 现有断言，确认 `scores.json` / `metrics.json` 的数值口径没有变。

## 5. `scores.json` 和 `metrics.json` 的说明方式

这里不建议往 JSON 里硬塞伪注释字段，因为那会污染机器读取口径，也会让输出显得更乱。

更合适的做法是：

1. 在 `docs/apis/experiment.md` 里补字段级说明。
2. 在本方案和后续 API 文档里给出注释式样例。
3. 保持真正落盘的 `scores.json` / `metrics.json` 仍是纯 JSON。

## 6. 验收标准

1. `results/experiments/{exp_id}/index.json` 不再出现每条结果重复写 `experiment_id`、`run_id`、`benchmark`、`case_id`、`model_id` 的平铺列表。
2. `scores.json`、`metrics.json` 的数值结果与当前口径一致。
3. `docs/apis/experiment.md` 能直接解释三个输出文件各自的职责。
4. `uv run pytest tests/experiment/test_runner.py tests/experiment/test_metrics.py` 通过。
5. 新格式有清晰版本标记，后续如果再改结构，不会和旧格式混淆。

## 7. 备注

- 这份方案只处理“实验结果输出可读性”问题，不改 `report.json` / `summary.json` 等单场景产物。
- 不碰 `reliability.json`，也不和 2026-07-27 的清理方案抢职责。
