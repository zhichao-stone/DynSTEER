from __future__ import annotations

from typing import Any, Mapping

from dynsteer.model import JsonValue, MISSING


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    """将数值裁剪到 [lower, upper] 闭区间。

    Args:
        value: 待裁剪的数值。
        lower: 闭区间下界，默认 0.0。
        upper: 闭区间上界，默认 1.0。

    Returns:
        裁剪后的数值：小于下界返回下界，大于上界返回上界，否则原样返回。
    """
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


def read_token(current: Any, token: str) -> Any:
    """读取轻量 JSON selector 中的单个 token。

    Args:
        current: 当前待读取的 JSON 层级，可为 dict、list 或标量。
        token: selector 拆分后的单段路径，支持 `name`、`name[index]` 和 `[index]` 形式。

    Returns:
        命中的值；路径不存在、类型不匹配或数组下标非法时返回 MISSING。
    """
    value = current
    rest = token
    while rest:
        if "[" in rest:
            name, tail = rest.split("[", 1)
            if name:
                if not isinstance(value, dict) or name not in value:
                    return MISSING
                value = value[name]
            index_text, after = tail.split("]", 1)
            if not isinstance(value, list):
                return MISSING
            try:
                index = int(index_text)
            except ValueError:
                return MISSING
            if index < 0 or index >= len(value):
                return MISSING
            value = value[index]
            rest = after.lstrip(".")
            if rest and "[" not in rest:
                if not isinstance(value, dict) or rest not in value:
                    return MISSING
                value = value[rest]
                rest = ""
        else:
            if not isinstance(value, dict) or rest not in value:
                return MISSING
            value = value[rest]
            rest = ""
    return value


def json_subsumes(actual: JsonValue, expected: JsonValue) -> bool:
    """判断 actual JSON 是否包含 expected JSON 的结构和值。

    Args:
        actual: 实际 JSON 值。
        expected: 期望 JSON 子结构；dict 要求键递归匹配，list 要求前缀元素递归匹配。

    Returns:
        actual 能覆盖 expected 的全部结构和值时返回 True，否则返回 False。
    """
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        for key, expected_value in expected.items():
            if key not in actual or not json_subsumes(actual[key], expected_value):
                return False
        return True
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) < len(expected):
            return False
        return all(json_subsumes(actual[index], value) for index, value in enumerate(expected))
    return actual == expected


def normalize_str_from_source(source: Mapping[str, str], key: str) -> str | None:
    """从配置来源读取非空字符串并去除首尾空白。

    Args:
        source: 字符串配置映射，例如环境变量。
        key: 需要读取的配置键。

    Returns:
        去除首尾空白后的字符串；值不存在、不是字符串或为空白时返回 None。
    """
    value = source.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
