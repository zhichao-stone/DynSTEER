# DynSTEER 阶段目标多语言化与 goal.py 精简修改方案

## 1. 背景与结论

当前 `dynsteer/stage/goal.py` 的阶段目标生成存在三类问题：

1. `stage_goal_semantics` 的确定性生成文本硬编码在 Python 函数中，且主要是英文，无法随 benchmark 的 `language` 配置切换。
2. LLM fallback 虽然已有 `dynsteer/prompt/templates/stage/goal_generation.en.md` 与 `goal_generation.zh.md`，但调用链没有把 `TaskCase.metadata["language"]` 传入，且 system prompt 仍硬编码中文。
3. `emit_message` 对 `match_policy == "exact"` 输出 `Exact wording is required.`，这与自然语言模型输出应按语义一致判断的设计不一致。

关于“固定模板能否适配不同 benchmark”的判断：

- 公共 `stage_goal` 模块不应包含 benchmark 私有规则，也不应直接读取 `metadata["toolsandbox"]` 等私有字段。
- 固定模板可以适配不同 benchmark 的前提是：各 benchmark adapter 先把私有约束归一化到公共 `Constraint.stage_goal_semantics`，例如 `set_state`、`preserve_state`、`emit_message`、`tool_call`。
- 如果某个 benchmark 无法提供足够通用的 `stage_goal_semantics`，`mode="auto"` 应回退到 LLM，并按 benchmark 语言选择对应 prompt。

本方案目标不是增加 benchmark 专用 hook，而是把确定性模板做成公共、多语言、可维护的模板资源，同时精简 `goal.py` 中重复和硬编码的逻辑。经 `rg "generate_stage_goals_with_llm"` 复核，项目内除 `goal.py` 自身和 `stage/__init__.py` re-export 外没有调用该函数，因此本方案进一步要求删除 `generate_stage_goals_with_llm()`，把 LLM fallback 直接并入 `generate_stage_goals()`。

## 2. 修改范围

需要修改：

| 文件 | 修改目的 |
| --- | --- |
| `dynsteer/stage/goal.py` | 接入 `language_from_task`；确定性模板按语言渲染；去除 `emit_message exact`；把 LLM fallback 并入 `generate_stage_goals`；删除未被调用的 `generate_stage_goals_with_llm`；精简重复校验和解析逻辑。 |
| `dynsteer/stage/__init__.py` | 删除 `generate_stage_goals_with_llm` 的 re-export；新增 `default_finish_stage_goal` 导出。 |
| `dynsteer/prompt/stage.py` | `build_stage_goal_generation_prompt` 默认语言改为英文；使用 `load_prompt_template`；新增 `build_stage_goal_system_prompt`。 |
| `dynsteer/prompt/__init__.py` | 导出新增的 `build_stage_goal_system_prompt`。 |
| `dynsteer/prompt/templates/stage/goal_system.en.md` | 新增英文 LLM stage goal system prompt。 |
| `dynsteer/prompt/templates/stage/goal_system.zh.md` | 新增中文 LLM stage goal system prompt。 |
| `dynsteer/prompt/templates/stage_goal/*.md` | 新增确定性 stage goal 片段模板，按语义 kind 与语言管理。 |
| `docs/apis/stage_goal.md` | 补充语言选择规则与 `emit_message` 语义一致规则。 |
| `tests/stage/test_goal_generation.py` | 新增阶段目标生成单元测试。 |
| `tests/prompt/test_stage_goal_prompt.py` | 新增 LLM fallback prompt 语言测试。 |

不需要修改：

- benchmark adapter 的私有 metadata 结构。
- `StageGoalSemanticKind` 枚举。
- `stage_evaluation_specs` 生成逻辑。
- StandardJudge / ExpensiveJudge 的评估 prompt。
- 为 LLM fallback 保留单独公共函数；后续统一使用 `generate_stage_goals(..., mode="llm")`。

## 3. 目标代码结构

新增确定性模板目录：

```text
dynsteer/prompt/templates/stage_goal/
  finish.en.md
  finish.zh.md
  objective.en.md
  objective.zh.md
  set_state.en.md
  set_state.zh.md
  preserve_state.en.md
  preserve_state.zh.md
  emit_message.en.md
  emit_message.zh.md
  tool_call.en.md
  tool_call.zh.md
```

继续复用现有 prompt 模板加载机制：

- `load_prompt_template(domain, name)`
- `PromptTemplate.render(language=...)`
- `TaskLanguage`
- `language_from_task(task_case)`

不新增独立 stage goal 模板加载器，避免和 `dynsteer/prompt/template.py` 形成第二套模板管理逻辑。

## 4. `dynsteer/stage/goal.py` 详细修改方案

### 4.1 调整 import

当前位置：文件顶部。

目标修改：

```python
from collections.abc import Callable
import json

from dynsteer.graph import FINISH_NODE_ID
from dynsteer.language import TaskLanguage, language_from_task
from dynsteer.llm.base import BaseLLM
from dynsteer.model import Constraint, LLMMessage, MilestoneGraph, StageGoalSemanticKind, StageInterval, TaskCase
from dynsteer.prompt.stage import build_stage_goal_generation_prompt, build_stage_goal_system_prompt
from dynsteer.prompt.template import load_prompt_template
from dynsteer.utils import enum_value
```

改进点：

- 删除 `generate_stage_goals_with_llm()` 后，不再需要函数内项目模块 import。
- 语言来源统一由 `language_from_task()` 读取。
- 确定性模板使用现有 `load_prompt_template()`。

### 4.2 保留公共常量，但新增语言化 finish 函数

当前位置：`DEFAULT_FINISH_STAGE_GOAL` 附近。

目标修改：

```python
DEFAULT_FINISH_STAGE_GOAL = "完成收尾检查：确认已达成的阶段目标没有被后续证据推翻。"


def default_finish_stage_goal(language: TaskLanguage = TaskLanguage.ENGLISH) -> str:
    """按语言返回默认收尾阶段目标。"""
    return _render_goal_template("finish", language)
```

说明：

- 保留 `DEFAULT_FINISH_STAGE_GOAL`，避免已有 `from dynsteer.stage import DEFAULT_FINISH_STAGE_GOAL` 失效。
- 后续运行时不再直接使用该常量，而是通过 `default_finish_stage_goal(language_from_task(task_case))` 获取对应语言文本。
- 如需更彻底精简，可在后续破坏性版本移除常量，但本轮不做。

同时需要在 `dynsteer/stage/__init__.py` 中导出 `default_finish_stage_goal`：

```python
from dynsteer.stage.goal import (
    DEFAULT_FINISH_STAGE_GOAL,
    default_finish_stage_goal,
    generate_stage_goals,
    required_stage_goal_keys,
    resolve_stage_goal,
    stage_goal_key,
    validate_stage_goals,
)
```

并在 `__all__` 中加入：

```python
"default_finish_stage_goal",
```

同时从 `__all__` 删除：

```python
"generate_stage_goals_with_llm",
```

### 4.3 精简 `generate_stage_goals`

当前位置：`generate_stage_goals(...)`。

目标代码：

```python
def generate_stage_goals(
    task_case: TaskCase,
    mode: str = "auto",
    llm_provider: Callable[[], BaseLLM | None] | None = None,
) -> dict[str, str]:
    """集中生成 TaskCase 的 stage_goals。"""
    if task_case is None:
        raise ValueError("task_case 不能为空")
    graph = task_case.milestone_graph
    if graph is None:
        raise ValueError("TaskCase 缺少 milestone_graph")

    language = language_from_task(task_case)
    normalized_mode = str(mode or "auto").strip().lower()

    if normalized_mode == "stored":
        validate_stage_goals(graph, task_case.stage_goals)
        return dict(task_case.stage_goals)
    if normalized_mode not in {"auto", "semantic", "llm"}:
        raise ValueError(f"未知 stage_goal_generation 模式: {mode}")

    if normalized_mode in {"auto", "semantic"}:
        semantic_goals = _generate_semantic_stage_goals(task_case, graph, language)
        if semantic_goals is not None:
            return semantic_goals
        if normalized_mode == "semantic":
            raise ValueError("stage_goal_generation=semantic 需要所有约束提供 stage_goal_semantics")

    if llm_provider is None:
        raise ValueError("stage_goal_generation=llm 需要配置 LLM provider")
    llm = llm_provider()
    if llm is None:
        raise ValueError("stage_goal_generation=llm 需要配置 DYNSTEER_JUDGE_PROVIDER")
    required_keys = required_stage_goal_keys(graph)

    raw_text = llm.chat(
        [
            LLMMessage(role="system", content=build_stage_goal_system_prompt(language)),
            LLMMessage(
                role="user",
                content=build_stage_goal_generation_prompt(
                    task_case,
                    required_keys=required_keys,
                    language=language,
                    graph=graph,
                ),
            ),
        ],
        temperature=0,
    )
    stage_goals = _parse_stage_goal_from_resp(raw_text)
    validate_stage_goals(graph, stage_goals)
    return stage_goals
```

改进点：

- 只在入口处读取一次 graph 与 language。
- `stored`、`semantic`、`llm` 都使用同一个 graph 对象，减少重复读取和重复校验。
- `semantic` 生成函数显式接收 `graph` 与 `language`，避免内部重复推断。
- LLM system prompt 与 user prompt 都按同一 language 渲染。
- 保持 `temperature=0`。
- 删除 `generate_stage_goals_with_llm()` 后，不需要额外的 `language: TaskLanguage | None` 参数，也不需要第二次 `language_from_task(task_case)` 或 `_task_graph(task_case)`。

### 4.4 删除 `generate_stage_goals_with_llm` 后可简化的代码

需要删除：

```python
def generate_stage_goals_with_llm(...):
    ...
```

并入后的简化点：

- `dynsteer/stage/goal.py` 少一个对外暴露函数，阶段目标生成入口真正收敛为 `generate_stage_goals()`。
- `dynsteer/stage/__init__.py` 少一个 import 与一个 `__all__` 项，避免把内部 fallback 流程包装成公共 API。
- `generate_stage_goals()` 内部已持有 `graph`、`language`、`llm`，LLM fallback 可以直接复用这些局部变量，不再需要为了跨函数传参而设计 `language: TaskLanguage | None`。
- `_task_graph()` 在删除 `generate_stage_goals_with_llm()` 后只剩单调用点，应一并删除，把 `task_case is None` 与 `task_case.milestone_graph is None` 校验直接放在 `generate_stage_goals()` 开头；这比保留一个单调用 helper 更符合“默认不设计中转函数”的约束。
- 测试只需要覆盖 `generate_stage_goals(..., mode="llm")`，不再需要单独测试 `generate_stage_goals_with_llm()`。

### 4.5 精简 `resolve_stage_goal`

当前位置：`resolve_stage_goal(...)`。

目标代码：

```python
def resolve_stage_goal(interval: StageInterval, task_case: TaskCase) -> str:
    """从 TaskCase.stage_goals 读取当前阶段目标。"""
    if interval is None:
        raise ValueError("interval 不能为空")
    language = language_from_task(task_case)
    milestone_id = interval.milestone_id
    if milestone_id is None:
        return default_finish_stage_goal(language)

    anchor_id = interval.stage_anchor_milestone_id
    if isinstance(anchor_id, str) and anchor_id.strip():
        key = stage_goal_key(anchor_id, milestone_id)
        stage_goal = task_case.stage_goals.get(key)
        if isinstance(stage_goal, str) and stage_goal.strip():
            return stage_goal.strip()
        if milestone_id != FINISH_NODE_ID:
            raise ValueError(f"TaskCase 缺少预生成 stage_goal: {key}")

    if milestone_id == FINISH_NODE_ID:
        return default_finish_stage_goal(language)
    raise ValueError(f"milestone 阶段缺少 stage_anchor_milestone_id: {milestone_id}")
```

改进点：

- 合并普通 milestone 与 finish milestone 的 lookup 逻辑。
- finish 节点优先读取已生成的 stage goal；缺失时按语言返回默认 finish 文本。
- 避免同一段 `stage_goal_key + get + strip` 逻辑重复出现。

### 4.6 修改 `_generate_semantic_stage_goals`

当前位置：`_generate_semantic_stage_goals(...)`。

目标代码：

```python
def _generate_semantic_stage_goals(
    task_case: TaskCase,
    graph: MilestoneGraph,
    language: TaskLanguage,
) -> dict[str, str] | None:
    semantic_goals: dict[str, str] = {}
    for milestone in graph.nodes:
        anchor_id = milestone.stage_anchor_predecessor_id
        if not isinstance(anchor_id, str) or not anchor_id:
            raise ValueError(f"milestone 缺少 stage_anchor_predecessor_id: {milestone.milestone_id}")
        if not milestone.constraints:
            return None

        pieces = [_constraint_goal_text_from_semantics(constraint, language) for constraint in milestone.constraints]
        if any(piece is None for piece in pieces):
            return None

        milestone_text = _semantic_text(milestone.description or milestone.name, milestone.milestone_id)
        objective = _render_goal_template(
            "objective",
            language,
            milestone_id=milestone.milestone_id,
            milestone_text=milestone_text,
        )
        semantic_goals[stage_goal_key(anchor_id, milestone.milestone_id)] = " ".join([objective, *pieces])

    validate_stage_goals(graph, semantic_goals)
    return semantic_goals
```

落地时建议把 `pieces` 的类型写得更明确，避免 mypy/IDE 对 `None` 推断不友好：

```python
pieces: list[str] = []
for constraint in milestone.constraints:
    piece = _constraint_goal_text_from_semantics(constraint, language)
    if piece is None:
        return None
    pieces.append(piece)
```

改进点：

- 目标句和 constraint 片段都按语言渲染。
- 不再硬编码 `Complete milestone ...`。

### 4.7 修改 `_constraint_goal_text_from_semantics`

当前位置：`_constraint_goal_text_from_semantics(...)`。

目标签名：

```python
def _constraint_goal_text_from_semantics(constraint: Constraint, language: TaskLanguage) -> str | None:
```

目标代码：

```python
def _constraint_goal_text_from_semantics(constraint: Constraint, language: TaskLanguage) -> str | None:
    """根据单个约束的公共语义 IR 生成 stage_goal 文本片段。"""
    semantics = constraint.stage_goal_semantics
    if semantics is None or not isinstance(semantics, dict):
        return None
    try:
        kind = enum_value(StageGoalSemanticKind, semantics.get("kind"), "stage_goal_semantics.kind")
    except ValueError:
        return None

    if kind == StageGoalSemanticKind.SET_STATE:
        return _set_state_goal(semantics, language)
    if kind == StageGoalSemanticKind.PRESERVE_STATE:
        return _preserve_state_goal(semantics, language)
    if kind == StageGoalSemanticKind.EMIT_MESSAGE:
        return _emit_message_goal(semantics, language)
    if kind == StageGoalSemanticKind.TOOL_CALL:
        return _tool_call_goal(semantics, language)
    return None
```

新增 helper：

```python
def _set_state_goal(semantics: dict[str, object], language: TaskLanguage) -> str:
    namespace = _semantic_text(semantics.get("namespace"), "state")
    expected = json.dumps(semantics.get("expected"), ensure_ascii=False, sort_keys=True)
    return _render_goal_template("set_state", language, namespace=namespace, expected=expected)


def _preserve_state_goal(semantics: dict[str, object], language: TaskLanguage) -> str:
    namespace = _semantic_text(semantics.get("namespace"), "state")
    return _render_goal_template(
        "preserve_state",
        language,
        namespace=namespace,
        reference_text=_reference_text(semantics.get("reference"), language),
    )


def _emit_message_goal(semantics: dict[str, object], language: TaskLanguage) -> str:
    sender = _semantic_text(semantics.get("sender"), "sender")
    recipient = _semantic_text(semantics.get("recipient"), "recipient")
    content = _semantic_text(semantics.get("content"), "")
    content_text = json.dumps(content, ensure_ascii=False) if content else _required_content_text(language)
    return _render_goal_template(
        "emit_message",
        language,
        sender=sender,
        recipient=recipient,
        content_text=content_text,
    )


def _tool_call_goal(semantics: dict[str, object], language: TaskLanguage) -> str:
    tool_name = _semantic_text(semantics.get("tool_name"), "the required tool")
    arguments = semantics.get("arguments")
    arguments_clause = ""
    if isinstance(arguments, dict) and arguments:
        expected_arguments = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        arguments_clause = _arguments_clause(expected_arguments, language)
    return _render_goal_template(
        "tool_call",
        language,
        tool_name=tool_name,
        arguments_clause=arguments_clause,
    )
```

关键语义变更：

- `_emit_message_goal()` 不读取 `match_policy`。
- 即使输入里保留历史 `match_policy="exact"`，stage goal 文本也只表达“语义一致即可，不要求字面完全一致”。
- `match_policy` 是否用于 scorer 或专用语义复判，仍由 `dynsteer/evaluate/semantic.py` 等评估链路决定；本轮只改变 stage goal 文本生成。

### 4.8 新增模板渲染 helper

当前位置：内部 helper 区域，建议放在 `_semantic_text` 前。

目标代码：

```python
def _render_goal_template(name: str, language: TaskLanguage, **kwargs: object) -> str:
    return load_prompt_template("stage_goal", name).render(language=language, **kwargs)
```

可选优化：

如果后续实测大量 case 反复读取模板导致加载开销明显，可以再加 `functools.lru_cache`：

```python
from functools import lru_cache


@lru_cache(maxsize=None)
def _stage_goal_template(name: str):
    return load_prompt_template("stage_goal", name)


def _render_goal_template(name: str, language: TaskLanguage, **kwargs: object) -> str:
    return _stage_goal_template(name).render(language=language, **kwargs)
```

本轮建议先采用无缓存版本，保持实现简单；如果在批量适配 benchmark 时观察到模板读取成为瓶颈，再引入缓存。

### 4.9 新增语言相关小 helper

目标代码：

```python
def _reference_text(reference: object, language: TaskLanguage) -> str:
    if isinstance(reference, dict) and reference.get("type") == "milestone_index":
        if language == TaskLanguage.CHINESE:
            return f"参考里程碑索引 {reference.get('value')}"
        return f"reference milestone index {reference.get('value')}"
    if isinstance(reference, dict) and reference.get("type") == "initial_state":
        return "初始状态" if language == TaskLanguage.CHINESE else "initial state"
    return "被引用状态" if language == TaskLanguage.CHINESE else "the referenced state"


def _required_content_text(language: TaskLanguage) -> str:
    return "必需内容" if language == TaskLanguage.CHINESE else "the required content"


def _arguments_clause(expected_arguments: str, language: TaskLanguage) -> str:
    if language == TaskLanguage.CHINESE:
        return f"，参数需与 {expected_arguments} 兼容"
    return f" with arguments compatible with {expected_arguments}"
```

说明：

- 这里保留少量语言分支，是因为这些片段来自动态结构字段，无法完全用模板文件表达。
- 这些 helper 均为内部函数，集中在文件后部，避免主流程被细节污染。

### 4.10 修改 `_parse_stage_goal_from_resp`

当前位置：`_parse_stage_goal_from_resp(...)`。

目标代码：

```python
def _parse_stage_goal_from_resp(raw: str) -> dict[str, str]:
    """从 LLM 返回的 JSON 字符串中解析 stage_goals 字典。"""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("LLM stage_goal 返回不能为空")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM stage_goal 返回必须是 JSON 对象") from exc
    if not isinstance(data, dict):
        raise ValueError("LLM stage_goal 返回必须是 JSON 对象")
    payload = data.get("stage_goals")
    if not isinstance(payload, dict):
        raise ValueError("LLM stage_goal.stage_goals 必须是 JSON 对象")

    stage_goals: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("LLM stage_goal key 必须是非空字符串")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"LLM stage_goal value 必须是非空字符串: {key}")
        stage_goals[key] = value.strip()
    return stage_goals
```

改进点：

- 不再静默过滤非法 key/value。
- 错误发生点更靠近 LLM 响应解析，便于定位模板或模型输出问题。

### 4.11 不再保留 `_task_graph`

原方案曾计划新增 `_task_graph()`，但在删除 `generate_stage_goals_with_llm()` 后，该 helper 只会服务 `generate_stage_goals()` 一个调用点，不再有共享价值。

因此目标是把 graph 校验直接写在 `generate_stage_goals()` 开头，替换原计划里的 `graph = _task_graph(task_case)`：

```python
if task_case is None:
    raise ValueError("task_case 不能为空")
graph = task_case.milestone_graph
if graph is None:
    raise ValueError("TaskCase 缺少 milestone_graph")
```

说明：

- 这会减少一个内部 helper。
- `generate_stage_goals()` 作为唯一生成入口，承担入口参数校验是合理的。

## 5. `dynsteer/prompt/stage.py` 详细修改方案

### 5.1 调整 import

目标修改：

```python
from dynsteer.prompt.template import load_prompt_template
```

删除：

```python
from dynsteer.prompt.template import PromptTemplate, load_prompt_text
```

### 5.2 新增 `build_stage_goal_system_prompt`

位置：放在 `build_stage_goal_generation_prompt` 前。

目标代码：

```python
def build_stage_goal_system_prompt(language: TaskLanguage = TaskLanguage.ENGLISH) -> str:
    """构造阶段目标生成器 system prompt。"""
    return load_prompt_template("stage", "goal_system").render(language=language)
```

### 5.3 修改 `build_stage_goal_generation_prompt`

目标代码：

```python
def build_stage_goal_generation_prompt(
    task_case: TaskCase,
    required_keys: list[str],
    language: TaskLanguage = TaskLanguage.ENGLISH,
    *,
    graph: MilestoneGraph | None = None,
) -> str:
    """构造一次性生成全部 stage_goal 的 LLM prompt。"""
    if task_case is None or required_keys is None:
        raise ValueError("task_case 和 required_keys 不能为空")
    effective_graph = graph or task_case.milestone_graph
    if effective_graph is None:
        raise ValueError("TaskCase 缺少 milestone_graph")
    payload = {
        "task_description": task_case.task_description,
        "milestone_graph": _graph_prompt_json(effective_graph),
        "required_stage_goal_keys": list(required_keys),
        "output_schema": {"stage_goals": {"milestone_id_1->milestone_id_2": "当前阶段自然语言目标"}},
    }
    return load_prompt_template("stage", "goal_generation").render(
        language=language,
        context_json=json.dumps(payload, ensure_ascii=False, indent=2),
    )
```

改进点：

- 默认语言与 `language_from_task()` 保持一致，都是英文。
- 使用 `load_prompt_template()` 一次性管理同名多语言模板。
- `goal_generation.zh.md` 与 `goal_generation.en.md` 继续复用，无需改动内容。

## 6. 新增模板内容

### 6.1 `dynsteer/prompt/templates/stage/goal_system.en.md`

```md
You are a DynSTEER stage goal generator. Return only a JSON object.
```

### 6.2 `dynsteer/prompt/templates/stage/goal_system.zh.md`

```md
你是 DynSTEER 阶段目标生成器，只返回 JSON 对象。
```

### 6.3 `dynsteer/prompt/templates/stage_goal/finish.en.md`

```md
Complete the final review: confirm that no later evidence has overturned the achieved stage goals.
```

### 6.4 `dynsteer/prompt/templates/stage_goal/finish.zh.md`

```md
完成收尾检查：确认已达成的阶段目标没有被后续证据推翻。
```

### 6.5 `dynsteer/prompt/templates/stage_goal/objective.en.md`

```md
Complete milestone {milestone_id}: {milestone_text}.
```

### 6.6 `dynsteer/prompt/templates/stage_goal/objective.zh.md`

```md
完成里程碑 {milestone_id}：{milestone_text}。
```

### 6.7 `dynsteer/prompt/templates/stage_goal/set_state.en.md`

```md
Make or verify {namespace} state satisfies {expected}. Use structured scorer evidence for this state requirement; no separate user-facing restatement is required unless another message requirement says so.
```

### 6.8 `dynsteer/prompt/templates/stage_goal/set_state.zh.md`

```md
设置或核验 {namespace} 状态满足 {expected}。该状态要求以结构化 scorer 证据为准；除非另有消息要求，否则不需要单独向用户复述。
```

### 6.9 `dynsteer/prompt/templates/stage_goal/preserve_state.en.md`

```md
Preserve {namespace} state relative to {reference_text}. This means the relevant state should remain unchanged or equivalent, not that the namespace must be empty.
```

### 6.10 `dynsteer/prompt/templates/stage_goal/preserve_state.zh.md`

```md
保持 {namespace} 状态相对于{reference_text}不变或语义等价；这不表示该 namespace 必须为空。
```

### 6.11 `dynsteer/prompt/templates/stage_goal/emit_message.en.md`

```md
Emit a message from {sender} to {recipient} conveying {content_text}. Semantic equivalence is sufficient; exact wording is not required.
```

### 6.12 `dynsteer/prompt/templates/stage_goal/emit_message.zh.md`

```md
由 {sender} 向 {recipient} 发送消息，表达 {content_text}。语义一致即可，不要求字面完全一致。
```

### 6.13 `dynsteer/prompt/templates/stage_goal/tool_call.en.md`

```md
Call tool {tool_name}{arguments_clause}.
```

### 6.14 `dynsteer/prompt/templates/stage_goal/tool_call.zh.md`

```md
调用工具 {tool_name}{arguments_clause}。
```

## 7. `dynsteer/prompt/__init__.py` 修改方案

目标代码：

```python
from dynsteer.prompt.judge import build_judge_prompt, build_judge_system_prompt
from dynsteer.prompt.stage import build_stage_goal_generation_prompt, build_stage_goal_system_prompt
from dynsteer.prompt.template import PromptTemplate, load_prompt_template, load_prompt_text


__all__ = [
    "PromptTemplate",
    "build_judge_prompt",
    "build_judge_system_prompt",
    "build_stage_goal_generation_prompt",
    "build_stage_goal_system_prompt",
    "load_prompt_template",
    "load_prompt_text",
]
```

## 8. `docs/apis/stage_goal.md` 修改方案

在“支持模式”后补充：

```md
语言选择规则：

- `TaskCase.metadata["language"]` 是 stage_goal 文本与 LLM fallback prompt 的统一语言来源。
- 缺省语言为 `en`；`benchmark.json language` 会经 harness/loader 写入 `TaskCase.metadata["language"]`。
- 确定性 `stage_goal_semantics` 模板与 LLM fallback prompt 均应通过同一语言配置渲染。
```

在 `emit_message` 说明附近补充：

```md
`emit_message` 的 stage goal 只要求消息内容与目标语义一致，不要求字面完全一致。即使历史数据中出现 `match_policy="exact"`，stage goal 文本也不应要求 exact wording；精确匹配或专用语义复判属于 scorer/matching 层职责，不属于 stage goal 文本职责。
```

同时确认 API 文档只声明 `generate_stage_goals(task_case, mode="auto", llm_provider=None)` 是唯一生成入口，不新增也不保留 `generate_stage_goals_with_llm` 入口。

## 9. 测试方案

### 9.1 新增 `tests/stage/test_goal_generation.py`

覆盖点：

1. `metadata={"language": "zh"}` 时，`generate_stage_goals(..., mode="semantic")` 输出中文模板。
2. `metadata={"language": "en"}` 或缺省 metadata 时，输出英文模板。
3. `emit_message` 即使传入 `match_policy="exact"`，输出也包含“Semantic equivalence is sufficient”或“语义一致即可”，且不包含 `Exact wording is required`。
4. `preserve_state` 能按语言渲染 `initial state` / `初始状态`。
5. `resolve_stage_goal()` 在 finish stage 缺失显式 stage goal 时按 `TaskCase.metadata["language"]` 返回默认 finish 文本。
6. `_parse_stage_goal_from_resp()` 遇到空 value 时抛出异常，而不是静默过滤。
7. `generate_stage_goals(..., mode="llm")` 使用 fake LLM provider 完成 LLM fallback，断言 system prompt 和 user prompt 均按 `TaskCase.metadata["language"]` 渲染，且返回值通过 `validate_stage_goals()`。

示例构造：

```python
from dynsteer.model import Constraint, ConstraintTarget, Milestone, MilestoneGraph, Operator, StageGoalSemanticKind, TaskCase
from dynsteer.stage.goal import generate_stage_goals


def _case(language: str, semantics: dict[str, object]) -> TaskCase:
    constraint = Constraint(
        constraint_id="c0",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$",
        operator=Operator.CUSTOM,
        stage_goal_semantics=semantics,
    )
    graph = MilestoneGraph(
        nodes=[
            Milestone(
                milestone_id="m0",
                name="Send answer",
                description="Send the requested answer",
                constraints=[constraint],
                stage_anchor_predecessor_id="__start__",
            )
        ],
        edges=[],
    )
    return TaskCase(
        task_id="t0",
        case_id="case0",
        task_description="Answer the user.",
        milestone_graph=graph,
        metadata={"language": language},
    )
```

### 9.2 新增 `tests/prompt/test_stage_goal_prompt.py`

覆盖点：

1. `build_stage_goal_system_prompt(TaskLanguage.ENGLISH)` 返回英文 system prompt。
2. `build_stage_goal_system_prompt(TaskLanguage.CHINESE)` 返回中文 system prompt。
3. `build_stage_goal_generation_prompt(..., language=TaskLanguage.ENGLISH)` 使用英文模板。
4. `build_stage_goal_generation_prompt(..., language=TaskLanguage.CHINESE)` 使用中文模板。

### 9.3 推荐执行命令

```powershell
uv run pytest tests/stage/test_goal_generation.py tests/prompt/test_stage_goal_prompt.py
```

如果修改影响导入路径，再补跑：

```powershell
uv run pytest tests
```

## 10. 验收标准

1. `generate_stage_goals()` 不再生成语言失配的固定英文文本。
2. LLM fallback 的 system prompt 和 user prompt 都随 `TaskCase.metadata["language"]` 切换。
3. `emit_message` 阶段目标文本不再出现 exact wording 要求。
4. `goal.py` 中没有函数内项目模块 import。
5. `goal.py` 主流程保持靠前，内部 helper 集中靠后。
6. 非法 LLM 响应不会被静默过滤。
7. `dynsteer/stage/goal.py` 不再定义 `generate_stage_goals_with_llm()`，`dynsteer/stage/__init__.py` 不再导出该函数。
8. `goal.py` 不保留只有 `generate_stage_goals()` 调用的 `_task_graph()` helper。
9. 新增 pytest 全部通过。

## 11. 实施顺序

1. 新增 `stage/goal_system` prompt 模板。
2. 新增 `stage_goal` 确定性模板文件。
3. 修改 `dynsteer/prompt/stage.py`。
4. 修改 `dynsteer/prompt/__init__.py`。
5. 修改 `dynsteer/stage/goal.py`，将 LLM fallback 并入 `generate_stage_goals()`，删除 `generate_stage_goals_with_llm()` 与 `_task_graph()`。
6. 修改 `dynsteer/stage/__init__.py`，删除 `generate_stage_goals_with_llm` 的 import 与 `__all__` 项。
7. 更新 `docs/apis/stage_goal.md`。
8. 新增测试。
9. 执行定向 pytest。
10. 检查未使用 import、重复 helper 与缓存目录。

## 附录A. 项目中没有把握实现的模块部分

1. 未来非 ToolSandbox benchmark 的 `stage_goal_semantics` 覆盖质量  
   没有完全把握的原因：当前仓库里能看到的主要语义映射来自 ToolSandbox。确定性模板是否能覆盖未来 benchmark，取决于 adapter 是否能把私有约束准确归一化为公共 `stage_goal_semantics`。本方案可以保证公共模板按语言和公共 kind 正确渲染，但不能替 adapter 自动理解未知 benchmark 的私有字段。

2. 各语言模板的领域表达是否最终满足论文或实验写作口径  
   没有完全把握的原因：中文和英文模板可以先做到语义一致、工程可用，但 stage goal 的措辞可能还需要结合实际实验样例、人类标注偏好和论文术语进行迭代。

3. 是否需要为 `stage_goal` 模板加载增加缓存  
   没有完全把握的原因：从静态代码看，模板读取开销通常不会成为瓶颈；但如果一次性适配大量 case，反复读取小模板文件可能有可测开销。本方案先保持简单，后续以性能数据决定是否加 `lru_cache`。
