# 2026-09-22 Pilot Runtime Failure Fixes

## 背景

两个 pilot 暴露出不同的运行时适配问题：

- SWE-bench Pro 官方镜像自带 `ENTRYPOINT ["/bin/bash"]`。`LocalDockerExecutor.start()` 只覆盖了 `command`，导致 `sleep infinity` 参数被 bash 当作脚本参数处理，容器启动后立即退出。
- SkillsBench 的 `verifier/test.sh` 在 Windows 数据源中是 CRLF 换行，复制进 Linux 容器后 bash 将 `` 保留在命令 token 中，verifier 以 exit code 2 失败。
- SWE 启动失败时，harness 会用新的异常摘要覆盖 `probe_environment()` 的底层结果，运行记录缺少容器状态、退出码和日志。
- Linux 复跑又暴露两个问题：probe 输出包含 `pwd/repo-root/HEAD/status` 四段，但解析时只消费了前两段，导致所有 HEAD 都被误读为 `/app`；进入异常分支后又发现 runtime 没有保存 `raw_output_dir`，诊断写入被 `AttributeError` 二次掩盖。

运行前提：代码可在 Windows 上编辑，但实验 controller 和任务容器都在 Linux 服务器执行。因此不处理 Windows text mode 写文件可能引入的 CRLF；只处理源码字节本身已经是 CRLF 的任务输入。

## 修改方案

1. `dynsteer/runtime/docker.py`
   - 默认保留任务镜像 entrypoint，并把 command 覆盖为 `["/bin/sh", "-c", "sleep infinity"]`。
   - `DockerRunSpec.override_entrypoint=True` 时才设置 `entrypoint=["/bin/bash"]` 和 `command=["-c", "sleep infinity"]`，供 SWE 官方镜像使用。
   - 启动后重载容器状态，若容器不是 `running`，抛出包含状态与退出码的异常。
   - 新增 `diagnostics()`，返回容器 ID、名称、State、退出码、容器错误、启动错误和尾部日志。

2. `dynsteer/adapter/swebench_pro/runtime.py`
   - 启动或 probe 失败时，先读取 Docker 诊断，再停止容器。
   - 保留 probe 原始 `exit_code/stderr` 等字段，并追加 `docker` 诊断字段。
   - 将诊断写入 run 目录的 `docker_diagnostics.json`，避免 raw summary 或 trajectory 后续丢失细节。
   - 保存 `raw_output_dir`，完整解析 probe 四段输出，并把容器初始工作目录、仓库根、HEAD、期望 HEAD 与初始 git status 全部写入 probe。
   - HEAD 与 sample `base_commit` 一致即视为初始状态有效；允许官方镜像因测试文件 checkout 而呈现初始脏状态。

3. `dynsteer/adapter/swebench_pro/harness.py`
   - 捕获启动异常时优先沿用 runtime 已记录的 probe，只补充异常类型与摘要，不再覆盖底层结果。

4. `dynsteer/adapter/skillsbench/runtime.py`
   - 任务 mirror 时对所有后续会进入构建上下文、skill 目录或 verifier 的 `.sh` 与 `.bash` 文件做 CRLF/CR 转 LF。
   - 任务文档、二进制文件和 Python 等其他文件保持原始字节复制。
   - 构建或启动失败时写出 `docker_diagnostics.json`；镜像构建错误继续完整写入 `docker-build.log`。
   - 保留任务镜像 entrypoint，避免绕过任务自定义初始化。

5. `dynsteer/experiment/metrics.py`
   - 空的有效分数集合不再产生 `0.0` 均值，改为 `null`。
   - `valid_native_score_count` 继续显式给出有效分数数量，避免 DEFAULT infrastructure failure 被误读为真实 0 分。

6. 回归测试
   - 用 fake Docker client 分别验证默认保留 entrypoint 与显式覆盖 entrypoint，并验证启动状态检查。
   - 验证 SWE probe 失败会保留 stderr、Docker 诊断并落盘。
   - 验证 Skills mirror 会规范化全部任务 shell 脚本，同时保留非 shell 文件字节。
   - 验证 DEFAULT 缺失分数不会在 native 完成率和 repeat 均值中被显示为 0。

## 验收标准

- 单元测试覆盖上述核心路径。
- `git diff --check` 无格式问题。
- 现有可用测试套件通过；本机没有运行 pilot 的 Docker daemon 时，不声称完成端到端重跑。

## 2026-09-22 追加：DEFAULT 原生评估器路径修复

### 排查结论

所有 `swebench_pro_pilot` DEFAULT 轨迹均已正常生成，`agent.patch` 与 `patch.json` 也存在。`native_stderr.log` 显示外部 `swe_bench_pro_eval.py` 在读取 `--patch_path` 时抛出 `FileNotFoundError`。原因是 `patch_path` 使用项目 cwd 下的相对路径，而子进程 cwd 被切换到 `data_root/source`，外部脚本因此把相对路径解析到了错误目录。

### 修改方案

1. `dynsteer/adapter/swebench_pro/evaluator.py`
   - 进入函数后统一 resolve `source_root`、`data_root` 与 `output_dir`。
   - `patch_path`、`native_dir`、外部 evaluator 脚本和 `run_scripts` 均使用绝对路径。
   - `eval_samples.jsonl` 继续按外部源码约定传相对名称，由 `data_root/source` cwd 解析。

2. `tests/adapter/swebench_pro/test_evaluator.py`
   - 新增回归测试：在项目 cwd 下传入相对 `output_dir`，fake 子进程在 `data_root/source` 执行时检查 patch 文件可读，并断言传给子进程的关键路径均为绝对路径。

### 验证

- 运行 `tests/adapter/swebench_pro/test_evaluator.py`。
- 现有 pilot 的 `patch.json` 已存在；修复后无需重跑 agent，可在服务器上只重试 DEFAULT 原生评估路径验证真实 evaluator。

## 附录A. 项目中没有把握实现的模块部分

- 无法在本机确认服务器上的实际镜像全部包含 `/bin/bash` 与 `sleep`。选择 bash 是因为现有 agent 执行命令和官方 evaluator 都已经依赖 bash；如遇极简镜像，需要改为按镜像探测 shell。
- SkillsBench 的 `deepseek-v4-pro / quantum-numerical-simulation` 镜像构建失败来自 qutip 依赖安装，和 verifier CRLF 是独立问题；本次只修复 verifier 换行问题，不在没有复现环境的情况下盲目修改依赖清单。
- SkillsBench `environment/skills` 中有若干无后缀 Python 脚本源文件为 CRLF。Python 在 Linux 可解析 CRLF 源码，且它们不是 shell 脚本，因此不做字节改写。
- 真实 SWE-bench Pro evaluator 依赖 Linux 服务器上的外部源码、镜像和 Docker daemon；当前会话只能用单元测试复现路径语义，不能直接完成端到端原生评估。
