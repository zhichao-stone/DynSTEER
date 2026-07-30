# SWE-bench Pro Adapter API

## 当前状态

当前代码包不再暴露 `swebench_pro` registry scaffold。SWE-bench 接入需要在真实 dataset schema、runner、adapter 和 harness 均可运行后重新新增。

## 重新接入要求

- 在 `dynsteer.adapter.registry` 中注册真实 adapter 与 harness。
- 提供可运行的 dataset 加载、repo checkout、agent 运行、patch 验证和 resolved rate 提取。
- 生成可被 DynSTEER 评估链路消费的 `TaskCase`、trajectory 与 pseudo-stage 数据。
