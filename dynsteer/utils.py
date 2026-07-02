from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from dynsteer.model import JsonObject, JsonValue, MISSING


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


def get_object(
    data: JsonObject,
    key: str,
    type: object = None,
    default: object = None,
    required: bool = True,
) -> object:
    """从 JSON 对象读取字段，并按需校验存在性和类型。

    Args:
        data: 待读取的 JSON 对象。
        key: 字段名。
        type: 可选的 `isinstance` 类型或类型元组。
        default: 字段缺失且非必填时返回的默认值。
        required: 字段是否必填。

    Returns:
        字段值或默认值。
    """
    if data is None:
        raise ValueError("待读取字段的 JSON 对象不能为空")
    if key not in data:
        if required:
            raise ValueError(f"缺少必要字段: {key}")
        return default
    value = data[key]
    if type is not None and not isinstance(value, type):  # type: ignore[arg-type]
        raise ValueError(f"字段 {key} 必须是 {type} 类型")
    return value


def enum_value(enum_class: type[Enum], value: object, field_name: str) -> Enum:
    """将 JSON 枚举值转换为指定 Enum 成员。"""
    if enum_class is None or field_name is None:
        raise ValueError("enum_class 和 field_name 不能为空")
    if value is None:
        raise ValueError(f"缺少枚举字段: {field_name}")
    try:
        return enum_class(value)
    except ValueError as exc:
        raise ValueError(f"字段 {field_name} 的枚举值非法: {value}") from exc


def enum_name(value: object) -> str:
    """读取枚举或类枚举对象的稳定大写名称。"""
    if value is None:
        return ""
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name.upper()
    raw = str(value)
    if "." in raw:
        raw = raw.rsplit(".", 1)[-1]
    return raw.upper()


def unknown_fields(data: JsonObject, known: set[str]) -> JsonObject:
    """返回 JSON 对象中不属于 known 集合的字段。"""
    if data is None or known is None:
        raise ValueError("data 和 known 不能为空")
    return {key: value for key, value in data.items() if key not in known}


def json_safe(value: object) -> JsonValue:
    """将常见 Python 对象转换为 JSON 安全值。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    enum_raw_value = getattr(value, "value", None)
    if isinstance(enum_raw_value, (str, int, float, bool)) or enum_raw_value is None and isinstance(value, Enum):
        return enum_raw_value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)
