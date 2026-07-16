from __future__ import annotations

from typing import TYPE_CHECKING

from dynsteer.model import LLMJudgeConfig

if TYPE_CHECKING:
    from dynsteer.judges.base import (
        BaseJudge,
        LLMJudge,
        LLMJudgeConfigurationError,
        LLMJudgeResponseError,
    )

__all__ = [
    "BaseJudge",
    "LLMJudge",
    "LLMJudgeConfig",
    "LLMJudgeConfigurationError",
    "LLMJudgeResponseError",
    "CheapJudge",
    "StandardJudge",
    "ExpensiveJudge",
]


def __getattr__(name: str) -> object:
    """按需导出 judge 类，避免 confidence 子模块触发循环导入。"""
    if name in {"BaseJudge", "LLMJudge", "LLMJudgeConfigurationError", "LLMJudgeResponseError"}:
        from dynsteer.judges import base

        return getattr(base, name)
    if name == "CheapJudge":
        from dynsteer.judges.cheap import CheapJudge

        return CheapJudge
    if name == "StandardJudge":
        from dynsteer.judges.standard import StandardJudge

        return StandardJudge
    if name == "ExpensiveJudge":
        from dynsteer.judges.expensive import ExpensiveJudge

        return ExpensiveJudge
    raise AttributeError(name)
