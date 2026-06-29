from __future__ import annotations

from collections.abc import Callable
from typing import Any

from dynsteer.adapter.base import BaseBenchmarkConstraintScorer
from dynsteer.evaluate.score import ScoringContext
from dynsteer.model import Constraint, ConstraintScore, JsonValue, Operator

import polars as pl


class ToolSandboxConstraintScorer(BaseBenchmarkConstraintScorer):
    """ToolSandbox 专用约束评分器。"""

    def __init__(self, module_loader: Callable[[str], Any] | None = None) -> None:
        self._module_loader = module_loader

    def score_custom_constraint(
        self,
        constraint: Constraint,
        source: object,
        reference_source: object | None,
        actual: JsonValue,
        reference_value: JsonValue,
        context: ScoringContext | None = None,
    ) -> ConstraintScore:
        """计算 ToolSandbox custom snapshot constraint 分数。

        Args:
            constraint: 当前 ToolSandbox 约束。
            source: 当前值来源。
            reference_source: 参考值来源。
            actual: 当前 selector 命中的实际值。
            reference_value: 当前约束的参考值。
            context: 可选评分上下文。

        Returns:
            单条 ToolSandbox 约束评分。
        """
        metadata = constraint.metadata.get("toolsandbox")
        if not isinstance(metadata, dict):
            return super().score_custom_constraint(
                constraint,
                source,
                reference_source,
                actual,
                reference_value,
                context=context,
            )
        if constraint.operator != Operator.CUSTOM:
            return super().score_constraint(constraint, source, reference_source, context=context)

        measure_name = str(metadata.get("snapshot_constraint") or "")
        try:
            score = self._score_toolsandbox_snapshot_constraint(
                measure_name=measure_name,
                constraint=constraint,
                actual=actual,
                reference_value=reference_value,
                context=context,
            )
        except Exception as exc:
            return ConstraintScore(
                constraint_id=constraint.constraint_id,
                score=0.0,
                missing=actual is None,
                evidence=[f"ToolSandbox custom constraint {constraint.constraint_id} 评分失败: {exc}"],
                actual=actual,
            )
        return ConstraintScore(
            constraint_id=constraint.constraint_id,
            score=score,
            missing=actual is None,
            evidence=[f"ToolSandbox custom constraint {constraint.constraint_id} 得分 {score:.3f} ({measure_name})"],
            actual=actual,
        )

    def _score_toolsandbox_snapshot_constraint(
        self,
        measure_name: str,
        constraint: Constraint,
        actual: JsonValue,
        reference_value: JsonValue,
        context: ScoringContext | None,
    ) -> float:
        """调用 ToolSandbox 原生 snapshot similarity 函数。"""
        if not measure_name:
            raise ValueError("缺少 snapshot_constraint")
        evaluation = self._load_toolsandbox_evaluation_module()
        measure = getattr(evaluation, measure_name, None)
        if not callable(measure):
            raise ValueError(f"不支持的 ToolSandbox snapshot_constraint: {measure_name}")
        snapshot = self._rows_to_dataframe(actual)
        target = self._rows_to_dataframe(constraint.expected)
        column_similarities = self._column_similarities(evaluation, constraint)
        reference_snapshot = self._reference_dataframe(constraint, context)
        kwargs = self._snapshot_constraint_kwargs(evaluation, constraint)
        return float(
            measure(
                snapshot=snapshot,
                target_dataframe=target,
                column_similarities=column_similarities,
                reference_snapshot=reference_snapshot,
                **kwargs,
            )
        )

    def _load_toolsandbox_evaluation_module(self) -> Any:
        """加载 ToolSandbox evaluation 模块。"""
        if self._module_loader is not None:
            return self._module_loader("tool_sandbox.common.evaluation")
        raise ModuleNotFoundError("tool_sandbox.common.evaluation")

    def _load_tool_trace_extractors_module(self) -> Any:
        """加载 ToolSandbox tool_trace extractor 模块。"""
        if self._module_loader is not None:
            return self._module_loader("tool_sandbox.common.tool_trace_extractors")
        raise ModuleNotFoundError("tool_sandbox.common.tool_trace_extractors")

    def _rows_to_dataframe(self, value: JsonValue) -> pl.DataFrame:
        """将 DynSTEER JSON rows 转为 polars DataFrame。"""
        rows: JsonValue
        if isinstance(value, dict) and isinstance(value.get("rows"), list):
            rows = value["rows"]
        else:
            rows = value
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise ValueError("ToolSandbox snapshot rows 必须是 list")
        return pl.DataFrame(rows)

    def _snapshot_constraint_kwargs(self, evaluation: Any, constraint: Constraint) -> dict[str, Any]:
        """恢复 ToolSandbox partial snapshot constraint 的关键字参数。"""
        metadata = constraint.metadata.get("toolsandbox")
        if not isinstance(metadata, dict):
            return {}
        raw_kwargs = metadata.get("snapshot_constraint_kwargs")
        if not isinstance(raw_kwargs, dict):
            return {}
        kwargs: dict[str, Any] = {}
        for key, value in raw_kwargs.items():
            if key == "extractor" and isinstance(value, str):
                extractors = self._load_tool_trace_extractors_module()
                extractor = getattr(extractors, value, None)
                if not callable(extractor):
                    raise ValueError(f"不支持的 ToolSandbox extractor: {value}")
                kwargs[key] = extractor
            else:
                kwargs[key] = value
        return kwargs

    def _column_similarities(self, evaluation: Any, constraint: Constraint) -> dict[str, Any]:
        """读取 ToolSandbox 默认列相似度并应用约束级覆盖。"""
        metadata = constraint.metadata.get("toolsandbox")
        namespace = ""
        if isinstance(metadata, dict):
            namespace = str(metadata.get("database_namespace") or constraint.namespace or "")
        column_similarities: dict[str, Any] = {}
        defaults = getattr(evaluation, "_default_dbs_column_similarities", {})
        for key, value in defaults.items():
            if str(key).upper().endswith(namespace.upper()):
                column_similarities.update(dict(value))
                break
        raw_overrides = metadata.get("column_similarity_measure") if isinstance(metadata, dict) else None
        if isinstance(raw_overrides, dict):
            for column_name, measure_name in raw_overrides.items():
                if not isinstance(measure_name, str):
                    continue
                measure = getattr(evaluation, measure_name, None)
                if not callable(measure):
                    raise ValueError(f"不支持的 ToolSandbox column similarity: {measure_name}")
                column_similarities[str(column_name)] = measure
        return column_similarities

    def _reference_dataframe(self, constraint: Constraint, context: ScoringContext | None) -> pl.DataFrame | None:
        """根据 ScoringContext 查找 ToolSandbox reference snapshot。"""
        metadata = constraint.metadata.get("toolsandbox")
        if not isinstance(metadata, dict):
            return None
        reference_index = metadata.get("reference_milestone_node_index")
        if reference_index is None or context is None:
            return None
        reference_milestone_id = "initial" if reference_index == -1 else f"m{reference_index}"
        reference_snapshot = context.matched_snapshots.get(reference_milestone_id)
        if reference_snapshot is None:
            return None
        namespace = constraint.namespace or str(metadata.get("database_namespace") or "")
        return self._rows_to_dataframe(reference_snapshot.namespaces.get(namespace))
