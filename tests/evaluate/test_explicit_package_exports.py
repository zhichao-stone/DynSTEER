import inspect
from pathlib import Path

import dynsteer.evaluate as evaluate
import dynsteer.evaluate.matching as matching
import dynsteer.llm as llm


def test_public_packages_do_not_use_getattr_reexports() -> None:
    """公开包导出不再使用 __getattr__ 懒加载。"""
    assert not hasattr(evaluate, "__getattr__")
    assert not hasattr(matching, "__getattr__")
    assert not hasattr(llm, "__getattr__")


def test_public_exports_point_to_real_modules() -> None:
    """公开符号可定位到真实实现模块。"""
    assert inspect.getmodule(evaluate.DynSTEEREvaluator).__name__ == "dynsteer.evaluate.evaluator"
    assert inspect.getmodule(evaluate.evaluate_checkpoint).__name__ == "dynsteer.evaluate.settlement"
    assert inspect.getmodule(matching.boundary_snapshot).__name__ == "dynsteer.evaluate.matching.boundary"
    assert inspect.getmodule(matching.analyze_milestone_step).__name__ == "dynsteer.evaluate.matching.milestone"
    assert inspect.getmodule(llm.build_llm).__name__ == "dynsteer.llm.factory"
    assert inspect.getmodule(llm.OpenaiLLM).__name__ == "dynsteer.llm.openai"


def test_evaluate_package_internal_imports_use_concrete_modules() -> None:
    """evaluate 包内部不再反向依赖包级 re-export。"""
    evaluate_root = Path(__file__).resolve().parents[2] / "dynsteer" / "evaluate"
    offenders: list[str] = []
    for path in evaluate_root.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "from dynsteer.evaluate import" in text or "from dynsteer.evaluate.matching import" in text:
            offenders.append(str(path.relative_to(evaluate_root.parents[1])))
    assert offenders == []
