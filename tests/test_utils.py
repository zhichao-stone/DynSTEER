from __future__ import annotations

from enum import Enum

import pytest

from dynsteer.model import TaskType
from dynsteer.utils import enum_name, enum_value, get_object, json_safe, unknown_fields


class LocalEnum(str, Enum):
    VALUE = "value"


def test_get_object_validates_required_default_and_type() -> None:
    with pytest.raises(ValueError, match="缺少必要字段: missing"):
        get_object({}, "missing")

    assert get_object({}, "optional", default={"ok": True}, required=False) == {"ok": True}

    with pytest.raises(ValueError, match="字段 metadata 必须是 .* 类型"):
        get_object({"metadata": []}, "metadata", dict)


def test_get_object_replaces_optional_string_list_helper() -> None:
    data = {"values": ["a", "b"]}

    values = get_object(data, "values", list, default=[], required=False)
    if not all(isinstance(item, str) for item in values):
        raise ValueError("字段 values 必须是 list[str] 类型")

    assert values == ["a", "b"]


def test_enum_and_json_helpers_return_stable_values() -> None:
    assert enum_value(TaskType, "general_task", "task_types") is TaskType.GENERAL
    assert enum_name(LocalEnum.VALUE) == "VALUE"
    assert unknown_fields({"known": 1, "extra": 2}, {"known"}) == {"extra": 2}
    assert json_safe({"enum": TaskType.GENERAL, "items": {1, 2}})["enum"] == "general_task"

    with pytest.raises(ValueError, match="字段 task_types 的枚举值非法"):
        enum_value(TaskType, "bad", "task_types")
