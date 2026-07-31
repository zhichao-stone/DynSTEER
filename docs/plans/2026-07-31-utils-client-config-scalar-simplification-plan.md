# DynSTEER utils 客户端配置与标量解析精简方案

生成日期：2026-07-31  
方案状态：仅制定执行方案，不直接修改主功能代码  
约束来源：`docs/constraints/code.md`  
审计范围：仅统计 `dynsteer/**/*.py`

## 0. 当前审计结论

1. 你指出的 `dynsteer/utils.py:_normalize_client_config_value()` 中 `383-392` 和 `398-407` 两段，不是互相重复，而是分别在处理 `int` 与 `float` 字段；但它们共享同一套“可选字符串 -> `strip()` -> 空值转 `None` -> 数值解析 -> 排除 `bool` -> 范围校验”的骨架。
2. 这套骨架在仓库里**已经有现成实现**，就是 `dynsteer.utils.parse_int_value()` 和 `dynsteer.utils.parse_float_value()`。
3. 因此，本轮精简的重点不是再造第三个数字解析器，而是让 `normalize_client_config()` 直接复用现有解析原语。
4. 除了 `utils.py`，`dynsteer` 目录下还存在几处同类重复实现，主要集中在 `harness/config.py` 与 `adapter/loader.py` 这类配置边界层。
5. `dynsteer/llm/factory.py` 当前已经在消费 `parse_int_value()` / `parse_float_value()`，是这条链路里比较理想的参考实现，不需要再引入本地重复解析器。

## 1. 重复点清单

| 位置 | 重复逻辑 | 现状 |
| --- | --- | --- |
| `dynsteer/utils.py:373-414` | `client_config` 的 int / float 字段归一化 | 建议改为复用 `parse_int_value()` / `parse_float_value()` |
| `dynsteer/utils.py:36-76` | 通用 int / float 字符串解析 | 已存在，适合作为统一原语 |
| `dynsteer/harness/config.py:24-53` | `benchmark.json` 中 `max_workers` 的正整数校验 | 可复用 `parse_int_value()` |
| `dynsteer/harness/config.py:122-133` | `DYNSTEER_READY_FRONTIER_PATIENCE` 的正整数校验 | 可复用 `parse_int_value()` |
| `dynsteer/harness/config.py:217-224` | `_optional_positive_int()` | 可删，改用共享解析函数 |
| `dynsteer/adapter/loader.py:100-106` | `_optional_int()` | 可删，改用共享解析函数 |
| `dynsteer/llm/factory.py:39-44, 76-81` | env / config 数值解析 | 已经是正确复用方式，作为目标样例保留 |

## 2. 精简原则

1. **配置边界只保留一个权威解析原语**  
   `parse_int_value()` / `parse_float_value()` 已经足够表达“可选字符串数值”的语义，不应在 `harness/config.py`、`adapter/loader.py` 再各写一套。

2. **`normalize_client_config()` 只负责字段级分发**  
   它应该保留“文本字段 / 整数字段 / 浮点字段 / 不支持字段”的边界判断，但数值转换本身交给 `parse_*`。

3. **不要把很小的 wrapper 过度抽象**  
   `optional_str()`、`normalize_str_from_source()`、`required_str()`、`compact_text()`、`compact_json_text()`、`first_text()` 目前虽然有局部重叠，但职责仍然清晰，先不强行合并。

4. **JSON 结构校验和字符串/数值 coercion 分离**  
   像 `threshold_config_from_mapping()` 这种“只接受 JSON 数字，不接受字符串”的边界，不应硬塞进 `parse_int_value()` / `parse_float_value()` 的 coercion 语义里。

## 3. 建议方案

### 3.1 先改 `dynsteer/utils.py`

保留 `normalize_client_config()` 作为唯一公共入口，但把 `_normalize_client_config_value()` 的数值分支改成直接调用现有 helper。

建议形态：

```python
if key in _CLIENT_CONFIG_INT_FIELDS:
    normalized_value = parse_int_value(
        value,
        f"{label}.{key}",
        default=None,
        min_value=1,
    )
    return normalized_value

if key in _CLIENT_CONFIG_FLOAT_FIELDS:
    normalized_value = parse_float_value(
        value,
        f"{label}.{key}",
        default=None,
        min_value=0.0,
    )
    if key == "timeout_seconds" and normalized_value is not None and normalized_value <= 0:
        raise ValueError(f"{label}.timeout_seconds 必须大于 0")
    return normalized_value
```

这样可以把当前两段几乎同构的字符串裁剪、空值处理、类型排除逻辑收口到已有原语。

### 3.2 再收 `harness/config.py`

把下面几处局部实现删掉，改用 `dynsteer.utils.parse_int_value()`：

1. `load_benchmark_manifest_metadata()` 中 `benchmark.json max_workers` 的手写正整数校验。
2. `_optional_positive_int()`。
3. `load_ready_frontier_patience_from_env()` 里的 `int()` + 范围校验。

这样 `harness/config.py` 就只保留“字段组合”和“策略组装”，不再持有重复的整数解析器。

### 3.3 再收 `adapter/loader.py`

把 `_optional_int()` 去掉，直接调用 `parse_int_value()`。

这一步的收益不只是少几行代码，更重要的是让 `TaskCase` 适配边界和 `harness/config.py` 使用同一套整数语义。

## 4. 不建议本轮合并的地方

1. `normalize_str_from_source()` 和 `optional_str()`  
   前者表达“从 mapping 的 key 读取”，后者表达“对任意值做可选字符串清洗”，语义差异还够清晰。

2. `as_number()` 和 `parse_float_value()`  
   前者面向 JSON 已经是数字的值，后者面向可能是字符串的配置输入，不是同一层次。

3. `compact_text()` / `compact_json_text()` / `first_text()`  
   这几个是明确的小 wrapper，重叠很少，属于可读性优先的写法。

## 5. 需要修改的文件

- `dynsteer/utils.py`
- `dynsteer/harness/config.py`
- `dynsteer/adapter/loader.py`

## 6. 验证标准

1. `normalize_client_config()` 的 int / float 分支不再手写字符串裁剪和 `int()` / `float()` 转换。
2. `harness/config.py` 不再保留 `_optional_positive_int()` 和 `load_ready_frontier_patience_from_env()` 的手写数值解析。
3. `adapter/loader.py` 不再保留 `_optional_int()`。
4. `llm/factory.py` 继续保持对 `parse_int_value()` / `parse_float_value()` 的复用。
5. `python -m compileall -q dynsteer main.py` 通过。

## 7. 落地顺序

1. 先改 `dynsteer/utils.py`，把 `normalize_client_config()` 的数值分支接到现有解析原语上。
2. 再改 `dynsteer/harness/config.py`，收口 `max_workers` 和 `ready_frontier_patience` 的重复整数解析。
3. 最后改 `dynsteer/adapter/loader.py`，删除 `_optional_int()`。
4. 做一轮 `compileall` 和相关配置路径的最小回归验证。

