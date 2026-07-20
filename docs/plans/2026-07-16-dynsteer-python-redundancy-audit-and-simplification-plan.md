# DynSTEER Python冗余核查报告与代码简化方案

生成日期：2026-07-16  
核查范围：`dynsteer/**/*.py`，不含测试文件与非 Python 文件。  
统计口径：使用 Python AST/tokenize 统计有效代码行，排除空行、`#`注释、模块/类/函数文档字符串；`import`、类型定义、数据类字段、可执行语句均计入有效行。

## 1. 总体结论

当前 `dynsteer` 的 Python 代码存在系统性冗余，不只是局部命名或风格问题。主要冗余来自四类：

1. 单次调用、逻辑很短的 helper 过多，导致函数签名、入参校验、注释和调用链膨胀。
2. 运行期评估链路中存在只做转发或轻包装的中间函数，尤其是 `stage/settlement.py` 的 checkpoint 结算链。
3. 多个模块重复做 JSON 数字、字符串、None、结构类型等边界校验，部分校验出现在已完成边界校验后的内部流程。
4. 部分函数参数、import、抽象接口默认实现存在静态冗余信号，需要按“外部接口/继承接口/内部函数”分级处理。

当前有效行数为 **10079 行**。第一轮简化后建议把有效行数压到 **7400-7800 行**，目标上限为 **7800 行**，相对当前减少约 **2279-2679 行**，降幅约 **22.6%-26.6%**。其中 `stage/settlement.py`、`evaluate/*`、`adapter/toolsandbox/*` 是收益最大的区域。

## 2. 当前代码规模与压缩目标

| 区域 | 当前有效行数 | 建议目标有效行数 | 主要压缩来源 |
| --- | ---: | ---: | --- |
| `dynsteer/evaluate` | 3066 | 2100-2250 | 内联单次 helper、合并诊断构造、减少重复 JSON/数值校验 |
| `dynsteer/adapter` | 2105 | 1550-1700 | `toolsandbox` 角色工厂与转换逻辑收敛、loader 小解析函数合并 |
| `dynsteer` 根文件 | 1506 | 1200-1300 | `model.py` 保守清理，`progress.py` helper 收敛，公共工具统一 |
| `dynsteer/stage` | 1020 | 700-760 | collapse checkpoint settlement 调用链，减少重复 stage goal/spec 校验 |
| `dynsteer/harness` | 821 | 620-680 | 去除未用 import，合并配置读取中的重复字段校验 |
| `dynsteer/judges` | 744 | 600-650 | 公共 judge 边界校验前移，保留继承接口 |
| `dynsteer/prompt` | 424 | 320-350 | 内联单次 prompt helper，删除无效参数 |
| `dynsteer/llm` | 393 | 300-330 | 保留 provider 边界，减少默认 hook 与重复响应解析 |
| **合计** | **10079** | **7400-7800** | 约 22.6%-26.6% 降幅 |

本次静态核查还得到如下指标：

| 指标 | 数量 | 说明 |
| --- | ---: | --- |
| Python 文件 | 66 | `dynsteer/**/*.py` |
| 函数/方法定义 | 547 | 含类方法、内部函数 |
| 简单且调用次数不超过 1 的候选函数 | 171 | 静态候选，需排除抽象接口与外部 API |
| 近似中转函数候选 | 29 | 多为 `return some_function(...)` 或很薄包装 |
| 精炼后确定未用 import | 1 | 排除 `__future__` 与 `__init__.py` re-export 后 |
| 非抽象函数未用参数候选 | 10 | 其中部分是扩展接口或闭包误判，需人工分级 |

## 3. 确定性冗余问题

### 3.1 `stage/settlement.py` checkpoint 结算链过长

当前链路：

```text
evaluate_checkpoint
  -> append_milestone_settlement
    -> append_stage_settlement
      -> evaluate_stage
```

关键位置：

| 文件位置 | 问题 |
| --- | --- |
| `dynsteer/stage/settlement.py:46` `evaluate_checkpoint` | 已经持有 `state`，但继续把 `state.settlements`、`state.weights`、`state.evaluation_policy` 等拆成大量参数传给下游。 |
| `dynsteer/stage/settlement.py:281` `append_milestone_settlement` | 只被 `evaluate_checkpoint` 调用；主要构造 `StageInterval` 与 `matching_detail`，随后转发给 `append_stage_settlement`。 |
| `dynsteer/stage/settlement.py:375` `append_stage_settlement` | 只被 `append_milestone_settlement` 调用；核心功能是调用 `evaluate_stage` 后组装 `HarnessStageSettlement`，没有形成真正复用抽象。 |
| `dynsteer/stage/settlement.py:461` `evaluate_stage` | 本身包含真实阶段评估逻辑，应保留为内部核心函数，但可以改为 `_evaluate_stage` 并减少不必要参数。 |

简化建议：

1. 删除 `append_milestone_settlement` 和 `append_stage_settlement` 两个中转层。
2. 在 `evaluate_checkpoint` 内直接构造 `StageInterval`、`matching_detail`，调用 `_evaluate_stage`，随后组装 `HarnessStageSettlement`。
3. `_evaluate_stage` 只接收当前阶段评估真正需要的对象；`weights`、`evaluation_policy` 可从 `state` 读取，避免反复拆装。
4. `state.milestone_frontier is None` 这类运行期不变量校验只在 checkpoint 入口保留一次。

预期收益：`stage/settlement.py` 从 **601 行** 降至 **420-460 行**。

### 3.2 `evaluate/policy.py` 截图类小 helper 过度拆分

`update_evaluation_policy` 只有一个外部入口，但当前拆出多个只在该函数中使用的小 helper：

| 函数 | 位置 | 建议 |
| --- | --- | --- |
| `_should_stop` | `evaluate/policy.py:54` | 内联到 `update_evaluation_policy`，策略终止条件短且只使用一次。 |
| `_is_high_confidence_pass` | `evaluate/policy.py:64` | 内联为局部布尔变量。 |
| `_upgrade_level` | `evaluate/policy.py:100` | 内联为条件表达式或局部小 lambda，避免单独函数。 |
| `_policy_reason` | `evaluate/policy.py:107` | 内联到创建 `EvaluationPolicyState` 前，减少一次结果对象回读。 |
| `_termination_reason` | `evaluate/policy.py:122` | 可保留为局部分支块，或内联到终止分支。 |
| `_next_dimension_levels` | `evaluate/policy.py:73` | 可保留，因为它承载逐维策略更新循环，比其他 helper 更有独立语义。 |

预期收益：`evaluate/policy.py` 从 **107 行** 降至 **70-80 行**。

### 3.3 `evaluate/quality.py` 多个一次性扫描 helper 可以合并

当前 `evaluate/quality.py` 有 13 个函数，10 个属于简单单次调用候选。保留两个外部入口：

```text
build_runtime_quality_diagnostics
build_stage_quality_diagnostics
```

但以下内部 helper 建议合并到一次扫描中：

| 函数 | 问题 | 建议 |
| --- | --- | --- |
| `_efficiency_warning_count` | 仅调用 `_efficiency_diagnostics` 后取一个字段。 | 删除，`warning_count` 直接使用已计算的 `efficiency`。 |
| `_first_step_index` / `_first_tool_call_index` | 只服务 `_efficiency_diagnostics`。 | 在 `_efficiency_diagnostics` 的单次循环中同时计算。 |
| `_tool_name` | 只读取 `latest_tool_call.tool_call.name`。 | 调用点内联。 |
| `_next_agent_message` | 只服务空工具结果后的 warning。 | 可作为局部循环，或保留但去掉多余分支。 |
| `_looks_like_identifier_key` | 一行逻辑。 | 内联为 `key.endswith(...)`。 |
| `_is_empty_tool_content` | 有一定语义，可保留或移入共享 JSON 工具；不要继续拆更小。 |

预期收益：`evaluate/quality.py` 从 **180 行** 降至 **125-140 行**。

### 3.4 明确的未用 import 与参数

确定可清理项：

| 文件位置 | 问题 | 建议 |
| --- | --- | --- |
| `dynsteer/harness/runner.py:10` | `HarnessCaseExecutionError` import 后未使用。 | 删除 import。 |
| `dynsteer/evaluate/diagnostics.py:308` | `build_milestone_matching_detail(graph, ...)` 的 `graph` 未使用。 | 删除参数，并同步唯一调用点。 |
| `dynsteer/evaluate/matching/milestone.py:60` | `analyze_milestone_step(task_case, ...)` 的 `task_case` 未使用。 | 删除参数，并同步调用点。 |
| `dynsteer/evaluate/runtime.py:282` | `task_case_snapshot(case_id, task_case, trajectory)` 的 `trajectory` 未使用。 | 删除参数，并同步调用点。 |
| `dynsteer/evaluate/scoring.py:399` | `enrich_stage_result(interval, ..., thresholds)` 的 `interval`、`thresholds` 未使用。 | 删除参数，保留结果富化职责。 |
| `dynsteer/prompt/template.py:14` | `_safe_format(..., **kwargs)` 静态显示 `kwargs` 未使用，需核查实现。 | 若确实未使用，删除参数或删除函数。 |

需保留或谨慎处理项：

| 文件位置 | 原因 |
| --- | --- |
| `GeneralScorer.score_custom_constraint(...)` | 是 benchmark scorer 扩展接口，默认实现可不使用全部参数。 |
| `CaseProgressReporter.case_advanced(...)` | 是进度上报父类接口，默认 no-op 可接受。 |
| `BaseLLM._response_usage(...)` | 是 provider hook，是否删除要看子类覆盖与外部调用。 |
| `adapter/toolsandbox/utils/roles.py` 的 `_model_factory`、`_environment_role_type` | 参数在嵌套闭包/动态类中使用，简单 AST 会误判，不能按未用参数直接删。 |

## 4. 高收益候选区域

### 4.1 `adapter/toolsandbox/utils/roles.py`

当前多个工厂函数只包一层 `_environment_role_type`：

```text
_openai_agent_factory
_generic_openai_agent_factory
_generic_openai_user_factory
_anthropic_agent_factory
_openai_server_agent_factory
_gemini_agent_factory
_environment_openai_user_type
_model_factory
```

建议改为配置驱动：

1. 保留 `get_agent_factory` / `get_user_factory` 两个入口。
2. 用一个 `RoleFactorySpec` 或简单 tuple 描述 `module_name`、`parent_class_name`、`mode`、`model_name`、`client_attr`。
3. 一个 `_build_role_factory(spec)` 负责返回无参 factory。
4. 删除多个单次调用 wrapper。

预期收益：`roles.py` 从 **189 行** 降至 **130-150 行**。

### 4.2 `evaluate/runtime.py`

可以保留运行期状态、pending stage、scoring context 的领域边界，但下面的小函数可内联到调用点：

| 函数 | 建议 |
| --- | --- |
| `_candidate_score_payload` | 只服务 `_ready_milestone_progress_from_candidate`，可内联。 |
| `_candidate_boundary_step_index` | 只服务 `_ready_milestone_progress_from_candidate`，可内联。 |
| `_attempt_step_index` | 只服务 `update_ready_frontier_progress_watch`，可在入口读取并校验。 |
| `_clamped_score` | 与其他文件数字 clamp 逻辑重复，建议迁移到 `utils.py` 的统一函数。 |

预期收益：`runtime.py` 从 **344 行** 降至 **260-285 行**。

### 4.3 `evaluate/scoring.py`

`GeneralScorer` 的主流程需要保留，但底部工具函数有部分冗余：

1. `enrich_stage_result` 删除未用参数。
2. `first_failure_stage_id` 只有一个调用点，可内联到 `_runtime_report`，或保留为清晰聚合函数；若保留，不应再写成“中转工具函数”风格。
3. 数字校验和 clamp 与 `diagnostics.py`、`runtime.py`、`settlement.py` 有重复，应统一到 `utils.py`。

预期收益：`scoring.py` 从 **450 行** 降至 **360-390 行**。

### 4.4 `adapter/toolsandbox/utils/convert.py`

该文件当前 **495 行**，同时承载：

1. role/actor/recipient 映射；
2. sandbox row 到 DynSTEER step 的转换；
3. scenario/milestone graph 转换；
4. context/snapshot/initial state 转换；
5. trajectory 反序列化。

这不是单纯行数问题，而是功能边界混杂。建议拆成同目录下更清晰的文件：

| 新文件 | 职责 |
| --- | --- |
| `roles.py` 已存在 | role 名称、actor、recipient 映射可向此处收敛。 |
| `trace.py` | sandbox rows、tool trace、tool arguments 转换。 |
| `scenario.py` | scenario constraints、milestone graph、task type 转换。 |
| `state.py` | database namespaces、initial state、snapshots。 |
| `trajectory.py` | sandbox rows 到 `TrajectoryStep` / `Trajectory`。 |

拆分时不要为了拆分而新增转发函数；调用方直接 import 目标函数。预期该区域总有效行数可从 **495 行** 降至 **380-420 行**，同时显著降低单文件复杂度。

## 5. 重复校验与公共工具收敛

静态扫描中重复出现较多的校验模式包括：

| 模式 | 次数 | 建议 |
| --- | ---: | --- |
| `value is None` | 18 | 只在外部入口/反序列化入口保留；内部流程依赖类型模型。 |
| `graph is None` | 13 | `TaskCase` 加载/适配完成后应保证 graph，不要在 finish 多个 helper 中反复校验。 |
| `session is None` | 8 | harness session 生命周期入口统一处理。 |
| `config is None` | 8 | public API 入口保留，内部 helper 删除。 |
| `not isinstance(data, dict)` | 6 | JSON loader 边界保留，解析链内部不重复。 |
| 数字字段判断 `bool/int/float` | 多处 | 提供 `utils.as_number` / `utils.clamped_number` 之类的小工具。 |

建议新增或整理 `dynsteer/utils.py` 中的公共工具，但只保留跨文件真实复用的函数：

```text
as_number(value, default=None)
as_int(value, default=None)
non_empty_str(value, default=None)
json_object(value, default=None)
```

注意：公共工具不应变成新的“大杂烩”。只有跨 3 个以上文件重复出现、且语义完全一致的校验才上移。

## 6. 分阶段实施方案

### 阶段 1：低风险机械清理

目标：不改变行为，清理确定性冗余。

1. 删除未用 import：`harness/runner.py` 的 `HarnessCaseExecutionError`。
2. 删除确定未用参数并同步调用点：
   - `build_milestone_matching_detail(graph, ...)`
   - `analyze_milestone_step(task_case, ...)`
   - `task_case_snapshot(..., trajectory)`
   - `enrich_stage_result(interval, ..., thresholds)`
3. 对 `evaluate/policy.py` 做小 helper 内联。
4. 跑 `pytest`，确保行为未变。

预期有效行数：**10079 -> 9600-9800**。

### 阶段 2：运行期评估链路简化

目标：处理用户点名的核心调用链。

1. 合并 `evaluate_checkpoint -> append_milestone_settlement -> append_stage_settlement`。
2. 保留 `_evaluate_stage` 作为真实阶段评估核心，但不作为对外 API。
3. `evaluate_checkpoint` 内直接组装 settlement 和状态推进结果。
4. 删除重复 frontier、policy、weights 参数拆装。
5. 补充/调整覆盖 checkpoint 成功、checkpoint 失败、策略终止、minefield 终止的测试。

预期有效行数：**9600-9800 -> 8900-9200**。

### 阶段 3：诊断与质量评估收敛

目标：减少 `evaluate` 内部一次性 helper 和重复扫描。

1. `evaluate/quality.py` 改为一次遍历收集 warning、empty result、failed result、efficiency 指标。
2. `evaluate/runtime.py` 内联 candidate 小字段解析函数。
3. `evaluate/diagnostics.py` 合并一行中转函数与重复格式化逻辑。
4. `evaluate/final.py` 保留 terminal state/message 两条领域线，但去掉重复 `graph is None` 校验。

预期有效行数：**8900-9200 -> 8200-8500**。

### 阶段 4：adapter/toolsandbox 收敛

目标：降低适配层单文件复杂度，减少工厂函数和转换函数碎片。

1. `roles.py` 用配置驱动替代多个一层 wrapper。
2. `convert.py` 按 trace/scenario/state/trajectory 拆分，删除拆分后产生的转发函数。
3. `scorer.py` 中 `_load_toolsandbox_*_module` 一行 wrapper 合并为带明确参数的 loader。
4. 保留与外部 `tool_sandbox` 包交互处的必要防御性校验。

预期有效行数：**8200-8500 -> 7600-8000**。

### 阶段 5：全局重复校验与公共工具整理

目标：达到目标上限 7800 行以内。

1. 将跨文件重复的数值/字符串/JSON 对象读取逻辑收敛到 `utils.py`。
2. 删除内部流程中重复的 None/type guard。
3. 只保留外部入口、反序列化入口、外部包边界、LLM/IO 边界的防御性校验。
4. 全量跑测试并补齐关键回归。

预期有效行数：**最终 7400-7800**。

## 7. 测试与验收标准

必须执行：

```powershell
pytest
```

建议补充或重点覆盖：

1. `tests/test_algorithm_revision.py`：策略升级/终止、checkpoint 结算、pending milestone。
2. `tests/test_agent_step_closure.py`：agent step 闭合后仍能触发 checkpoint。
3. `tests/test_progress.py`：progress 简化后显示与完成态不回退。
4. 新增 settlement 回归测试：覆盖 milestone pass、milestone fail、structural failure、finish settlement。
5. 若修改 `adapter/toolsandbox`，至少增加不依赖真实外部服务的 fake module loader 测试。

验收标准：

1. `dynsteer/**/*.py` 有效行数不高于 **7800 行**。
2. 不再存在只调用一次且只做转发的新增函数。
3. 内部 helper 的入参不再重复传递 `state` 中已包含的数据。
4. 公共接口、继承接口、外部包边界的防御性校验保留。
5. 单个文件不混杂跨度过大的职责；拆分后不得新增纯转发模块。

## 8. 不建议简化的边界

1. 不建议删除 `__init__.py` 中的 re-export，除非先确认外部包导入方式；静态扫描会把 re-export 误报成未用 import。
2. 不建议把 `model.py` 的数据模型与业务流程混在其他模块中。`model.py` 行数较高，但它是领域 schema 边界，应保守处理。
3. 不建议取消 judge/LLM/harness 抽象接口的参数，即使默认实现未使用全部参数；这些参数通常是子类覆盖契约。
4. 不建议为了压缩行数合并差异很大的模块，例如把 matching、diagnostics、runtime 全部塞回一个文件。

## 附录A. 项目中没有把握实现的模块部分

1. `adapter/toolsandbox` 与外部 `tool_sandbox` 包的动态类、角色工厂、Pyo3 异常兼容存在外部依赖。没有真实 sandbox 集成环境时，只能通过 fake module loader 与单元测试验证主要行为，不能完全确认所有外部角色类初始化路径。

2. LLM judge 相关行为依赖 OpenAI/Anthropic 等外部服务与环境变量。简化时可以保证接口和本地单测通过，但不能在无真实 API 配置的情况下验证线上响应格式差异。

3. 部分 public 函数可能被仓库外部脚本调用，例如 `load_trajectory`、`all_rubrics`、log buffer 访问函数。仓库内静态扫描显示未使用不等于可以删除；落地前需要用户确认是否存在外部调用入口。

   ==> 不存在外部调用入口

4. `docs/constraints/code.md` 要求核心逻辑保留空值检查和结构化异常处理，因此“去重校验”需要以边界前移为原则，不能机械删除所有 guard。

   ==> 没错，所有参数检查应只在函数调用链的起始处进行检查、中间函数没有必要做重复校验。
