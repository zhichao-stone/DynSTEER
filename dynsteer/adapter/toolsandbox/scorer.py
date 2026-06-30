from __future__ import annotations

from collections.abc import Callable
from typing import Any

from dynsteer.adapter.base import BaseBenchmarkConstraintScorer
from dynsteer.evaluate.score import ScoringContext
from dynsteer.model import Constraint, ConstraintScore, JsonValue, Operator

import polars as pl


_FALLBACK_TOOLSANDBOX_SCHEMAS: dict[str, dict[str, Any]] = {
    "SANDBOX": {
        "sandbox_message_index": pl.Int32,
        "sender": pl.String,
        "recipient": pl.String,
        "content": pl.String,
        "openai_tool_call_id": pl.String,
        "openai_function_name": pl.String,
        "conversation_active": pl.Boolean,
        "tool_call_exception": pl.String,
        "tool_trace": pl.List(pl.String),
        "visible_to": pl.List(pl.String),
    },
    "SETTING": {
        "sandbox_message_index": pl.Int32,
        "device_id": pl.String,
        "cellular": pl.Boolean,
        "wifi": pl.Boolean,
        "location_service": pl.Boolean,
        "low_battery_mode": pl.Boolean,
        "latitude": pl.Float64,
        "longitude": pl.Float64,
    },
    "CONTACT": {
        "sandbox_message_index": pl.Int32,
        "person_id": pl.String,
        "name": pl.String,
        "phone_number": pl.String,
        "relationship": pl.String,
        "is_self": pl.Boolean,
    },
    "MESSAGING": {
        "sandbox_message_index": pl.Int32,
        "message_id": pl.String,
        "sender_person_id": pl.String,
        "sender_phone_number": pl.String,
        "recipient_person_id": pl.String,
        "recipient_phone_number": pl.String,
        "content": pl.String,
        "creation_timestamp": pl.Float64,
    },
    "REMINDER": {
        "sandbox_message_index": pl.Int32,
        "reminder_id": pl.String,
        "content": pl.String,
        "creation_timestamp": pl.Float64,
        "reminder_timestamp": pl.Float64,
        "latitude": pl.Float64,
        "longitude": pl.Float64,
    },
}


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
            return self._custom_constraint_failure_score(constraint, actual, exc)
        except BaseException as exc:
            if not self._is_pyo3_panic_exception(exc):
                raise
            return self._custom_constraint_failure_score(constraint, actual, exc)
        return ConstraintScore(
            constraint_id=constraint.constraint_id,
            score=score,
            missing=actual is None,
            evidence=[f"ToolSandbox custom constraint {constraint.constraint_id} 得分 {score:.3f} ({measure_name})"],
            actual=actual,
        )

    def _custom_constraint_failure_score(
        self,
        constraint: Constraint,
        actual: JsonValue,
        exc: BaseException,
    ) -> ConstraintScore:
        """将 ToolSandbox 原生评分异常转换为可诊断的约束失败。"""
        exc_type = f"{exc.__class__.__module__}.{exc.__class__.__name__}"
        return ConstraintScore(
            constraint_id=constraint.constraint_id,
            score=0.0,
            missing=actual is None,
            evidence=[f"ToolSandbox custom constraint {constraint.constraint_id} 评分失败: {exc_type}: {exc}"],
            actual=actual,
        )

    def _is_pyo3_panic_exception(self, exc: BaseException) -> bool:
        """判断异常是否为 Polars/PyO3 Rust panic 包装异常。"""
        exc_type = exc.__class__
        module_name = str(getattr(exc_type, "__module__", ""))
        class_name = str(getattr(exc_type, "__name__", ""))
        return module_name == "pyo3_runtime" or class_name == "PanicException"

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
        namespace = constraint.namespace or self._toolsandbox_namespace(constraint)
        snapshot = self._rows_to_dataframe(actual, namespace=namespace)
        target = self._rows_to_dataframe(constraint.expected, namespace=namespace)
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

    def _rows_to_dataframe(self, value: JsonValue, namespace: str | None = None) -> pl.DataFrame:
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
        return self._restore_namespace_schema(pl.DataFrame(rows), namespace)

    def _toolsandbox_namespace(self, constraint: Constraint) -> str:
        """读取 ToolSandbox 约束对应的数据库命名空间。"""
        metadata = constraint.metadata.get("toolsandbox")
        if isinstance(metadata, dict):
            namespace = metadata.get("database_namespace")
            if isinstance(namespace, str):
                return namespace
        return constraint.namespace or ""

    def _restore_namespace_schema(self, dataframe: pl.DataFrame, namespace: str | None) -> pl.DataFrame:
        """恢复 ToolSandbox JSON 快照丢失的 Null 列类型。"""
        if dataframe is None or not namespace:
            return dataframe
        schema = self._namespace_schema(namespace)
        if not schema:
            return dataframe
        result = dataframe
        for column_name, dtype in schema.items():
            if column_name not in result.columns:
                continue
            if result.schema.get(column_name) == pl.Null:
                result = result.with_columns(pl.col(column_name).cast(dtype))
        return result

    def _namespace_schema(self, namespace: str) -> dict[str, Any]:
        """获取 ToolSandbox namespace 的列类型定义。"""
        normalized = namespace.upper()
        try:
            execution_context = self._load_toolsandbox_execution_context_module()
            schemas = getattr(getattr(execution_context, "ExecutionContext"), "dbs_schemas", {})
            for key, value in dict(schemas).items():
                if str(key).upper().endswith(normalized):
                    return dict(value)
        except (AttributeError, ModuleNotFoundError, TypeError, ValueError):
            return _FALLBACK_TOOLSANDBOX_SCHEMAS.get(normalized, {})
        return _FALLBACK_TOOLSANDBOX_SCHEMAS.get(normalized, {})

    def _load_toolsandbox_execution_context_module(self) -> Any:
        """加载 ToolSandbox execution_context 模块。"""
        if self._module_loader is not None:
            return self._module_loader("tool_sandbox.common.execution_context")
        raise ModuleNotFoundError("tool_sandbox.common.execution_context")

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
        return self._rows_to_dataframe(reference_snapshot.namespaces.get(namespace), namespace=namespace)
