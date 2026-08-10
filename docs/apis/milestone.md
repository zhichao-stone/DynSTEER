# Milestone 自动生成 API

## 配置

`MilestoneGenerationConfig` 仅包含：

- `use_origin_milestone: bool`：是否优先使用 benchmark 原生图。
- `simulated_path_count: int`：独立模拟路径数，必须大于等于 5。
- `generator: JsonObject`：生成器 LLM 配置。

有效去重路径门槛固定为 `max(N - 2, 3)`。生成器对一个 case 串行调用 N 次，每次只生成一条路径；单条响应不合法只淘汰该路径。

## 公开任务视图

`GeneratorTaskView` 只向生成器暴露 instruction、公开 asset、Agent 可见工具 schema、公开 environment schema、output contract、evidence catalog 和 invariant catalog。

ToolSandbox 使用其专用消息可见性转换。SWE-bench Pro 与 SkillsBench 共用 AgentCompass contract。工具 evidence 和 invariant ID 根据契约文本生成稳定哈希标识，不依赖数组位置。

## 单路径生成协议

模板输入：

```text
TASK: {task}
EVIDENCE: {evidence}
INVARIANTS: {invariants}
PATH_INDEX: {path_index}
PATH_COUNT: {path_count}
STRATEGY: {strategy}
```

响应必须是精确 JSON schema：

```json
{
  "atoms": [
    {
      "name": "...",
      "description": "...",
      "evidence_id": "...",
      "expected_literal_index": 0,
      "terminal": true
    }
  ],
  "minefield_invariant_ids": []
}
```

`expected_literal_index` 是 evidence 的 `allowed_expected_literals` 零基索引；`expected_policy=none` 时必须为 0。只有最后一个 atom 的 `terminal` 为 `true`。

## 编译规则

路径对齐 signature 为：

```text
(evidence_id, canonical(expected), occurrence_index)
```

名称、描述、terminal 和 strategy 不参与对齐或路径去重。节点、节点间有向先后关系和 minefield 都必须获得有效去重路径中至少 `ceil(K * 2 / 3)` 条路径支持。边在达到门槛后进行 DAG 校验和传递约简。

### `compile_task_case()`

```python
compile_task_case(
    view: GeneratorTaskView,
    config: MilestoneGenerationConfig,
    llm: BaseLLM,
    response_output_dir: Path | None = None,
) -> tuple[MilestoneGraph, GenerationReport]
```

`response_output_dir` 用于诊断性记录每条独立路径的 LLM 原始文本。配置后，响应会在 JSON 解析前以 UTF-8 写入：

```text
<response_output_dir>/path_<两位序号>_<strategy>.txt
```

对应 `path_summaries[].response_record` 保存绝对文件路径、字符数、SHA-256、是否以 JSON Markdown 围栏开头，以及首个非空字符是否为 `{`。因此，即使响应随后发生 JSON 或 schema 校验错误，原始文本仍可审计。未配置目录时不写文件，`response_record` 为 `null`。

原始响应可能复述任务中的用户数据，只应写入受控实验目录，不应直接输出到终端日志或提交到公开仓库。

## GenerationReport

`GenerationReport` 字段：

- `generation_status`：`generated` 或 `auto_rejected`。
- `requested_path_count`：请求的独立路径数 N。
- `minimum_valid_path_count`：`max(N - 2, 3)`。
- `valid_path_count`：通过单路径响应解析的路径数。
- `distinct_path_count`：按对齐后 signature 序列去重、实际进入共识的路径数。
- `candidate_atom_count`：全部有效路径中的局部 atom 总数。
- `aligned_atom_count`：有效去重路径中不同 atom signature 数量。
- `consensus_node_count`：达到三分之二支持度并写入图的节点数。
- `graph_valid`：是否成功构造有效图。
- `reasons`：路径拒绝或图拒绝原因。
- `path_summaries`：每条请求路径的状态、策略、去重、计数和可选原始响应记录摘要。

## Reliability 指标

单 case `metrics` 同时包含：

- `strict.ged_similarity` 与 `strict.node_set_f1`：比较完整名称、描述、expected 和 matching route 等严格标签。
- `structural.ged_similarity` 与 `structural.node_set_f1`：只比较 terminal 和按 target、selector、operator、namespace、evaluator_hint 排序的 constraint shape。
- `node_count_delta`、`edge_count_delta`：prediction 相对 reference 的数量差。
- `fgw`：仅基于 strict descriptor 的可选 FGW 指标。

## Reliability 结果覆盖策略

以下两个入口使用相同的覆盖规则：

```bash
bash ./scripts/start_milestone_reliability.sh --exp <配置文件路径>
bash ./scripts/start_milestone_reliability_no_docker.sh --exp <配置文件路径>
```

结果目录固定为 `results/milestone/<experiment_id>`：

- 默认情况下，如果该目录已存在，程序会在适配 case 或调用 LLM 前拒绝启动，不修改任何已有结果；
- 显式传入 `--force` 时，程序会先删除整个同名实验目录，再从空目录重新生成；旧 case、`summary.json`、`index.json` 和 `llm_outputs` 都不会保留；
- 需要保留历史批次时，应为配置使用新的 `experiment_id`，不要传入 `--force`。

`experiment_id` 只能包含字母、数字、点、下划线和连字符，不能包含路径分隔符。删除前还会确认目标是当前项目 `results/milestone` 的直接子目录。
