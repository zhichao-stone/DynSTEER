# Milestone Reliability 实验结果防覆盖与 `--force` 方案

## 1. 目标

为 `start_milestone_reliability.sh` 与 `start_milestone_reliability_no_docker.sh` 建立统一的结果目录保护语义：

- 默认运行：目标实验结果目录已存在时立即拒绝，不写入任何 case、summary、index 或 LLM 输出；
- `--force`：显式清空目标实验结果目录后重新执行，确保不会混入旧 case 或旧批次 LLM 输出。

## 2. 具体修改

### 2.1 `milestone_reliability.py`

1. 命令行增加 `--force` 布尔参数，默认值为 `false`。
2. 在开始适配 case 和调用 LLM 前解析唯一结果目录：
   `results/milestone/<experiment_id>`。
3. 校验 `experiment_id` 只能映射为单个安全目录名，并确认最终路径的直接父目录是当前项目的 `results/milestone`，防止路径穿越或误删其他目录。
4. 目标目录不存在时正常创建并执行。
5. 目标目录存在且未传 `--force` 时抛出清晰错误，提示更换 `experiment_id` 或显式使用 `--force`。
6. 目标目录存在且传入 `--force` 时，删除且仅删除已校验的目标实验目录，然后创建空目录并执行。
7. 删除失败视为基础设施错误，停止运行，不在不完整目录上继续。

### 2.2 启动脚本

1. 更新 Docker 与 no-docker 脚本 usage，展示 `[--force]`。
2. 保持参数原样透传；`--force` 最终由 Python CLI 解析，脚本不自行删除结果目录。

### 2.3 文档

在 milestone API/实验文档中明确：

- 默认拒绝覆盖；
- `--force` 会删除整个同名实验目录，包括旧 case、summary、index 和 `llm_outputs`；
- 不希望删除历史结果时应使用新的 `experiment_id`。

### 2.4 测试

使用临时目录覆盖以下场景：

1. `--force` 参数解析；
2. 目标目录不存在时正常运行；
3. 目标目录存在且无 `--force` 时拒绝，并确认哨兵文件未变化；
4. 目标目录存在且有 `--force` 时清空旧内容并生成新结果；
5. 非法 `experiment_id` 无论是否 `--force` 都被拒绝；
6. 现有 milestone 测试无回归。

## 3. 安全边界

- 删除动作只由用户显式传入 `--force` 授权。
- 删除前必须同时满足：结果根目录已解析、目标是结果根目录的直接子目录、目录名与安全化后的 `experiment_id` 完全一致、目标不等于结果根目录。
- Python 实现统一承担删除逻辑，避免 Docker/no-docker 脚本各自实现一套路径处理。
- 不修改 `.gitignore`，不删除 `docs/constraints` 或 `docs/plans` 中任何文档。

## 4. 验收标准

- 默认运行绝不会修改已存在的同名实验目录；
- `--force` 能得到无历史残留的新实验目录；
- 两个启动脚本均能透传 `--force`；
- 非法路径无法触发递归删除；
- PyTest、Python 语法检查和 shell 语法检查通过。

## 附录A. 项目中没有把握实现的模块部分

本次没有需要猜测的核心实现部分。唯一需要明确的产品语义是“不覆盖”应当拒绝启动还是自动创建新目录；基于用户同时指定 `--force` 才覆盖，本方案采用更可预测的“默认拒绝启动”，不擅自改变配置中的 `experiment_id` 或自动生成难以定位的新目录。
