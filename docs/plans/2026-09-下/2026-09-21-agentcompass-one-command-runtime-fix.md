# AgentCompass 一键启动修复方案

## 背景与问题

当前 `start_experiment_no_docker.sh` 已能创建 `.venv-agentcompass`，但仍会在启动阶段失败：

1. Windows 下 SkillsBench 目录加载被 `agentcompass.benchmarks.swebench_pro` 间接带入 `fcntl` 依赖。
2. SkillsBench host-process 路径中，SkillsBench 与 SWE-bench Pro 的兼容补丁混在一起，导致无关 benchmark 被额外加载。
3. OpenHands host-process 安装脚本无条件执行 Debian/RHEL 系统包引导，服务器非 root 用户会在 `apt` 上触发 `Permission denied`。
4. 启动脚本只检查 `.venv-agentcompass/.dynsteer/source` 戳，不检查 Python 解释器和核心导入，损坏或不完整环境不会被自动修复。
5. LiteLLM 在实验启动前尝试联网拉取价格表，弱网环境产生大量超时输出。

## 代码修改

### 1. 组件隔离导入

新增 `dynsteer/adapter/agentcompass/components.py`，提供 `import_agentcompass_component()`。该函数在导入 `agentcompass.<collection>.<component>` 前为 `benchmarks`、`harnesses`、`environments` 和 `recipes` 的第一层集合包构造一个不带包级 `__init__` 副作用的别名，避免导入任意其他组件。

首轮实现曾把 `agentcompass.benchmarks.skillsbench` 误当作需要绕过的集合包，导致导入 `skillsbench.benchmark` 时仍无法建立正确的父链。已修正为只别名 `agentcompass.benchmarks` 这一层集合包。

`runtime.py`、`recipes.py`、`compat.py` 统一使用该工具：

- `recipes.py` 只加载 `agentcompass.benchmarks.swebench_pro`。
- `compat.py` 只在 SkillsBench 路径加载 `agentcompass.benchmarks.skillsbench.benchmark`。
- `runtime.py` 的 `_agentcompass_api()` 接收 benchmark 名，只有 `swebench_pro` 导入并注册 host-process recipe。
- 目标 harness 与 `host_process` environment 也通过同一工具导入，避免 `agentcompass.harnesses` / `agentcompass.environments` 父包全量导出。

### 2. Host-process 兼容层

`apply_host_process_compat(benchmark)` 改为按 benchmark 应用：

- `skillsbench`：重写 OpenHands 运行根、SkillsBench workspace、skills staging、verifier 和 wrapper 路径到用户可写目录。
- `swebench_pro`：注册 `swebench_pro_host_process` recipe，并将 fresh evaluator 对齐到任务仓库路径。
- 不再为 SWE-bench Pro 导入 SkillsBench 或 OpenHands 的 SkillsBench 补丁。

OpenHands 安装补丁会移除 `platform_bootstrap_commands()`。host-process 下不应假设宿主允许 root 包管理；curl/wget、Python、git 等工具仍由脚本自行检查。

### 3. 启动脚本自愈

`scripts/agentcompass_environment.sh` 增强：

- 设置 `UV_LINK_MODE=copy`，避免跨文件系统 hardlink 警告。
- 设置 `LITELLM_LOCAL_MODEL_COST_MAP=true` 与 `LITELLM_LOG=ERROR`，使用本地价格表并抑制无意义日志。
- 环境戳纳入 Python 版本。
- 创建环境后校验 `agentcompass` 与 `polars`、`networkx`、`scipy`、`tqdm` 可导入。
- 校验失败时重建并重装 `.venv-agentcompass`；二次校验仍失败才终止，错误信息明确指向源码与环境。

### 4. API 文档同步

更新 `docs/apis/agentcompass.md`：

说明 recipes 与 host-process 补丁按 benchmark 按需导入，Windows 不会因为无关 benchmark 的 Unix-only 模块失败；启动脚本会校验并自动重建损坏的 `.venv-agentcompass`；LiteLLM 使用本地价格表。

### 5. OpenHands 静默初始化观测

Linux 完整实验进入 OpenHands `start_session` 后会执行三段耗时动作：下载 micromamba、创建 Python runtime、安装 `openhands-sdk` 和 `openhands-tools`。AgentCompass 的 host-process 执行器会等待安装命令结束，终端默认不会实时显示子进程输出，因此可能连续几分钟没有新日志。

DynSTEER 兼容层现在会记录 runtime 初始化的开始、完成和耗时；安装脚本对已能导入的 OpenHands runtime 直接复用，避免每个 case 都重复下载和重建；micromamba 下载增加 wget 网络超时，弱网下不再无限等待。

### 6. 服务器离线资源

服务器日志显示 GitHub 下载缓慢后进入 micromamba 阶段，最终失败发生在 PyPI 依赖安装。DynSTEER 的 OpenHands 安装命令现在支持两个本地资源：

- `/tmp/agentcompass/openhands/micromamba-linux-64`
- `/tmp/agentcompass/openhands/wheelhouse`

存在本地 micromamba 时跳过 GitHub 下载；存在本地 wheelhouse 时启用 `PIP_NO_INDEX=1` 和 `PIP_FIND_LINKS`，完全从本地 wheel 安装 OpenHands 依赖。`dist/agentcompass-offline-openhands.zip` 已包含 Linux x86_64 的 micromamba、OpenHands 183 个锁定依赖、SWE-bench Pro 的 mini-swe-agent 77 个锁定依赖和使用说明。

### 7. 复用检查回归修复

给 OpenHands 安装命令添加复用检查时，早期拼接逻辑把原始脚本首部的 `install_root`、`runtime_root` 和 `micromamba` 变量定义丢弃了。安装脚本随后执行空变量命令，在服务器上表现为 `rm: cannot remove '/bin': Permission denied` 和 `mkdir: cannot create directory '/mamba-root': Permission denied`。

修正后的包装函数保留完整原始脚本，并把复用检查插入到变量定义之后、破坏性清理之前。已验证生成脚本首部包含 `install_root=/tmp/agentcompass/openhands`，且 OpenHands harness 持有的初始化函数引用与 `remote_runner` 补丁一致。

## 验收

1. `bash scripts/start_experiment_no_docker.sh --exp data/experiments/skillsbench_pilot.json --source ../AgentCompass --workers 1 --only_adapt` 能通过环境检查和任务目录加载。
2. `bash scripts/start_experiment_no_docker.sh --exp data/experiments/swebench_pro_pilot.json --source ../AgentCompass --workers 1 --only_adapt` 能通过 SWE-bench Pro 组件与 recipe 注册路径。
3. `uv run pytest` 或最小 Python 导入检查确认无语法错误。
4. 不修改 `../AgentCompass` 与 `../ToolSandbox`。

## 附录A. 项目中没有把握实现的模块部分

1. **OpenHands 真实执行链路**：本地 Windows 不能运行 Linux-only 的 OpenHands runtime；本次只能验证环境导入与 case 适配，完整执行仍需 Linux 服务器小样本验证。
2. **网络与模型依赖**：OpenHands/mini-swe-agent 依赖安装和 Qwen API 调用受网络、密钥与配额影响，代码只能保证可自愈的环境准备与清晰错误，不能伪造密钥或绕过外部服务。
3. **SWE-bench Pro 评测脚本**：不同实例的系统依赖由官方 run script 控制，host-process 不能保证所有实例都满足非 root 权限；本方案只移除 DynSTEER 引入的无条件 root apt bootstrap。
