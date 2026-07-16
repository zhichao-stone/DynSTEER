# DynSTEER ready frontier 早停与空报告修复方案

> 生成日期：2026-07-15  
> 当前状态：方案设计，不落地代码。  
> 问题来源：基于 `results/toolsandbox/run_0_qwen-plus-latest_user_qwen-plus-latest`、`runs/toolsandbox/run_0_qwen-plus-latest_user_qwen-plus-latest`、`data/toolsandbox/adapted_cases` 与 `logs/2026-07-14.log` 的核查结果。  
> 约束遵循：本方案遵循 `docs/constraints/code.md`，先在 `docs/plans` 输出修复方案；不主动提交 git；后续代码落地需补充测试、API 文档与必要中文注释。

## 1. 问题结论

当前异常不是 `data/toolsandbox/adapted_cases` 中 milestone 图转换错误，也不是展示面板单纯误显。核心问题有三类：

1. `ready frontier no progress` 早停策略按原始消息 step 计数，会在 agent 发出 tool call 后、execution environment 返回 tool result 前立即停止 case。
2. 策略提前终止时没有补写未完成 milestone 的 `fail` / `missing` synthetic stage，导致 `report.json` 中 `stage_reports` 为空或缺项。
3. `overall_score([])` 当前返回 `1.0`，使空阶段报告被误判为满分。

这三点共同造成截图中的现象：

- `find_current_city_low_battery_mode`、`find_current_city_low_battery_mode_all_tools`、`find_temperature_low_battery_mode` 的 m0 都没有匹配。
- 轨迹最后停在 `set_low_battery_mode_status({"on": false})` 的 agent tool call。
- 后续 environment tool result 没有进入轨迹，因此状态快照中 `low_battery_mode` 仍为 `true`，m0 不能通过。
- `report.json` 的 `stage_reports=[]`，但 `overall_score=1.0`，汇总分被明显抬高。

## 2. 现象证据

### 2.1 受影响 case

| case | 当前 coverage | 当前 score | termination_code | 主要问题 |
|---|---:|---:|---|---|
| `find_current_city_low_battery_mode` | `none` | `1.0` | `milestone_no_progress:m0` | 停在关闭低电量 tool call 后，没有 tool result |
| `find_current_city_low_battery_mode_all_tools` | `none` | `1.0` | `milestone_no_progress:m0` | 同上 |
| `find_temperature_low_battery_mode` | `none` | `1.0` | `milestone_no_progress:m0` | 同上 |
| `modify_contact_with_message_recency` | `partial` | `0.9216` | `milestone_no_progress:m3` | 已跑偏，但策略终止后没有补 m3/m4 pending stage，分数偏高 |

### 2.2 典型轨迹断点

`find_current_city_low_battery_mode` 当前轨迹末尾：

```text
#28 agent tool_call get_current_location {}
#29 environment tool_result PermissionError: Location service is not enabled.
#30 agent message 询问是否启用定位
#31 user message Yes
#32 agent tool_call set_location_service_status {"on": true}
#33 environment tool_result PermissionError: Location service cannot be turned on in low battery mode
#34 agent message 询问是否关闭低电量模式
#35 user message Yes
#36 agent tool_call set_low_battery_mode_status {"on": false}
```

缺失的关键下一步应是：

```text
#37 environment tool_result set_low_battery_mode_status 成功
```

只要 #37 进入轨迹，m0 的 `SETTING.low_battery_mode=false` 才有机会匹配，后续 location / wifi / city milestone 才会进入 ready frontier。

### 2.3 adapted 数据核查结论

`data/toolsandbox/adapted_cases` 与 `runs/*/raw_summary.json` 中的 milestone graph 节点和边一致。低电量 case 的 m0 定义为：

```json
{
  "namespace": "SETTING",
  "expected": {
    "rows": [
      {
        "low_battery_mode": false
      }
    ],
    "columns": [
      "low_battery_mode"
    ]
  }
}
```

该期望是合理的。问题不在 adapted graph，而在运行期过早停止与报告补全逻辑。

## 3. 根因分析

### 3.1 早停策略按原始消息 step 观察无进展

当前 `evaluate_runtime_step(...)` 对每个新增 step 都会：

1. 将 step 追加到 trajectory。
2. 对当前 ready milestone 打分。
3. 调用 `update_ready_frontier_progress_watch(...)` 更新停滞计数。
4. 当连续 `ready_frontier_patience=8` 次无有效提升时，生成 `milestone_no_progress:*` 终止决策。

低电量链路在 m0 成功前需要经历：

- 第一次工具调用失败。
- agent 询问用户。
- 用户确认。
- 第二次工具调用失败。
- agent 再询问用户。
- 用户再确认。
- agent 发出关闭低电量 tool call。

这些 step 都没有改变 `low_battery_mode`，因此 m0 评分一直是 0。当前策略在第 8 次无进展观察时刚好停在关闭低电量 tool call 之后，execution environment 还没有机会执行。

更准确的修复语义应是：所有涉及执行进展的 step 计数都不应以单条原始消息为单位，而应以 agent 发起的完整行为为单位。一个完整 agent step 定义为：

```text
Agent -> X 发起行为
X -> Agent 返回反馈
```

其中：

- `Agent -> EXECUTION_ENVIRONMENT` 的工具调用，应以对应 `EXECUTION_ENVIRONMENT -> Agent` 的 tool result 作为闭合点。
- `Agent -> USER` 的询问或回复，应以后续 `USER -> Agent` 消息作为闭合点；如果会话自然结束，则以自然结束边界作为闭合点。
- `USER -> Agent`、`EXECUTION_ENVIRONMENT -> Agent` 等反馈消息本身不应单独增加 agent step 计数，它们只用于闭合前一个 agent step。

这样可以保证一次计数里同时包含 agent 的执行行为和行为反馈，避免当前“只看到 tool call、尚未看到 tool result”就判断无进展。

### 3.2 策略提前终止绕过 pending stage 补全

当前 evaluator 中，只有 `not terminated_by_policy` 时才执行：

```python
pending_stage_reports = pending_milestone_stage_results(task_case, state)
```

因此：

- 自然结束 case 可以得到 pending milestone 的 `fail` / `missing` stage。
- 策略提前终止 case 不会得到 pending stage。

这会造成 `stage_reports` 缺失。例如 `modify_contact_with_message_recency` 的 raw diagnostics 已经显示 m3/m4 未完成，但 `report.json` 只保留 m0/m1/m2 三个已通过阶段，导致 score 偏高。

### 3.3 空 stage 报告被算成满分

当前 `overall_score(stage_reports, minefield_score)` 在 `stage_reports` 为空且没有 minefield 时返回 `1.0`。这在“没有 milestone 的空任务”语义下也许曾经是工程兜底，但对当前 DynSTEER milestone 评估是不成立的。

只要 task 有 milestone graph，空 `stage_reports` 就应表示没有任何 milestone 证据被结算，不能给满分。

### 3.4 task_description mismatch 仅是日志噪音

日志中出现大量：

```text
task_description与首条用户消息不一致
```

核查后确认，`task_description_mismatched(...)` 只作为 warning 日志输出条件使用，不影响评分、早停、coverage、报告生成或展示数据。并且运行轨迹只记录初始 sandbox message index 之后的新消息；原始用户任务消息通常在起始 context 中，不在本次运行新增 trajectory 里。因此这条 warning 容易把后续确认消息 `Yes` 或 end_conversation 消息误报为“不一致”，建议删除相关函数与日志分支。

## 4. 修复目标

本次修复建议达成以下目标：

1. 不再在未闭合的 agent tool call 后立即触发 `ready frontier no progress` 策略终止。
2. 任意策略终止都能生成未完成 milestone 的 synthetic pending stage。
3. 空 `stage_reports` 不再产生满分。
4. 所有对外 `step_count` 语义尽量统一为 agent 发起的完整步骤数；如仍需保留原始消息数量，应显式命名为 `raw_step_count` 或 `message_count`。
5. 前端展示使用补全后的 `stage_reports` 后，能看到 fail / missing，而不是一片 `not_started`。
6. 保留 ready frontier no-progress 策略的价值：明显跑偏或长期无进展时仍可提前停止。
7. 单元测试覆盖低电量链路的“完整 agent step 闭合后再计数”边界。

## 5. 推荐修复方案

### 5.1 P0：no-progress 按完整 agent step 计数

推荐将 `ready frontier no progress` 的 patience 观察从“每条原始消息 step”改为“每个完整 agent step”。早停策略只能在 agent 发起的行为已经收到反馈后更新停滞计数并做终止判断。

推荐规则：

```text
如果当前原始消息不能闭合一个 agent step：
    允许记录 milestone attempt 诊断；
    但不增加 ready frontier stale 计数；
    不触发 no-progress 终止。

如果当前原始消息闭合了一个 agent step：
    基于该闭合边界上的最新 trajectory / snapshot 更新 ready frontier watch；
    若连续 N 个完整 agent step 都无有效提升，再触发 no-progress 终止。
```

这样可以保留 milestone 的实时匹配能力，又避免切断工具调用的因果闭环。

建议实现方式：

1. 在运行期状态中记录最近一个未闭合的 agent outbound step，或在 trajectory 辅助函数中识别当前原始消息是否闭合 agent step。
2. 新增内部函数：

```python
def _closes_agent_step(trajectory: Trajectory, step: TrajectoryStep) -> bool:
    ...
```

3. `evaluate_runtime_step(...)` 仍可在每条原始消息后做 milestone matching，但 `_ready_frontier_no_progress_decision(...)` 只在 `_closes_agent_step(...)` 为真时更新 patience。
4. runtime metrics 中的 `step_count` 改为完整 agent step 数；如 display 或诊断仍需要原始轨迹消息数，新增 `raw_step_count` 或 `message_count`。
5. 后续 environment tool result 进入时，如果 milestone 通过，则 checkpoint 推进 frontier 并重置 watch；如果仍不通过，再按完整 agent step 增加一次 stale。

不建议的替代方案：

- 仅把 `ready_frontier_patience` 调大：可以缓解当前 case，但不能解决“tool call 后立即截断”的结构性问题。
- 禁用 no-progress 策略：会重新放大长流程跑偏时的成本。
- 只忽略所有 tool_call 评分：会削弱以工具调用本身为 milestone 的 case。
- 仅在 agent tool_call 上 defer：能修复当前低电量问题，但没有统一所有 step_count 的语义，后续仍容易在 agent/user 多轮确认链路中出现类似误计数。

### 5.2 P0：策略提前终止也补 pending milestone stage

推荐把 pending stage 补全从 `if not terminated_by_policy` 分支中移出来，形成统一收尾逻辑。

建议逻辑：

```python
pending_stage_reports = pending_milestone_stage_results(task_case, state)
if pending_stage_reports:
    state.stage_reports.extend(pending_stage_reports)
elif not terminated_by_policy:
    append finish settlement
```

设计语义：

- 策略提前终止：补未完成 milestone 的 fail/missing stage，但不追加 finish stage。
- 自然结束且仍有 pending milestone：补 pending stage，不追加 finish stage。
- 自然结束且所有 milestone 完成：追加 finish stage。
- fatal minefield 终止：同样补 pending stage，让报告可解释。

需要避免重复补全：

- `pending_milestone_stage_results(...)` 已基于 `state.matched_settlements` 与 `state.match_attempts` 生成未完成 milestone。
- 若某些 synthetic pending stage 已存在，应避免重复追加。可以在 evaluator 收尾前构造已有 `synthetic_pending_milestone` 的 milestone id 集合过滤，或者让 `pending_milestone_stage_results(...)` 内部过滤已有报告。

推荐优先在 evaluator 收尾处过滤，避免改变 runtime helper 的单一职责。

### 5.3 P0：空 stage report 不得满分

推荐修改 `overall_score(...)`：

```python
def overall_score(stage_reports: list[StageEvaluationResult], minefield_score: float) -> float:
    if not stage_reports:
        return 0.0
    ...
```

影响评估：

- 当前三个 `none + 1.0` case 会回到 0 分或由 pending stage 给出 0 分。
- 该逻辑符合“没有任何 milestone 证据即没有完成任务”的评估语义。

如果担心未来存在无 milestone graph 的任务，可以在 `_runtime_report(...)` 中根据 graph 是否为空决定 score，而不是让 `overall_score(...)` 承担 graph 语义。但当前项目已要求 adapted case 带 milestone graph，建议采用更简洁的 `overall_score([])=0.0`。

### 5.4 P1：删除 task_description mismatch 诊断

核查后确认：`task_description_mismatched(...)` 只在 `DynSTEEREvaluator.evaluate(...)` 中作为 warning 日志输出条件使用，不参与评分、早停、coverage、报告分数或前端展示。

当前实现还存在一个额外问题：`task_case_snapshot(...)` 的 `initial_user_message_excerpt` 来自运行期新增 trajectory 的第一条 user message。对于 ToolSandbox，它经常是后续确认语句 `Yes` 或 end_conversation 触发消息，而不是原始任务请求，因此该 warning 在当前流程中主要制造噪音。

建议直接删除：

1. 删除 `task_description_mismatched(...)`。
2. 删除 `DynSTEEREvaluator.evaluate(...)` 中的 `evaluator_task_description_mismatch` warning 分支。
3. 删除不再使用的 `_initial_user_message_excerpt(...)`，并从 `task_case_snapshot(...)` 中移除 `initial_user_message_excerpt` 字段，除非后续另有明确审计需求。
4. 删除或更新对应测试与 API 文档描述。

该删除不影响评估流程，只减少误导性日志和无效诊断字段。

### 5.5 P1：run_configs 中显式配置 patience 的读取策略

当前 `ready_frontier_patience` 主要由环境变量 `DYNSTEER_READY_FRONTIER_PATIENCE` 控制，`run_configs.json` 中同名字段不会直接覆盖该值。已在 `.env.example` 与 `.env` 中补充：

```text
DYNSTEER_READY_FRONTIER_PATIENCE=16
```

后续如果希望实验配置可复现，建议采用：

```text
run_configs.json 显式字段 > 环境变量 > 默认值 8
```

不过该项不是当前截图问题的根因。若优先降低改动面，本轮可以只在文档中说明临时验证方式：

```powershell
$env:DYNSTEER_READY_FRONTIER_PATIENCE = "16"
```

正式代码修复仍应以“不截断未闭合 tool call”为主。

### 5.6 P1：展示面板无需先做重逻辑改造

只要后端 `report.json` 中补齐 pending stage，`display` 理论上可以展示 fail/missing 阶段。若补齐后仍出现 `not_started`，再检查：

- `display/build.py` 的 `_active_stage_definitions(...)`
- `display/build.py` 的 `_stage_reports(...)`
- `display/index.html` 中 `stage_reports` 与 graph node 的匹配逻辑

当前不建议先从前端兜底修，因为会掩盖后端报告不完整的问题。

## 6. 涉及模块与代码边界

### 6.1 必改模块

| 文件 | 修改点 |
|---|---|
| `dynsteer/evaluate/step.py` | no-progress 仅按完整 agent step 更新 patience |
| `dynsteer/evaluate/evaluator.py` | 策略终止后也补 pending stage；统一收尾逻辑；删除 mismatch warning |
| `dynsteer/evaluate/scoring.py` | `overall_score([])` 改为 0 |
| `dynsteer/evaluate/runtime.py` | 删除 `task_description_mismatched(...)` 与不再使用的首条用户消息摘要 helper |
| `tests/test_ready_frontier_progress.py` 或新增测试 | 覆盖完整 agent step 计数与空报告得分 |

### 6.2 可能改动模块

| 文件 | 修改点 |
|---|---|
| `dynsteer/evaluate/runtime.py` | 如需过滤重复 pending stage，可加轻量 helper |
| `dynsteer/adapter/toolsandbox/utils/convert.py` | 补充原始首条用户消息 metadata |
| `docs/apis/evaluate.md` | 更新策略提前终止与 pending stage 行为 |
| `docs/apis/harness.md` | 如调整 run config 字段优先级则同步更新 |
| `.env.example` / `.env` | 增加 `DYNSTEER_READY_FRONTIER_PATIENCE=16` |

### 6.3 不建议改动的边界

本次不建议修改：

- ToolSandbox 原生 scenario 与 starting context。
- `data/toolsandbox/adapted_cases` 中低电量 case 的 milestone 期望。
- `ToolSandboxConstraintScorer` 的 snapshot similarity 语义。
- 前端用“未匹配就强行标失败”的兜底逻辑。

## 7. 测试方案

### 7.1 单元测试：no-progress 按完整 agent step 计数

新增或扩展测试，构造一个 ready m0 长期 0 分的运行期状态：

1. 前 8 次观察让 watch 达到 patience。
2. 其中未闭合的 agent tool_call 不增加 stale 计数。
3. 断言 `evaluate_runtime_step(...)` 在未闭合 tool_call 上不返回 stop decision。
4. 下一次 environment tool_result 闭合 agent step 后，如果仍无进展，再断言可以返回 stop。
5. 下一次 environment tool_result 如果使 m0 PASS，断言 checkpoint 成功且 watch 重置。

测试应尽量使用项目已有接口，不新建只为测试服务的生产接口。

### 7.2 单元测试：策略终止也补 pending

构造一个 fake harness / fake state 或直接测试 evaluator 收尾 helper：

1. graph 有 m0、m1。
2. 运行中没有 matched milestone。
3. 模拟 `terminated_by_policy=True`。
4. 断言最终 `report.stage_reports` 包含 m0 fail 与 m1 missing。
5. 断言 `milestone_coverage="none"`、`overall_score=0.0`。

如果不抽 helper，测试会较重；建议将 evaluator 收尾中“补 pending 或 finish”的逻辑抽成小的私有函数，便于覆盖。

### 7.3 单元测试：空报告得分

直接覆盖：

```python
assert overall_score([], 0.0) == 0.0
assert overall_score([], 1.0) == 0.0
```

如果后续决定保留“无 milestone graph 空任务满分”的语义，则该逻辑应在 `_runtime_report(...)` 中显式处理，而不是放在 `overall_score(...)` 的默认分支里。

### 7.4 回归测试：当前 toolsandbox 问题 case

在 API key 和 ToolSandbox 依赖可用时，建议重跑：

```powershell
uv run python main.py --benchmark toolsandbox --workers 1
```

重点核查：

- `find_current_city_low_battery_mode` 的 trajectory 不再停在 `set_low_battery_mode_status` tool_call。
- `find_temperature_low_battery_mode` 的 trajectory 不再停在 `set_low_battery_mode_status` tool_call。
- 若 agent 后续继续失败，报告也应显示 fail/missing stage，而不是 `stage_reports=[]`。
- run summary 中 `average_overall_score` 不再被 `none + 1.0` 抬高。

如需快速验证，可先只跑这三个 case，后续再完整跑 8 个 case。

## 8. 验收标准

修复后应满足：

1. `find_current_city_low_battery_mode*` 不会在关闭低电量 tool call 后立即停止。
2. 策略提前终止 case 的 `report.json.stage_reports` 不为空，且包含未完成 milestone 的 synthetic pending stage。
3. 无 milestone matched 的 case，`overall_score` 不得为 1.0。
4. 对外 `step_count` 与 early-stop patience 计数统一为完整 agent step；原始消息数量如需保留必须显式命名。
5. `summary.json.stage_count=0` 时，如果 graph 有 milestone，报告中必须能解释哪些 milestone fail/missing。
6. `logs` 中仍可看到策略提前终止原因，但不再出现大量误导性的 mismatch warning 和空报告满分。
7. 新增测试通过，且不引入重复 helper 或大范围兼容旧接口代码。

## 9. 实施顺序

建议按以下顺序落地：

1. 修改 `overall_score([])`，先消除最危险的满分假象。
2. 修改 evaluator 收尾逻辑，让策略终止也补 pending stage。
3. 修改 no-progress 的完整 agent step 计数语义，解决低电量 case 被截断的根因。
4. 补充单元测试。
5. 运行相关 pytest。
6. 在可用环境中重跑目标 case。
7. 如报告已经正确，重新生成 display 数据。
8. 删除 `task_description_mismatched` warning 逻辑和无效摘要字段。

## 10. 风险与回滚

### 10.1 风险

- `overall_score([])=0.0` 可能改变历史无 milestone 任务的默认评分语义。
- no-progress 按完整 agent step 计数后，会比原始消息 step 计数更宽松，但这是必要的因果闭环成本。
- 策略终止后补 pending stage 会让当前汇总分明显下降，这是修正后的真实表现，不应视为回归。

### 10.2 回滚方式

如出现不可接受的成本上升，可临时：

1. 保留 `overall_score([])=0.0` 与 pending stage 补全。
2. 将 no-progress defer 限制为“agent tool_call 且 recipient 为 execution environment”。
3. 通过环境变量临时调低或调高 `DYNSTEER_READY_FRONTIER_PATIENCE` 做对照实验。

不建议回滚 pending stage 补全和空报告得分修复，因为它们直接关系到报告可信度。

## 11. 附录A. 项目中没有把握实现的模块部分

1. ToolSandbox 原生 role 的一步推进细节  
   没有完全把握的是 `ExecutionEnvironment.respond()` 对所有工具调用的消息生成时机是否始终为“一次 agent tool_call 后下一次 advance 必定生成 tool_result”。目前从现有轨迹看符合该模式，但需要用目标 case 重跑确认。

2. 全量 benchmark 的模型行为稳定性  
   当前分析基于一次 `qwen-plus-latest` 运行记录。代码修复可以保证不截断未闭合 tool call，但不能保证 agent 后续一定完成 city/weather 任务，因为模型策略本身可能仍然跑偏。

3. 展示面板在补齐 pending stage 后的最终视觉效果  
   后端报告补全后，前端大概率能显示 fail/missing；但 `display/build.py` 与 `display/index.html` 有 active stage 过滤逻辑，仍需在重新生成 `display/data.js` 后目视核查。

4. 历史无 milestone graph 任务的评分语义  
   当前项目主流程要求 adapted case 带 milestone graph，因此 `overall_score([])=0.0` 是合理的。但如果历史上存在无 graph 的临时调试任务，需要确认是否接受该行为变化。

