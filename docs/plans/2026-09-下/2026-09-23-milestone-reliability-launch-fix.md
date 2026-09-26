# 2026-09-23 milestone reliability 启动最小修复方案

## 1. 目标

让 `data/experiments/toolsandbox_milestone_reliability_main.json` 可以通过统一配置加载，并用以下命令启动：

```bash
bash scripts/start_milestone_reliability.sh \
  --exp data/experiments/toolsandbox_milestone_reliability_main.json \
  --workers 4
```

## 2. 修正后的原则

- 不为 reliability 增加配置文件名白名单或伪模型特判。
- 配置文件名只用于人工定位；运行时实验身份以顶层 `experiment_id` 为准。
- reliability 配置的 `models[0]` 直接使用 `toolsandbox_main.json` 中已有的 `qwen3-max-2026-01-23` 模型项，因此自然满足统一 ToolSandbox 模型校验。
- `milestone_generation.generator` 与 `toolsandbox_main.json` 的 `fixed-judge` 使用同一 provider、模型、凭据和 base URL。

## 3. 代码修改

- 移除 `load_experiment_config()` 中对配置文件主名的运行时校验。
- 删除只服务该校验的 `_assert_experiment_file_name()` 与 `_experiment_type_from_id()`。
- 恢复 `_validate_model_client()` 的普通签名和逻辑，不包含 reliability 参数或跳过逻辑。

## 4. 配置修改

- 将 `toolsandbox_milestone_reliability_main.json` 的 `models[0]` 替换为 `toolsandbox_main.json` 中完整的 `qwen3-max-2026-01-23` 模型项。
- 将 generator 的 provider/model/api_key/base_url 同步为 `fixed-judge` 配置。
- 保留 509 个 case、1 个 repeat、GED 配置和输出路径不变。

## 5. 验证

- 配置加载与矩阵展开应得到 1 个 spec、509 个 case。
- reliability 分组应得到 1 个 group。
- generator 应能构造 OpenAI-compatible LLM 客户端。
- 启动脚本使用 `--help` 做 dry-run，不发起真实 LLM 请求。

## 附录A. 项目中没有把握实现的模块部分

- 未实际调用线上模型运行 509 个 case，因此无法在本地确认模型服务的实时可用性。
