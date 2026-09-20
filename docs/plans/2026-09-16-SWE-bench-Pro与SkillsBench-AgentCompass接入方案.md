# SWE-bench Pro 与 SkillsBench AgentCompass 接入方案

## 1. 目标与边界

本方案回答两个问题：如何固化 AgentCompass 的 case list；现有 DynSTEER adapter 能否通过 AgentCompass 完成 SWE-bench Pro 与 SkillsBench 的执行、轨迹转换、原生评分和环境依赖引入。本方案不修改 `../AgentCompass`，也不执行大规模实验。

AgentCompass 固定核查版本为 `04d138a1c1decd2c9caa8c2659c698d7ffb677b4`，即 `dynsteer/adapter/agentcompass/runtime.py` 中已有的 `AGENTCOMPASS_COMMIT`。

## 2. Case List 生成方式

AgentCompass 的官方枚举入口是 `tools/list_samples.py`：加载内置组件后，构造 `harness="none"` 的 `RunRequest`，再调用 `BENCHMARKS.create(benchmark_id).load_tasks(request)` 并输出 `task.task_id`。

DynSTEER 已在 `load_task_records()` 中复用同一接口，并增加白名单投影与缓存：

1. 调用 `bootstrap_runtime(data_dir=...)`。
2. 调用 `load_builtin_components()`。
3. 构造 `harness="none"`、`model="task-catalog"` 的 request。
4. 调用目标 benchmark 的 `load_tasks()`。
5. 只保留 `task_id`、`question`、`category` 和少量 SWE 分层元数据。

两个 benchmark 的 ID 来源固定如下：

| Benchmark | case ID 来源 | 分层字段 |
| --- | --- | --- |
| SWE-bench Pro | 数据集 `instance_id` | task metadata 的 `repo` |
| SkillsBench | 任务目录名 `sample_dir.name` | task metadata 的 `metadata.category` |

case list 必须先通过上述接口读取全量 ID，再按 seed `202608` 分层抽样，固化到实验配置的 `case_ids`。禁止运行时随机抽样，也禁止从 trajectory、detail 或结果目录反向推导 ID。

目标规模沿用主实验方案：SWE-bench Pro 主实验 100、消融 50，按 `repo` 分层；SkillsBench 主实验 100、消融 50，按 `category` 分层。SkillsBench 官方 v1.1 共 87 个任务，不足 100 时固化全量 87 个，并在配置 metadata 记录 `available_case_count` 与 `requested_case_count`，不重复任务补齐。

## 3. 当前 Adapter 能力核查

`BaseAgentCompassHarness` 已覆盖完整执行链路：

1. `list_case_ids()` 从 AgentCompass task catalog 排序返回 ID。
2. `start_case()` 校验 case 存在。
3. `advance_case()` 调用 `run_agentcompass_case()`。
4. runtime 将 `sample_ids` 固定为 `[case_id]`，`k=1`，`avgk=true`，`reuse=false`。
5. 返回的 ACTF trajectory 调用 `convert_actf_steps()` 转为 DynSTEER raw step。
6. SWE 子类将 `resolved` 映射为 0/1 native score。
7. SkillsBench 子类将 verifier `reward` 映射为 partial reward native score。
8. detail 脱敏只保留 status、correct、trajectory、native evaluation 白名单和 provenance。

因此两个 benchmark 的 replay 主实验和消融不需要重写 harness；只需补齐运行环境、依赖开关、case list 和配置。

## 4. 运行配置

SWE-bench Pro 推荐使用 AgentCompass 官方推荐组合：

```json
{
  "harness": "mini_swe_agent",
  "environment": "docker",
  "model_api_protocol": "openai-chat",
  "enabled_recipes": ["swebench_pro_docker_prebaked"],
  "benchmark_params": {
    "eval_timeout": 3600
  },
  "harness_params": {
    "launch_mode": "local",
    "install_strategy": "install_if_missing",
    "step_limit": 250,
    "cost_limit": 3.0,
    "command_timeout": 2400,
    "timeout": 12000
  },
  "model_params": {
    "temperature": 0,
    "max_tokens": 32768,
    "timeout": 3600
  }
}
```

SkillsBench 推荐使用 `openhands`：

```json
{
  "harness": "openhands",
  "environment": "docker",
  "model_api_protocol": "openai-chat",
  "benchmark_params": {
    "data_version": "1.1",
    "execute_timeout_multiplier": 20.0,
    "verifier_timeout_multiplier": 8.0
  }
}
```

模型凭据只通过 `MODEL_API_KEY` 与 `MODEL_BASE_URL` 环境变量进入 AgentCompass。实验配置和产物不得写入 key；`base_url` 如必须写入，只能写非敏感 endpoint，不得写 token。

## 5. 依赖引入

AgentCompass 要求 Python 3.12+。项目基础 Python 下限已统一为 3.12；但 AgentCompass 的 `openai>=2.41.1` 与 DynSTEER/ToolSandbox 的 `openai==1.17.0` 不兼容，不能进入同一个 uv 锁文件。正式执行前必须准备 `.venv-agentcompass` 独立环境，并以 `--no-deps` 方式安装 DynSTEER 源码。

AgentCompass 基础依赖包含 `datasets`，足够 SWE-bench Pro 和 SkillsBench 的 `load_tasks()`。SWE-bench Pro 自带评测脚本，host 不需要官方 `swebench` 包。mini-SWE-agent local 模式由 AgentCompass 的 `DependencySpec("mini-swe-agent")` 按需安装到控制器解释器；SkillsBench 推荐的 OpenHands 在任务容器内安装或复用，不应污染 DynSTEER host 环境。

当前缺口是：DynSTEER runtime 没有暴露 AgentCompass 的 `auto_install_dependencies`，也未把它传入 `run_evaluation_request()`。需要新增配置字段并显式传参。任务容器内依赖仍由 AgentCompass harness/environment 负责安装；DynSTEER 不绕过 AgentCompass 直接向容器执行安装命令。

## 6. 代码修改项

1. `metadata.agentcompass.auto_install_dependencies` 新增为 bool，默认 false。
2. `run_agentcompass_case()` 把该值传给 `run_evaluation_request(auto_install_dependencies=...)`。
3. catalog 加载保持 `auto_install_dependencies=false`，避免只查看 case list 时触发安装；缺少基础依赖时直接报可安装错误。
4. API 文档补充 case list 语义、Python 3.12 环境要求和依赖开关。
5. 用 fake runtime API 构造测试，验证 false/true 分别传给 `run_evaluation_request()`。

## 7. 数据与 Pilot

1. 通过启动脚本的 `--source ../AgentCompass` 自动准备 `.venv-agentcompass` Python 3.12 独立环境；脚本会安装 AgentCompass 后再以 `--no-deps` 安装本项目。
2. 用 `load_task_records()` 分别导出 SWE-bench Pro 与 SkillsBench 全量 ID。
3. 记录每个 benchmark 的全量数量、分层计数和缺失原因。
4. 按 seed `202608` 抽样并固化 case list。
5. SWE-bench Pro 每个模型先跑 2 个 case、`repeats=1`；SkillsBench 同样先跑 2 个 case。
6. Pilot 检查 AgentCompass run detail、ACTF step、native score、trajectory token、错误状态和脱敏产物。
7. Pilot 通过后再扩大到主实验；空图消融臂单独生成 `.no-milestone-graph.json` 并只写入对应 data_root/adapted_cases。

## 8. 验收标准

1. 两份固化 case list 可被 `expand_experiment_matrix()` 展开，且所有 ID 都通过 AgentCompass catalog 校验。
2. `default` 结果包含 native score、native evaluation status 和 provenance。
3. `dynsteer_replay` 可消费同一 identity 的 Default trajectory，并输出 DynSTEER score 与 runtime metrics。
4. ACTF cost 满足一次归属：native token 只写入代表 raw step，派生 raw step 为 0；真实缺失保持 null。
5. SWE resolved 只有 0/1；SkillsBench reward 缺失时 native score 与 completion 均不入分母，不用 0 冒充。
6. 关闭 `auto_install_dependencies` 时不发生 host 依赖安装；开启时只允许 AgentCompass 自己执行可信 extra 安装。
7. 所有运行产物可追溯 AgentCompass commit、run_id、run_dir、detail path 和 detail digest。

## 附录A. 没有把握的部分

1. OpenHands 在不同 Docker 版本、镜像源和资源限制下的安装稳定性需要 pilot 确认；本方案不假设首次安装必然成功。
2. SkillsBench 官方 v1.1 只有 87 个任务，无法满足名义上的 100 个主实验 case；应记录全量数量，不得重复任务。
3. SWE-bench Pro 的 `dockerhub_tag` 是否覆盖当前抽样任务需要 catalog 与 metadata 核查；缺失时应记为 implementation failure，不可切换数据集或隐藏失败。
4. Provider usage 依赖 mini-SWE-agent/OpenHands 转换后的 `prompt_tokens_len` 与 `completion_tokens_len`；不同 provider 或协议可能缺失，必须保持 null。
