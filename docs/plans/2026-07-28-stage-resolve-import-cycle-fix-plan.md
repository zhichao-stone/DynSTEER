# stage 目标解析循环导入修复方案

## 1. 问题目标

当前 `dynsteer.stage.goal` 导入 `dynsteer.prompt.stage` 时，会先执行 `dynsteer.prompt.__init__`，而 `prompt.__init__` 提前导入 `prompt.judge`。`prompt.judge` 又从 `dynsteer.stage.goal` 导入 `resolve_stage_goal`，导致 `stage.goal` 尚未初始化完成时被反向读取，触发循环导入。

本次修复目标是把阶段目标解析能力迁移到更底层的 `dynsteer.stage.resolve`，使 judge prompt 不再依赖 `dynsteer.stage.goal`。

## 2. 代码结构调整

新增模块：

```text
dynsteer/stage/resolve.py
```

模块职责：

- `stage_goal_key`：生成阶段目标 key。
- `required_stage_goal_keys`：返回 milestone graph 需要的 stage goal key。
- `resolve_stage_goal`：从 `TaskCase.stage_goals` 解析当前阶段目标。
- `DEFAULT_FINISH_STAGE_GOAL`：提供 finish 阶段默认中文目标。

## 3. 修改范围

- `dynsteer/stage/resolve.py`：新增底层解析模块，不依赖 `dynsteer.stage.goal`。
- `dynsteer/stage/goal.py`：移除 `stage_goal_key`、`required_stage_goal_keys`、`resolve_stage_goal` 定义，改为从 `dynsteer.stage.resolve` 导入。
- `dynsteer/stage/spec.py`：改为从 `dynsteer.stage.resolve` 导入 stage goal key 与 required key。
- `dynsteer/prompt/judge.py`：改为从 `dynsteer.stage.resolve` 导入 `resolve_stage_goal`。
- `dynsteer/stage/__init__.py`：从 `dynsteer.stage.resolve` 显式导出解析相关公共接口。
- `dynsteer/prompt/__init__.py`：不再提前导入 `prompt.judge`，避免包初始化阶段扩大依赖面。
- `dynsteer/evaluate/__init__.py`：不再提前导入 runtime、settlement、evaluator，避免 `prompt.judge -> evaluate.semantic` 时触发 evaluate 包级循环导入。
- `dynsteer/evaluate/matching/__init__.py`：不再提前导入 milestone，避免 `evaluate.scoring -> matching.boundary` 时反向加载 `evaluate.scoring`。

## 4. 验证方式

- 运行 `python -m compileall dynsteer/stage dynsteer/prompt` 检查语法。
- 运行最小 import 验证，确认 `dynsteer.stage.goal` 与 `dynsteer.prompt.judge` 不再因为循环导入失败。
- 如验证被第三方可选依赖阻断，记录阻断原因，不引入与本次循环导入无关的大范围改动。

## 附录A. 项目中没有把握实现的模块部分

暂无没有把握的实现部分。本次改动只调整阶段目标解析函数的位置和导入关系，不改变评估业务语义。唯一需要关注的是当前环境是否安装了 LLM provider 可选依赖；如果缺失，可能会影响完整包级 import 验证，但不影响本次循环导入重构本身。
