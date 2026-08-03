# 2026-08-03 ToolSandbox Replay Source 完整性检查序列化结构修复方案

## 1. 问题与目标

### 1.1 问题

`dynsteer.experiment.runner._validate_toolsandbox_replay_source()` 直接读取 DEFAULT `trajectory.json`，当前按以下嵌套结构提取原生消息索引：

```python
snapshot["raw"]["sandbox_message_index"]
```

但 `dynsteer.harness.outputs.snapshot_to_json()` 会把 `StateSnapshot.raw` 展平到 snapshot 顶层，真实落盘结构为：

```json
{
  "snapshot_id": "toolsandbox:16",
  "after_step_id": "s16",
  "after_step_index": 16,
  "namespaces": {},
  "sandbox_message_index": 16
}
```

因此，只要 ToolSandbox native `milestone_mapping` 中存在整数 `snapshot_index`，当前检查构造的 `snapshot_indexes` 就可能为空，并把实际存在的 snapshot 误判为缺失。

### 1.2 触发范围

该检查位于 `run_default_case()` 中，在统一实验执行以下方法时可能触发：

- `default`；
- `dynsteer_replay`；
- `dynsteer_replay_static`；
- `dynsteer_replay_static_weighting`；
- `dynsteer_replay_static_routing`。

即使 `force_eval=False` 且复用已有 DEFAULT 产物，`run_default_case()` 读取缓存产物后仍会执行完整性检查。以下情况直接跳过：

- benchmark 不是 `toolsandbox`；
- `default_result.raw.milestone_mapping` 缺失、不是对象或为空。

### 1.3 修复目标

1. 完整性检查通过项目统一 trajectory loader 读取 snapshot raw metadata，不直接依赖 JSON 展平细节。
2. native mapping 指向真实存在的 `sandbox_message_index` 时通过。
3. mapping 指向缺失 index 时继续 fail fast，错误保留 benchmark、model、case、milestone 和 snapshot index。
4. 不修改 native mapping，不使用最近 snapshot 替代缺失 snapshot。
5. 不改变非 ToolSandbox、空 mapping、DEFAULT/replay 轨迹复用和其他实验流程。

## 2. 修改范围

修改文件：

- `dynsteer/experiment/runner.py`
- `tests/test_toolsandbox_replay_source_completeness.py`
- `docs/apis/harness.md`

不修改：

- ToolSandbox adapter 与 adapted cases；
- trajectory JSON schema 与 `snapshot_to_json()`；
- native milestone mapping；
- evaluator、实验指标和已有实验产物；
- 其他 benchmark 的 runner 行为。

## 3. 代码修改方案

### 3.1 使用统一 loader 恢复 `StateSnapshot.raw`

修改 `dynsteer/experiment/runner.py::_validate_toolsandbox_replay_source()`。

保留当前前置跳过逻辑。读取 `output.trajectory_path` 后，不再直接遍历 JSON snapshot 字典，而是复用 runner 已导入的 `load_trajectory()`：

```python
trajectory_data = read_json_file(
    output.trajectory_path,
    str(output.trajectory_path),
    dict,
)
trajectory = load_trajectory(trajectory_data)
snapshot_indexes = {
    snapshot.raw["sandbox_message_index"]
    for snapshot in trajectory.snapshots
    if isinstance(snapshot.raw.get("sandbox_message_index"), int)
}
```

采用该实现的原因：

- `load_trajectory()` 通过 `_load_snapshot()` 把 snapshot 顶层未知字段统一恢复到 `StateSnapshot.raw`；
- 完整性检查面向项目内存模型，不重复实现一套落盘 schema 解析；
- `runner.py` 已使用并导入 `load_trajectory()`，不增加依赖或中转函数；
- 若未来 raw 字段的序列化细节调整，只需维护公共 serializer/loader 边界。

### 3.2 保持缺失 mapping 的错误语义

保留当前逐 milestone 检查：

```python
for milestone_id, item in mapping.items():
    snapshot_index = item.get("snapshot_index") if isinstance(item, dict) else None
    if isinstance(snapshot_index, int) and snapshot_index not in snapshot_indexes:
        raise ValueError(...)
```

错误必须继续包含：

- `benchmark`；
- `model`；
- `case`；
- `milestone`；
- `snapshot_index`。

本次不扩展为兼容多种旧 JSON 嵌套格式，也不增加“最近 snapshot”回退。

### 3.3 不新增业务中转函数

修复直接落在 `_validate_toolsandbox_replay_source()` 内。`load_trajectory()` 已是公共解析入口，不再新增只负责转调 loader 的辅助函数。

## 4. 测试方案

新建或更新 `tests/test_toolsandbox_replay_source_completeness.py`。

### 4.1 使用真实 serializer 构造 fixture

测试不得手写错误的嵌套 `raw` JSON。通过以下现有接口构造落盘数据：

1. 创建带 `raw={"sandbox_message_index": index}` 的 `StateSnapshot`；
2. 创建 `Trajectory`；
3. 调用 `dynsteer.harness.outputs.trajectory_to_json()` 生成真实 JSON 对象；
4. 写入临时 `trajectory.json`；
5. 调用 `_validate_toolsandbox_replay_source()`。

这样测试同时约束 serializer、loader 和 runner 检查之间的真实契约。

### 4.2 测试用例

1. **存在的 mapping index 通过**
   - snapshots 包含 index `3`、`4`；
   - mapping 指向 `4`；
   - 不抛异常。

2. **缺失的 mapping index 失败**
   - snapshots 只包含 index `3`；
   - mapping 指向 `4`；
   - 抛出 `ValueError`；
   - 错误包含 benchmark、model、case、milestone 和 `snapshot_index=4`。

3. **空 mapping 跳过**
   - ToolSandbox default result 的 milestone mapping 为空；
   - trajectory 即使没有 snapshots 也不报错。

4. **非 ToolSandbox 跳过**
   - benchmark 使用其他名称；
   - 不读取或校验 ToolSandbox mapping。

5. **多个 milestone 全量检查**
   - 两个 milestone 分别映射不同 index；
   - 任一 index 缺失都明确指出对应 milestone，证明不是只校验首项。

6. **真实 JSON 结构断言**
   - fixture 生成后断言 `sandbox_message_index` 位于 snapshot 顶层；
   - 断言落盘 snapshot 不包含人为构造的嵌套 `raw` 字段。

### 4.3 覆盖率

`_validate_toolsandbox_replay_source()` 行覆盖率不低于 80%，覆盖正常通过、缺失失败、空 mapping 跳过和非 ToolSandbox 跳过分支。

## 5. API 文档修改

在 `docs/apis/harness.md` 的 ToolSandbox replay source 完整性部分补充：

- `trajectory.json` 中 snapshot raw metadata 采用顶层展开格式；
- runner 通过 `load_trajectory()` 恢复 `StateSnapshot.raw` 后校验；
- native mapping 只接受真实存在的 sandbox message index，不做近邻替代。

## 6. 实施顺序

1. 修改 runner 完整性检查，使用 `load_trajectory()`。
2. 使用 `trajectory_to_json()` 重写测试 fixture。
3. 增加存在、缺失、跳过和多 milestone 测试。
4. 更新 Harness API 文档。
5. 运行目标测试与全部 pytest。
6. 执行 Python 编译检查和 `git diff --check`。
7. 清理 `.coverage`、`.pytest_cache`、`__pycache__` 等测试产物。

## 7. 验收标准

- 真实 `snapshot_to_json()`/`trajectory_to_json()` 产物可以通过完整性检查。
- 合法 native mapping 不再被误判为缺失。
- 缺失 snapshot 仍 fail fast，错误身份字段完整。
- 非 ToolSandbox 与空 mapping 行为不变。
- 不修改 mapping，不增加最近 snapshot 回退。
- `_validate_toolsandbox_replay_source()` 行覆盖率不低于 80%。
- 全部 pytest、编译检查和 `git diff --check` 通过。

## 附录A. 项目中没有把握实现的模块部分

本修复没有需要猜测的外部协议：当前 serializer 的顶层展开行为与 loader 把未知字段恢复到 `StateSnapshot.raw` 的行为均可由项目代码直接确认。

需要注意但不属于本方案的事项：现有实验轨迹中究竟有多少 native mapped snapshot 真正缺失，只能在修复检查后对重新执行或已有产物运行审计得到。本方案只修正检查读取结构，不承诺把真实缺失数降为零；真实缺失仍应由 ToolSandbox history 采集链路负责解决。
