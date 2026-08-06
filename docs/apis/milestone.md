# Milestone 自动生成 API

## 配置

`MilestoneGenerationConfig` 是运行期唯一配置来源：

- `use_origin_milestone: bool = true`：原始 graph 含节点或 minefield 时优先使用原 graph。
- `simulated_path_count: int = 6`：一次生成请求中的模拟路径数，最小为 3。
- `generator: object = {}`：LLM 的 provider、model、temperature、timeout、max_tokens 和 retry 等非敏感配置；密钥仍由现有 LLM factory 从环境变量读取。

配置只在首次适配或 `force_adapt=true` 时生效。adapted TaskCase 已存在且未强制重建时，loader 直接读取其中持久化的 graph；修改配置不会隐式重生成。

## 公开生成视图

`GeneratorTaskView` 仅包含 benchmark、task/case ID、language、公开 instruction、Agent 可见 public assets、tool schema、environment schema、output contract，以及编译器允许选择的 `PublicEvidence` 和 `PublicInvariant` 目录。ground truth、gold patch、测试、reward、verifier、ToolSandbox matcher、target dataframe 和初始数据库值不允许进入视图。

`digest()` 对所有公开字段执行稳定 JSON 序列化并返回 SHA-256。

## `compile_task_case()`

```python
compile_task_case(
    view: GeneratorTaskView,
    config: MilestoneGenerationConfig,
    llm: BaseLLM,
) -> tuple[MilestoneGraph, GenerationReport]
```

每个 case 最多调用 LLM 一次，固定执行：一次多样化路径生成 → 共享 atom 对齐 → 三分之二契约共识 → 一致顺序边与传递约简 → DAG 校验。生成节点的 `necessity_basis="synthetic_consensus"` 只表示合成路径共识，不代表真实必经步骤或 gold path。

输入引用、expected 公开来源、有效差异路径数、终态、DAG、可执行节点或 hidden leakage 不合法时，函数抛出携带 `GenerationReport` 的 `MilestoneGenerationError`，loader 不保存失败 graph。

生成 graph 保存到 adapted TaskCase 后与原生 graph 共用现有 enrich、stage goal/spec 和 evaluator/runtime 流程。评估期只读取 `TaskCase.milestone_graph`，不读取 generation config 或 report。若实验不希望在线停止，继续使用现有 `strategy.policy_stop=false`。

ToolSandbox generated constraint 只支持公开 literal expected；不绑定 matcher、隐藏状态或运行后工具结果产生的动态 expected。
