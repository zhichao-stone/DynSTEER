# 2026-08-03 ToolSandbox 并行工具调用闭包修复方案

## 1. 问题与目标

### 1.1 问题现象

ToolSandbox DEFAULT 实验会偶发抛出：

```text
AgentStepProtocolError: 上一个 agent outbound 尚未闭合，不能继续接收新的 agent outbound:
pending_step_id=s16, current_step_id=s17
```

已观察到至少两个不同 case、不同 step index 触发同一错误：

- `find_days_till_holiday_insufficient_information`：`s18 -> s19`；
- `modify_reminder_with_recency_latest`：`s16 -> s17`。

这不是固定 scenario 数据损坏。ToolSandbox 的 Agent role 允许一次模型响应返回多个 `tool_calls`，并将其依次写为多条 `AGENT -> EXECUTION_ENVIRONMENT` SANDBOX row；ExecutionEnvironment 随后批量执行并写回对应的多条结果。合法序列可以是：

```text
s16  AGENT -> EXECUTION_ENVIRONMENT  tool_call(id=A)
s17  AGENT -> EXECUTION_ENVIRONMENT  tool_call(id=B)
s18  EXECUTION_ENVIRONMENT -> AGENT  tool_result(id=A)
s19  EXECUTION_ENVIRONMENT -> AGENT  tool_result(id=B)
```

当前 `AgentStepTracker` 只有一个 `pending_outbound`。摄入 `s16` 后，它要求先出现 `EXECUTION_ENVIRONMENT -> AGENT` 才允许下一个 Agent outbound，因此在 `s17` 处把合法并行调用误判为协议错误。

### 1.2 为何今天开始出现

提交 `6f85a98` 修复了 ToolSandbox 历史轨迹完整性：`ToolSandboxHarness.advance_case()` 从只读取当前最新 SANDBOX row，改为读取并筛选全部新增历史 rows。

旧实现面对一次响应中的两个工具调用，只会暴露最后一条 row，并把前一条调用静默漏掉；tracker 因看不到完整序列而不会报错。新实现正确保留了所有并行调用，进而暴露 `AgentStepTracker` 长期存在的单 pending 假设。

因此不能通过恢复“只取最新 row”来消除异常，否则会重新造成轨迹、snapshot 和 replay source 不完整。

### 1.3 修复目标

1. 支持同一次 Agent 响应产生的多个并行工具调用。
2. 使用 `openai_tool_call_id` 将工具结果与对应调用精确配对，不能仅按最近调用或 route 猜测。
3. 每个 Agent tool outbound 分别形成一个 `AgentStepClosure`，分别触发 milestone matching 和 agent step 计数。
4. 保持串行工具调用、`AGENT -> USER` 对话闭包和终局消息自闭合行为不变。
5. Default、在线 DynSTEER、Replay 共用同一 tracker 语义，不在三个消费者中分别实现兼容逻辑。
6. 保留 ToolSandbox 完整历史采集，不丢弃任何并行调用或结果。
7. 对缺失、重复或无法配对的并行调用 ID 明确 fail fast，避免生成错误闭包。

## 2. 闭包与计数语义

### 2.1 闭包粒度

并行工具调用不合并成单个大闭包。每个工具调用独立闭合：

```text
closure A = [tool_call(id=A), tool_result(id=A)]
closure B = [tool_call(id=B), tool_result(id=B)]
```

选择独立闭包而不是批次闭包，原因如下：

- 当前定义以每个 `Agent -> X` outbound 为一个 agent step；
- milestone 需要分别观察每个工具调用，不能只使用并行批次中最后一条同 route step；
- `ingest(raw_step)` 仍可在每个 result 到达时返回至多一个 closure，无需改变消费者接口；
- 串行调用与并行调用采用相同计数口径。

因此两个并行工具调用完成后：

- `AgentStepTracker.completed_count += 2`；
- Default 进度增加 2；
- 在线 DynSTEER 分别进行两次 closed-agent-step 评估；
- Replay 使用相同顺序重放两次闭包。

### 2.2 配对规则

按以下优先级匹配反馈：

1. 若 feedback 带非空 `openai_tool_call_id`，必须与 pending tool outbound 的同名 ID 精确匹配。
2. feedback 带 ID 但不存在对应 pending outbound 时，抛出包含 feedback step 和 call ID 的 `AgentStepProtocolError`，不得回退到 route 匹配。
3. feedback 不带 ID 且相反 route 只有一个 pending outbound 时，保留现有 sender/recipient 串行回退。
4. feedback 不带 ID 且存在多个同 route pending outbound 时，抛出“并行反馈缺少 correlation id”的明确异常，不得按 FIFO 或最近调用猜测。

`openai_tool_call_id` 已保存在 `TrajectoryStep.raw`：ToolSandbox row 转换会写入该字段，trajectory serializer 将 raw 字段展平，公共 loader 会把未知顶层字段恢复到 `TrajectoryStep.raw`。本次不在 `TrajectoryStep` 上新增重复字段。

### 2.3 允许并行的 outbound 范围

只有满足以下全部条件时，tracker 才允许在已有 pending outbound 时接收新的 outbound：

1. 已有 pending 和新 step 均为 `EventType.TOOL_CALL`；
2. route 均为 `Actor.AGENT -> Actor.ENVIRONMENT`；
3. 每个并行 outbound 都有非空 `openai_tool_call_id`；
4. 所有 pending call ID 唯一。

以下情况继续视为协议错误：

- pending `AGENT -> USER` 尚未收到用户反馈时又出现新的 Agent outbound；
- tool call 与普通 Agent message 混合并行；
- 第二个并行 tool call 缺少 ID；
- 并行 outbound 使用重复 ID。

这样只放宽 ToolSandbox 已证实存在的并行工具协议，不把任意连续 Agent 消息都解释为合法并行。

### 2.4 closure steps 的内容

- 串行路径保持当前行为：closure 包含 outbound、闭包前的中间 raw steps 和最终反馈。
- 并行工具路径采用相关性闭包：每个 closure 只包含自身 tool outbound、可明确归属于该调用的步骤及匹配 result；不把兄弟 tool call/result 重复塞入其他 closure。
- 多 pending 期间若出现既无 correlation ID、又无法唯一归属的中间反馈，明确报错，不构造交叉污染的 closure。
- 所有 raw steps 仍完整保存在 `Trajectory.steps`，raw-step minefield 检查不受 closure 分组影响。

### 2.5 finalize 语义

保持现有终局规则：

- 无 pending 时返回 `None`；
- 唯一 pending 为 `AGENT -> USER` 且 event type 为 `MESSAGE/FINAL` 时允许自然自闭合；
- pending tool call 不在 `finalize()` 中伪造结果或自闭合；
- 多个 pending tool call 未收到结果即结束时不得合并为虚假 closure。

本次不扩展为“结束时自动容错未返回工具结果”。

## 3. 代码修改方案

### 3.1 `dynsteer/model.py`

修改 `AgentStepTracker`，将单 pending 状态改为可表示多个并行 tool outbound 的状态。

建议字段：

```python
pending_outbounds: dict[str, TrajectoryStep]
pending_steps: dict[str, list[TrajectoryStep]]
completed_count: int
```

字典 key 使用 tracker 内部稳定 key：

- 有 `openai_tool_call_id`：`tool:<call_id>`；
- 串行且无 call ID：`step:<step_id>`。

不得只以 `step_id` 配对 tool result，因为 result 有自己的 step ID；也不得只以 route 作为 key，因为并行调用具有相同 route。

#### 3.1.1 `ingest()`

保持返回类型：

```python
def ingest(self, raw_step: TrajectoryStep) -> AgentStepClosure | None
```

处理顺序：

1. 若为 Agent outbound：
   - 无 pending：建立一个 pending；
   - 有 pending：调用内部并行合法性检查；
   - 合法并行 tool call：按 call ID 新增 pending；
   - 其他连续 outbound：抛出结构化协议错误。
2. 若不是 Agent outbound 且无 pending：返回 `None`。
3. 若 feedback 带 call ID：精确查找 pending key并校验 reciprocal route。
4. 若 feedback 不带 call ID：仅在 reciprocal candidate 唯一时使用现有 route 回退。
5. 找到匹配 pending 后，只向该 pending 的 steps 追加当前反馈，构造 closure，删除该 pending，`completed_count += 1`。
6. feedback 无法唯一归属时抛出明确异常；普通无关 raw step 在唯一 pending 场景继续按现有行为追加。

#### 3.1.2 内部辅助逻辑

辅助函数放在 `AgentStepTracker` 内部函数区，避免把 tracker 细节扩散到 ToolSandbox adapter：

- `_is_parallel_tool_outbound(step)`：判断允许的并行 route/event；
- `_tool_call_id(step)`：从 `step.raw["openai_tool_call_id"]` 读取并清理非空字符串；
- `_pending_key(step)`：生成 pending key；
- `_matching_pending_key(feedback)`：按 ID/route 解析唯一 pending；
- `_complete_pending(key)`：完成指定 pending 并更新计数。

若实现中能在不降低可读性的情况下合并其中函数，应优先保持简洁，不为每个单行表达式创建中转函数。

#### 3.1.3 异常信息

新的 `AgentStepProtocolError` 至少包含：

- pending step IDs；
- current step ID；
- current `actor/recipient/event_type`；
- current call ID；
- 相关 pending call IDs；
- 错误类别：重复 ID、缺少 ID、未知 result ID 或 route 不匹配。

不得吞掉或降级这些协议错误。

### 3.2 `dynsteer/adapter/toolsandbox/utils/trace.py`

原则上不修改业务转换逻辑。当前 `sandbox_rows_to_step_dicts()` 已把 `openai_tool_call_id` 写入 step raw 数据，足以供 tracker 使用。

实施时只需核查并通过测试锁定：

- tool call 与 tool result 均保留相同 `openai_tool_call_id`；
- `trajectory_from_sandbox_rows()` 转换后该字段仍位于 `TrajectoryStep.raw`；
- serializer/loader 往返后该字段不丢失。

只有测试证明字段在某一边界丢失时，才在对应现有转换函数中修复；不新增另一套 ToolSandbox correlation 映射。

### 3.3 `dynsteer/adapter/toolsandbox/harness.py`

不回退 `advance_case()` 的完整历史读取，保留：

- `get_all_history_snapshots=True`；
- 按 `sandbox_message_index` 升序排序；
- 重复 index 检查；
- `last_sandbox_message_index` 在转换成功后推进。

并行闭包属于统一 tracker 职责，不应在 harness 中重排为伪串行序列，也不应丢弃前面的 tool call。

### 3.4 消费路径

下列路径继续调用同一个 `AgentStepTracker.ingest()`，预计无需修改：

- `dynsteer/harness/outputs.py::write_default_case_outputs()`；
- `dynsteer/evaluate/evaluator.py::evaluate()`；
- `dynsteer/evaluate/evaluator.py::evaluate_trajectory()`。

实施时必须验证：

- 每条 tool result 至多产生一个 closure；
- 并行结果全部返回后产生与 outbound 数量相同的 closure；
- `completed_agent_steps` 和 `tracker.completed_count` 保持一致；
- replay 即使 result 顺序与 outbound 顺序不同，也按 call ID 产生正确 closure；
- early termination 后未消费的其他 pending 不会被误计为已闭合 step。

不在三个调用方增加 ToolSandbox 特判或并行 pending 容器。

### 3.5 指标语义

`runtime_metrics.step_count` 继续表示已闭合 Agent outbound 数量：

- 两个串行 tool call：2；
- 一次模型响应中的两个并行 tool call：2；
- 未返回 result 的 pending tool call：不计入已闭合 step。

`raw_step_count` 仍表示完整 trajectory raw step 数量，不受 tracker 分组影响。本次不把 `step_count` 改为 LLM 请求轮数；如后续需要“模型推理轮数”，应新增独立指标，不能复用本字段。

## 4. 测试方案

### 4.1 新建 `tests/test_agent_step_tracker_parallel.py`

直接使用公开模型构造 `TrajectoryStep`，覆盖 tracker 核心状态机：

1. 单 tool call/result 串行闭合行为不变。
2. `AGENT -> USER -> AGENT` 对话闭合行为不变。
3. 两个并行 tool outbound、同序 result：生成两个独立 closure，call ID 不串线。
4. 两个并行 tool outbound、逆序 result：仍按 call ID 正确配对。
5. 第一个 result 到达后只移除对应 pending，另一个仍可继续闭合。
6. 并行 outbound 缺少 call ID 时明确失败。
7. 重复 outbound call ID 时明确失败。
8. result 携带未知 call ID 时明确失败，不回退最近 pending。
9. 多 pending 下 result 缺少 call ID 时明确失败。
10. pending 用户消息后连续 Agent outbound 仍失败。
11. 唯一无 ID 串行 feedback 仍可按 reciprocal route 闭合。
12. `finalize()` 只自闭合终局 Agent-to-User message，不自闭合工具调用。
13. `completed_count` 只在真实 closure 产生时增长。

核心 tracker 分支行覆盖率不低于 80%。

### 4.2 新建 `tests/test_toolsandbox_parallel_tool_closure.py`

使用 ToolSandbox 风格 row 字典，经现有公开转换链路验证真实字段契约：

```text
s16 call A
s17 call B
s18 result A
s19 result B
```

测试内容：

1. `sandbox_rows_to_step_dicts()` 保留两组相同 call ID。
2. `trajectory_from_sandbox_rows()` 后 call ID 位于 `TrajectoryStep.raw`。
3. tracker 摄入转换后的四个 step 不抛异常。
4. 两个 closure 分别为 `(s16, s18)` 与 `(s17, s19)`。
5. serializer/loader 往返后 replay 仍产生相同闭包。
6. 并行结果逆序时仍能精确配对。

测试不调用外部 LLM，不依赖随机模型再次生成并行调用。

### 4.3 Default / Replay 消费契约测试

在已有 evaluator/harness 测试位置补充最小 fake harness 或固定 trajectory 测试：

1. Default 消费一批包含两个 outbound 的 advance，再消费两个 results，不报错且进度累计 2。
2. Replay 同一 trajectory 不报错，`runtime_metrics.step_count == 2`。
3. 在线 DynSTEER 对两个 closure 分别调用 closed-agent-step 评估。
4. raw-step minefield 仍对两个 tool call 各执行一次，不因 closure 延迟而漏检。

测试必须调用现有入口，不为测试新增业务接口。

### 4.4 回归测试

至少运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agent_step_tracker_parallel.py -q
.venv\Scripts\python.exe -m pytest tests/test_toolsandbox_parallel_tool_closure.py -q
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall -q dynsteer tests
git diff --check
```

测试后清理 `.coverage`、`.pytest_cache` 和 `__pycache__` 等可再生中间产物。

## 5. API 文档修改

### 5.1 `docs/apis/evaluate.md`

更新 Agent step closure 协议：

- tracker 支持多个带唯一 correlation ID 的并行 tool outbound；
- tool result 优先按 `openai_tool_call_id` 配对；
- 无 ID 仅允许唯一 reciprocal pending 的串行回退；
- 每个 tool outbound 独立闭合和评分；
- 未闭合 outbound 不触发 milestone matching、checkpoint 或 step count。

### 5.2 `docs/apis/harness.md`

补充 ToolSandbox 历史采集与并行调用契约：

- `advance_case()` 必须返回全部新增 SANDBOX rows；
- 同一 advance 中允许出现多个连续 Agent tool outbound；
- harness 不负责把它们伪串行化；
- correlation ID 由 row 转换保留并交给公共 tracker。

### 5.3 `docs/apis/experiment.md`

若文档已有 `step_count/average_agent_step_count` 定义，补充：并行 tool call 按独立已闭合 outbound 计数，而不是按单次模型响应计数。

## 6. 实施顺序

1. 先增加 tracker 并行状态机单元测试，复现 `s16/s17` 错误。
2. 修改 `AgentStepTracker` pending 数据结构和 ID 配对逻辑。
3. 增加 ToolSandbox row -> trajectory -> tracker 集成测试。
4. 增加 Default、在线 DynSTEER、Replay 消费契约测试。
5. 更新 evaluate、harness、experiment API 文档。
6. 运行目标测试、全量 pytest、编译和 diff 检查。
7. 检查未使用 import、未使用函数和重复兼容逻辑。
8. 清理测试缓存，保留测试源文件。

## 7. 验收标准

1. 两个或多个带唯一 `openai_tool_call_id` 的连续 Agent tool outbound 不再触发“上一个 outbound 尚未闭合”。
2. 同序和逆序 tool results 都与正确 outbound 配对。
3. 每个并行 tool outbound 产生独立 closure，closure 不包含兄弟调用/结果。
4. 两个并行工具调用完整返回后 `completed_count == 2`。
5. milestone matching 能分别观察两个 tool call，不只观察批次最后一个。
6. Default、在线 DynSTEER 和 Replay 对同一 trajectory 使用一致闭包与计数结果。
7. ToolSandbox 完整历史 rows 和 snapshots 不回退、不丢失。
8. 缺失、重复、未知 correlation ID 的并行协议明确失败，不静默猜测。
9. 串行 tool、对话消息、终局 finalize 行为保持不变。
10. 核心 tracker 测试覆盖率不低于 80%，全量测试、编译和 `git diff --check` 通过。

## 8. 不采用的方案

### 8.1 回退为只读取最新 SANDBOX row

不采用。该方案只是重新隐藏异常，会丢失并行调用、结果和 mapped snapshots，破坏 replay source 完整性。

### 8.2 遇到第二个 outbound 时自动闭合第一个

不采用。没有 tool result 就不构成闭包，会提前触发 milestone matching 并产生错误 step count。

### 8.3 将整批并行调用合并为一个 closure

不采用。相同 route 下 milestone boundary 可能只选择最后一个工具，且会把多个 Agent outbound 错计为一个 step。

### 8.4 无 ID 时按 FIFO 猜测多个并行结果

不采用。结果可能逆序返回，FIFO 会静默串错调用和结果；多 pending 无 correlation ID 应明确失败。

## 9. 冗余与约束检查

- 并行状态只在公共 `AgentStepTracker` 实现一次。
- ToolSandbox adapter 只负责保留已有 correlation metadata，不实现第二套闭包逻辑。
- Default、在线评估、Replay 不分别增加 benchmark 特判。
- 不新增只转调 tracker 的中转函数。
- 不修改 ToolSandbox 上游源码或原始 scenario。
- 不删除 `docs/constraints`、`docs/plans` 中任何已有文档。
- 新增核心逻辑包含中文接口说明、明确异常和对应单元测试。

## 附录A. 项目中没有把握实现的模块部分

目前不能证明所有未来 benchmark 的并行 Agent outbound 都提供 correlation ID。因此本方案只对“带唯一 `openai_tool_call_id` 的并行工具调用”提供确定性支持；对于多个同 route pending 且 feedback 无 ID 的情况选择 fail fast，不做 FIFO 或最近调用推断。

此外，`agent_step_count` 是否还需要增加“单次 LLM 响应批次数”这一独立指标属于后续实验指标设计问题。本方案遵循当前“每个已闭合 Agent outbound 计一步”的既有契约，不新增或重定义模型推理轮数指标。
