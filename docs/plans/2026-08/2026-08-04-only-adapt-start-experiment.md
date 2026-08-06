# `start_experiment.sh --only_adapt` 修复方案

## 1. 目标

让统一实验配置入口支持 `./scripts/start_experiment.sh --exp PATH --only_adapt`，仅为实验配置中涉及的各 benchmark 生成或复用 `adapted_cases`，不执行任何评估，也不写入实验评估汇总结果。

## 2. 实现范围

1. `main.py`
   - 在统一实验 `--exp` 分支识别 `--only_adapt`。
   - 从实验矩阵构造 benchmark harness 配置，按 benchmark/data root/case 列表去重，复用现有适配流程。
   - 输出生成的 adapted case 路径并正常退出，不调用 `run_experiment`。
2. `scripts/start_experiment.sh`
   - 在帮助、参数解析和容器内命令转发中支持 `--only_adapt`/`--only-adapt`。
3. `scripts/start_experiment_no_docker.sh`
   - 与 Docker 入口保持同样的帮助、参数解析和 Python 参数转发行为。

## 3. 设计约束

- 适配逻辑继续复用现有 `load_task_case(..., force_adapt=...)`，不新增 benchmark 私有接口。
- 统一实验矩阵中的 model/method/repeat 只影响评估，不应导致同一 benchmark 的数据重复适配。
- `--force_adapt` 在 `--only_adapt` 下仍表示重建已有 adapted case。
- 不修改 `docs/constraints` 下已有文档，不主动提交 git commit。

## 附录A. 项目中没有把握实现的模块

- 未掌握外部 benchmark 源码及其运行时依赖的全部行为；本次仅验证入口参数传递、配置展开和适配流程调用，不替代真实 benchmark 数据适配回归。

## 5. 验收

- `bash -n` 通过两个启动脚本。
- `python -m py_compile main.py` 通过。
- `main.py --help` 和两个脚本的 `--help` 显示 `--only_adapt`。
- 通过静态检查确认统一实验分支在 `only_adapt` 时不会调用评估 runner。
