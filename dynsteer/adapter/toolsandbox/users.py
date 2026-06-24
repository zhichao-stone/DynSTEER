from __future__ import annotations

import importlib
from collections.abc import Callable
from functools import lru_cache

from dynsteer.adapter.toolsandbox.agents import _openai_client_from_env, _role_impl_name


_OPENAI_USER_CLASSES = {
    "GPT_3_5_0125": "GPT_3_5_0125_User",
    "GPT_4_0125": "GPT_4_0125_User",
    "GPT_4_o_2024_05_13": "GPT_4_o_2024_05_13_User",
}
_TOOL_SANDBOX_USER_FALLBACK_NAMES = {"Cli"}


def get_user_factory(role_impl_type: object) -> Callable[[], object] | None:
    """根据 ToolSandbox role 类型创建 DynSTEER 本地 user 工厂。

    Args:
        role_impl_type: ToolSandbox 的 RoleImplType 枚举值。

    Returns:
        可直接实例化 user 的零参数工厂；没有本地适配时返回 None。
    """
    if role_impl_type is None:
        raise ValueError("role_impl_type 不能为空")
    role_name = _role_impl_name(role_impl_type)
    if role_name in _OPENAI_USER_CLASSES:
        user_type = _environment_openai_user_type(_OPENAI_USER_CLASSES[role_name])
        return user_type
    if role_name in _TOOL_SANDBOX_USER_FALLBACK_NAMES:
        return None
    return _generic_openai_user_factory(role_name)


def _generic_openai_user_factory(model_name: str) -> Callable[[], object]:
    user_type = _generic_openai_user_type()

    def factory() -> object:
        return user_type(model_name=model_name)

    return factory


@lru_cache(maxsize=None)
def _environment_openai_user_type(parent_class_name: str) -> type:
    parent_type = getattr(
        importlib.import_module("tool_sandbox.roles.openai_api_user"),
        parent_class_name,
    )

    class DynsteerOpenAIEnvironmentUser(parent_type):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            self.openai_client = _openai_client_from_env()

    DynsteerOpenAIEnvironmentUser.__name__ = f"DynSTEER{parent_type.__name__}"
    DynsteerOpenAIEnvironmentUser.__qualname__ = DynsteerOpenAIEnvironmentUser.__name__
    return DynsteerOpenAIEnvironmentUser


@lru_cache(maxsize=1)
def _generic_openai_user_type() -> type:
    parent_type = getattr(
        importlib.import_module("tool_sandbox.roles.openai_api_user"),
        "OpenAIAPIUser",
    )

    class DynsteerGenericOpenAIEnvironmentUser(parent_type):  # type: ignore[misc, valid-type]
        def __init__(self, model_name: str) -> None:
            self.model_name = model_name
            self.openai_client = _openai_client_from_env()

    DynsteerGenericOpenAIEnvironmentUser.__name__ = f"DynSTEER{parent_type.__name__}"
    DynsteerGenericOpenAIEnvironmentUser.__qualname__ = DynsteerGenericOpenAIEnvironmentUser.__name__
    return DynsteerGenericOpenAIEnvironmentUser
