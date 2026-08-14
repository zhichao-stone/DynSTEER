# 移除代码版本区分修改计划

## 1. 目标

移除业务代码中所有以 `*_version` 表达产物、schema、缓存或时间数据版本的字段、常量、校验与分支。代码只处理当前统一数据结构，不保留旧版本兼容、版本失效或按版本选择逻辑。

依赖声明中的包版本、项目打包所需的 `pyproject.toml` `version`，以及用于检测 Docker Compose 是否可用的 `docker compose version` 命令不属于业务版本区分，不在本次范围内。

## 2. 修改范围

1. `dynsteer/harness/outputs.py`
   - 删除结果 schema 版本常量和所有 `result_schema_version` 输出。
   - 删除缓存读取时基于结果版本判断失效的分支，仅按完整产物是否存在判断缓存可用。
2. `dynsteer/metrics.py`
   - 删除 `timing_schema_version` 的写入、输出和辅助函数。
   - timing 可用性直接由当前 `execution_timing` 结构及 step 覆盖情况决定。
3. `dynsteer/adapter/loader.py`
   - 删除生成图基于 `schema_version` 强制重新适配的分支。
   - 删除 adaptation cost 与 stage goal template metadata 中的版本字段。
4. `dynsteer/milestone/compiler.py`、`dynsteer/milestone/model.py`
   - 删除生成图和生成报告中的 schema 版本字段。
5. `dynsteer/experiment/metrics.py`
   - 删除成本明细中的 schema 版本字段。
6. `milestone_reliability.py`
   - 删除 summary/case 产物 schema 版本字段。
   - 删除可靠性配置 metadata 的版本默认值和版本校验。

## 3. 验收方式

1. 全局检索 Python 与结构化配置文件，确认业务代码中不再存在 `*_version` / `version_*` 字段或相应版本字符串。
2. 运行 Python 编译检查。
3. 运行项目现有测试；若项目没有测试文件，则记录现状并以编译检查和定向导入检查作为最低验收。
4. 检查 diff，确保未改动依赖版本、未覆盖工作区中既有修改，也未新增兼容代码。

## 附录A. 项目中没有把握实现的模块部分

无。现有版本字段及其读取、写入和分支均可从当前调用链直接确认；删除后使用当前数据结构本身完成校验，不需要推测外部协议。
