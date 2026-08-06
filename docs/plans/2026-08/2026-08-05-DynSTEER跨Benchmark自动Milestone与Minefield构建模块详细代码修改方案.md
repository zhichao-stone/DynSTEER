# DynSTEER 跨 Benchmark 自动 Milestone 与 Minefield 构建模块最终代码修改方案

> 最终修订日期：2026-08-06
> 修改范围：仅限 DynSTEER；`../AgentCompass` 与 `../ToolSandbox` 只读，不修改。
> 本方案只列需要新增或修改的文件、接口和验收内容。

## 1. 新增 `dynsteer/milestone` 模块

### 1.1 `dynsteer/milestone/model.py`

新增以下冻结 dataclass；生成期模型只放在本文件，不修改运行期 `dynsteer/model.py`：

```python
@dataclass(frozen=True)
class MilestoneGenerationConfig:
    use_origin_milestone: bool = True
    simulated_path_count: int = 6
    generator: JsonObject = field(default_factory=dict)

@dataclass(frozen=True)
class PublicEvidence:
    evidence_id: str
    target: ConstraintTarget
    selector: str
    operator: Operator
    source_ref: str
    evaluator_hint: str = "rule"
    namespace: str | None = None
    expected_policy: Literal["public_literal", "none"] = "public_literal"
    metadata: JsonObject = field(default_factory=dict)

@dataclass(frozen=True)
class PublicInvariant:
    invariant_id: str
    description: str
    source_ref: str
    evidence_id: str
    severity: Literal["warning", "error", "fatal"] = "warning"

@dataclass(frozen=True)
class GeneratorTaskView:
    benchmark: str
    task_id: str
    case_id: str
    language: str
    instruction: str
    public_assets: list[JsonObject]
    tool_schema: JsonObject
    environment_schema: JsonObject
    output_contract: JsonObject
    evidence_catalog: tuple[PublicEvidence, ...]
    invariant_catalog: tuple[PublicInvariant, ...]

    def digest(self) -> str: ...

@dataclass(frozen=True)
class GenerationReport:
    generation_status: Literal["generated", "auto_rejected"]
    valid_path_count: int
    distinct_path_count: int
    contract_atom_count: int
    consensus_node_count: int
    graph_valid: bool
    leakage_count: int
    reasons: tuple[str, ...] = ()
    path_summaries: tuple[JsonObject, ...] = ()

    def to_dict(self) -> JsonObject: ...

class MilestoneGenerationError(ValueError):
    report: GenerationReport
```

具体约束：

- `MilestoneGenerationConfig.__post_init__()` 只校验 `simulated_path_count >= 3`、布尔值和 `generator` 类型；不增加 digest cache、reference policy、质量 profile 或兼容字段。
- `GeneratorTaskView.digest()` 对上述公开字段做稳定 JSON 序列化和 SHA-256；adapter 不重复实现 hash。
- instruction 固定使用 `source_ref="instruction"`；`public_assets`、tool、output contract 项都必须带唯一 `source_ref`，compiler 只接受这些已登记引用。
- `PublicEvidence` 一条记录对应一个确定的 `target/selector/operator/evaluator_hint`，compiler 只能选择记录，不能让 LLM 自由发明 scorer 形状。
- `expected_policy="public_literal"` 时，expected 必须是 `source_ref` 指向公开值的直接标量或子结构，compiler 用结构相等校验，不接受 LLM 猜测或改写；`"none"` 只用于不需要 expected 的 operator。
- 不定义 `ReferenceTrajectory`、optional milestone、OR evidence、profile registry 或第二套运行期 graph 模型。

### 1.2 `dynsteer/milestone/compiler.py`

只暴露一个公开入口：

```python
def compile_task_case(
    view: GeneratorTaskView,
    config: MilestoneGenerationConfig,
    llm: BaseLLM,
) -> tuple[MilestoneGraph, GenerationReport]:
    ...
```

本文件同时保存私有生成模型、校验 helper 和以下固定常量，不再新增 `validate.py`、planner、path compiler 或 quality profile 文件：

- `COMPILER_VERSION`；
- `MIN_DISTINCT_PATHS = 3`；
- `CONSENSUS_RATIO = 2 / 3`；
- hidden-key 黑名单。

`compile_task_case()` 的具体实现固定为：

1. 把 `GeneratorTaskView`、证据 ID 白名单、请求路径数交给 LLM，一次请求返回共享 atom 目录、差异路径和 minefield 候选；禁止按路径循环调用 LLM。

   LLM 响应固定为以下最小 JSON 形状，不接受额外顶层结构：

   ```json
   {
     "atoms": [
       {
         "atom_id": "a1",
         "name": "...",
         "description": "...",
         "source_refs": ["instruction"],
         "evidence_id": "...",
         "expected": null,
         "terminal": false
       }
     ],
     "paths": [
       {"strategy": "shortest", "atom_ids": ["a1"]}
     ],
     "minefields": [
       {"minefield_id": "mf1", "invariant_id": "...", "expected": null}
     ]
   }
   ```

2. LLM 输出中的每条路径只保存有序 atom ID。所有路径复用同一个 atom 目录，因此语义等价步骤在生成时即共享 atom，不再实现第二套 embedding/聚类流程。
3. 校验 JSON 结构、`source_ref`、`evidence_id`、expected 来源、atom 引用和路径终态；每条路径必须以 `terminal=true` atom 结束，非法路径删除并记录原因。
4. 用有序 atom ID 签名去重；去重后少于 3 条有效路径时构造 `auto_rejected` report 并抛出 `MilestoneGenerationError`。
5. LLM 路径只用于增加解法、顺序和工具选择的多样性，不证明“真实必经性”。atom 同时满足以下三项才编译为 milestone：
   - 回指显式公开任务契约；
   - 选择的 `PublicEvidence` 能由现有 scorer 执行；
   - 出现在至少三分之二的有效差异路径中。
6. 三分之二是 compiler 内部固定常量，不新增配置项。生成节点写入 `metadata["necessity_basis"]="synthetic_consensus"`；低频替代步骤和无契约来源步骤只写入 `GenerationReport.path_summaries`，不进入 `MilestoneGraph.nodes`。
7. 对保留 atom 构造边：只有在所有同时包含两个 atom 的有效路径中顺序一致时才保留前置关系；删除自环、冲突边和传递边，最终必须为 DAG。
8. minefield 候选必须引用 `invariant_catalog` 和对应 `PublicEvidence`；LLM 不能提高 `PublicInvariant.severity`。无公开 invariant 或无可执行 detector 的候选直接删除。
9. 使用现有 `Constraint`、`Milestone`、`Minefield`、`MilestoneGraph` 输出；不修改现有 scorer 接口。
10. graph 必须至少包含一个可执行 milestone、所有 constraint selector 合法、DAG 合法且 hidden leakage 为 0，否则构造 `auto_rejected` report 并抛出 `MilestoneGenerationError`；失败 graph 不返回给 loader。
11. 成功 graph metadata 只写 `source="generated"`、`compiler_version`、`view_digest` 和 `necessity_basis="synthetic_consensus"`；完整 `GenerationReport` 不嵌套进 graph。
12. 核心步骤和异常使用结构化中文日志；不得记录完整 prompt、secret、gold、verifier 或 LLM reasoning。

所有只被 compiler 使用的 `_Atom`、`_Path`、JSON 解析、DAG 校验、传递约简和泄漏扫描函数都留在本文件后半部分，按主函数到 helper 的顺序排列。

### 1.3 `dynsteer/milestone/__init__.py`

仅显式导出：

- `MilestoneGenerationConfig`
- `PublicEvidence`
- `PublicInvariant`
- `GeneratorTaskView`
- `GenerationReport`
- `MilestoneGenerationError`
- `compile_task_case`

禁止 `__getattr__`、字符串模块映射和懒加载 re-export。

### 1.4 `dynsteer/prompt/templates/milestone/generation.en.md` 与 `generation.zh.md`

新增同一输出 schema 的中英文模板：

- 要求一次返回共享 atom 目录、`simulated_path_count` 条策略不同的路径和 minefield 候选；
- strategy 至少覆盖最短路径、状态优先、产物优先、替代工具/实现和保守路径；
- 路径必须引用共享 atom ID，不输出思维链；
- atom/minefield 必须引用输入中已有的 `source_ref` 和 `evidence_id`；
- 明确路径多数只是 synthetic consensus，不得声称 gold path 或真实必经；
- compiler 直接调用现有 `load_prompt_template("milestone", "generation")`，不新增只做转发的 prompt builder。

## 2. 配置与唯一加载调度

### 2.1 `dynsteer/harness/model.py`

在 `HarnessRunConfig` 增加：

```python
milestone_generation: MilestoneGenerationConfig = field(
    default_factory=MilestoneGenerationConfig
)
```

该字段是运行期唯一配置来源；禁止再从 `metadata` 解析同名配置。

### 2.2 `dynsteer/harness/config.py`

新增唯一 parser：

```python
def milestone_generation_from_mapping(
    data: Mapping[str, Any] | None,
) -> MilestoneGenerationConfig:
    ...
```

修改内容：

- 把 `milestone_generation` 加入 `_RUN_CONFIG_CONTROL_FIELDS`；
- `load_harness_run_configs()` 只调用上述 parser 一次并写入 `HarnessRunConfig`；
- 仅接受 `use_origin_milestone`、`simulated_path_count`、`generator`，未知字段报错；
- generator 中只保存 provider、model、temperature、timeout、max_tokens、retry 等非敏感字段；API key 继续由现有 `build_llm_from_config()` 从环境变量读取；
- 不把配置复制到 `metadata`，不增加 manifest 级第二套默认值。

### 2.3 `dynsteer/experiment/model.py`

在 `ExperimentRunSpec` 增加同一个 `MilestoneGenerationConfig` 字段。

`to_metadata()` 不写 `milestone_generation` 或其副本；该对象只在 experiment config 转成 `HarnessRunConfig` 时传递，避免结果 metadata 成为第二配置来源。

### 2.4 `dynsteer/experiment/config.py`

修改内容：

- `expand_experiment_matrix()` 从每个 benchmark spec 的 `milestone_generation` 调用 `milestone_generation_from_mapping()`，写入 `ExperimentRunSpec`；
- `build_harness_config()` 直接把该对象传给 `HarnessRunConfig`；
- 不在 `_merge_metadata()` 中合并或复制 `milestone_generation`；
- direct harness 配置和 experiment 配置共用 `harness/config.py` 的同一个 parser。

### 2.5 `dynsteer/adapter/base.py`

在 `BaseBenchmarkAdapter` 增加抽象接口：

```python
@abstractmethod
def generator_task_view(
    self,
    config: HarnessRunConfig,
    task_case: TaskCase,
    case_id: str,
) -> GeneratorTaskView:
    ...
```

现有三个 adapter 都必须实现；loader 只通过该接口获取生成输入，不按 benchmark 写条件分支。

contract 的统一范围固定如下：

```text
_adapt_task_case
-> adapter.generator_task_view(config, task_case, case_id)
-> benchmark contract.py 投影原始字段
-> GeneratorTaskView
-> compile_task_case
```

- 对 loader 统一：所有 benchmark 都只暴露上述 `generator_task_view(config, task_case, case_id)`；
- 对 compiler 统一：所有 adapter 都返回同一个 `GeneratorTaskView`，source ref、evidence 和 expected 校验只在 model/compiler 实现；
- 对 benchmark 内部不强行统一：ToolSandbox 需要 context/tool conversion，AgentCompass 需要 PreparedTask 已核实字段，两者原始入参不同，各自 `contract.py` 只负责投影；
- SWE-bench Pro 与 SkillsBench 的 ACTF evidence catalog 相同，因此集中在 `adapter/agentcompass/contract.py` 实现一次；
- 不新增全局 contract registry、contract 基类或只转发参数的统一 builder。

### 2.6 `dynsteer/adapter/loader.py`

不新增第二个生成调度函数，直接扩展现有 `_adapt_task_case()`。只有创建/强制重建 adapted case 时允许进入生成流程：

```text
adapter.adapt_task_case
-> 按 MilestoneGenerationConfig 选择 origin graph 或 compile_task_case
-> enrich_milestone_graph
-> _postprocess_task_case
-> validate_stage_evaluation_specs
-> save_task_case
```

具体规则：

- Default 方法保持现有行为，不构造 milestone，不调用生成 LLM。
- `load_task_case()` 在 adapted case 文件存在且 `force_adapt=false` 时，直接 `parse_task_case()` 并使用文件中已经保存的 `MilestoneGraph`；不构造 view、不读取 `milestone_generation` 配置、不调用 LLM。
- adapted case 文件不存在或 `force_adapt=true` 时进入 `_adapt_task_case()`；这是自动生成的唯一触发点。
- `use_origin_milestone=true` 且 adapter 返回的原 graph 有 node 或 minefield：直接使用原 graph，并在 `MilestoneGraph.metadata` 写 `source="origin"`，不构造 LLM。
- 原 graph 为空，或 `use_origin_milestone=false`：调用 adapter 的 `generator_task_view()`，再用 `build_llm_from_config(config.milestone_generation.generator)` 和 `compile_task_case()` 生成 graph。
- 未配置 generator 或 compiler 抛出 `MilestoneGenerationError` 时适配失败，不保存空 graph。
- generated graph 直接赋给 `task_case.milestone_graph`；`GenerationReport.to_dict()` 只作为适配审计信息写入 `TaskCase.metadata["milestone_generation"]`，后续 evaluator 不读取该字段。
- 完成 enrich、stage goal/spec 后只调用现有 `save_task_case()` 一次；graph、stage goal 和 report 一起持久化到 adapted case 文件。
- 后续修改 generator、路径数或 origin/generated 选择时，必须显式使用现有 `force_adapt=true` 重建；配置变化本身不触发隐式重生成。
- `refresh_task_cases_for_experiment()` 只读取 `task_case.milestone_graph.metadata["source"]`：generated graph 跳过 adapter 的 origin expected 刷新，只重新 materialize stage goals/specs；origin graph 继续调用现有 refresh。
- `_postprocess_task_case()` 仍是 enrich route、stage goal template、materialize 和 stage spec 的唯一入口；compiler 不复制这些逻辑。
- 不新增 digest cache、独立 cache 文件、fresh source/cached case 合并或“加载时检查是否需要重生成”的分支。

## 3. Benchmark 输入投影

### 3.1 新增 `dynsteer/adapter/toolsandbox/utils/contract.py`

只实现两个被真实调用的函数：

```python
def agent_facing_tool_schema(
    context: object,
    module_loader: Callable[[str], object],
) -> JsonObject:
    ...

def build_toolsandbox_generator_view(
    config: HarnessRunConfig,
    task_case: TaskCase,
    context: object,
    module_loader: Callable[[str], object],
) -> GeneratorTaskView:
    ...
```

`agent_facing_tool_schema()`：

- 调用当前 ToolSandbox 源码确认存在的 `context.get_available_tools(scrambling_allowed=True)`；
- 按 callable 的 `visible_to` 过滤 Agent 不可见工具；
- 使用 `tool_sandbox.common.tool_conversion.convert_to_openai_tools` 生成 `name/description/parameters`；
- 保留 Agent-facing 扰动名称，不把 execution-facing 名称泄漏给 generator；
- 输出 allow/deny/augmentation 生效后的最终 schema，不读取 evaluation matcher；schema digest 统一由 `GeneratorTaskView.digest()` 覆盖。

`build_toolsandbox_generator_view()`：

- instruction 使用现有 `task_description_from_steps()` 的结果；
- public assets 只包含 Agent 可见的 system/user 消息；工具定义只放在 `tool_schema`，不重复复制；不包含 CONTACT、SETTING 等初始数据库行；
- environment schema 只描述 `source="toolsandbox"` 和 `stateful=true`，不包含数据库 namespace、schema 或数据值；
- evidence catalog 只登记现有 scorer 已支持的 tool call、tool result 和 Agent→User message；generated graph 不创建 state snapshot expected；
- invariant catalog 只收录 Agent 可见 system/user 消息中的明确禁止项；
- ToolSandbox 原生 `scenario.evaluation`、`milestone_matcher`、`minefield_matcher`、`target_dataframe` 永远不进入 view；
- 如果 expected 只能来自 matcher、隐藏状态或运行后工具结果，则不生成该 constraint，并在 report 记录 `unsupported_dynamic_expected`；不实现运行期动态 expected binder。

### 3.2 `dynsteer/adapter/toolsandbox/adapter.py`

修改内容：

- `adapt_task_case()` 调用 `agent_facing_tool_schema()`，替换当前 `tool_schema={"source":"toolsandbox"}` 占位值；
- `environment_schema` 改为不含状态值的稳定公开 schema；
- 新增 `generator_task_view()`，加载当前 case 的 cached scenario/context 后调用 `build_toolsandbox_generator_view()`；
- 保留 `milestone_graph_from_scenario()` 和现有 `refresh_task_case_for_experiment()`，它们只服务 origin graph；
- generated refresh 分支由 loader 统一跳过，本 adapter 不再增加第二个 mode parser。

### 3.3 `dynsteer/adapter/toolsandbox/utils/scenario.py`

只给 `milestone_graph_from_scenario()` 输出 metadata 增加 `source="origin"`。

不在本文件加入自动生成、schema 提取或 generated expected 逻辑。

### 3.4 新增 `dynsteer/adapter/agentcompass/contract.py`

新增共享函数：

```python
def build_agentcompass_generator_view(
    config: HarnessRunConfig,
    task_case: TaskCase,
    *,
    environment_schema: JsonObject,
    output_contract: JsonObject,
) -> GeneratorTaskView:
    ...
```

该函数由 SWE-bench Pro 与 SkillsBench 两个 adapter 共同调用，集中完成：

- `TaskCase.task_description` 到 instruction 的映射；
- ACTF 转换后已有的 message、tool call、tool result evidence catalog；
- `PreparedTask.input.tools` 为空时输出空 tool list，不猜测 Codex/Claude CLI 内部工具 schema；
- public asset、language、source ref 和 view digest 所需字段的统一构造；
- invariant catalog 只收录 instruction/output contract 中明确出现的禁止项或输出限制，没有明确规则时返回空 tuple；
- ground truth、patch、tests、reward、verifier、`RunResult.extra` 不进入 view。

不得在两个 benchmark adapter 中各复制一份 evidence catalog 或 hash 逻辑。

### 3.5 `dynsteer/adapter/swebench_pro/adapter.py`

新增 `generator_task_view()`：

- 调用共享 `build_agentcompass_generator_view()`；
- environment schema 写当前已核实的 repo workspace：`/app/{case_id}/repo`；
- output contract 写 `/app/{case_id}/patch.txt` 和“仅输出解决问题的 unified diff”；
- 公开任务字段继续使用现有 `TaskCase.task_description`、`repo`、`base_commit`；
- 不读取 `AgentCompassTaskRecord.ground_truth`、评测 patch 或 evaluation workspace。

### 3.6 `dynsteer/adapter/skillsbench/adapter.py`

新增 `generator_task_view()`：

- 调用共享 `build_agentcompass_generator_view()`；
- environment schema 写已核实的 workspace `/root`；
- AgentCompass 当前 `PreparedTask.output` 为空，因此 `output_contract={}`，任务契约只来自公开 instruction；
- 不读取 `tests_dir` 内容、`test.sh`、reward 或 verifier 输出。

## 4. API 文档与测试

### 4.1 新增 `docs/apis/milestone.md`

记录：

- `MilestoneGenerationConfig` 配置字段与默认值；
- `GeneratorTaskView` 的公开输入白名单；
- `compile_task_case()` 输入、输出和异常；
- 自动生成的固定算法：“一次多样化路径生成 → 共享 atom 对齐 → 三分之二契约共识 → DAG”；
- `synthetic_consensus` 不代表真实必经；
- generation config 只在适配时生效；adapted TaskCase 已存在时直接使用已存 graph，修改配置后必须 `force_adapt`；
- graph 保存到 adapted TaskCase 后与原生 graph 使用同一评估流程；evaluator/runtime 只读取 `TaskCase.milestone_graph`，不读取 generation config/report；
- 如实验不希望触发在线停止，继续使用现有 `strategy.policy_stop=false`，不增加 generation 专用策略；
- ToolSandbox 动态 expected 只允许公开 literal，不支持 hidden/runtime-derived expected。

### 4.2 新增 `tests/milestone/test_compiler.py`

使用 mock `BaseLLM` 覆盖：

- 一次 LLM 调用产生多条路径；
- atom ID 共享、路径去重和少于 3 条自动拒绝；
- 三分之二边界：4/6 保留、3/6 删除；
- 无契约 source、未知 evidence、hidden key、非法 expected 拒绝；
- 稳定 DAG、冲突顺序不建边、传递边删除；
- minefield 必须受 invariant severity 上限约束；
- 成功 graph metadata 与 report 字段；
- 核心 compiler 行覆盖率不低于 80%。

### 4.3 新增 `tests/milestone/test_loader.py`

覆盖：

- origin graph + `use_origin_milestone=true` 不构造 LLM；
- origin graph + false 进入 compiler；
- 无 origin graph 自动生成；
- 首次适配生成 graph、stage goal 和 report，并写入 adapted case 文件；
- adapted case 已存在且 `force_adapt=false` 时只反序列化 graph，不构造 view、不调用 LLM；
- generator 配置变化但未设置 `force_adapt` 时仍使用已存 TaskCase；
- `force_adapt=true` 时按当前配置重新适配并只生成、保存一次；
- generated refresh 不复制 ToolSandbox origin expected；
- stage goal/spec 仍只由 `_postprocess_task_case()` 生成；
- Default 方法不调用生成模块。

### 4.4 新增 `tests/adapter/test_generator_views.py`

覆盖三个 adapter：

- ToolSandbox tool allow/deny、工具名扰动和 Agent `visible_to`；
- ToolSandbox view 不含 matcher、target dataframe 和初始数据库值；
- SWE-bench Pro workspace/output contract 字段；
- SkillsBench workspace 与空 output contract；
- AgentCompass view 不含 ground truth、tests、reward、verifier；
- 两个 AgentCompass adapter 共用同一 evidence catalog builder。

### 4.5 验收命令

实施完成后执行：

```powershell
uv run pytest --cov=dynsteer.milestone --cov-report=term-missing
uv run pytest
uv run python -m compileall dynsteer
uv run ruff check dynsteer tests
```

测试产生的 `__pycache__`、`.pytest_cache`、coverage 临时文件在验收后清理；不删除测试源码和现有文档。

## 5. 冗余与死代码验收

代码完成前逐项确认：

1. 自动生成只有 `compile_task_case()` 一个公开入口，并且只由现有 `_adapt_task_case()` 在创建/强制重建 TaskCase 时调用；不新增 loader 调度包装函数。
2. 配置只有 `MilestoneGenerationConfig` 和 `milestone_generation_from_mapping()`；metadata 不作为配置来源。
3. LLM 每个 case 最多一次生成请求；不按路径调用，不增加第二个 judge/generator factory。
4. 路径共享 atom ID，不实现 embedding 聚类、reference 分支、counterfactual proof 或独立 validator。
5. ToolSandbox schema 只在 `contract.py` 提取；AgentCompass evidence catalog 只在共享 `contract.py` 定义。
6. 不新增只调用另一函数的中转函数，不保留未被 loader/adapter/tests 调用的公开接口。
7. 不修改 evaluator、runtime、现有 `MilestoneGraph`、scorer、ACTF converter、registry 和 stage goal/spec 算法；评估只消费持久化后的 graph。
8. 不新增加载期 digest 检查、隐式重生成、origin/generated 双份 compiler、运行期 origin 语义审计、独立 cache 文件、profile registry、optional/OR 节点或 benchmark 占位 runner。
9. 静态检查确认无未使用 import、未调用函数、多余接口参数和重复序列化/校验逻辑。
10. 所有核心函数补充中文 docstring、关键步骤中文注释和结构化中文日志；API 文档与最终接口一致。

## 附录A. 项目中没有把握实现的模块部分

- **多路径共识阈值的跨 benchmark 泛化效果**：`2/3` 的代码行为完全确定，但它是经验阈值，是否在所有 benchmark 上达到最佳 precision/recall 需要实验验证。实现中只保留这一固定阈值和报告字段，不预埋多套阈值算法或未使用配置分支。
