# 2026-07-29 agent/user 模型独立配置修复方案
## 1. 现状结论

当前代码**不能**给 `agent` 和 `user` 两个角色分别配置独立的 `api_key`、`base_url` 等连接参数。

### 现有链路

1. `judge` 链路已经支持细粒度配置。
   - `dynsteer/llm/factory.py` 会构造 `LLMConfig`
   - `dynsteer/llm/base.py` 会把 `api_key`、`base_url` 透传给 provider client
2. `agent` / `user` 目前只做“角色类型选择”，不接收独立 client 配置。
   - `dynsteer/adapter/toolsandbox/harness.py` 只从 `config.metadata["agent"]`、`config.metadata["user"]` 读角色名
   - `dynsteer/adapter/toolsandbox/utils/roles.py` 再按角色类型去构造固定 client
3. 当前 client 只能从全局环境变量取值。
   - OpenAI 路径：`OPENAI_API_KEY`、`OPENAI_BASE_URL`
   - Anthropic 路径：`ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL`

### 结果

- 可以给整个进程配一套全局 key / base_url
- 不能给 agent 和 user 各自单独配 key / base_url
- 不能在 run config 或 experiment JSON 中直接声明两套连接参数

## 2. 目标

1. 允许 `agent` 和 `user` 各自独立配置连接参数。
2. 保留旧配置完全可用。
3. 敏感信息继续优先来自环境变量，不把真实密钥写进示例文件。

## 3. 推荐配置形状

### 3.1 benchmark 运行配置

在 `data/{benchmark}/run_configs.json` 中继续保留：

```json
{
  "scenarios": ["wifi_off"],
  "agent": "GPT_4_o_2024_05_13",
  "user": "GPT_4_o_2024_05_13",
  "agent_client": {
    "api_key_env": "DYNSTEER_AGENT_API_KEY",
    "base_url_env": "DYNSTEER_AGENT_BASE_URL"
  },
  "user_client": {
    "api_key_env": "DYNSTEER_USER_API_KEY",
    "base_url_env": "DYNSTEER_USER_BASE_URL"
  }
}
```

如果想写死值，也允许：

```json
{
  "agent_client": {
    "api_key": "sk-xxx",
    "base_url": "https://example.com/v1"
  }
}
```

### 3.2 experiment 运行配置

在 `data/experiments/*.json` 里，把同样的 `agent_client` / `user_client` 放进：

- `models[*].harness_metadata`

因为 `dynsteer/experiment/config.py` 已经会把 `harness_metadata` 原样合并进 `ExperimentRunSpec.metadata`，所以这里**不需要新加字段透传代码**，只需要新增文档和示例。

## 4. 需要修改的位置

### 4.1 `dynsteer/adapter/toolsandbox/harness.py`

#### 现状

当前 `_toolsandbox_roles()` 只做两件事：

1. 读取 `config.metadata["agent"]` 和 `config.metadata["user"]`
2. 调用 `get_agent_factory(agent_type)` / `get_user_factory(user_type)` 创建 role

#### 修改目标

改成同时读取：

1. `config.metadata["agent"]`
2. `config.metadata["user"]`
3. `config.metadata["agent_client"]`
4. `config.metadata["user_client"]`

#### 建议改法

把这段：

```python
agent_type = self._role_impl_type(config.metadata.get("agent"), "agent")
user_type = self._role_impl_type(config.metadata.get("user"), "user")
agent_factory = get_agent_factory(agent_type) or getattr(cli_utils, "AGENT_TYPE_TO_FACTORY").get(agent_type)
user_factory = get_user_factory(user_type) or getattr(cli_utils, "USER_TYPE_TO_FACTORY").get(user_type)
```

改成：

```python
agent_type = self._role_impl_type(config.metadata.get("agent"), "agent")
user_type = self._role_impl_type(config.metadata.get("user"), "user")
agent_client_config = config.metadata.get("agent_client")
user_client_config = config.metadata.get("user_client")
agent_factory = get_agent_factory(agent_type, agent_client_config) or getattr(cli_utils, "AGENT_TYPE_TO_FACTORY").get(agent_type)
user_factory = get_user_factory(user_type, user_client_config) or getattr(cli_utils, "USER_TYPE_TO_FACTORY").get(user_type)
```

也就是说，`agent_client` / `user_client` 不是去改 `agent` / `user` 角色名，而是单独透传给 role factory。

#### 兼容要求

- `agent` / `user` 仍然只表示角色类型
- 没有 `agent_client` / `user_client` 时，行为不变
- `agent_client` / `user_client` 只影响 client 初始化，不影响 scenario、run_id、case 选择

### 4.2 `dynsteer/adapter/toolsandbox/utils/roles.py`

这是这次修复的核心文件。

#### 现状

当前只有固定环境变量客户端构造：

```python
def _client_kwargs(api_key_env: str, base_url_env: str, default_api_key: str | None = None) -> dict[str, str]
def _openai_client_from_env(default_api_key: str | None = None) -> object
def _anthropic_client_from_env() -> object
```

以及：

```python
def get_agent_factory(role_impl_type: object) -> Callable[[], object] | None
def get_user_factory(role_impl_type: object) -> Callable[[], object] | None
```

#### 修改目标

把 role factory 改成可接收可选 client 配置，并把配置优先级改成：

1. 显式 `api_key`
2. 显式 `api_key_env`
3. 旧全局 env
4. `openai_server` 场景下的 `EMPTY` 默认 key 只作为最后兜底

#### 建议代码形态

把签名改成：

```python
def get_agent_factory(role_impl_type: object, client_config: Mapping[str, Any] | None = None) -> Callable[[], object] | None: ...
def get_user_factory(role_impl_type: object, client_config: Mapping[str, Any] | None = None) -> Callable[[], object] | None: ...
```

然后把 `_role_factory(...)` 改成也接收 `client_config`，并把它继续传给 `_build_role_factory(...)`。

#### 新增或改写的 helper

建议把 `_client_kwargs(...)` 改成支持配置对象，例如：

```python
def _client_kwargs(
    client_config: Mapping[str, Any] | None,
    api_key_env: str,
    base_url_env: str,
    default_api_key: str | None = None,
) -> dict[str, object]:
```

读取规则建议如下：

1. `client_config["api_key"]` 非空时直接用
2. 否则如果 `client_config["api_key_env"]` 存在，就读该环境变量
3. 否则读旧的 `api_key_env`
4. `base_url` 同理
5. `timeout_seconds` 可选支持，存在时一并透传

#### 角色类型分支

`RoleFactorySpec.mode` 维持不变，只是 client 来源改成可配置：

- `openai`：构造 `OpenAI(...)`
- `anthropic`：构造 `anthropic.Anthropic(...)`
- `openai_server`：继续走 OpenAI-compatible client，只是默认 `EMPTY` 仅作为兜底
- `pass`：保持原样，不注入 client

#### 需要特别注意

1. `_role_factory()` 里有 generic fallback 逻辑，`client_config` 必须一路传过去
2. `get_agent_factory/get_user_factory` 的默认返回值语义不能变
3. 不能把 client 配置塞进 `model_name`
4. 不能把 `agent_client` / `user_client` 变成新的角色名字段

### 4.3 `dynsteer/harness/config.py`

这部分**功能上不一定必须改**，但建议补一个前置校验。

#### 可选改动

在 `load_harness_run_configs(...)` 里增加：

1. `agent_client`、`user_client` 如果出现，必须是 JSON 对象
2. 空字符串字段要视为未配置
3. 识别不了的字段名直接报错，避免拼错后静默失效

#### 为什么可选

因为这个文件已经会把 `run_configs.json` 的额外字段全部放进 `HarnessRunConfig.metadata`，真正的功能开关在 `harness.py` / `roles.py`。

如果你希望最小改动，这个文件可以不动。

### 4.4 `dynsteer/experiment/config.py`

这部分**不需要改代码**。

原因很简单：

1. `models[*].harness_metadata` 已经会被 `_merge_metadata(...)` 合并
2. `ExperimentRunSpec.to_metadata()` 已经把整份 `metadata` 带下去了
3. 只要 `agent_client` / `user_client` 被放进 `harness_metadata`，下游 harness 就能拿到

这里只需要改文档示例，不需要改逻辑。

## 5. 文档和示例要改哪里

### 5.1 `docs/apis/harness.md`

补充说明：

1. `HarnessRunConfig.metadata` 里现在支持 `agent_client` / `user_client`
2. `agent` / `user` 是角色名，不是 client 参数容器
3. 角色的连接参数由 `*_client` 控制

### 5.2 `docs/apis/experiment.md`

补充说明：

1. `models[*].harness_metadata` 可以放 `agent_client` / `user_client`
2. `judge_profiles` 仍然只管 judge，不管 ToolSandbox 的 agent/user
3. 不要把 agent/user 的 client 配置误写到 `judge_profiles`

### 5.3 `README.md`

把当前 `run_configs.json` 示例扩展成新形状，明确展示：

1. `agent`
2. `user`
3. `agent_client`
4. `user_client`

### 5.4 `.env.example`

补充示例环境变量：

1. `DYNSTEER_AGENT_API_KEY`
2. `DYNSTEER_AGENT_BASE_URL`
3. `DYNSTEER_USER_API_KEY`
4. `DYNSTEER_USER_BASE_URL`

如果保留 `api_key_env` / `base_url_env` 方案，这些名字就作为推荐示例，不必强制写死。

### 5.5 示例数据

更新：

1. `data/toolsandbox/run_configs.json`
2. `data/experiments/toolsandbox_partial_main.json`

让示例能明确展示独立配置入口。

## 6. 测试要补什么

建议新增 3 组测试。

### 6.1 工厂参数透传测试

目标文件建议：

- `tests/test_toolsandbox_role_client_config.py`

覆盖点：

1. `agent_client.api_key` 能覆盖全局 env
2. `user_client.base_url_env` 能读独立环境变量
3. 不传 `agent_client` / `user_client` 时，仍然走旧 env 逻辑

### 6.2 harness 透传测试

目标文件建议：

- `tests/test_toolsandbox_harness_client_config.py`

覆盖点：

1. `_toolsandbox_roles()` 会把 `metadata["agent_client"]` / `metadata["user_client"]` 传给 factory
2. `agent` / `user` 仍然只负责角色类型

### 6.3 配置兼容性测试

覆盖点：

1. 旧 `run_configs.json` 仍可运行
2. experiment 的 `harness_metadata` 里放 client 配置不会丢
3. 错误类型的 `agent_client` / `user_client` 能尽早报错

## 7. 不需要改的地方

1. `dynsteer/llm/base.py`
2. `dynsteer/llm/factory.py`
3. `dynsteer/experiment/config.py`
4. `dynsteer/main.py`

原因：

- 这些文件只管 judge 或 experiment 总装，不管 ToolSandbox 的 agent/user client 注入
- 这次问题的真正入口在 `toolsandbox/harness.py` 和 `toolsandbox/utils/roles.py`

## 8. 验收标准

1. 同一个 run 里，agent 和 user 可以各自使用不同的 `api_key` / `base_url`
2. 未配置新字段时，旧行为完全不变
3. experiment 和 benchmark-only 两条入口都能拿到同一套 client 配置语义
4. 文档示例能直接指导用户写出可用配置

