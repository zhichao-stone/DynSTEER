# finish 空阶段轨迹诊断修复方案

## 1. 问题目标

当前 replay 收尾时，若 `finish_stage_anchor_predecessor_id` 对应的真实 milestone 已经落在轨迹最后一步，则 finish 阶段区间为 `(last_step, last_step]`，没有新增 step。`build_stage_trace()` 会继续调用 `trajectory.get_interval(start_boundary_step_index, end_step_index)`，触发 `ValueError: min_index 必须小于 max_index`。

本次修复目标是让 finish 阶段的诊断报告正确表达“无新增 step”，而不是把空区间当作非法输入。

## 2. 修复范围

- `dynsteer/evaluate/diagnostics.py`
  - 在 `build_stage_trace()` 中识别 `start_boundary_step_index == end_step_index`。
  - 空区间时直接写出 `steps=[]`、`step_count=0`，保留快照 delta 摘要。
  - 增加 `empty_stage_interval` 与 `empty_stage_interval_reason`，便于报告消费者判断这是合法空阶段。
  - `start_boundary_step_index > end_step_index` 仍沿用现有异常路径，不吞掉真正的边界错误。

- `tests/test_finish_empty_stage_trace.py`
  - 新增回归测试，构造一个 terminal milestone 已在最后一步完成的 replay finish 结算。
  - 验证 `finish_settlement()` 不再因空 finish trace 报错。
  - 验证输出 settlement 与 metadata 中的 stage trace 明确记录空区间。

## 3. finish 阶段是否还需要评估

普通非空 milestone graph 的 finish 阶段仍需要保留，因为它承担最终覆盖率检查、terminal state/message 复核、fatal minefield 汇总和报告写入。没有新增 step 时，不需要再对“新增阶段轨迹”做额外切片评估，但仍需要生成 finish settlement。

空 milestone graph 是例外：没有固定 milestone 可覆盖时，finish 本身就是整条轨迹的终态评估入口，即使不存在“新增 finish step”，仍可能需要 whole-trajectory evaluator。

## 4. 验证方式

- 使用项目虚拟环境运行新增的单元测试：
  - `.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider tests\test_finish_empty_stage_trace.py`

## 附录A. 项目中没有把握实现的模块部分

暂无没有把握的实现部分。本次改动仅处理 finish trace 的空区间诊断表达，不修改 milestone 匹配、终态复核、LLM judge 路由或 `Trajectory.get_interval()` 的全局边界校验语义。
