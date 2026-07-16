from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
import re
from typing import Any, Mapping

from dynsteer.model import JsonObject, JsonValue, MISSING


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    """将数值裁剪到 [lower, upper] 闭区间。"""
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


def read_token(current: Any, token: str) -> Any:
    """读取轻量 JSON selector 中的单个 token。"""
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
    """判断 actual JSON 是否包含 expected JSON 的结构和值。"""
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
    """从配置来源读取非空字符串并去除首尾空白。"""
    value = source.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def compact_text(value: object, limit: int = 160) -> str:
    """压缩任意值为单行短文本。"""
    if value is None:
        raise ValueError("摘要文本不能为空")
    if limit < 0:
        raise ValueError("摘要长度不能为负数")
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."


def first_text(values: list[str], limit: int = 160) -> str | None:
    """返回列表中的首条短文本。"""
    if not values:
        return None
    return compact_text(values[0], limit)


def string_list(value: object) -> list[str]:
    """将 list 值转换为字符串列表，非 list 返回空列表。"""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def clean_evidence_items(values: list[str], limit: int | None = None) -> list[str]:
    """清洗 evidence 文本，去除重复裸 step 引用。

    入参：
        values: 原始 evidence 字符串列表。
        limit: 可选最大返回条数。
    输出：
        保序去重后的 evidence；当存在 `step N: ...` 具体证据时，删除裸 `step N`。
    """
    if values is None:
        raise ValueError("evidence values 不能为空")
    if limit is not None and limit < 0:
        raise ValueError("evidence limit 不能为负数")

    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)

    detailed_steps = _detailed_step_indexes(deduped)
    cleaned = [
        item
        for item in deduped
        if not _is_redundant_bare_step_reference(item, detailed_steps)
    ]
    return cleaned[:limit] if limit is not None else cleaned


def get_object(
    data: JsonObject,
    key: str,
    type: object = None,
    default: object = None,
    required: bool = True,
) -> object:
    """从 JSON 对象读取字段，并按需校验存在性和类型。"""
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
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: json_safe(getattr(value, field.name)) for field in fields(value)}
    enum_raw_value = getattr(value, "value", None)
    if isinstance(enum_raw_value, (str, int, float, bool)) or (
        enum_raw_value is None and isinstance(value, Enum)
    ):
        return enum_raw_value
    if isinstance(value, dict):
        return {_json_safe_key(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)


def _json_safe_key(key: object) -> str:
    """把 JSON dict key 转成稳定字符串。"""
    raw_value = getattr(key, "value", None)
    if isinstance(raw_value, str):
        return raw_value
    return str(key)


def _detailed_step_indexes(values: list[str]) -> set[int]:
    """提取形如 `step N: ...` 的具体 step 证据编号。"""
    indexes: set[int] = set()
    for value in values:
        match = re.match(r"^\s*step\s+(\d+)\s*:", value, flags=re.IGNORECASE)
        if match is not None:
            indexes.add(int(match.group(1)))
    return indexes


def _is_redundant_bare_step_reference(value: str, detailed_steps: set[int]) -> bool:
    """判断裸 step 或 step 区间是否已被具体证据覆盖。"""
    single = re.match(r"^\s*step\s+(\d+)\s*$", value, flags=re.IGNORECASE)
    if single is not None:
        return int(single.group(1)) in detailed_steps

    step_range = re.match(r"^\s*step\s+(\d+)\s*[-~]\s*(\d+)\s*$", value, flags=re.IGNORECASE)
    if step_range is None:
        return False
    start = int(step_range.group(1))
    end = int(step_range.group(2))
    if start > end:
        start, end = end, start
    return any(index in detailed_steps for index in range(start, end + 1))
