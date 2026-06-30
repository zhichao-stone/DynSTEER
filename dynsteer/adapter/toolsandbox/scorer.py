from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

from dynsteer.adapter.base import BaseBenchmarkConstraintScorer
from dynsteer.evaluate.score import ScoringContext
from dynsteer.model import (
    Boundary,
    Constraint,
    ConstraintScore,
    JsonValue,
    Milestone,
    MilestoneScore,
    Operator,
    StageStatus,
    StateSnapshot,
    Trajectory,
)
from dynsteer.utils import clamp

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

    def score_milestone(
        self,
        milestone: Milestone,
        boundary: Boundary,
        trajectory: Trajectory,
        reference_snapshots: list[StateSnapshot],
        context: ScoringContext | None = None,
    ) -> MilestoneScore:
        """按 ToolSandbox 原生语义聚合 milestone 约束分数。"""
        if milestone is None or boundary is None or trajectory is None or reference_snapshots is None:
            raise ValueError("ToolSandbox milestone 评分参数不能为空")
        if not any(self._is_toolsandbox_constraint(constraint) for constraint in milestone.constraints):
            return super().score_milestone(
                milestone,
                boundary,
                trajectory,
                reference_snapshots,
                context=context,
            )
        if len(milestone.constraints) == 0:
            return super().score_milestone(
                milestone,
                boundary,
                trajectory,
                reference_snapshots,
                context=context,
            )

        constraint_scores: list[ConstraintScore] = []
        score_product = 1.0
        non_guardrail_count = 0
        hard_pass = True
        for constraint in milestone.constraints:
            source = self._source_for_constraint(constraint, boundary, trajectory, reference_snapshots)
            reference = self._reference_for_constraint(constraint, reference_snapshots)
            result = self.score_constraint(constraint, source, reference, context=context)
            constraint_scores.append(result)
            constraint_score = clamp(float(result.score))
            score_product *= constraint_score
            if self._is_toolsandbox_guardrail(constraint):
                if constraint_score <= 0.0:
                    hard_pass = False
            else:
                non_guardrail_count += 1

        score = score_product ** (1.0 / non_guardrail_count) if non_guardrail_count > 0 else score_product
        score = 0.0 if not hard_pass else clamp(score)
        threshold = milestone.pass_threshold if milestone.pass_threshold is not None else 0.8
        if not hard_pass:
            status = StageStatus.FAIL
        elif score >= threshold:
            status = StageStatus.PASS
        elif score >= 0.6:
            status = StageStatus.WARN
        else:
            status = StageStatus.FAIL

        missing_count = sum(1 for item in constraint_scores if item.missing)
        return MilestoneScore(
            milestone_id=milestone.milestone_id,
            boundary_id=boundary.boundary_id,
            score=score,
            status=status,
            evidence=[line for item in constraint_scores for line in item.evidence],
            missing_ratio=missing_count / len(constraint_scores),
            hard_constraints_all_pass=hard_pass,
            constraint_scores=constraint_scores,
        )

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

    def _is_toolsandbox_constraint(self, constraint: Constraint) -> bool:
        """判断约束是否来自 ToolSandbox 适配层。"""
        if constraint is None:
            return False
        return isinstance(constraint.metadata.get("toolsandbox"), dict)

    def _is_toolsandbox_guardrail(self, constraint: Constraint) -> bool:
        """读取 ToolSandbox guardrail 标记。"""
        metadata = constraint.metadata.get("toolsandbox") if constraint is not None else None
        return isinstance(metadata, dict) and bool(metadata.get("guardrail"))

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
        if namespace.upper() == "SANDBOX":
            return self._restore_sandbox_schema(dataframe, schema)
        result = dataframe
        for column_name, dtype in schema.items():
            if column_name not in result.columns:
                continue
            if result.schema.get(column_name) == pl.Null:
                result = result.with_columns(pl.col(column_name).cast(dtype))
        return result

    def _restore_sandbox_schema(self, dataframe: pl.DataFrame, schema: dict[str, Any]) -> pl.DataFrame:
        """恢复 SANDBOX 消息表的原生 enum/list schema。"""
        if dataframe is None or schema is None:
            raise ValueError("SANDBOX schema 恢复参数不能为空")
        result = dataframe
        for column_name, dtype in schema.items():
            if column_name not in result.columns:
                continue
            if result.schema.get(column_name) == dtype:
                continue
            try:
                result = result.with_columns(pl.col(column_name).cast(dtype))
            except Exception as exc:
                raise ValueError(f"ToolSandbox SANDBOX schema 恢复失败: column={column_name}") from exc
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
            for column_name, measure_spec in raw_overrides.items():
                column_similarities[str(column_name)] = self._restore_column_similarity(evaluation, measure_spec)
        return column_similarities

    def _restore_column_similarity(self, evaluation: Any, measure_spec: object) -> Any:
        """从字符串或结构化 partial 规格恢复 ToolSandbox 列相似度函数。"""
        if isinstance(measure_spec, str):
            measure = getattr(evaluation, measure_spec, None)
            if not callable(measure):
                raise ValueError(f"不支持的 ToolSandbox column similarity: {measure_spec}")
            return measure
        if isinstance(measure_spec, dict):
            measure_name = measure_spec.get("callable")
            if not isinstance(measure_name, str) or not measure_name:
                raise ValueError(f"不支持的 ToolSandbox column similarity: {measure_spec}")
            measure = getattr(evaluation, measure_name, None)
            if not callable(measure):
                raise ValueError(f"不支持的 ToolSandbox column similarity: {measure_name}")
            raw_keywords = measure_spec.get("partial_keywords", {})
            if raw_keywords is None:
                raw_keywords = {}
            if not isinstance(raw_keywords, dict):
                raise ValueError(f"ToolSandbox partial column similarity 参数非法: {measure_name}")
            return partial(measure, **dict(raw_keywords))
        raise ValueError(f"不支持的 ToolSandbox column similarity: {measure_spec}")

    def _reference_dataframe(self, constraint: Constraint, context: ScoringContext | None) -> pl.DataFrame | None:
        """根据 ScoringContext 查找 ToolSandbox reference snapshot。"""
        metadata = constraint.metadata.get("toolsandbox")
        if not isinstance(metadata, dict):
            return None
        reference_index = metadata.get("reference_milestone_node_index")
        if reference_index is None:
            return None
        if context is None:
            raise ValueError(
                "ToolSandbox reference snapshot 缺失: "
                f"constraint={constraint.constraint_id}, reference_milestone_node_index={reference_index}"
            )
        reference_milestone_id = "initial" if reference_index == -1 else f"m{reference_index}"
        reference_snapshot = context.matched_snapshots.get(reference_milestone_id)
        if reference_snapshot is None:
            raise ValueError(
                "ToolSandbox reference snapshot 缺失: "
                f"constraint={constraint.constraint_id}, reference_milestone_node_index={reference_index}"
            )
        namespace = constraint.namespace or str(metadata.get("database_namespace") or "")
        return self._rows_to_dataframe(reference_snapshot.namespaces.get(namespace), namespace=namespace)
