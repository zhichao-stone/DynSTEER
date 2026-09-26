# 统一实验启动脚本加载 env 文件方案

## 背景

`build_llm_from_env()` 只读取 `os.environ`。当前 `start_experiment.sh` 与 `start_experiment_no_docker.sh` 不会加载项目根目录的 `.env`，因此文件中已有 `DYNSTEER_JUDGE_PROVIDER` 也不会进入 Python 子进程，导致 stage goal LLM 工厂返回 `None`。README 已声明支持 `--env-file`，但当前脚本实现丢失了该能力。

## 修改内容

1. 在 `scripts/experiment_bootstrap.sh` 中新增共享函数 `load_env_file()`：
   - 相对路径基于项目根目录解析；
   - 文件不存在时输出警告并继续，保持默认 `.env` 可选；
   - 使用 `set -a` 后 source 文件，使 `.env` 中的键值覆盖当前 shell 同名变量并导出到子进程；
   - 加载完成后恢复 `set +a`。
2. 在两个统一实验启动脚本中新增 `--env-file PATH` 与 `--no-env-file`：
   - 默认值取 `DYNSTEER_ENV_FILE`，未设置时为 `.env`；
   - 参数解析后、创建 uv 环境前加载；
   - Docker 与 host 启动链路行为保持一致。
3. 更新 README 中运行说明，使文档与脚本行为一致。

## 行为约定

- `.env` 是可选文件；存在则加载，不存在不阻塞启动。
- 显式传入 `--env-file missing.env` 同样只警告并继续，与旧版脚本行为一致。
- `.env` 中的同名变量优先于启动前已存在的 shell 环境变量。
- 脚本后续显式导出的运行 profile、缓存目录、Python 编码设置仍会覆盖 `.env` 中可能存在的同名值，避免破坏 benchmark 启动边界。

## 验证

1. 使用 `bash -n` 校验所有被修改 shell 脚本语法。
2. 用临时 env 文件验证覆盖行为、相对路径解析和 `--no-env-file`。
3. 运行 `--exp` 参数透传 smoke check，确认未改变原有实验参数。

## 附录A. 项目中没有把握实现的模块部分

`.env` 文件格式没有统一标准。本次恢复旧版 `source` 行为，可支持注释、空值和引号，但不实现 Python dotenv 的变量插值、多行值和 escape 规则；当前项目 `.env` 只使用简单键值对，满足现有需求。
