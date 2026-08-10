# DynSTEER Milestone 生成最终代码修改方案

## 1. dynsteer/milestone/model.py

### 1.1 MilestoneGenerationConfig

保留 use_origin_milestone、simulated_path_count、generator 三个字段，不新增任务模式、路径执行模式或兼容字段。

保留 simulated_path_count >= 5 校验，将异常文本改为：

~~~python
if self.simulated_path_count < 5:
    raise ValueError(
        "simulated_path_count 必须大于等于 5；"
        "有效去重路径下限为 max(simulated_path_count - 2, 3)"
    )
~~~

### 1.2 GenerationReport

将 GenerationReport 替换为：

~~~python
@dataclass(frozen=True)
class GenerationReport:
    """记录 milestone 编译成功或自动拒绝的审计摘要。"""

    generation_status: Literal["generated", "auto_rejected"]
    requested_path_count: int
    minimum_valid_path_count: int
    valid_path_count: int
    distinct_path_count: int
    candidate_atom_count: int
    aligned_atom_count: int
    consensus_node_count: int
    graph_valid: bool
    reasons: tuple[str, ...] = ()
    path_summaries: tuple[JsonObject, ...] = ()

    def to_dict(self) -> JsonObject:
        """转换为 JSON 可序列化字典。"""
        return json_safe(self)
~~~

字段口径固定为：

- valid_path_count：通过 _parse_path_response() 的单路径数量；
- distinct_path_count：按对齐后 signature 序列去重、实际进入共识的路径数量；
- candidate_atom_count：全部有效路径中的局部 atom 总数；
- aligned_atom_count：有效去重路径中不同 atom signature 的数量；
- consensus_node_count：达到三分之二支持度并写入图的节点数量。

删除 contract_atom_count、leakage_count、actual_valid_distinct_path_count、evidence_allowlist_summary 及 compiler 中的全部赋值。distinct_path_count 已表示共识实际使用数，不保留第二个同义字段。

PublicEvidence、PublicInvariant、GeneratorTaskView、MilestoneGenerationError 和 GeneratorTaskView.digest() 保持现有接口。

## 2. 新增 dynsteer/adapter/contract.py

该文件集中实现 ToolSandbox、SWE-bench Pro、SkillsBench 共用的基础 evidence、稳定工具 evidence 和公开 invariant 提取；其他 adapter 不再复制这些逻辑。

文件 import 固定为：

~~~python
from __future__ import annotations

import hashlib
import re

from dynsteer.milestone.model import PublicEvidence, PublicInvariant
from dynsteer.model import ConstraintTarget, JsonObject, Operator
from dynsteer.utils import json_safe
~~~

### 2.1 stable_contract_slug()

新增：

~~~python
def stable_contract_slug(value: str) -> str:
    """根据公开契约值生成稳定且抗冲突的标识。"""
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")
    digest = hashlib.sha256(value.strip().encode("utf-8")).hexdigest()[:10]
    return f"{normalized or 'item'}_{digest}"
~~~

工具和 invariant 的 evidence/source 标识都调用该函数。adapter 中不得再按数组 index 拼接 ID。

### 2.2 base_public_evidence()

新增并只在此处定义两条通用 evidence：

~~~python
def base_public_evidence() -> list[PublicEvidence]:
    """返回所有 benchmark 共用的基础公开 evidence。"""
    return [
        PublicEvidence(
            evidence_id="agent_message_instruction",
            target=ConstraintTarget.STEP,
            selector="$.content",
            operator=Operator.FUZZY_MATCH,
            source_ref="instruction",
        ),
        PublicEvidence(
            evidence_id="tool_result_present",
            target=ConstraintTarget.TOOL_RESULT,
            selector="$",
            operator=Operator.ADDED,
            source_ref="instruction",
            expected_policy="none",
        ),
    ]
~~~

ToolSandbox contract 与 AgentCompass contract 都直接调用该函数，不复制这两个对象。

### 2.3 normalize_tool_contract()

新增接口：

~~~python
def normalize_tool_contract(
    tool_schema: JsonObject,
) -> tuple[JsonObject, list[PublicEvidence]]:
    """复制公开工具 schema，并返回稳定的工具 evidence。"""
~~~

函数内容固定为：

1. 用 json_safe() 复制 tool_schema，并确认结果是字典；
2. 遍历 tools，从 OpenAI function.name 或顶层 name 读取非空工具名；
3. 用 stable_contract_slug(name) 生成 slug；
4. 在复制后的工具对象写入 source_ref=f"tool:{slug}:name"、value=name；
5. 生成：

~~~python
PublicEvidence(
    evidence_id=f"tool_call_{slug}",
    target=ConstraintTarget.TOOL_CALL,
    selector="$.name",
    operator=Operator.EQUALS,
    source_ref=source_ref,
)
~~~

6. 重复原始工具名抛出 ValueError，不静默覆盖；
7. 返回复制后的完整 schema 和工具 evidence；description、parameters、required、type、enum 等公开字段原样保留。

### 2.4 build_public_invariants()

新增接口：

~~~python
def build_public_invariants(
    contents: list[str],
) -> tuple[list[JsonObject], list[PublicEvidence], list[PublicInvariant]]:
    """从公开文本提取去重的不变量及其可执行 evidence。"""
~~~

函数只扫描包含 must not、do not、never、禁止、不得、严禁的非空行，并按完整规则文本去重。每条规则用 stable_contract_slug(rule) 生成 source_ref、evidence_id、invariant_id。source asset 固定为：

~~~python
{
    "kind": "invariant_source",
    "source_ref": f"invariant:{slug}",
    "value": rule,
}
~~~

对应 evidence 固定使用 STEP、$.content、CONTAINS、public_literal；PublicInvariant severity 保持 warning。函数返回新列表，不修改 contents 或调用方已有列表。

## 3. dynsteer/adapter/agentcompass/contract.py

SWE-bench Pro 与 SkillsBench 继续共同调用 build_agentcompass_generator_view()。不修改 dynsteer/adapter/swebench_pro/adapter.py 和 dynsteer/adapter/skillsbench/adapter.py，也不在两者中增加 benchmark 专用 atom/path 逻辑。

### 3.1 import

删除 re。保留 PublicEvidence、ConstraintTarget、Operator，新增：

~~~python
from dynsteer.adapter.contract import (
    base_public_evidence,
    build_public_invariants,
    normalize_tool_contract,
    stable_contract_slug,
)
~~~

### 3.2 build_agentcompass_generator_view()

签名保持不变，函数体改为：

~~~python
language = str(config.metadata.get("language") or "en")
tool_schema, tool_evidence = normalize_tool_contract(task_case.tool_schema)
normalized_output = _with_source_refs(output_contract, "output")
invariant_assets, invariant_evidence, invariants = build_public_invariants(
    [task_case.task_description]
)
evidence = [
    *base_public_evidence(),
    *tool_evidence,
    *_output_evidence(normalized_output),
    *invariant_evidence,
]
return GeneratorTaskView(
    benchmark=config.benchmark,
    task_id=task_case.task_id,
    case_id=task_case.case_id,
    language=language,
    instruction=task_case.task_description,
    public_assets=invariant_assets,
    tool_schema=tool_schema,
    environment_schema=_with_source_refs(environment_schema, "environment"),
    output_contract=normalized_output,
    evidence_catalog=tuple(evidence),
    invariant_catalog=tuple(invariants),
)
~~~

保留 config/task_case 非空校验。normalize_tool_contract() 已完成 tool_schema 类型校验，本函数不重复校验。

### 3.3 _output_evidence()

将 _actf_evidence_catalog() 替换为：

~~~python
def _output_evidence(output_contract: JsonObject) -> list[PublicEvidence]:
    result: list[PublicEvidence] = []
    for key, value in output_contract.items():
        if not isinstance(value, dict) or not isinstance(
            value.get("source_ref"), str
        ):
            continue
        slug = stable_contract_slug(str(key))
        result.append(
            PublicEvidence(
                evidence_id=f"output_{slug}",
                target=ConstraintTarget.STEP,
                selector="$.content",
                operator=Operator.CONTAINS,
                source_ref=str(value["source_ref"]),
            )
        )
    return result
~~~

该函数只处理 output evidence；基础和工具 evidence 均来自共享模块。

### 3.4 删除代码

完整删除 _actf_evidence_catalog()、_public_task_assets()、_explicit_invariants()、_tool_name()。

repo、base_commit 不再复制到 public_assets，也不进入生成 prompt。_with_source_refs() 保留，且只用于 environment/output。

## 4. dynsteer/adapter/toolsandbox/utils/contract.py

### 4.1 import

删除 re、PublicEvidence、PublicInvariant、ConstraintTarget、Operator 的直接 import，新增：

~~~python
from dynsteer.adapter.contract import (
    base_public_evidence,
    build_public_invariants,
    normalize_tool_contract,
)
~~~

### 4.2 agent_facing_tool_schema()

该函数只保留 ToolSandbox 特有行为：读取 scrambling_allowed=True 后的工具、按 visible_to 过滤、调用 convert_to_openai_tools、返回 {"tools": tools}。该函数不生成 source_ref、evidence_id 或 invariant。

### 4.3 build_toolsandbox_generator_view()

公开消息提取逻辑保留原有顺序和 actor，普通 message asset 继续使用 message:{index}:content。

工具与 invariant 部分替换为：

~~~python
raw_tool_schema = agent_facing_tool_schema(context, module_loader)
tool_schema, tool_evidence = normalize_tool_contract(raw_tool_schema)
public_texts = [
    str(asset["value"])
    for asset in public_assets
    if isinstance(asset.get("value"), str)
]
invariant_assets, invariant_evidence, invariants = build_public_invariants(
    [task_case.task_description, *public_texts]
)
public_assets.extend(invariant_assets)
evidence = [
    *base_public_evidence(),
    *tool_evidence,
    *invariant_evidence,
]
~~~

GeneratorTaskView 的其余字段保持现有含义；evidence_catalog 使用 tuple(evidence)，invariant_catalog 使用 tuple(invariants)。

### 4.4 删除代码

完整删除现有工具 index 循环和 _public_invariants()。_visible_to_agent()、_message_visible_to_agent() 保留。

## 5. dynsteer/milestone/compiler.py

### 5.1 常量、内部类型与删除项

import 区新增 hashlib；Counter、ceil、NoReturn、PublicEvidence、Operator 保留供新实现使用。保留 CONSENSUS_RATIO = 2 / 3，新增 PATH_STRATEGIES：

~~~python
PATH_STRATEGIES = (
    "direct-shortest",
    "prerequisite-first",
    "state-check-first",
    "artifact-or-result-first",
    "alternative-tool",
    "verification-first",
    "conservative",
)

_PathSignature = tuple[str, str, int]
~~~

将 _Atom、_Path 替换为：

~~~python
@dataclass(frozen=True)
class _PathAtom:
    name: str
    description: str
    evidence_id: str
    expected: JsonValue
    terminal: bool


@dataclass(frozen=True)
class _PathCandidate:
    path_index: int
    strategy: str
    atoms: tuple[_PathAtom, ...]
    minefield_invariant_ids: tuple[str, ...]


@dataclass(frozen=True)
class _AlignedPath:
    path_index: int
    strategy: str
    atoms: tuple[_PathAtom, ...]
    signatures: tuple[_PathSignature, ...]
    minefield_invariant_ids: tuple[str, ...]


@dataclass(frozen=True)
class _ConsensusCluster:
    representative: _PathAtom
    support_count: int
    terminal_support_count: int
~~~

删除 _HIDDEN_KEYS、_TOP_LEVEL_KEYS、_Atom、_Path、_parse_response()、_parse_atoms()、_parse_paths()、_distinct_paths()、_path_summaries()、_milestone_from_atom()、_compile_minefields()、_validate_expected()、_count_hidden_leakage()。精确 schema 校验已覆盖额外 key，不保留 hidden-key 二次扫描。

### 5.2 路径下限

新增唯一实现：

~~~python
def _minimum_valid_path_count(requested_path_count: int) -> int:
    """返回 N−2 且不低于 3 的有效去重路径下限。"""
    return max(requested_path_count - 2, 3)
~~~

compile_task_case()、_reject() 和报告只接收该函数算出的 minimum。其他 Python 文件不得再次写 simulated_path_count - 2 或 max(..., 3)。

### 5.3 compile_task_case()

主流程固定为：

~~~python
requested = config.simulated_path_count
minimum = _minimum_valid_path_count(requested)
source_values = _source_values(view)
evidence_by_id = _evidence_catalog(view, source_values)
payload = _generator_payload(view, source_values)

valid_paths: list[_PathCandidate] = []
path_summaries: list[JsonObject] = []
reasons: list[str] = []

for path_index in range(requested):
    strategy = PATH_STRATEGIES[path_index % len(PATH_STRATEGIES)]
    try:
        candidate = _simulate_path(
            payload,
            path_index,
            requested,
            strategy,
            llm,
            evidence_by_id,
            source_values,
            view,
        )
    except (RuntimeError, TypeError, ValueError) as exc:
        reason = f"path[{path_index}] rejected: {exc}"
        reasons.append(reason)
        path_summaries.append(_rejected_path_summary(path_index, strategy, reason))
    else:
        valid_paths.append(candidate)
        path_summaries.append(_valid_path_summary(candidate))

aligned_paths = [_align_path(path) for path in valid_paths]
distinct_paths = _distinct_aligned_paths(aligned_paths)
path_summaries = _mark_duplicate_summaries(path_summaries, distinct_paths)

if len(distinct_paths) < minimum:
    _reject(
        f"有效差异路径不足: requested={requested}, "
        f"minimum={minimum}, actual={len(distinct_paths)}",
        ...
    )

threshold = ceil(len(distinct_paths) * CONSENSUS_RATIO)
clusters = _consensus_clusters(distinct_paths, threshold)
if not clusters or not any(
    cluster.terminal_support_count >= threshold
    for cluster in clusters.values()
):
    _reject("三分之二共识未产生可执行 terminal milestone", ...)

nodes = _milestones_from_clusters(clusters, distinct_paths, evidence_by_id)
edges = _consensus_edges(distinct_paths, set(clusters), threshold)
minefields = _consensus_minefields(
    distinct_paths,
    threshold,
    view,
    evidence_by_id,
    source_values,
)
~~~

随后构造 MilestoneGraph、GenerationReport 并返回。_consensus_edges() 是唯一 DAG 校验点；compile_task_case() 不重复调用 _is_dag()。N 次调用串行执行，不增加 ThreadPoolExecutor、async 包装或路径并发配置。

_consensus_clusters()、_consensus_edges() 或 _consensus_minefields() 抛出的 ValueError 在 compile_task_case() 的单一共识编译 try/except 中转为 _reject()；不让内部图编译错误绕过 GenerationReport，也不在各 helper 内分别组装报告。

### 5.4 _generator_payload() 与 _public_task_payload()

_generator_payload(view, source_values) 返回 task、evidence、invariants，不再返回 path_count。每条 evidence 保留 evidence_id、source_ref、target、selector、operator、expected_policy、allowed_expected_literals。

_public_task_payload() 返回：

~~~python
{
    "instruction": view.instruction,
    "public_assets": [
        asset
        for asset in view.public_assets
        if asset.get("kind") != "invariant_source"
    ],
    "tool_schema": view.tool_schema,
    "environment_schema": view.environment_schema,
    "output_contract": view.output_contract,
}
~~~

不得加入 benchmark、task_id、case_id、repo、base_commit、matcher、gold 或初始私有状态。

### 5.5 _simulate_path() 与 _parse_path_response()

新增 _simulate_path(payload, path_index, path_count, strategy, llm, evidence_by_id, source_values, view)。它渲染 prompt，传入 task、evidence、invariants、path_index、path_count、strategy，调用一次 llm.chat()，再且仅再调用一次 _parse_path_response()。不得缓存或复用其他 path 的响应。

新增 _parse_path_response(raw, path_index, strategy, evidence_by_id, source_values, view)，集中完成：

1. 顶层 key 必须且只能是 atoms、minefield_invariant_ids；
2. atoms 为非空数组；
3. atom key 必须且只能是 name、description、evidence_id、expected_literal_index、terminal；
4. name、description、evidence_id 为非空字符串；
5. expected_literal_index 为非 bool 整数；
6. evidence_id 必须存在；
7. public_literal 的 index 必须在允许值范围内，由 compiler 取出 expected；
8. none evidence 只接受 index == 0，并设置 expected=None；
9. 只有最后一个 atom 的 terminal 为 true；
10. minefield_invariant_ids 为不重复字符串数组，每个 ID 必须存在。

返回的 _PathCandidate 视为已验证对象；后续不得再次校验 evidence、literal index、terminal 或 invariant ID。

### 5.6 _align_path() 与路径去重

_align_path() 按 atom 顺序维护 Counter，signature 固定为：

~~~python
base = (atom.evidence_id, _canonical_json(atom.expected))
occurrence_index = occurrences[base]
signature = (base[0], base[1], occurrence_index)
occurrences[base] += 1
~~~

name、description、terminal 不参与对齐。_distinct_aligned_paths() 仅按 signatures 元组去重，保留 path_index 最小的路径；strategy 不参与去重。

### 5.7 _consensus_clusters() 与节点

新增 _consensus_clusters(paths, threshold)，返回 dict[_PathSignature, _ConsensusCluster]。每条 path 对同一 signature 最多计一次支持；支持数达到 threshold 才进入 clusters；representative 取 path_index 最小、atom 位置最前的候选；support_count 和 terminal_support_count 在本函数内各统计一次并写入 cluster，后续节点构造不得重新计数。

新增唯一 ID helper：

~~~python
def _milestone_id(signature: _PathSignature) -> str:
    digest = hashlib.sha256(
        _canonical_json(signature).encode("utf-8")
    ).hexdigest()[:12]
    return f"m_{digest}"
~~~

_milestones_from_clusters() 统一调用该 helper，并直接读取 cluster.representative 与 cluster.support_count。Constraint 全部字段从 evidence 派生，expected 使用 representative.expected。Milestone metadata 只写 evidence_id、signature、support_count、necessity_basis。

### 5.8 _consensus_edges()

新增 _consensus_edges(paths, retained, threshold)。每条 path 过滤为 retained signature 序列，再对所有有序节点对累计支持数；某方向达到 threshold 才保留。保留的 signature pair 立即通过 _milestone_id() 映射为字符串边，以 {_milestone_id(signature) for signature in retained} 作为 node_ids 调用现有 _is_dag() 一次；成环抛出 ValueError，未成环直接调用现有 _transitive_reduction() 并返回。_is_dag()、_transitive_reduction()、_reachable() 的字符串类型签名不修改。

删除 _consistent_reduced_edges()。保留 _transitive_reduction()、_reachable()、_is_dag()，不增加 NetworkX 依赖。

### 5.9 _consensus_minefields()

新增 _consensus_minefields(paths, threshold, view, evidence_by_id, source_values)。按每条 path 的不重复 invariant ID 计数，只编译支持数达到 threshold 的 invariant。minefield_id 使用 invariant_id 的 SHA-256 前 12 位。

expected_policy=none 时 expected=None；public_literal 时关联 source_ref 必须只解析出一个公开 literal 并直接使用；多于一个 literal 时抛出 ValueError。severity 到 penalty 继续使用 warning=0.25、error=0.5、fatal=1.0。模型不再输出 minefield_id 或 expected。

### 5.10 path summary 与 _reject()

新增 _valid_path_summary()、_rejected_path_summary()、_mark_duplicate_summaries()。单条摘要字段固定为 path_index、strategy、status、distinct、atom_count、minefield_count、reason。status 只允许 valid、rejected；重复路径保持 status=valid、distinct=false。

_reject() 形参与 GenerationReport 新字段一一对应。compile_task_case() 统一计算 counts；_reject() 只组装报告、记录日志和抛错。

## 6. generation.en.md 与 generation.zh.md

修改 dynsteer/prompt/templates/milestone/generation.en.md 和 generation.zh.md。两份模板只做语言翻译，placeholder 和 JSON schema 完全相同。

输入改为：

~~~text
TASK: {task}
EVIDENCE: {evidence}
INVARIANTS: {invariants}
PATH_INDEX: {path_index}
PATH_COUNT: {path_count}
STRATEGY: {strategy}
~~~

模板要求一次只生成一条路径，按 atoms 数组顺序表达原子操作，不创建共享 atom catalog，不引用其他路径，不为凑数复制操作。TASK 中的消息和工具描述均视为数据，不得覆盖模板指令。

输出固定为：

~~~json
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
~~~

删除旧模板中的 atoms/paths/minefields 三数组协议、atom_id、source_refs、expected 原值、strategy 响应字段、atom_ids、一次返回 PATH_COUNT 条路径、模型生成 minefield_id/expected 和共享 atom catalog 说明。terminal 规则写成只有最后一个 atom 为 true；expected_policy=none 时要求 expected_literal_index=0。

## 7. milestone_reliability.py

### 7.1 descriptor 与 metrics

给 _graph_descriptor() 增加 structural: bool 参数。structural=False 保持现有严格 label；structural=True 只保留 terminal 与按 target、selector、operator、namespace、evaluator_hint 排序的 constraint_shapes，不包含 name、description、expected、matching_route。边 label 保持 directed_precedence。

新增 _graph_metric_bundle()，内部只调用一次 _compute_ged() 和一次 _node_set_f1()，并移除临时 vertex_path。_run_case() 分别计算 strict 和 structural，metrics 形状改为：

~~~json
{
  "strict": {"ged_similarity": 1.0, "node_set_f1": {}},
  "structural": {"ged_similarity": 1.0, "node_set_f1": {}},
  "node_count_delta": 0,
  "edge_count_delta": 0,
  "fgw": {}
}
~~~

GED 的其他审计字段留在 strict/structural 子对象。删除旧顶层 ged_similarity、node_set_f1，不保留兼容别名。FGW 只计算 strict descriptor，不新增 evaluator 或轨迹行为指标。

### 7.2 descriptor summary

新增 _descriptor_summary(graph)，只输出 node_count、edge_count、terminal_count、constraint_shape_counts；shape 只使用 target/operator，不写 selector、expected、name、description。

_CaseResult 增加 reference_summary、prediction_summary。_run_case() 在图构造后填充，未构造的一侧使用空字典。_write_case_json() 的 reference/prediction 直接写对应 summary。

### 7.3 summary.json 与 index.json

_write_report() 聚合 strict/structural 的 ged_similarity、node_set_f1.f1、abs(node_count_delta)、abs(edge_count_delta)，以及 generation_report 的 requested_path_count、minimum_valid_path_count、valid_path_count、distinct_path_count；metric_definition 按嵌套字段更新。

index payload 保持当前精简形状，不加入 experiment_config_sha256、networkx_version、ignored_matrix_dimensions、run_id、schema_version，也不增加这些字段的读取、计算或兼容处理。case/summary 当前已有的 schema_version 和 case run_id 不属于 index 精简范围，本方案不扩大其删除范围。

## 8. docs/apis/milestone.md

用最终接口替换旧生成协议，只保留：

- MilestoneGenerationConfig 三个字段；
- GenerationReport 新字段及口径；
- max(N - 2, 3) 有效去重路径门槛；
- N 次独立单路径调用；
- 单路径 placeholder 与精确输出 schema；
- signature = evidence_id + canonical(expected) + occurrence_index；
- 节点、边、minefield 的三分之二支持规则；
- ToolSandbox 使用专用公开消息转换，SWE-bench Pro/SkillsBench 共用 AgentCompass contract；
- strict 与 structural reliability metric 字段。

删除共享 atom catalog、N−1 门槛、旧 atoms/paths/minefields 响应、actual_valid_distinct_path_count 和已删除 report 字段说明。不得写 task mode、behavioral evaluator 或并发路径接口。

## 9. 测试代码

当前仓库没有项目测试文件。新增以下 pytest 文件，测试直接调用公开入口或待实现模块函数，不为测试增加生产包装器。

### 9.1 tests/milestone/test_compiler_paths.py

覆盖 N 次 llm.chat()；N=6 时 4 条有效去重路径通过、3 条拒绝；N=5 时 minimum=3；单条非法 JSON/evidence/index/terminal 只淘汰该路径；相同 signature 序列 distinct=false；strategy 不改变去重结果。

### 9.2 tests/milestone/test_compiler_consensus.py

覆盖不同文本的同 signature 聚合；occurrence_index 分离重复操作；ceil(K * 2 / 3) 节点支持；terminal 支持不足拒绝；边支持门槛、成环拒绝、transitive reduction；minefield expected/penalty 派生。

### 9.3 tests/milestone/test_compiler_schema.py

覆盖顶层和 atom 精确 key；public_literal index；none policy index=0；非整数、bool、负数、越界 index；terminal 位置；unknown/duplicate invariant ID。

### 9.4 tests/adapter/test_contract.py

覆盖 stable slug、完整工具 schema、distraction 下 ID 稳定、三个 benchmark 共用 evidence、invariant 去重、repo/base_commit 排除、ToolSandbox 消息顺序和隐藏字段排除。

### 9.5 tests/milestone/test_reliability.py

覆盖 structural/strict 差异、descriptor summary 脱敏、summary 新路径字段，以及 index.json 不含 experiment_config_sha256、networkx_version、ignored_matrix_dimensions、run_id、schema_version。

## 10. 冗余代码清理与验收

### 10.1 旧实现删除检查

带单词边界搜索，确认旧 _Atom、_Path、_parse_response、_parse_atoms、_parse_paths、_distinct_paths、_path_summaries、_milestone_from_atom、_compile_minefields、_validate_expected、_count_hidden_leakage、_consistent_reduced_edges、_actf_evidence_catalog、_public_task_assets、_explicit_invariants、_public_invariants、_tool_name 均不存在。新类型 _PathAtom、_PathCandidate、_AlignedPath 不计入旧 _Path 精确名称搜索。

dynsteer 中不得再出现 tool_call_{index}、tool:{index}:name、simulated_path_count - 1、simulated_path_count - 2。N−2 公式只能以 requested_path_count - 2 出现在 _minimum_valid_path_count()。

### 10.2 重复实现检查

base_public_evidence()、stable_contract_slug()、normalize_tool_contract() 各只定义一次；invariant 正则只定义一次；两个 adapter contract 不得直接构造 tool_call evidence；_parse_path_response() 后不得再次校验 evidence/literal index/terminal/invariant ID；_consensus_edges() 外不得再次对生成边调用 _is_dag()；不增加同义 wrapper。

### 10.3 import、函数和参数清理

逐文件删除未使用 import、未调用私有函数和多余参数。重点检查 compiler 中 Counter、hashlib、ceil、NoReturn、PublicEvidence，以及两个 adapter contract 中 re、ConstraintTarget、Operator。

### 10.4 验证命令

~~~powershell
uv run python -m compileall dynsteer milestone_reliability.py
uv run pytest tests/milestone tests/adapter --cov=dynsteer.milestone --cov=dynsteer.adapter.contract --cov=dynsteer.adapter.agentcompass.contract --cov=dynsteer.adapter.toolsandbox.utils.contract --cov=milestone_reliability --cov-report=term-missing --cov-fail-under=80
git diff --check
~~~

测试后删除本次生成的 __pycache__、.pytest_cache、.coverage；不删除 tests 源文件，不修改 .gitignore。

## 附录A. 项目中没有把握实现的模块部分

没有把握的是：仅使用 evidence_id + canonical(expected) + occurrence_index，可能无法合并语义相同但由不同 evidence ID 表达的 atom。

不同 benchmark 或同一工具契约可能存在 evaluator 语义接近、但 target/selector/operator 不同的两个 evidence。确定性 signature 会选择不合并；使用 name/description 模糊匹配又可能把重复轮次或相近动作错误合并。

本方案不增加模糊文本阈值、第二次 LLM 聚类或 benchmark 私有 matcher。代码先保持确定性对齐，并通过 aligned_atom_count、consensus_node_count、strict/structural 指标暴露未合并情况；后续只有在实验确认系统性 under-merge 后，再单独设计可验证的 evidence 等价关系。
