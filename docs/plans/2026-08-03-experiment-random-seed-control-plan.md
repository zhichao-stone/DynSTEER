# 2026-08-03 实验随机种子控制修改方案

## 1. 修改目标

为统一实验入口增加 `--random_seed` / `--random-seed` 参数，默认值为 `202608`。在 `main.py` 完成参数解析后、加载 ToolSandbox scenario 前调用 `random.seed()`，使不同进程构造 ToolSandbox scenario 时获得一致的 Python 随机序列。

## 2. 修改范围

1. 修改 `main.py`：增加整数参数，启动时立即设置 Python `random` 全局种子，并记录结构化日志。
2. 修改 `scripts/start_experiment.sh` 与 `scripts/start_experiment_no_docker.sh`：解析并转发两种参数拼写；未传入时由 `main.py` 使用默认值。
3. 修改 `docs/apis/experiment.md`：说明参数默认值、作用边界以及时间戳不受随机种子控制。
4. 新增 PyTest：验证默认值、两种参数拼写和 `main()` 设置随机状态的行为。

## 3. 语义边界

- 只固定 Python 标准库 `random`；当前 ToolSandbox 的 `random.shuffle()` 由此稳定。
- 不修改 LLM provider 的采样参数，不声称控制远端模型的非确定性。
- 不冻结 `datetime.now()`；ToolSandbox 动态时间戳仍以各进程 scenario 构造时刻为准。
- 不修改既有实验配置 JSON schema，命令行参数作为进程级实验设置。

## 4. 验收标准

1. 不传参数时 `random_seed == 202608`。
2. `--random_seed 7`、`--random-seed=7` 均解析为整数 7。
3. 两次使用相同 seed 执行 `main()` 初始化后，Python `random` 产生相同序列。
4. Docker 与非 Docker 启动脚本都能转发该参数。
5. PyTest 与 `compileall` 通过。

## 附录A. 项目中没有把握实现的模块部分

远端 LLM 服务是否支持 provider 级 seed、以及服务端是否保证确定性不在当前代码控制范围内，因此本次只保证 ToolSandbox scenario 构造所使用的 Python `random` 可复现。ToolSandbox 中基于 `datetime.now()` 的动态数据仍可能因两个进程启动时间不同而不同。
