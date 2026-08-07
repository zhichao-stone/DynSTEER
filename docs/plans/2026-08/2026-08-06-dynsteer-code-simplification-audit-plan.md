# DynSTEER 当前代码精简审计与执行方案

> 状态：仅制定执行方案，不在本轮修改生产代码。  
> 审计基线：当前 `HEAD`（`438cc30`）。  
> 约束依据：`docs/constraints/code.md`。  
> 目标：删除当前仍存在的重复实现、重复状态推导、重复校验和无必要的算法扫描；不通过压缩排版、合并语句或牺牲核心注释来制造行数下降。

## 1. 审计结论

当前主功能入口为 `main.py`、`display/build.py`，生产代码为 `dynsteer/**/*.py` 与 `display/**/*.py`。仓库当前没有测试 Python 文件（`438cc30` 已移除测试），因此本方案把“先补行为基线测试”列为落地前置步骤；测试文件不计入有效行数。

静态审计没有发现可以直接删除的生产模块，也没有发现除日志缓冲 API 之外的“定义后完全没有被主流程引用”的顶层函数。`dynsteer.log.get_log_buffer()`、`clear_log_buffer()` 当前没有仓库内调用，但 `docs/constraints/code.md` 明确要求保留日志缓冲区能力，不能误删。

当前仍可确认的冗余集中在以下十九类：

1. `skillsbench` 与 `swebench_pro` 对 AgentCompass 的 task/result/adapter/harness 映射存在重复骨架。
2. `write_default_case_outputs()` 自己复制了一套 session 推进、trajectory 收集和运行指标拼装流程；live/replay 另有一套同类流程。
3. snapshot “按 step 查找最近快照”在 `evaluate/matching/boundary.py`、`evaluate/diagnostics.py`、`evaluate/scoring.py` 形成三套实现；`Trajectory.extend_snapshots()` 还会在重复 ID 替换时无条件触发排序。
4. “所有硬失败是否都属于 emit-message”在 `evaluate/matching/milestone.py` 与 `evaluate/settlement.py` 重复实现；空 tool 内容在 `diagnostics.py` 也有两套相近判定。
5. AgentCompass detail 的 `task_id/attempts/attempt` 结构在 `run_agentcompass_case()` 验证后，`_sanitize_detail()` 再次验证。
6. `HarnessRunConfig`、`harness/config.py`、`BaseBenchmarkHarness` 在不同层重复校验同一批入口字段；`ToolSandboxHarness._role_impl_type()` 还带有未使用的 `role_label` 参数。
7. `display/build.py` 重复实现了 `utils.string_list()`、`compact_json_text()` 的薄包装；实验指标中多处手工构造分桶与平均值。
8. milestone 主流程把已知的 `TrajectoryStep` 包装成 Boundary，随后又从 Boundary 反查 step/snapshot；同一闭合 step 还会在 minefield、milestone 候选和实际 scoring step 三处重复构造 Boundary。
9. `ScoringContext` 在每个 raw step 反复从 settlements 和 snapshots 重建 matched boundary/snapshot 集合，`refresh_reference_anchors()` 内还会重复重建。
10. `evaluate_agent_step()` 同时传 `step` 与 `closure_steps`，评分器参数又通过 Optional + `get_effective_scorer()` 重复兜底。
11. frontier 同时维护可推导的 `blocked_candidate_ids`；候选评分同时保存领域对象与 JSON 副本；settlement 列表、matched 索引和 report fallback 形成多处事实推导。
12. `GeneralScorer` 为每个 constraint 重复扫描 graph 查所属 milestone、扫描 trajectory 查 step/snapshot；final milestone diagnostics 在同一次 finalize 流程中重复全量生成。
13. `Trajectory` 在有序 `steps` 之外同时维护 `first_step_index`、`latest_step_index` 和仅供两个调用点使用的 `successor_by_boundary`，三者均可直接从 steps 二分或取首尾得到。
14. `AgentStepTracker.pending_outbounds` 与 `pending_steps[key][0]` 保存同一 outbound step，两个字典需要同步增删。
15. milestone 拓扑同时存在于 `MilestoneGraph.edges`、`Milestone.dependency_predecessor_ids`、`metadata.graph_analysis.augmented_edges` 以及每次初始化 frontier 时重建的静态索引中。
16. route 同时写入 constraint metadata 和 milestone `route_groups`；运行期实际只使用 milestone 的唯一 sender/recipient，且每个候选再次复制、规范化 metadata。
17. `RuntimeEvaluationDecision.next_state` 实际始终引用调用者已经原地修改的同一个 state；`EvaluationTerminationState.should_stop` 又与 `termination_code` 重复表达是否终止。
18. minefield 在每个 raw step 全量重评，即使其 source step/snapshot 没有变化；命中又以 Boundary ID 去重，导致同一危险证据可能在后续 step 重复记账。
19. finish anchor/区间在 settlement 和 terminal evidence 中重复推导；`HarnessRunResult` 还保存四个生产调用者从不读取的字段并重复校验。

本轮不把必要的抽象接口、第三方适配边界、结构化日志、领域模型序列化契约判定为死代码。任何删除都必须先有主流程引用证据和行为测试。

## 2. 有效行数基线与统计口径

按 `docs/constraints/code.md` 的“有效行数”定义，当前基线如下：

| 范围 | 文件数 | 有效行数 |
| --- | ---: | ---: |
| `dynsteer/**/*.py` | 105 | 13,609 |
| `display/**/*.py` | 2 | 684 |
| `main.py` | 1 | 131 |
| 合计 | 108 | **14,424** |

统计方法：仅扫描 `rg --files -g '*.py'` 得到的源码；排除测试路径、空白行、仅 `#` 注释行、模块/类/函数 docstring 的完整物理行，以及完整的 `logger.*(...)` 调用行。`print()` 是 CLI 用户输出而不是日志，仍计入有效行。该数字是可复核基线，不把为了可读性保留的多行代码压成一行。

## 3. 目标架构：每个事实只有一个权威来源

落地后的数据流应收敛为：

```text
AgentCompass detail
  -> agentcompass/runtime.py 统一 schema 边界
  -> agentcompass/utils/result.py 统一公共状态字段
  -> benchmark-specific result summary（只处理 score/resolved/reward 差异）
  -> BaseAgentCompassHarness._build_run_data()
  -> 现有 harness/output writer
```

```text
Harness session
  -> collect_session_trajectory()（唯一推进/收集循环）
  -> default 或 evaluator callback 只提供“每步附加行为”
  -> _evaluation_payloads() / _default_payloads()（唯一 termination/schema 组装）
  -> _write_output_payloads()（唯一文件写入）
```

```text
Trajectory
  -> snapshot_at_or_before(step_index)（唯一最近快照查询）
  -> boundary、scoring、diagnostics 复用同一实现
```

所有“不能为空/类型正确”的检查只放在外部输入边界或 dataclass 构造边界；内部调用链不重复把已经构造成功的对象再次规范化。

## 4. 详细执行步骤

### 4.1 P0：先建立行为基线，不改生产逻辑

新增但不计入有效行数的测试目录，至少覆盖：

- `tests/adapter/agentcompass/test_result_common.py`：两 benchmark 的 status/error/score/resolved 映射。
- `tests/harness/test_outputs_flow.py`：default、evaluate、replay 的四个 JSON 产物和 termination 结构。
- `tests/evaluate/test_snapshot_lookup.py`：空轨迹、重复 snapshot ID、同 step 多 snapshot、乱序输入。
- `tests/evaluate/test_semantic_failure.py`：纯 emit-message 硬失败与混合硬失败。
- `tests/milestone/test_compiler_edges.py`：边顺序、传递约简、DAG 结果。

先在测试中固定当前 JSON 字段、阶段状态和异常类型，再进行每个 P1/P2 改动。测试缺失是本项目当前无法完全确认外部调用者的主要原因。

### 4.2 P1：收敛 AgentCompass 两个 benchmark 的重复骨架

涉及：

- `dynsteer/adapter/agentcompass/contract.py`
- 新增 `dynsteer/adapter/agentcompass/result.py`
- `dynsteer/adapter/skillsbench/utils/task.py`
- `dynsteer/adapter/swebench_pro/utils/task.py`
- `dynsteer/adapter/skillsbench/utils/result.py`
- `dynsteer/adapter/swebench_pro/utils/result.py`
- `dynsteer/adapter/skillsbench/adapter.py`
- `dynsteer/adapter/swebench_pro/adapter.py`
- `dynsteer/adapter/skillsbench/harness.py`
- `dynsteer/adapter/swebench_pro/harness.py`

#### 4.2.1 统一 task case 基础构造

在 `agentcompass/result.py`（或同一 AgentCompass 公共工具模块）增加：

```python
def task_case_from_record(
    record: AgentCompassTaskRecord,
    *,
    benchmark: str,
    description: str,
    metadata: JsonObject,
) -> TaskCase:
    return TaskCase(
        task_id=f"{benchmark}::{record.task_id}",
        case_id=record.task_id,
        task_description=description,
        metadata={"benchmark": benchmark, "category": record.category, **metadata},
    )
```

SkillsBench 只传 `record.question` 和 `agentcompass_commit`；SWE-bench Pro 在调用前组装 `question + requirements + interface`，并传 `repo/base_commit`。两个 `utils/task.py` 不再各自重复 `TaskCase(...)` 的公共字段。

#### 4.2.2 统一 detail 公共校验与状态标志

新增公共函数：

```python
def validated_attempt(detail: Mapping[str, object]) -> Mapping[str, object]:
    """只校验 detail -> attempt 的公共结构，并返回唯一 attempt。"""

def status_flags(status: str) -> JsonObject:
    return {
        "run_error": status in {"run_error", "run_error_or_eval_error"},
        "eval_error": status in {"eval_error", "run_error_or_eval_error"},
    }
```

`skillsbench/utils/result.py` 与 `swebench_pro/utils/result.py` 只保留 benchmark 特有逻辑：SkillsBench 的 `score/reward/test_return_code`，SWE-bench Pro 的 `resolved/completed/timed_out/returncode`。重复的 `_STATUSES`、`detail/attempt/evaluation` 三层检查和 `run_error/eval_error` 计算删除。

#### 4.2.3 适配器改为配置驱动的薄实现

在 AgentCompass 公共模块增加 `adapt_record_case()` 与 `build_agentcompass_generator_view()` 的公共部分；两个 adapter 只提供：

- benchmark 名称；
- task description builder；
- metadata builder；
- environment schema；
- output contract。

保留 `task_case.case_id == case_id` 的一次边界检查，删除两份完全相同的 records 查找/异常包装骨架。若确认没有仓库外代码依赖 `SkillsBenchConstraintScorer`、`SWEBenchProConstraintScorer` 的具体类型，则两个空 scorer 类删除，harness 直接返回 `GeneralScorer()`；若外部依赖测试证明需要类型名，则保留类但只保留 `pass`，不再新增任何空方法。

#### 4.2.4 修复 contract 的隐式副作用

`agentcompass/contract.py::_actf_evidence_catalog()` 当前直接给 `task_case.tool_schema["tools"]` 中的原对象写入 `source_ref` 和 `value`。改为复制 tool dict，返回 `(tool_schema, evidence)` 或在 `build_agentcompass_generator_view()` 内一次性构造新 schema：

```python
tool_schema = json_safe(task_case.tool_schema)
tools = tool_schema.get("tools", [])
for index, tool in enumerate(tools):
    tool = dict(tool)
    tool["source_ref"] = f"tool:{index}:name"
    tool["value"] = name
    tools[index] = tool
```

这样重复生成 view 不会累积字段，也不需要下游再次清洗同一对象。

预计净减少：**85～115 行**。

### 4.3 P1：统一 session 收集与 output payload

涉及：

- `dynsteer/harness/outputs.py`
- `dynsteer/evaluate/evaluator.py`
- `dynsteer/experiment/runner.py`

#### 4.3.1 抽取唯一 session 推进器

新建 `dynsteer/harness/session.py`（避免 `outputs.py` 与 evaluator 形成循环依赖）并增加：

```python
def collect_session_trajectory(
    harness: BaseBenchmarkHarness,
    session: object,
    trajectory: Trajectory,
    *,
    on_step: Callable[[TrajectoryStep], bool | None] | None = None,
    on_batch: Callable[[HarnessAdvanceResult], None] | None = None,
) -> int:
    """推进 session、追加 snapshots/steps、同步 final_state/metrics，返回闭合 agent step 数。"""
```

`on_step` 返回真值时停止当前 batch 后的继续推进。该函数唯一负责：`timed_advance_case()`、`append_execution_timing()`、`extend_snapshots()`、`append_step()`、`final_state_from_session()`、`metrics_from_session()`、`AgentStepTracker` 的闭合计数。`write_default_case_outputs()` 通过 `on_step` 只做 tracker ingest；在线 evaluator 通过回调执行评估和提前停止，但复用底层追加与指标更新逻辑。禁止在 default/replay/evaluate 中再次复制 while/for 骨架。

#### 4.3.2 统一 termination 与 schema 组装

增加单一内部函数：

```python
def _termination_payload(raw: Mapping[str, object]) -> JsonObject:
    return {
        "should_stop": bool(raw.get("reason")),
        "code": raw.get("code"),
        "reason": raw.get("reason"),
        "detail": raw.get("detail") or {},
    }
```

`write_default_case_outputs()` 不再先 `pop termination_code/reason/detail` 再手工重组；default/evaluate/replay 都把 `EvaluationTerminationState.to_dict()` 送入同一个 payload builder。`report.json`、`summary.json`、`raw_summary.json` 的 schema 版本和 termination 字段只在 `_evaluation_payloads()`/`_default_payloads()` 各保留一个明确入口，避免同一字段在多个层级重复生成。

#### 4.3.3 删除无效的重复状态镜像

在 `experiment/runner.py` 中只从 `summary.json` 的 `score/milestone_coverage/minefield_match_count/termination.code/runtime_metrics` 构造 `ExperimentCaseResult`；不再从 raw summary 或 default reference 回退推导同一事实。保留 default output cache 本身，因为 replay 明确依赖它的 trajectory。

预计净减少：**65～95 行**。

### 4.4 P1：建立唯一 snapshot 查询与增量索引

涉及：

- `dynsteer/model.py::Trajectory`
- `dynsteer/evaluate/matching/boundary.py`
- `dynsteer/evaluate/diagnostics.py`
- `dynsteer/evaluate/scoring.py`
- `dynsteer/evaluate/final.py`
- `dynsteer/evaluate/runtime.py`
- `dynsteer/evaluate/settlement.py`

#### 4.4.1 `Trajectory` 增加唯一查询接口

在 `Trajectory` 内维护按 `(after_step_index, snapshot_id)` 排序的 snapshots，并增加：

```python
def snapshot_at_or_before(self, step_index: int) -> StateSnapshot | None:
    position = bisect_right(
        self.snapshots, step_index,
        key=lambda snapshot: snapshot.after_step_index,
    ) - 1
    return self.snapshots[position] if position >= 0 else None
```

`boundary.py`、`diagnostics.py` 删除 `_latest_snapshot_id()`、`_latest_snapshot_at_or_before()` 和重复的候选列表扫描；`boundary_snapshot()` 只处理显式 `snapshot_id`，找不到时调用唯一的 step 查询。`GeneralScorer.constraint_sources()`、finish 检查、settlement 锚点刷新统一使用同一接口。

#### 4.4.2 修正 `extend_snapshots()` 的不必要排序

当前重复 ID 替换也把 `out_of_order` 置为 `True`，导致每次替换都全量排序。修改为：

- 已存在 ID：原位替换，不触发排序；
- 新 ID 且新 key 小于尾部：标记乱序；
- 仅在真正新增乱序 snapshot 时排序并重建索引。

同时删除 `__post_init__()` 中重复的 `latest_step_index` 类型注解赋值，保留一次顺序检查。

预计净减少：**25～45 行**，并把重复查询从 O(n) 收敛为 O(log n)。

### 4.5 P1：合并语义失败和空 tool 判定

涉及：

- `dynsteer/evaluate/semantic.py`
- `dynsteer/evaluate/matching/milestone.py`
- `dynsteer/evaluate/settlement.py`
- `dynsteer/evaluate/diagnostics.py`

在 `semantic.py` 增加唯一公共函数：

```python
def hard_failure_is_semantic_message_only(
    milestone: Milestone,
    scores: Iterable[ConstraintScore],
) -> bool:
    """返回是否存在硬失败且所有硬失败均为 emit_message。"""
```

`matching/milestone.py::_hard_failures_are_semantic_emit_messages()` 与 `settlement.py::_semantic_only_hard_failure()` 删除，调用公共函数。公共函数内部只构建一次 `constraint_id -> score` 映射，并统一 `missing/threshold` 语义。

将 `diagnostics._is_empty_collection_content()` 与 `_is_empty_tool_content()` 合并为：

```python
def _empty_tool_content(value: object, *, treat_null_as_empty: bool = True) -> bool:
    ...
```

查询工具分类传 `treat_null_as_empty=False`，一般 tool 结果传默认值，删除两套重复的 list/dict/字符串空值判断。

`evaluate/settlement.py::evaluate_checkpoint()` 将“是否接受 checkpoint”的布尔表达式计算一次，后续 if 和 semantic metadata 均复用该变量。

预计净减少：**35～55 行**。

### 4.6 P1：把重复校验收敛到边界

涉及：

- `dynsteer/adapter/agentcompass/runtime.py`
- `dynsteer/harness/config.py`
- `dynsteer/harness/model.py`
- `dynsteer/adapter/base.py`
- `dynsteer/adapter/toolsandbox/harness.py`

1. `run_agentcompass_case()` 已验证唯一 detail、唯一 attempts 后，将选中的 `raw_attempt` 直接传给 `_sanitize_detail()`；删除 `_sanitize_detail()` 对 `task_id/attempts/attempt` 的重复结构校验，只保留 benchmark 结果字段和白名单校验。
2. `HarnessRunConfig.__post_init__()` 作为构造边界的唯一类型/range 校验点；`load_harness_run_configs()` 保留 JSON 对象/未知字段/配置项定位信息，但删除对 benchmark、data_root、布尔开关、patience、min_delta 的重复值校验，让 dataclass 统一抛出异常。
3. `BaseBenchmarkHarness.prepare_config()` 只调用一次 `_validate_config()`；默认 no-op 的 `metrics_from_session()`、`initial_state_from_session()`、`final_state_from_session()`、`raw_summary_from_session()` 不再为“不读取 session 的空实现”重复做 `session is None` 检查。真正有行为的子类入口继续使用 `_require_session()`；`stop_case()` 仍保留 reason/session 边界检查，因为它是可覆盖的生命周期接口。
4. 删除 `ToolSandboxHarness._role_impl_type(role_name, role_label)` 的未使用 `role_label` 参数，改为 `_role_impl_type(role_name)`。
5. `roles.py` 中 `_environment_role_type()` 已把 client config 归一化后，向 `_openai_client_from_config()`/`_anthropic_client_from_config()` 传入归一化 mapping；私有 `_client_kwargs()` 不再重复执行同一 normalize/字段过滤。

预计净减少：**25～40 行**。

### 4.7 P2：复用公共文本/列表工具并收紧实验指标分桶

涉及：

- `display/build.py`
- `dynsteer/utils.py`
- `dynsteer/experiment/metrics.py`

1. `display/build.py` 删除 `_string_list()`、`_text()`，直接复用 `dynsteer.utils.string_list()` 与 `compact_json_text()`；调用点保留原有 limit 和 `None` 语义。
2. 在 `experiment/metrics.py` 增加一个私有 `_group_average(results, key, score_getter)`，让 `model_scores()` 与 `repeat_model_scores()` 共享“过滤 score、分桶、求平均”逻辑；不合并含义不同的 rank consistency、delta、cost 指标。
3. `discriminability_score()` 先计算一次排序后的 model 列表，复用到 `model_pairs`，删除同一 mapping 的重复 `sorted(normalized)`。
4. 不删除 `repeat_rank_consistency()`、`rank_tau_by_repeat()` 等主流程实际写入 `metrics.json` 的函数；它们不是死代码，只能做内部扫描收敛。

预计净减少：**8～20 行**。

### 4.8 P2：优化 milestone compiler 的边算法（以复杂度下降为主，行数不作硬目标）

涉及：`dynsteer/milestone/compiler.py`。

1. `_consistent_reduced_edges()` 当前对每一对 atom 反复扫描路径并调用 `items.index()`。先为每条 distinct path 建 `atom_id -> position` 映射，再按 pair 统计先后关系，避免重复线性查找。
2. `_transitive_reduction()` 当前每移除一条边都重建 successors。预先构造 adjacency，DFS 只读取该索引；保持输出 edge 排序与 DAG 判定不变。
3. `_parse_atoms()`、`_parse_paths()`、`_compile_minefields()` 的严格 schema 校验保留，因为这些是 LLM 输出安全边界，不属于冗余校验。

该阶段预计净行数变化为 **-5～+5 行**，主要收益是路径数/atom 数增大时的时间复杂度和内存分配下降，而不是排版压缩。

## 5. 明确不做的改动

- 不把 `model.py` 的所有 dataclass 强行改成通用 `asdict/json_safe`；`StageEvaluationResult`、`TrajectoryEvaluationReport` 等 `to_dict()` 是稳定输出契约，必须用 golden JSON 验证后才可进一步收敛。
- 不删除 `BaseBenchmarkHarness` 的真实 lifecycle hook（`start_case`、`advance_case`、`stop_case`、`teardown_case`、state/metrics/summary 提取）；它们在 evaluator 或 benchmark 子类中有实际调用或覆盖价值。
- 不删除日志 buffer、结构化 formatter、第三方依赖边界；这些由约束或运行时边界明确要求。
- 不保留仅为了“未来可能使用”而没有调用者的 wrapper；若外部使用证据出现，应在测试中把它升级为明确 public API，而不是隐式兼容。
- 不修改 `.gitignore`，不删除 `docs/constraints` 或已有 `docs/plans`。

## 6. 实施顺序与验收

1. 建立 P0 行为测试和当前 JSON golden fixture。
2. 完成 AgentCompass 公共 result/task/adapter 收敛，运行 adapter smoke tests。
3. 完成 session collector 与 output payload 收敛，运行 default/evaluate/replay writer tests。
4. 完成 snapshot 单一查询接口，运行乱序/重复 ID/空轨迹测试并做一次真实结果回放。
5. 完成 semantic/diagnostics dedup，再运行 evaluator/settlement tests。
6. 完成边界校验收敛、display 工具复用和 metrics 分桶收敛。
7. 最后落地 compiler 算法优化；比较同一输入下 graph、edges、report 完全一致。
8. 执行：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
uv run pytest -q -p no:cacheprovider --cov=dynsteer --cov-report=term-missing
uv run python -m compileall dynsteer display main.py
uv run python -m display.build --help
uv run python main.py --help
git diff --check
```

核心 evaluator、settlement、outputs、adapter runtime、snapshot、compiler 覆盖率目标不低于 80%。第三方 AgentCompass/ToolSandbox 使用 mock 边界测试，不在单元测试中发起网络请求。

## 7. 初版有效行数缩减估算

> 本节是首次审计的估算；加入第 8～10 节的 milestone 匹配链路补充后，以第 11 节的更新结果为准。

| 精简项 | 预计净减少有效行 |
| --- | ---: |
| AgentCompass task/result/adapter/harness 公共骨架 | 85～115 |
| session collector、termination、output payload 收敛 | 65～95 |
| snapshot 单一接口与增量索引 | 25～45 |
| semantic hard-failure、empty-tool、checkpoint 接受判定 | 35～55 |
| 边界校验和 ToolSandbox/roles 参数收敛 | 25～40 |
| display/utils 与 metrics 小型去重 | 8～20 |
| compiler 边算法重写 | -5～+5 |
| **合计** | **238～375** |

因此：

- 当前有效行数：**14,424**；
- 预期净缩减：**约 320 行**（验收允许区间 238～375 行）；
- 预期落地后：**约 14,104 行**，保守区间 **14,049～14,186 行**。

最终数字必须在实现后用同一 AST 统计脚本重新计算；不得通过删除 docstring、关键注释、日志之外的可执行行换取目标。若因保留外部依赖的空 scorer 类型或 golden schema 兼容而少删约 15～25 行，应以行为稳定为准，并在最终报告中单独列出。

## 附录 A：当前无法完全确认的部分

1. 当前仓库没有测试文件，无法仅凭仓库内静态搜索证明 `get_log_buffer()`、适配器具体 scorer 类型、`display` JSON 字段没有仓库外调用者；因此本方案将 scorer 删除列为“先测后删”，日志 buffer 明确保留。
2. AgentCompass 是可选依赖，`run_agentcompass_case()` 的真实返回 schema 需要在固定版本上用 fixture 验证；detail 白名单收敛不能只依赖类型推断。
3. `runs/`、`results/` 中的历史 JSON 不是源码契约的完整样本。display 旧产物字段是否被外部分析脚本读取，实施前需抽样检索并用 golden fixture 固化。
4. compiler 的传递约简优化需要对冲突路径、重复 atom、同一层多 snapshot 的边界样本做基准测试；若结果顺序不能保持字节级稳定，只接受复杂度改进而不删除排序步骤。

## 附录 B：完成标志

- 所有 P1 项均有对应行为测试且通过；
- `rg` 不再发现已删除 helper、重复 termination 字段或两套 snapshot 查询入口；
- default/evaluate/replay 的 report、summary、raw_summary 字段与 golden fixture 一致；
- AgentCompass 两个 benchmark 的 task/result 公共字段只保留一份实现；
- compiler graph 结构和 stage settlement 结果不变；
- 生产代码无语法错误，核心覆盖率达到 80%；
- 用同一统计口径复核有效行数，并在交付说明中报告实际缩减量。

## 8. 本次追加审计：milestone 匹配链路去除冗余中间层

本节针对“`trajectory → boundary → step` 是否必须经过 `Boundary`”进行代码级复核。结论是：**当前 `Boundary` 不是独立业务实体，而是把一个 `step_index`、一个可推导的 snapshot 和若干展示字段打包后的临时对象；在 milestone 匹配主流程中可以删除。** 不能删除的是阶段区间的左开边界语义，即 `(start_boundary_step_index, end_step_index]`，它应降为整数而不是继续保留对象。

### 8.1 当前真实调用链与重复构造

当前链路实际是：

```text
raw TrajectoryStep
  -> AgentStepTracker.ingest() 闭合 AgentStepClosure
  -> closure.end_step 作为当前结束 step
  -> evaluate_agent_step() 构造一次 Boundary
  -> _score_candidate() 按 milestone route 再构造 scoring Boundary
  -> GeneralScorer.score_milestone()
  -> evaluate_checkpoint() 生成 StageInterval/Settlement
```

此外，每个 raw step 的 minefield 即时检查在 `evaluate_step_minefields()` 先构造一份 Boundary；同一个 step 闭合 agent step 后，`evaluate_agent_step()` 又构造第二份。`_score_candidate()` 收到的前一份 Boundary 并不用于实际评分，只用于 predecessor 起点比较和未命中诊断；真正评分使用的是根据 `milestone_scoring_step()` 选择出的 scoring step。因此当前存在两层重复：

1. 同一 step 在 minefield 和 milestone 阶段重复构造 Boundary；
2. 当前候选 Boundary 与 scoring Boundary 同时存在，但只有 scoring Boundary 代表评分事实。

### 8.2 以 step index/TrajectoryStep 直接表达评分点

涉及文件：

- `dynsteer/model.py`：删除 `Boundary`；`MilestoneStepAnalysis.hit` 改为保存 `(Milestone, TrajectoryStep, MilestoneScore)`，或统一使用内部 `ScoredCandidate` 保存 milestone、`scoring_step` 和 score。
- `dynsteer/evaluate/matching/boundary.py`：删除 `candidate_boundary_for_current_step()`、`boundary_step()`、`boundary_snapshot()`；其查询职责移入 `Trajectory`。
- `dynsteer/evaluate/matching/milestone.py`：`analyze_milestone_step()` 和 `_score_candidate()` 直接传递 `step_index`/`TrajectoryStep`；候选详情使用 `step_index` 和 `step_id`，不再生成 `boundary_id`、`snapshot_id`、`reason` 副本。
- `dynsteer/evaluate/matching/minefield.py`、`dynsteer/evaluate/settlement.py`、`dynsteer/evaluate/final.py`、`dynsteer/evaluate/step.py`：将 Boundary 参数改为 `step_index` 或已解析的 `TrajectoryStep`，快照通过统一的 `Trajectory.snapshot_at_or_before(step_index)` 获取。
- `dynsteer/evaluate/scoring.py` 与 `dynsteer/adapter/toolsandbox/scorer.py`：评分 source 解析直接接收 step/step index；`MilestoneScore.boundary_id` 改为 `evaluated_at_step_index`，或者在结算层统一补充该字段后从评分对象删除。
- `dynsteer/harness/model.py::HarnessStageSettlement`：删除 `boundary_id`；milestone settlement 的 `boundary_step_index` 始终等于 `end_step_index`，也一并删除，统一用 `end_step_index` 表达实际评分/结算位置。
- `dynsteer/evaluate/diagnostics.py`：候选和结算诊断改用 `step_index`/`boundary_step_index`；保留区间字段和 `(start_boundary_step_index, end_step_index]` 说明。

`Trajectory` 增加两个唯一接口：`step_at(index)` 和 `snapshot_at_or_before(index)`。前者使用有序 steps 的二分查找，后者使用现有 snapshot 索引；不再为每个约束线性扫描整条轨迹。`_step_reason()` 如果仅用于 Boundary 展示，则随 Boundary 一并删除；如果用户界面确实需要事件分类，则改成只在诊断组装阶段根据 `TrajectoryStep` 计算一次。

迁移顺序必须是“先改内部类型，再删模块”：先让所有生产调用点只传 step index/step，再删除 Boundary 字段和模块，最后更新 JSON golden fixture。不得为了兼容旧对象在新接口中保留一个无行为的 Boundary wrapper；若外部产物仍需字段，直接在唯一 serializer 中由 step index 生成兼容 JSON。

### 8.3 保留而不误删的阶段区间语义

`StageInterval.start_boundary_step_index` 不是冗余对象。它定义阶段的左开端点，`stage/trajectory.py::stage_steps()` 依赖该语义返回 `(start_boundary_step_index, end_step_index]` 内的 steps。实现上只将其改名为更直接的 `start_exclusive_step_index`（若会破坏既有报告，则暂时保留旧 JSON key），不要把起点和终点合并成单一 step，也不要删除 `Trajectory.successor_by_boundary`。这部分是业务区间定义，不是 Boundary 临时对象的残留。

## 9. 本次追加审计：匹配状态、评分上下文和诊断的进一步收敛

### 9.1 `ScoringContext` 不应每个 raw step 重建完整 Boundary/snapshot 集合

位置：`dynsteer/evaluate/runtime.py::scoring_context()`，调用自 `evaluate_step_minefields()`、`evaluate_agent_step()`、`refresh_reference_anchors()` 和 finish 检查。

当前函数每次调用都会遍历 `matched_settlements`，重新构造 Boundary，再扫描 trajectory snapshots 找对应快照，并把 `reference_anchor_snapshots` 复制覆盖到 `matched_snapshots`。这既重复分配对象，也重复做 O(milestone × snapshot) 查询。

修改方案：

1. `ScoringContext.matched_boundaries` 改为 `matched_step_indexes: Mapping[str, int]`；它直接由 settlement 的 `end_step_index` 在结算时写入，禁止每次从 settlement 重建。
2. 将 `reference_anchor_snapshots` 作为已匹配 milestone 的唯一参考快照表；milestone 首次结算时写入其 snapshot，ToolSandbox 动态锚点前移时原位替换。`scoring_context()` 只引用该表，不再为已匹配项重新扫描 snapshots。
3. initial snapshot 只构造一次并放入 runtime/context 缓存，不在每个 step 创建同一个 `StateSnapshot("initial", ...)`。
4. 将 `refresh_reference_anchors()` 改为返回（或更新）当前 context；同一 raw step 的 minefield 检查与闭合 agent step 评分复用同一 context。只有 anchor 真正前移时才更新映射。

### 9.2 删除 `step`/`closure_steps` 和 Optional scorer 的重复接口

`evaluator.py::_evaluate_closed_agent_step()`、`evaluate/step.py::evaluate_agent_step()`、`matching/milestone.py::analyze_milestone_step()` 目前同时传递 `closure.end_step` 和 `list(closure.steps)`，并在下游用 `closure_steps or [step]` 做重复兜底。改为直接传 `AgentStepClosure`，由调用者读取 `closure.end_step`，由评分选择逻辑读取 `closure.steps`；删除两个并行参数及其空列表兜底分支。

`dynsteer/evaluate/scoring.py::get_effective_scorer()` 和 `analyze_milestone_step(..., scorer: GeneralScorer | None)`、`evaluate_minefields_at_boundary(...)` 中的 fallback 也属于已由 harness 保证的入口条件。统一要求 `scorer: GeneralScorer`，删除 `GeneralScorer()` 的隐式兜底和 Optional 分支；直接调用的公共 API 若需保留，另用边界测试证明后再决定是否保留兼容适配。

### 9.3 frontier 的 blocked 列表是可推导状态

`MilestoneFrontierState` 同时保存 `remaining_predecessor_count` 与 `blocked_candidate_ids`。后者不是新的业务事实：当前“接近前沿但仍缺前驱”的集合可按 graph 顺序由以下条件得到：

```text
milestone 未匹配
且 0 < remaining_predecessor_count[milestone_id]
    < len(milestone.dependency_predecessor_ids)
```

将 `blocked_candidate_ids` 从模型和 `advance_milestone_frontier()` 删除，`blocked_candidate_milestones(frontier, matched_ids)` 直接按上述条件生成；保留 `ready_ids` 作为有序热路径缓存。该条件特意要求“已有至少一个前驱完成”，与当前 blocked 列表的语义一致，不会把从未靠近执行前沿的节点全部加入诊断。实施前必须用多前驱、单前驱、非法前驱和重复 settlement 样本固定输出顺序；若大图基准证明每步全图过滤不可接受，可保留 blocked 作为纯性能缓存，但不得再把它当作独立语义状态来源。

`advance_milestone_frontier()` 的 `matched` 参数只用于防止 successor 已结算时重复扣减。若行为测试确认 milestone 结算具有“一次成功、不可重复”的不变量，则删除该参数和对应判断；否则保留该保护，并将重复 settlement 视为上游 invariant 错误而不是在此处继续堆叠兼容逻辑。

### 9.4 候选评分对象与 JSON 诊断副本分离

`matching/milestone.py::_score_candidate()` 当前同时返回 `Boundary`、`MilestoneScore` 和已序列化的 `detail_json`。候选尚未选中时就复制完整 score/boundary，之后 `attempt_detail` 又长期保存同一份 JSON。改为内部只返回 `ScoredCandidate(milestone, scoring_step, score)`；在选择完成、生成 `attempt_detail` 或 blocked diagnostics 时一次性序列化。若不新增 dataclass，则至少让 detail 只保存 `step_index`、`step_id` 和 score，并从领域对象推导 boundary 字段，避免对象与 JSON 双份驻留。

### 9.5 settlement、report 和 final diagnostics 的重复状态源

`RuntimeEvaluationState.settlements` 是按时间顺序输出的 start/milestone/finish 列表，`matched_settlements` 是按 milestone 查找的索引；两者用途不同，不能简单删除其一，但当前 milestone 结算和 finish 结算分散地直接 `append()`/赋值，存在状态不一致风险。增加唯一的 `_record_settlement()`：统一追加 ordered list，并仅对 `kind == "milestone"` 更新索引；删除各处重复写入。

`evaluator.py::_runtime_report()` 在 `matched_settlements` 为空时又从 `stage_reports` 反推 matched ids。这是第二个事实来源；在 `_record_settlement()` 不变量建立后删除该 fallback，coverage 只读 matched index。`pending_milestone_stage_results()` 与 `_build_runtime_result()` 会在同一次 finalize 流程中两次调用 `build_final_milestone_diagnostics()`；在 finalize 阶段生成一次并传递给 pending/report builder，避免同一 `match_attempts` 全量扫描两遍。

### 9.6 约束归属和当前 step 不应在每个 constraint 内反查

`GeneralScorer._stage_start_step_index()` 为每个 constraint 遍历整个 milestone graph，寻找该 constraint 属于哪个 milestone，然后再从 `ScoringContext.matched_boundaries` 取起点；`constraint_sources()`/`boundary_step()` 还会为每个 constraint 重新按 index 扫描 trajectory。评分一个 milestone 时，当前 milestone 和 scoring step 本来已经在调用栈中。

修改为在 `score_milestone()` 入口一次解析：

1. 根据当前 milestone 的 `stage_anchor_predecessor_id` 和 `matched_step_indexes` 得到一个 `stage_start_index`；
2. 根据 scoring step 得到一个 `TrajectoryStep` 和一个最近 snapshot；
3. 将这三个已解析值传给所有 constraint source 计算，删除 graph 反查和重复 `boundary_step()`/snapshot 扫描。

milestone 约束直接使用当前 milestone 的已知归属；minefield 约束没有当前 milestone 时由调用者显式传入当前扫描区间的起点，缺失时使用唯一的全轨迹默认起点，不再扫描 milestone graph 猜测归属。这样评分复杂度从“每个 constraint 扫描 graph 和 trajectory”降为“每个评分点一次索引查询 + 每个 constraint O(1) 取 source”。

### 9.7 有意保留的 finish 重评

`final.py::_terminal_state_checks()` 在 finish 时重评 terminal state constraints，不是与 milestone 匹配重复：它验证的是最终轨迹状态是否仍满足 terminal constraint；`_terminal_message_checks()` 明确跳过已确认的 emit_message，正是为避免重复重评。该逻辑保留，只把 Boundary 参数替换为 final step index/snapshot。

## 10. 本次补充后的实施顺序与验收增量

在原第 6 节顺序之后追加：

1. 先以行为测试锁定 `Boundary` 诊断字段、阶段区间 `(start, end]`、多前驱 blocked 候选顺序和 terminal final recheck 结果。
2. 先引入 `Trajectory.step_at()`/`snapshot_at_or_before()` 和 step-index 版本评分入口，再删除 Boundary 模型和 `matching/boundary.py`，避免一次改动同时改变查询和评分语义。
3. 引入 `_record_settlement()`、单次 final diagnostics 缓存和共享 ScoringContext，检查 live/default/replay 三条入口的 settlement 顺序、coverage、minefield 结果完全一致。
4. 最后删除 Optional scorer、closure 双参数、blocked 列表和 graph 反查；每项删除都必须有 `rg` 引用清零和对应回归测试。

新增验收命令：

```powershell
rg -n "Boundary|candidate_boundary_for_current_step|boundary_snapshot|boundary_step|get_effective_scorer|blocked_candidate_ids|closure_steps" dynsteer
```

上述搜索结果只允许出现在迁移兼容 serializer、测试 fixture 或方案文档中；生产匹配主流程不应再依赖这些符号。

## 11. 第二轮审计后的有效行数估算

| 本次新增精简项 | 预计净减少有效行 |
| --- | ---: |
| Boundary 临时对象及 matching/boundary.py 删除、step-index 接口迁移 | 35～65 |
| ScoringContext 重建、快照复制和同一步 context 复用 | 15～30 |
| Closure 双参数、Optional scorer/get_effective_scorer | 8～15 |
| blocked_candidate_ids 推导化及 frontier 参数收敛 | 10～20 |
| ScoredCandidate 与 JSON 延迟序列化 | 5～15 |
| settlement 统一写入、coverage fallback、final diagnostics 单次计算 | 15～25 |
| constraint 归属反向索引与重复 step/snapshot 扫描消除 | 12～20 |
| **本次补充合计** | **100～185** |

叠加原方案 **238～375 行** 的精简项后，预计总净缩减为 **338～560 行**。按当前 14,424 行基线，实施后预计为 **13,864～14,086 行**，中位目标约 **13,975 行**。最终仍必须使用原第 2 节同一统计脚本复核；若为保持真实外部 scorer 类型、报告字段或第三方适配契约而少删代码，应在验收报告中单独说明，不得通过压缩可读性排版或删除必要注释凑数。

## 12. 第三轮追加审计：仍可删除的重复状态与重复扫描

### 12.1 `Trajectory` 不应同时维护 steps 与三个可推导字段

位置：`dynsteer/model.py::Trajectory`、`dynsteer/stage/trajectory.py`、`dynsteer/evaluate/settlement.py`。

`steps` 已被严格校验为 index 递增，但 `Trajectory` 仍维护：

- `first_step_index`：等于 `steps[0].index`，空轨迹时才使用默认值；
- `latest_step_index`：等于 `steps[-1].index`；
- `successor_by_boundary`：为每个 step 保存后继关系，但生产代码只有两个 `stage_start_step_index()` 调用点使用。

删除这三个 dataclass 状态字段和 `_append_step_index()`。`first_step_index`、`latest_step_index` 如需保留调用语义，改为只读 property；`append_step()` 直接与 `steps[-1].index` 比较完成递增校验。新增 `Trajectory.first_step_after(boundary_index, end_index)`，用 `bisect_right()` 找到左开区间内首个 step；删除 `stage_start_step_index(successor_by_boundary, ...)` 和整张 successor map。这样不会把非连续 step index 错误地当作 `boundary + 1`，也不会引入线性扫描。

### 12.2 `AgentStepTracker` 的两个 pending 字典保存了同一个事实

位置：`dynsteer/model.py::AgentStepTracker`。

`pending_outbounds[key]` 与 `pending_steps[key][0]` 是同一个 Agent outbound step。所有新增、完成和错误诊断都要同步维护两个字典。改为唯一的 `pending_steps: dict[str, list[TrajectoryStep]]`：

- pending outbound 由 `steps[0]` 读取；
- pending keys 直接使用 `pending_steps.keys()`；
- `_complete_pending()` 只 `pop()` 一次；
- route/correlation 检查遍历每个列表的第一个 step。

`completed_count` 保留，因为已完成 closure 不再存储，无法从当前 pending 集合推导。不得为了删除该计数而长期保存全部历史 closure。

### 12.3 milestone graph 拓扑存在四份表示

位置：

- `dynsteer/model.py::MilestoneGraph/Milestone/MilestoneFrontierState`
- `dynsteer/graph.py::enrich_milestone_graph`
- `dynsteer/evaluate/matching/frontier.py`
- `dynsteer/evaluate/final.py`
- `dynsteer/evaluate/settlement.py`
- `dynsteer/prompt/stage.py`

当前同一拓扑被表示为：`graph.edges`、每个 node 的 `dependency_predecessor_ids`、`metadata.graph_analysis.augmented_edges`，以及每次 live/replay 初始化 frontier 时重建的 `milestone_by_id/dependents_by_id/order_by_id`。finish 又重新扫描 edges 构造 outgoing 和 terminal ids，并在失败时回退解析 metadata JSON。

以 `MilestoneGraph.edges` 作为唯一持久化输入事实，在 graph enrichment 时构造一个类型化、只读的 `MilestoneTopology`：

```python
@dataclass(frozen=True)
class MilestoneTopology:
    milestone_by_id: Mapping[str, Milestone]
    predecessors_by_id: Mapping[str, tuple[str, ...]]
    successors_by_id: Mapping[str, tuple[str, ...]]
    order_by_id: Mapping[str, int]
    stage_anchor_by_id: Mapping[str, str]
    root_ids: tuple[str, ...]
    terminal_ids: tuple[str, ...]
    finish_anchor_id: str
```

删除 `Milestone.dependency_predecessor_ids`、`stage_anchor_predecessor_id` 和供内部读取的 `metadata.graph_analysis`。诊断或 prompt 若需要 `augmented_edges`，只在 serializer 中由 topology 生成，不得再让业务逻辑解析 metadata JSON。`MilestoneFrontierState` 只保留运行期变化的 predecessor count、ready ids 和 topology 引用；不再为每次 evaluation/replay 复制静态 node/dependent/order 映射。

如果不希望在 `MilestoneGraph` 上持有派生 cache，可由 `RuntimeEvaluationState` 持有唯一 topology；两种实现只能选一种，禁止 graph 和 runtime 各存一份。

### 12.4 route enrichment 写入了未被匹配算法使用的 constraint metadata

位置：`dynsteer/adapter/route.py`、`dynsteer/evaluate/matching/milestone.py`。

`_set_constraint_route_metadata()` 为每个 constraint 写入 route 和 route_source，但匹配算法只读取 milestone metadata 中第一个 `route_groups[0].route`；`constraint_ids` 也没有参与 scoring step 选择。改为每个 milestone 只保存一个类型化 `matching_route: tuple[Actor, Actor] | None`，删除 constraint 级 `milestone_matching` metadata、`route_groups` JSON 结构和 `_milestone_route_groups()` 每次复制字典的逻辑。

对同一 `AgentStepClosure`，先构造一次 `last_step_by_route[(actor, recipient)]`，所有 ready/blocked milestone 直接 O(1) 选择 scoring step；当前实现为每个 milestone 反向扫描一遍 closure，复杂度从 O(candidate × closure_steps) 降为 O(closure_steps + candidate)。route_source 仅在编译/适配诊断需要时由解析函数返回，不写回每个 constraint 的长期 metadata。

### 12.5 reference anchor 的引用集合不应每个 raw step 重扫 graph

位置：`dynsteer/evaluate/settlement.py::refresh_reference_anchors()`。

`referenced_ids` 完全由静态 constraint metadata 决定，但当前在每个 raw step 遍历所有未完成 milestones 和 constraints 重新生成；`milestone_by_id` 也每次重建。把“哪些 milestone 被动态 reference”预计算到 topology/scoring context 中，运行期只取 `referenced_ids ∩ matched_ids`；milestone 对象直接从 topology 读取。结合第 9.1 节共享 context 后，每个新 snapshot 最多构造一次 context，并只对可能前移的 reference anchor 评分。

### 12.6 minefield 应在 source 改变时评分，而不是每个 raw step 重复命中

位置：`dynsteer/evaluate/step.py`、`dynsteer/evaluate/matching/minefield.py`、`RuntimeEvaluationState`。

当前每个 raw step 都扫描所有 minefields。tool-call minefield 在后续 message step 仍会通过 interval lookup 找到同一个 tool call；state minefield 在没有新 snapshot 时仍会看到同一个 snapshot。命中去重 key 使用 `(minefield_id, boundary_id)`，而 Boundary ID 随 step 改变，因此同一危险证据会被重复追加，影响 `minefield_match_count` 和报告体积。

修改方案：

1. graph enrichment 时按 constraint target 编译 minefield trigger；tool call/result、message、snapshot、metric 只在对应 source 发生变化时进入候选集合。
2. scorer 返回本次实际使用的 `source_step_index` 或 `source_snapshot_id`；命中唯一键改为 `(minefield_id, source_identity)`，而不是当前评估 step 的 Boundary ID。
3. 混合 target minefield 以各 source identity 的稳定 tuple 作为 fingerprint；fingerprint 未变化则跳过整组评分。
4. `max_minefield_score` 和 `fatal_minefield` 可从已去重 matches 推导。若阶段热路径基准证明扫描 matches 有成本，则保留为内部 cache，但由唯一 `_record_minefield_matches()` 更新，禁止调用点分别维护 matches/max/fatal 三份状态。

fatal minefield 的即时终止语义保持不变；该优化只避免输入没有变化时重复评分和重复记账。

### 12.7 `RuntimeEvaluationDecision.next_state` 是伪状态转换层

位置：`dynsteer/model.py::RuntimeEvaluationDecision`、`dynsteer/evaluate/step.py`、`settlement.py`、`evaluator.py`、`telemetry.py`。

所有决策函数都原地修改传入的 `RuntimeEvaluationState`，随后把同一对象作为 `next_state` 返回；没有任何调用创建独立 next state。删除 `next_state`，将类型改为只携带 `checkpoint/stage_result/termination` 的 `RuntimeEvaluationOutcome`。evaluator 和 telemetry 已持有 state，直接显式传入即可，避免让读者误以为这里实现了不可变状态转换。

`EvaluationTerminationState.should_stop` 与 `termination_code is not None` 在所有生产构造点保持一一对应。将 `should_stop` 改为只读 property，由 code 推导；`to_dict()` 继续输出兼容的 `should_stop`。禁止同时可写 bool 和 code，消除二者不一致的非法状态。若确实存在“无 code 但停止”的需求，应在构造边界统一补一个明确 code，而不是恢复第二个布尔事实源。

### 12.8 finish anchor/interval 只计算一次

`finish_settlement()` 已计算 finish anchor、boundary/start/end index，随后 `final.py::_terminal_step_evidence()` 又从 graph metadata 和 matched settlements 完整推导一次同一 anchor/boundary。增加唯一 `finish_interval(task_case, trajectory, matched, topology)`，返回 `StageInterval`；settlement、finish verification 和 terminal evidence 共享该对象。

`_terminal_state_checks()` 为了只评分 terminal state constraints，会复制整个 Milestone 对象。将 scorer 的内部核心改为接收 `milestone_id/pass_threshold/constraints/scoring_step`，正常 milestone 和 terminal subset 复用同一聚合函数，不再创建字段几乎完全相同的临时 Milestone。

### 12.9 运行结果对象和输出 payload 仍保存重复字段

`HarnessRunResult` 中 `benchmark`、`case_id`、`task_case`、`raw_output_dir` 没有生产读取者，只有构造和 `__post_init__()` 校验；outputs 实际只使用 trajectory、raw summary、settlements、report 和 termination。删除四个死字段及校验。

`_build_runtime_result()` 已把 termination 和 stage settlements 写入 `raw_summary`，`harness/outputs.py::_evaluation_payloads()` 又从 `HarnessRunResult` 覆盖写入同一字段。选择后者作为唯一 serializer，runtime raw summary 只保存 runtime 独有 diagnostics/metrics，不提前生成 output schema 字段。

进一步检查 `RuntimeEvaluationState.settlements` 与 `stage_reports`：accepted milestone/finish 在两张列表里重复保存 stage id、milestone id、status、score 和 evidence。优先改为单一 `RuntimeStageRecord(interval, kind, result, matching_detail)` 列表；settlement JSON 和 report stage JSON 都在输出层投影。pending synthetic stage 只设置 `kind="pending"`，自然不会生成 settlement。若一次合并风险过高，至少先落地第 9.5 节的统一写入函数，再以 golden report 验证单列表迁移。

## 13. 第三轮审计后的最终有效行数估算

| 第三轮新增精简项 | 预计净减少有效行 |
| --- | ---: |
| Trajectory 首尾/successor 派生状态删除 | 15～25 |
| AgentStepTracker pending 双映射收敛 | 6～12 |
| graph/topology/node metadata/frontier 静态表示统一 | 12～25 |
| route metadata 与 closure route 扫描收敛 | 10～20 |
| reference anchor 静态引用集合预计算 | 5～10 |
| minefield trigger、source fingerprint 与聚合状态统一 | -5～15 |
| RuntimeEvaluationDecision/termination 单一状态源 | 8～15 |
| finish interval、terminal 临时 Milestone 删除 | 6～12 |
| HarnessRunResult 死字段、payload 重复生成、stage record 收敛 | 18～31 |
| **第三轮新增合计** | **75～145** |

综合三轮审计：

- 当前有效行数基线：**14,424 行**；
- 第一、二轮预计净缩减：**338～560 行**；
- 第三轮新增预计净缩减：**75～145 行**；
- **总预计净缩减：413～705 行**；
- **预计实施后有效行数：13,719～14,011 行**，中位目标约 **13,865 行**。

minefield trigger 与类型化 topology 可能为了降低复杂度新增少量结构代码，所以其最低估算允许出现净增；最终验收仍以行为一致、状态源唯一和算法复杂度下降为优先，不以强行压行数为目标。
