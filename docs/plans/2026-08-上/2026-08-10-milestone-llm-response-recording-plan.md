# Milestone LLM 原始响应记录实施方案

## 1. 背景与目标

`toolsandbox_milestone_reliability_partial_main` 当前批次的 150 条候选路径全部以“LLM milestone 返回必须是 JSON 对象”被拒绝，但结果产物没有保留 LLM 原始响应，无法确认响应是 Markdown 代码围栏、解释正文、截断 JSON 还是其他格式。

本次目标是在不改变 milestone prompt、模型参数和解析宽严度的前提下，记录每次 milestone 路径生成的原始 LLM 文本，并把文件位置及安全摘要写入逐路径审计结果，使下一次复跑可以直接核对失败原文。

## 2. 修改范围

### 2.1 `dynsteer/milestone/compiler.py`

1. 为 `compile_task_case()` 增加可选的 `response_output_file: Path | None` 参数。
2. 每次 `llm.chat()` 返回非空文本后、进入 JSON 解析前，立即更新当前 case 的 UTF-8 聚合 JSON 文件。
3. JSON key 使用固定路径序号和固定策略名，例如 `path_00_direct-shortest`，不使用模型返回内容或用户输入构造文件名。
4. 在 `path_summaries` 中增加 `response_record`：
   - `path`：聚合响应文件路径；
   - `key`：当前响应在文件中的 key；
   - `character_count`：字符数；
   - `sha256`：UTF-8 文本哈希；
   - `starts_with_json_fence`：是否以 Markdown JSON 围栏开头；
   - `starts_with_object`：首个非空字符是否为 `{`。
5. 无论后续 JSON/schema 校验成功或失败，只要 LLM 已返回文本，就保留响应文件及元数据。
6. 将 JSON 语法错误细化为包含 `JSONDecodeError` 行、列和原因的诊断，避免继续与“合法 JSON 但顶层不是对象”混为一类。
7. 未配置 `response_output_file` 时不写文件，保持正常 adapter 生成流程不产生额外落盘副作用。

### 2.2 `milestone_reliability.py`

1. 为每个 case 构造稳定的响应文件：
   `results/milestone/<experiment_id>/<benchmark>/llm_outputs/<case_id>.json`。
2. 把该文件传给 `compile_task_case()`。
3. 原始响应与 case 评测 JSON 分目录保存，避免大段模型输出膨胀汇总文件。
4. 默认防覆盖或显式 `--force` 清空语义保证当前实验目录只属于一个批次，不再使用 `run_id` 目录。

### 2.3 `docs/apis/milestone.md`

补充 `compile_task_case()` 的可选响应记录目录、输出文件结构和敏感信息注意事项。

### 2.4 测试

增加针对性 PyTest，覆盖：

1. 纯 JSON 响应成功时原文仍被记录；
2. 带 Markdown 围栏的响应被拒绝，但原文、哈希和 fence 标记完整保留；
3. JSON 语法错误包含行列诊断；
4. 未配置目录时不创建响应文件。

测试通过项目现有 `compile_task_case()` 接口调用，不新增仅供测试使用的生产接口。

## 3. 文件结构

```text
results/milestone/<experiment_id>/
├── index.json
├── summary.json
├── <benchmark>/
│   ├── <case_id>.json
│   └── llm_outputs/
│       └── <case_id>.json
└── ...
```

## 4. 安全与边界

- 原始响应可能复述用户指令、联系人或其他任务数据，因此只写入实验结果目录，不写入终端日志。
- 代码不自动脱敏原始文本，否则会妨碍定位 JSON 格式问题；是否提交或共享 `llm_outputs` 由实验执行者决定。
- 文件路径完全由受控的 benchmark/case 安全文件名、整数序号和固定策略集合构造，禁止使用响应内容构造路径。
- 本次不自动修复 Markdown fence，也不启用 `response_format=json_object`，避免在增加观测能力的同时改变待观测行为。确认真实响应后，再独立决定输出协议修复方式。

## 5. 验收标准

- 复跑任一 case 后，每次成功返回的 LLM 文本都有对应 UTF-8 `.txt` 文件；
- JSON 解析失败不会导致原始响应丢失；
- case JSON 的每条路径摘要可以定位聚合响应文件及 key，并校验字符数、SHA-256；
- 记录功能关闭时，现有调用行为不变；
- PyTest、Python 语法检查通过；
- 不修改现有 prompt、模型参数、JSON 宽松解析规则和 `.gitignore`。

## 附录A. 项目中没有把握实现的模块部分

本次无法提前确认的是 Qwen 实际返回的非法文本形态，因为上一批结果没有保存原始响应。记录功能可以在下一次复跑后消除这一不确定性，但本次不能把“模型复制了 Markdown 围栏”当成已经证实的根因。

另一个边界是原始响应可能含有 benchmark 任务数据。本次按用户明确需求保存完整原文，并将其限制在实验结果目录；不擅自设计可能破坏诊断价值的通用脱敏器。
