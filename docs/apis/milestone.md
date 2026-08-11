# Milestone 多路径生成 API

## 1. 生成入口

```python
def compile_task_case(
    view: GeneratorTaskView,
    config: MilestoneGenerationConfig,
    llm: BaseLLM,
    response_output_file: Path | None = None,
) -> tuple[MilestoneGraph, GenerationReport]: ...
```

该接口只在 benchmark 适配阶段、Agent 启动前调用。合法 `GeneratorTaskView` 下，无论 LLM 超时、非法 JSON、零路径或全部路径不可模拟，接口都返回 schema 合法的 `MilestoneGraph`；graph 可以为空。只有 view 本身违反程序不变量，例如 evidence ID 重复或 TOOL_CALL evidence 缺少真实工具名，才抛出 `ValueError`。

`MilestoneGenerationConfig` 只包含 `use_origin_milestone`、范围 1～8 的 `max_candidate_path_count`、`enable_repair` 和 `generator`，不新增算法配置。

## 2. 公开任务视图

`GeneratorTaskView` 字段为：

- `benchmark / task_id / case_id / language`；
- `turns: tuple[GeneratorTurn, ...]`，每项包含稳定 `turn_id`、原始 `instruction` 和 `source_ref`；
- `public_assets`；
- first-user boundary 的 `initial_state`；
- Agent 最终可见的 `tool_schema`；
- 确切工具名形式的 `environment_rules`；
- TOOL_CALL `evidence_catalog`。

人工 reference、matcher、evaluation、verifier、trajectory 和 final state 不进入 view 或 compiler payload。`GoalContract`、`ToolEffect`、`ArgumentBinding`、`PublicInvariant` 和 output contract 已删除。

## 3. 路径 JSON

generation 与 refinement 使用同一最小 schema：

```json
{
  "paths": [{
    "turns": [{
      "turn_id": "turn_0",
      "disposition": "executable",
      "operations": [{
        "evidence_id": "tool_call_search_holiday_675a1156fc",
        "arguments": {"holiday_name": "Christmas Day"}
      }],
      "forbidden_evidence_ids": []
    }]
  }]
}
```

arguments 是普通 JSON，仅辅助 prompt 和 recovery 规则匹配，不参与 operation 身份或静态来源证明。路径去重键只包含 turn ID、disposition、evidence ID 序列和排序后的 forbidden evidence。

parser 逐路径容错：顶层错误得到零路径；超上限时截断；单条 path/turn/operation 错误只淘汰该 path；附加字段忽略并记录；unknown evidence、turn 缺失或乱序、非 executable 含 operation 会使对应路径不可模拟。

## 4. 环境模拟与 recovery

compiler 深拷贝 initial state，按路径顺序离线模拟，不调用真实工具。环境规则统一为：

```json
{
  "applies_to_tools": ["set_wifi_status"],
  "applies_when_arguments": {"on": true},
  "state_path": "SETTING.low_battery_mode",
  "blocked_value": true,
  "recovery_tool": "set_low_battery_mode_status",
  "recovery_arguments": {"on": false},
  "recovered_value": false
}
```

阻塞时递归模拟 recovery。recovery tool 不可见、规则非法或形成 cycle 时，只淘汰对应路径。LLM 已显式给出正确 recovery 时不会重复插入。compiler 不插入 status getter，也不根据工具名猜测隐含状态效果。

## 5. 多路径聚合

operation 节点按 turn 和具体 evidence 计算最小出现次数：

```text
mandatory_count(turn, evidence_id)
    = min(count(path, turn, evidence_id) for path in simulatable_paths)
```

重复调用使用 `(turn_id, evidence_id, occurrence_index)` 对齐，不会被 set 合并。只有所有路径共同成立的 occurrence 先后关系才生成边，之后执行传递约简。相邻非空 turn 之间连接前一轮共同 sink 与后一轮共同 root。

milestone metadata 只保留 `turn_id`、`evidence_id`、`occurrence_index` 和 `necessity_basis=path_intersection`。不会生成 response fallback 节点。

minefield 只来自同一 turn 的 `forbidden_evidence_ids` 路径交集，severity 固定 `fatal`，penalty 固定 1.0。

## 6. Refinement 反例搜索

出现解析/模拟错误、没有可模拟路径、规范化后少于两条不同路径，或第一轮存在初步共同 operation 时，最多调用一次 refinement。第二轮同时：

1. 修复结构、unknown evidence 和环境错误；
2. 重新核查每条路径完整性；
3. 尝试为每个初步共同 operation 找到完整绕过路径。

第二轮只要存在可模拟路径，就使用其返回的完整 ensemble；否则回退第一轮。compiler 不比较 round quality、不选择最短路径，也不选择单条最佳路径。

## 7. 空图与报告

`GenerationReport.empty_reason` 取值语义：

| 原因 | 含义 |
|---|---|
| `no_operation_required` | 可模拟路径均不需要工具 |
| `no_common_operation` | 可模拟路径有工具，但没有共同具体 operation |
| `no_simulatable_path` | 两轮均无可模拟路径 |
| `planner_failed` | LLM 调用均失败 |
| `invalid_response` | 响应无法解析 |

报告同时包含 returned/parsed/simulatable/final path 数、最终采用轮次、逐路径淘汰原因、recovery 前后规范化序列、被反例移除的初步共同 operation，以及两轮摘要。指定 `response_output_file` 时，两轮原始响应与 SHA-256 均会保留。

## 8. Reliability

人工 reference 只用于生成完成后的评价，不进入 task view。primary 为：

```text
operation_topology_exact =
    operation tool-name multiset exact
    and mandatory occurrence topology exact
    and fatal minefield tool multiset exact
```

operation、topology 和 minefield 分别报告 precision、recall、F1 与 exact。response/disposition 只做 diagnostic。空 graph 仍进入全部 case 分母，并通过 reference/generated 空图 confusion matrix 区分 true-empty、false-empty 与 false-nonempty。

ToolSandbox tool-name scrambling 场景仅在 reliability 阶段通过 adapter 提供 reference execution-facing→Agent-facing 别名；该映射不进入 `GeneratorTaskView`、prompt 或 generated graph。reference 的明确 `set_state` 约束按 namespace、state 字段和 snapshot operation 精确映射到实际工具名，不使用字符串 capability 模糊匹配。
