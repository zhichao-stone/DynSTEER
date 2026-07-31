# 2026-07-31 实验输出路径补齐 model 层修复方案

## 1. 背景

当前实验产物路径在 `method` 前缺少 `model_id`，导致不同 `model_id` 的 case 结果会落到同一目录下，缓存复用时容易读到错误产物。

现有目标路径应调整为：

- `runs/experiments/<experiment_id>/<benchmark>/<model_id>/<method>/<case_id>/`
- `results/experiments/<experiment_id>/<benchmark>/<model_id>/<method>/<case_id>/`

其中 `model_id` 直接取实验配置 `models[*].model_id`。

## 2. 修改目标

1. 在实验输出路径中补上 `model_id` 层，避免不同模型共享 case 目录。
2. 让 harness / evaluator / experiment runner 继续共用同一套 case 路径生成逻辑。
3. 同步更新实验输出文档、看板扫描逻辑和相关说明，避免旧路径描述继续误导。
4. 评估当前真实模型名和 case 名组合下的路径长度，判断是否会触发 Windows 经典路径上限。

## 3. 计划修改点

### 3.1 `dynsteer/harness/paths.py`

- 将 `case_output_dir()` 扩展为支持实验态 `model_id` 层。
- 保持普通 benchmark harness 的旧路径不变。
- 仅在 `config.metadata` 中同时存在实验标识与模型标识时插入 `model_id` 目录。

### 3.2 `dynsteer/harness/outputs.py`

- 统一让 default / replay / evaluate 三类 case 输出都走新的路径 helper。
- 方法级汇总文件继续按 `method` 目录写出，但汇总内容要能正确反映 `benchmark` 与 `model_id`。
- 补充必要的 metadata，方便后续展示层和索引层读取。

### 3.3 `dynsteer/evaluate/evaluator.py`

- 让 evaluator 侧的 `raw_output_dir` 与新路径规则保持一致。
- 补充报告 metadata 中的实验上下文信息，避免汇总时只能靠路径猜测。

### 3.4 `dynsteer/experiment/*`

- 保持 `ExperimentCaseResult` 与 `index.json` 的分层结构。
- 确认 `output_paths` 写入的是新目录。

### 3.5 `display/build.py` / `display/index.html`

- 让看板识别带 `model_id` 的实验路径。
- run 标识显示 `benchmark / model / method`，避免同 benchmark 不同模型在 UI 上混成一个 run。

### 3.6 文档

- 更新 `docs/apis/experiment.md`
- 更新 `docs/apis/display.md`
- 视情况同步 `README.md`

## 4. 路径长度评估

先按当前仓库里的真实模型名与 case 名评估。

- 若新路径仍低于 Windows 常见 260 字符边界，则保留可读路径。
- 若未来出现更长模型名或更深实验目录，再考虑缩短根目录名或引入更短的实验别名。

## 5. 验证

1. 生成一个实验输出，确认 `runs/results` 下确实落到 `.../<benchmark>/<model_id>/<method>/<case_id>/`。
2. 检查不同 `model_id` 的同一 case 是否不再共用缓存。
3. 重新生成 `display/data.js`，确认 run 切换不会把不同模型混在一起。
4. 跑最小范围测试，确认路径 helper 与展示扫描逻辑都通过。

## 附录A. 项目中没有把握实现的模块部分

1. 如果后续要在 `runs/` 根目录下一次性汇总多个 experiment_id，`display/build.py` 还需要再显式引入 `experiment_id` 分组；本次先按单实验根目录和普通 harness 根目录处理。
2. 本次路径修复按用户要求不额外引入 `repeat_index` 目录层；如果后续实验要做多次重复且必须物理隔离，还需要再补一层重复编号目录。
