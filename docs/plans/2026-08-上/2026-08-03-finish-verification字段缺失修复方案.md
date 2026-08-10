# Finish verification 字段缺失修复方案

## 1. 问题与目标

`finish_settlement()` 要求 `build_finish_verification()` 返回值始终包含
`whole_trajectory_evaluation_required`。当前仅空 milestone graph 分支包含该字段，
非空 graph 在 replay finish 结算时会触发 `KeyError`。

本次修复目标是统一返回契约，使非空 milestone graph 明确声明不需要完整轨迹评估，
并通过回归测试覆盖实际报错调用链。

## 2. 修改范围

### 2.1 `dynsteer/evaluate/final.py`

在非空 milestone graph 的 verification 返回值中补充：

- `empty_milestone_graph=False`；
- `fixed_milestones_applicable=True`；
- `whole_trajectory_evaluation=False`；
- `whole_trajectory_evaluation_required=False`；
- `coverage_basis="milestone_graph"`；
- `default_reference_used=False`。

这些字段与空 graph 分支使用同一份输出契约。其中关键修复字段为
`whole_trajectory_evaluation_required=False`，其余字段用于消除两条生产分支的结构差异，
并准确描述非空 graph 的评估方式。

不把消费端改为 `dict.get()`：该字段是 finish 路由的必要判定条件，生产端缺字段应直接修复，
避免未来再次静默降级。

### 2.2 `tests/test_finish_verification.py`

新增非空 milestone graph 回归测试：

1. 构造一个无额外终态约束的 milestone 和已匹配 settlement；
2. 调用公开评估接口 `finish_settlement()`；
3. 断言不再抛出 `KeyError`；
4. 断言 finish evaluation 使用 `milestone_graph`，且不要求完整轨迹 judge；
5. 断言 finish 结算状态保持为通过。

## 3. 验收方式

- 使用项目 uv 环境运行新增回归测试；
- 对修改文件执行 Python 编译检查；
- 检查 Git diff，确认没有改动用户已删除的测试文件或其他无关文件；
- 检查实现中没有新增无用 import、函数或兼容分支。

## 4. 风险与回滚

风险较低。非空 milestone graph 已由固定 milestone 和终态约束完成确定性评估，
将 `whole_trajectory_evaluation_required` 设为 `False` 与现有业务语义一致。
如需回滚，仅需撤销本方案新增的返回字段和回归测试。

## 附录A. 项目中没有把握实现的模块部分

无。本次异常堆栈、生产端返回分支和消费端读取位置均已定位，字段在非空 graph 下的语义明确。
