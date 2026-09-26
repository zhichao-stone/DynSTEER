# Milestone 候选图生成 API

## 1. 生成入口

```python
def compile_task_case(
    view: GeneratorTaskView,
    config: MilestoneGenerationConfig,
    llm: BaseLLM,
    response_output_file: Path | None = None,
) -> tuple[MilestoneGraph, GenerationReport]: ...
```

生成器每批请求两张彼此独立的完整候选图，默认累计 6 张有效 observation，最多请求 4 批。达到目标后立即停止；前三批不足时第四批仅用于补足。

`MilestoneGenerationConfig` 字段为：

- `use_origin_milestone`；
- `target_candidate_graph_count`，范围 2～8；
- `max_candidate_batch_count`，范围 1～4；
- `generator`。

`generator.provider` 和 `generator.model` 必填。`generator.api_key` 和 `generator.base_url` 可直接填实验私有凭据；未提供 `api_key` 时按 provider 默认环境变量读取。

没有 origin graph 的 benchmark 必须配置非空 generator；有 origin graph 的 benchmark 可以省略 generator。

目标数不得超过批次数的两倍。旧 path count 与 repair 字段不再接受。

## 2. 任务视图与隔离边界

`GeneratorTaskView` 使用 `public_state`、`simulation_state` 和 `tool_contracts` 区分输入：prompt 只序列化 benchmark、task/case ID、language、turns、Agent 可见 public assets、public state、tool schema、evidence catalog，以及从 contract 安全投影出的输出 selector/type/cardinality、state effect 字段和 executor argument 映射。完整 tool contracts、simulation state、environment rules、reference graph、expected rows 和 matcher 仅供 compiler 确定性校验、recovery 和 preserve 派生，禁止进入 prompt。

`digest()` 对完整 view 计算摘要，因此私有契约或模拟状态变化仍会使缓存失效。

## 3. 双图响应

```json
{
  "graphs": [
    {"dispositions": {}, "nodes": [], "edges": [], "minefields": []},
    {"dispositions": {}, "nodes": [], "edges": [], "minefields": []}
  ]
}
```

parser 接受 0～2 张候选以保留部分成功；超过 2 张时整批 schema 失败。节点只允许 `tool_call`、`set_state`、`emit_message`，`preserve_state` 由 compiler 派生。动态值使用 `node_output` binding，公开常量使用带精确 `source_ref` 的 `public_literal`。

`edges` 固定使用二元数组：`[["source_local_id", "target_local_id"]]`。顶层、graph、node、value source 与 minefield 均使用精确字段集合；不接受对象 edge、额外字段或旧 schema fallback。

公开状态叶节点使用 `public_state:<dot.path>` 作为 `public_literal.source_ref`；例如 `public_state:settings.wifi`。validator 会将该引用与 payload 中对应叶节点的值精确比对。

## 4. 校验、去重与聚合

每张图独立执行 turn/disposition、local ID、DAG、evidence、工具参数 schema、公开来源、producer output contract、state effect contract 和 minefield effect contract 校验。一张非法图不影响同批另一张图。

`set_state` 除 effect 外还要求 executor contract 显式声明 `state_evaluator="toolsandbox_snapshot"`。当前 compiler 只编译这一种已实现的状态评分协议；仅声明 namespace/operation、但没有可评分协议的非 ToolSandbox contract 会以 `state_contract_unscorable` 失败关闭。

候选图先执行确定性归一：无下游 consumer 的 ToolSandbox terminal 状态工具会按公开 `state_fields` 投影为 `set_state`；`MESSAGING.send` 归一为 `MESSAGING.add`；`wifi_enabled` 等唯一别名归一为契约字段；结构化日期、AM/PM 时间和数字单位 literal 允许唯一确定性派生。字段级 provenance/schema 错误只移除对应字段并记录 `field_binding_unresolved`，不会删除整个 producer；state goal 缺少必需字段时仍失败关闭。

同批相同完整图 signature 只计一票；不同批相同 signature 作为重复 observation 分别计票。同一 observation 内相同 identity 的重复节点也只计一票，`support_count <= observation_count`。

operation identity 与 binding identity 分离：tool call 存在性按 `(turn_id, evidence_id)` 聚合；state goal 按 turn、namespace、operation 和字段意图聚合；动态答案按确定性 producer 类别归一，静态答案仍使用规范化文本。节点、disposition 和 minefield 使用严格多数：`2 * support > observation_count`。edge 分母只包含同时出现两个端点的 observation：`2 * support(u,v) > eligible(u,v)`。

tool argument 先扫描全部支持者的字段并集，再逐字段独立聚合。动态 binding 或 literal binding 获严格多数时保留；optional 字段缺席获严格多数，或出现/缺席各半时，选择合法缺席并交给现有 dangling producer 修剪；required 字段无严格多数时保留 operation，并将 `argument_binding_status` 标记为 `unresolved`，不伪造 producer 或 literal。

minefield 投票身份为 `(turn_id, evidence_id, reason_code)`，`missing_inputs` 不再参与存在性拆票。核心身份当选后，从每个 observation 至多一票的合法候选中按精确 tuple 出现次数、与 `required_dynamic_inputs` 交集大小、排序 tuple 依次择优；非 `missing_required_input` reason 固定输出空 tuple。
多数聚合后，compiler 强制加入 state goal 字段的契约佐裁、环境 recovery 和相邻 turn 的确定性依赖，校验 DAG，执行传递约简，并按工具 effect contract 派生 ToolSandbox preserve constraints。tool argument binding 不启用契约佐裁，以免在无严格多数时伪造可执行参数。该聚合是经验性必经估计，不是形式化证明。

聚合后还会执行 closure 校验：state goal 引用未保留 producer 时删除该目标；tool argument binding 无法闭合时退化为 name-only milestone；disposition 降级后清理 executable 节点和悬空 edge；此外主动执行悬空无用探针剪枝（`_prune_dangling_producers`），自动修剪出度为0、无任何参数引用、非恢复节点且写集为空的冗余只读工具。binding、recovery、turn-order edge 会在 aggregation support 中分别记录依赖依据。

## 5. 动态 constraint

`Constraint.expected_template` 保存运行时 binding 模板，与静态 `expected` 不能同时非空：

```json
{"$binding":{"source_milestone_id":"m_xxx","selector":"$.person_id","cardinality":"one"}}
```

通用 scorer 从 `ScoringContext.trajectory` 和 producer milestone 的匹配 boundary 定位真实工具结果。优先按 OpenAI call ID 配对；无 call ID 时只接受唯一相邻成功结果，歧义或缺失时评分为 missing，不猜测。

ToolSandbox generated `set_state` 根据 operation、match、values 和 binding 构造 add/update/remove/set 目标 dataframe；origin adapted constraint 继续使用静态 expected。

## 6. 状态与报告

只要至少一个批次顶层 JSON/schema 成功，即返回 `generation_status=generated`，即使合法候选为空。只有所有批次均调用失败或顶层 JSON/schema 失败时返回 `generation_failed`；adapter 不会把该结果保存为成功 adapted case。

`GenerationReport` 记录请求数、返回/解析/合法图数、accepted observation、全局唯一 signature、同批重复、目标是否达到、聚合节点/边/minefield、candidate summaries、validation issues、aggregation support 和每批响应 digest，并输出 `terminal_state_projected_count`、`state_field_repaired_count`、`literal_derivation_count`、`field_unresolved_count` 与 `empty_after_terminal_majority_count`。原始响应按 `batch_01`～`batch_04` 增量写入独立文件。这些诊断保留在逐 case `generation_report` 中；`compile_task_case` 抛出的 generation_failed 会连同 `exception_stage` 与完整 traceback 写入 case JSON，`summary.json` 不再重复汇总诊断字段。

## 7. Reliability 与实验重复

canonical semantics 分别输出 `goals`、`operations`、`topology`、`minefields` 和 `preserves`。对于批量状态目标（`cardinality="all"`），根据受影响真实行数展开等量的底层操作，消除粒度抽象层级差异对操作多重集 F1 的惩罚。origin ToolSandbox snapshot goal 会根据真实 snapshot measure、状态评分 contract 和 first-user simulation state 归一为与 generated symbolic goal 相同的 namespace/operation/cardinality/match/values 公共语义；运行期 ID、timestamp 和 producer milestone ID 只保留动态占位，不进入 identity。旧 reference minefield 未保存 reason code 时，仅做 contract 可证明的确定性归一：精确 tool schema 显示缺失 required arguments 时归一为 `missing_required_input`；contract `writes` 或 `state_effect` 非空时归一为 `unsafe_side_effect`；其余 fatal 调用归一为 `unsafe_tool_call`。显式 reason code 不被覆盖，也不根据 case ID、工具名或 benchmark 名称猜测。

milestone reliability 的 `summary.json` 仅保留核心报告指标：`evaluation.tool_operation_micro`、`evaluation.tool_operation_macro`、`evaluation.fatal_minefield`、`topology.ged_similarity.mean`、`graph_return_count`、`status_counts` 与 `valid_dag_compilation_rate`。Tool operation micro 按全部 primary completed case 的逐 case TP/generated/reference 计数聚合，macro 按逐 case Precision/Recall/F1 求均值；fatal minefield 使用同一 primary 口径做 micro 聚合；DAG 返回率为 `graph_return_count / sum(status_counts.*)`。其他语义、图论、生成和使用量指标保留在逐 case JSON 或原始 response 中，仅用于排障，不进入 summary。

reliability repeat 是独立执行维度。每个 repeat 使用隔离的 case 与 `llm_outputs` 路径，并把 `base_seed + repeat_index` 传入 OpenAI-compatible 请求。当前 30-case 配置使用 3 个 repeat，对应 seed `202608/202609/202610`。

`scripts/start_milestone_reliability.sh` 会在宿主机解析每个 benchmark 的 `benchmark.json.source_root`，将源码挂载到容器中的对应路径，再由内部脚本按相同路径加载源码。`--workers N` 使用线程池并发执行最多 N 个相互隔离的 case；case JSON 和 LLM response 文件仍按 benchmark/repeat/case 分路径写入，最终 summary 保持实验配置中的稳定顺序。

`scripts/start_milestone_reliability_no_docker.sh` 与 milestone 自动生成、后续动态评估共用项目 `.venv`。脚本按统一 `uv.lock` 精确同步环境；ToolSandbox 实验额外启用 `toolsandbox` dependency group，但继续使用 DynSTEER 锁定的 `anthropic`、`networkx`、`openai`、`polars` 和 Pydantic 等核心版本。benchmark 源码通过 `benchmark.json.source_root` 注入 Python 路径，不安装 ToolSandbox distribution，因此其旧精确依赖不会降级项目环境。后续 `start.sh`、`start_no_docker.sh`、`start_experiment.sh` 和 `start_experiment_no_docker.sh` 也复用相同依赖组与源码注入方式，不会重新 editable install。每次启动还会检查缺少 `METADATA`、重复 distribution、空 Pydantic 版本以及 ToolSandbox 导入失败；精确同步会清理旧 editable distribution 和历史残留。Windows 默认使用 `UV_LINK_MODE=copy` 和无缓存安装，避免 hardlink/共享 cache 文件锁。

ToolSandbox contract 按 Agent-facing 工具名建立，tool-name scrambling 时同步映射 output/effect 和 environment recovery rule。未核实的 selector、类型或 effect 不猜测，相关动态候选以 `contract_incomplete` 失败关闭。
