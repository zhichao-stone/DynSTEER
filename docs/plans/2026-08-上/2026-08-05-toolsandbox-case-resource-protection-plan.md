# ToolSandbox case 级进程资源保护方案

## 1. 目标

为 ToolSandbox 实验增加 case 级超时、CPU/内存/进程数限制和强制回收能力，避免 `role.respond()`、execution environment 或第三方工具进入无限循环后长期占用服务器资源。

保护目标是：单个异常 case 可被隔离、诊断并标记失败，后续 case 仍可继续；正常 case 的执行语义和结果格式保持不变。

## 2. 根因与设计约束

- OpenAI client 的 request timeout 只限制 HTTP 请求，不能限制 `role.respond()` 的工具执行、数据库快照和第三方代码。
- `ThreadPoolExecutor` 的 future timeout 不能终止已经运行的 Python 线程；仅在主线程等待超时不足以回收资源。
- 因此必须以“每个 case 一个受监管的子进程”为隔离边界，在父进程中监控 deadline，并在超时时终止整个进程组。
- 资源限制应只作用于 case worker，不能让一个 case 的 CPU/内存异常拖垮整个实验容器。

## 3. 配置设计

在统一 experiment 配置或 benchmark manifest 增加可选字段：

```json
{
  "case_timeout_seconds": 900,
  "case_cpu_seconds": 600,
  "case_memory_mb": 4096,
  "case_process_limit": 64,
  "case_kill_grace_seconds": 5,
  "continue_on_case_timeout": true
}
```

- `case_timeout_seconds`：墙钟时间上限，默认 900 秒。
- `case_cpu_seconds`：Linux worker 的 CPU 时间上限，默认不启用或取墙钟上限的 2/3。
- `case_memory_mb`：worker 虚拟内存上限，默认不启用，避免与 benchmark 自身内存需求冲突。
- `case_process_limit`：限制 worker 及其子进程数量，默认 64。
- `case_kill_grace_seconds`：TERM 到 KILL 的宽限期。
- `continue_on_case_timeout`：超时后记录失败并继续后续 case；默认开启。

配置解析时校验正整数，并将字段放入 `HarnessRunConfig.metadata`；不改变现有配置时的默认行为。

## 4. 执行架构

### 4.1 父进程监管器

在 `dynsteer/experiment/runner.py` 增加 case supervisor：

1. 父进程创建一个 case worker（Linux 优先使用 `fork`，必要时提供 `spawn` 兼容路径）。
2. worker 执行现有的 `run_default_case`、`run_replay_case` 或 `run_evaluate_case`，不改变 case 内部逻辑。
3. worker 通过临时结果文件或 multiprocessing queue 返回序列化结果和结构化异常。
4. 父进程按 `case_timeout_seconds` 轮询 worker。
5. 超时顺序执行：发送终止信号 → 等待 `case_kill_grace_seconds` → 强制 kill；同时回收整个 process group，防止 ToolSandbox 启动的 shell/子进程遗留。
6. 记录 `case_timeout`、运行时长、worker PID、最近日志和资源峰值，并按配置决定继续或终止实验。

不能直接用 `future.result(timeout=...)` 代替该方案，因为线程无法被安全终止。

### 4.2 Worker 资源限制

worker 启动后、加载 benchmark 前设置 Linux `resource.setrlimit`：

- `RLIMIT_CPU`：CPU 秒数；触发后由父进程捕获并记录。
- `RLIMIT_AS`：虚拟内存上限（仅在配置启用时设置）。
- `RLIMIT_NPROC`：子进程/线程数量上限。

同时创建独立进程组（`os.setsid` 或等价方式），父进程终止时按组回收。Windows 环境不设置 Unix rlimit，但仍使用墙钟 watchdog 和进程树回收接口。

## 5. 日志与结果语义

每个 case 至少输出以下结构化事件：

- `case_started`：case_id、method、repeat、worker_pid、limits。
- `case_finished`：elapsed、cpu_seconds、peak_memory、结果路径。
- `case_timeout`：deadline、elapsed、最近阶段、worker_pid。
- `case_resource_limit`：CPU/内存/进程数限制类型。
- `case_worker_crashed`：退出码和 stderr 尾部。

超时 case 写入与普通失败一致的 case-level result，`score` 为空，`raw.termination_reason` 使用 `case_timeout` 或具体资源限制；实验汇总继续统计已完成 case，并明确失败计数。

## 6. 代码修改范围

- `dynsteer/harness/model.py`：增加经过校验的资源限制配置访问接口，或统一从 metadata 读取。
- `dynsteer/experiment/runner.py`：增加 process supervisor、超时终止、进程组回收和 worker 结果传递。
- `dynsteer/log.py`/输出模块：增加上述结构化事件和超时结果字段。
- `dynsteer/adapter/toolsandbox/harness.py`：保留现有 role retry；补充 case_id/阶段日志，不在此处实现不可终止的线程 watchdog。
- `main.py`/配置解析：读取并校验新增字段。

不直接修改第三方 ToolSandbox 源码；如需限制其外部命令，依赖 worker 的 process group 和 `RLIMIT_NPROC` 统一兜底。

## 7. 测试与验收

- 单元测试：正常 worker 返回、异常退出、墙钟超时、CPU 限制、子进程树回收、超时后继续下一个 case。
- 集成测试：构造一个会 `while True` 的模拟 role，确认父进程在 deadline 后终止且没有残留子进程。
- ToolSandbox 回归：正常 case 的 trajectory、score、结果路径与改造前一致。
- 运行验收：在服务器上先用 1～3 个 case 验证，确认日志能显示 `case_started`/`case_timeout`，再恢复 509 case 实验。

## 8. 未覆盖部分

- 无法从父进程安全中断已经在同一进程内执行的 Python 线程；因此不采用纯线程 watchdog 作为最终方案。
- 具体默认 timeout 需要根据 Toolsandbox case 的 P95/P99 耗时校准，初始值只用于防止无限等待。
