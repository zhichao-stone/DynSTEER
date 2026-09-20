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

`GeneratorTaskView` 使用 `public_state`、`simulation_state` 和 `tool_contracts` 区分输入：prompt 只序列化 benchmark、task/case ID、language、turns、Agent 可见 public assets、public state、tool schema 和 evidence catalog。simulation state、tool contracts 与 environment rules 仅供 compiler 确定性校验、recovery 和 preserve 派生，禁止进入 prompt。

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

同批相同完整图 signature 只计一票；不同批相同 signature 作为重复 observation 分别计票。节点、disposition 和 minefield 使用严格多数：`2 * support > observation_count`。edge 分母只包含同时出现两个端点的 observation：`2 * support(u,v) > eligible(u,v)`。

多数聚合后，compiler 强制加入 binding（在无严格多数时引入工具契约先验仲裁，若候选中有且仅有一个由契约显式支持的输出 selector 则采纳）、环境 recovery 和相邻 turn 的确定性依赖，校验 DAG，执行传递约简，并按工具 effect contract 派生 ToolSandbox preserve constraints。该聚合是经验性必经估计，不是形式化证明。

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

`GenerationReport` 记录请求数、返回/解析/合法图数、accepted observation、全局唯一 signature、同批重复、目标是否达到、聚合节点/边/minefield、candidate summaries、validation issues、aggregation support 和每批响应 digest。原始响应按 `batch_01`～`batch_04` 增量写入独立文件。

## 7. Reliability 与实验重复

canonical semantics 分别输出 `goals`、`operations`、`topology`、`minefields` 和 `preserves`。对于批量状态目标（`cardinality="all"`），根据受影响真实行数展开等量的底层操作，消除粒度抽象层级差异对操作多重集 F1 的惩罚。origin ToolSandbox snapshot goal 会根据真实 snapshot measure、状态评分 contract 和 first-user simulation state 归一为与 generated symbolic goal 相同的 namespace/operation/cardinality/match/values 公共语义；运行期 ID、timestamp 和 producer milestone ID 只保留动态占位，不进入 identity。旧 reference minefield 未保存 reason code 时，仅在其精确 tool schema 显示缺失 required arguments 时确定性归一为 `missing_required_input`，不根据 case ID 或工具名猜测。

评测图论指标引入语义图描述符（`mode="semantic"`），基于规范化语义（而非字符级或字面值）构建 NetworkX 有向图，输出真实的 `semantic.ged_similarity` 与 `semantic.node_set_f1`，废弃易受字面值钝化的 Strict 图指标核心地位。汇总报告顶层单列 `safety_evaluation`（涵盖致命雷区正例召回率 `fatal_positive_recall`、漏报数 `fatal_minefield_miss_count`、误报数 `spurious_fatal_minefield_count` 及逐工具召回率），将 dispositions 移出多重集总分，单独报告 `turn_disposition_accuracy`。外部 FGW 依赖标记为已弃用（Deprecated）并安全停用。

reliability repeat 是独立执行维度。每个 repeat 使用隔离的 case 与 `llm_outputs` 路径，并把 `base_seed + repeat_index` 传入 OpenAI-compatible 请求。当前 30-case 配置使用 3 个 repeat，对应 seed `202608/202609/202610`。

`scripts/start_milestone_reliability.sh` 会在宿主机解析每个 benchmark 的 `benchmark.json.source_root`，将源码挂载到容器中的对应路径，再由内部脚本按相同路径加载源码。`--workers N` 使用线程池并发执行最多 N 个相互隔离的 case；case JSON 和 LLM response 文件仍按 benchmark/repeat/case 分路径写入，最终 summary 保持实验配置中的稳定顺序。

`scripts/start_milestone_reliability_no_docker.sh` 与 milestone 自动生成、后续动态评估共用项目 `.venv`。脚本按统一 `uv.lock` 精确同步环境；ToolSandbox 实验额外启用 `toolsandbox` dependency group，但继续使用 DynSTEER 锁定的 `anthropic`、`networkx`、`openai`、`polars` 和 Pydantic 等核心版本。benchmark 源码通过 `benchmark.json.source_root` 注入 Python 路径，不安装 ToolSandbox distribution，因此其旧精确依赖不会降级项目环境。后续 `start.sh`、`start_no_docker.sh`、`start_experiment.sh` 和 `start_experiment_no_docker.sh` 也复用相同依赖组与源码注入方式，不会重新 editable install。每次启动还会检查缺少 `METADATA`、重复 distribution、空 Pydantic 版本以及 ToolSandbox 导入失败；精确同步会清理旧 editable distribution 和历史残留。Windows 默认使用 `UV_LINK_MODE=copy` 和无缓存安装，避免 hardlink/共享 cache 文件锁。

ToolSandbox contract 按 Agent-facing 工具名建立，tool-name scrambling 时同步映射 output/effect 和 environment recovery rule。未核实的 selector、类型或 effect 不猜测，相关动态候选以 `contract_incomplete` 失败关闭。
