# Constraints API

## 目标

`Constraint` 描述 milestone / minefield 的结构化评分条件。其核心字段分工如下：

- `target`: 决定从哪里取证据。
- `selector`: 决定在证据对象中取哪一小块值。
- `operator`: 决定实际值与 expected/reference 如何比较。

函数式表达：

```text
source = resolve_source(constraint.target, boundary)
actual = select_value(source, constraint.selector)
reference = select_value(reference_source, constraint.selector)  # 仅 delta operator 需要
score = apply_operator(actual, expected_or_reference)
```

## Source

`ConstraintTarget` 的通用来源：

- `state_snapshot`: 当前 boundary 最近的 `StateSnapshot`。
- `metric`: `trajectory.metrics`。
- `tool_call`: 当前 boundary step 的 `tool_call`。
- `tool_result`: 当前 boundary step 的 `tool_result`。
- `step`: 当前 boundary step。

benchmark 可通过专用 scorer 处理 `custom` operator 和私有 source 语义。

## Selector

当前通用 selector 只支持：

- `$`: 返回完整 source。
- `$.a.b.c`: 按字段逐层读取。
- 空值或未命中返回 `None`。

selector 不表达“全阶段曾经发生过某事”。如果要判断一段轨迹中是否曾调用某工具，应由 adapter 选择 milestone boundary，或由 benchmark scorer 专门处理。

## Operator

通用静态比较：

- `equals`: actual 与 expected 完全相等。
- `contains`: 只做浅层包含，支持字符串子串、列表元素、dict key。
- `one_of`: actual 属于 expected 列表。
- `json_subsumes`: actual 是结构化 JSON，且覆盖 expected 子结构。
- `fuzzy_match`: 字符串相似度，适用于浅层文本，不用于结构化状态判定。

通用 delta 比较：

- `added`: reference 不存在，actual 存在。
- `removed`: reference 存在，actual 不存在。
- `updated`: reference 和 actual 都存在且不同。
- `unchanged_since`: actual 与 reference 相等。

benchmark 专用：

- `custom`: 通用 scorer 不解释 expected，由 `evaluator_hint` 和 metadata 指定的专用 scorer 接管。

`ast_match` 已移除，避免 enum 看似支持但 scorer 未实现。

## Delta Reference

delta operator 必须提供明确 reference source。通用 scorer 先从 `ScoringContext.matched_snapshots[reference_milestone_id]` 读取参考 snapshot。

如果找不到对应 matched snapshot：

- 该 constraint 返回 `missing=True`。
- score 为 `0.0`。
- evidence 标记 reference milestone 未命中。

不再把 `reference_milestone_id` 直接当作 `snapshot_id`。

## 示例

状态字段精确匹配：

```json
{
  "target": "state_snapshot",
  "namespace": "default",
  "selector": "$.user.email",
  "operator": "equals",
  "expected": "alice@example.com"
}
```

工具结果状态集合判断：

```json
{
  "target": "tool_result",
  "selector": "$.status",
  "operator": "one_of",
  "expected": ["ok", "success"]
}
```

结构化 JSON 子集：

```json
{
  "target": "state_snapshot",
  "selector": "$.orders.order_123",
  "operator": "json_subsumes",
  "expected": {"status": "confirmed", "paid": true}
}
```

ToolSandbox 专用 custom：

```json
{
  "target": "state_snapshot",
  "selector": "$",
  "operator": "custom",
  "evaluator_hint": "toolsandbox",
  "metadata": {"toolsandbox": {"constraint_index": 3}}
}
```
