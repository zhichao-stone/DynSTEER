# Milestone operation 聚合召回率离线扫描方案

## 背景

当前保存响应 replay 中，tool operation 指标为：

- micro：P=82.14%，R=70.70%，F1=75.99%；
- macro：P=79.79%，R=78.00%，F1=77.07%。

主要问题是 recall 明显低于 precision。用户希望探索是否存在聚合层面的调整，使 precision 不明显下降的同时显著提升 recall 与 F1。本阶段仅做无 LLM 离线扫描，不直接修改生产代码。

## 扫描设计

1. 重新读取 `results/milestone/toolsandbox_milestone_reliability_main` 中保存的 LLM responses。
2. 捕获每个 case 通过当前校验与 canonicalization 后的 observations。
3. 基于 compiler 当前 `_aggregate_and_compile()` 源码生成参数化副本，避免手写简化模拟造成偏差。
4. 先用原 strict-majority 参数 replay，要求生成的 operation multiset 与当前 compiler 完全一致。
5. 扫描以下维度：
   - turn disposition：严格多数、三分之一且为最大值、三分之一、plurality；
   - graph node：全严格多数、全三分之一、readonly 三分之一/write 严格、readonly 任意/write 严格、readonly 任意/write 三分之一、全任意；
   - fatal terminal conflict：保持当前整 turn 降级，并探索只删除与 fatal tool 冲突的 operation。
6. 每个 policy 重新计算：
   - operation micro；
   - operation macro；
   - operation multiset 总数；
   - aggregated FP/FN；
   - fatal exact，确认 minefield 修改未被误伤。

## 判收标准

优先寻找同时满足以下条件的 policy：

- micro F1 高于当前；
- macro F1 不低于当前；
- micro precision 降幅控制在可接受范围内，例如不超过 2 个百分点；
- recall 提升幅度显著大于 precision 损失；
- 不放松 write 工具的副作用误报控制，或能给出 readonly/write 分层理由。

## 附录A. 项目中没有把握实现的模块部分

- 降低 graph node 阈值后，动态 binding、recovery edge 和 dangling producer prune 都可能产生连带影响；因此必须调用完整 `_aggregate_and_compile()` 流程，而不能只对 canonical node 做简单 multiset 投票。
- fatal terminal conflict 的“只删冲突工具”策略可能改变安全语义；即使指标提升，也必须单独评估是否允许同一 turn 中其他工具继续保留。

## 推荐落地策略：set_state 半数支持

离线完整 replay 显示，整体降低 node 阈值会带来过多 FP；但仅降低 `set_state` 节点阈值是较优折中：

- 普通-tool_call：继续要求严格多数 `2 * support > n`；
- `set_state`：允许至少半数 `2 * support >= n`；
- turn disposition：继续严格多数；
- fatal terminal conflict：继续按整 turn 安全降级。

指标：

- micro：P 82.14% -> 81.96%，R 70.70% -> 72.64%，F1 75.99% -> 77.02%；
- macro：P 79.79% -> 82.08%，R 78.00% -> 80.48%，F1 77.07% -> 79.43%。

### 生产修改

1. `dynsteer/milestone/compiler.py` 的 `_aggregate_and_compile()`：
   - `set_state` 节点使用半数支持；
   - 其他节点保持严格多数。
2. `_compile_node()` metadata：
   - 记录 `support_policy`；
   - 半数支持的 set_state 标记为 `set_state_half`；
   - 其余保持 `graph_ensemble_strict_majority`。

### 测试修改

在 `tests/milestone/test_generation_stability.py` 增加：

1. `set_state` 3/6 支持应接受，并记录 `set_state_half`；
2. `set_state` 2/6 支持应拒绝；
3. 普通 `tool_call` 3/6 支持仍应拒绝，防止副作用工具阈值回退。
## 落地验证结果

已按上述最小策略修改生产代码，并基于保存 responses 做无网络完整 replay：

- 普通 `tool_call` 与 turn disposition 仍为严格多数；
- `set_state` operation 存在性达到半数即保留；
- 动态 binding、minefield 聚合、fatal terminal conflict 与 prompt 均保持不变；
- metadata 仅对真正靠 tie 支持保留的 `set_state` 标记 `set_state_half`。

生产 replay 指标：

| 指标 | 修改前 | 修改后 | 变化 |
|---|---:|---:|---:|
| Micro P | 82.14% | 81.96% | -0.17 pp |
| Micro R | 70.70% | 72.64% | +1.94 pp |
| Micro F1 | 75.99% | 77.02% | +1.03 pp |
| Macro P | 79.79% | 82.08% | +2.29 pp |
| Macro R | 78.00% | 80.48% | +2.48 pp |
| Macro F1 | 77.07% | 79.43% | +2.36 pp |

其他检查：

- fatal exact 保持 `67 / 92 / 97`，P=72.83%，R=69.07%，F1=70.90%；
- replay 仍只有历史 1 个 capture error：`remove_contact_by_phone_no_search_contacts_insufficient_information_alt`；
- 共 13 个 `set_state` 节点通过半数支持保留，分布于 12 个 primary case；
- 完整 milestone 测试通过：`46 passed`；
- 结果文件：`results/milestone/toolsandbox_milestone_reliability_main/summary_operation_set_state_half_replay.json`。
