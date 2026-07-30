from collections.abc import Callable
from functools import partial
import json
from typing import Any
from dynsteer.adapter.base import BaseBenchmarkConstraintScorer
from dynsteer.adapter.toolsandbox.utils.runtime import load_toolsandbox_module
from dynsteer.adapter.toolsandbox.utils.trace import tool_trace_items
from dynsteer.evaluate.semantic import is_semantic_emit_message_constraint
from dynsteer.model import Boundary, Constraint, ConstraintScore, JsonObject, JsonValue, Milestone, MilestoneScore, Operator, ScoringContext, StageStatus, StageGoalSemanticKind, StateSnapshot, Trajectory
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

    def __init__(self, module_loader: Callable[[str], Any] | None=None) -> None:
        self._module_loader = module_loader or load_toolsandbox_module

    def score_milestone(self, milestone: Milestone, boundary: Boundary, trajectory: Trajectory, reference_snapshots: list[StateSnapshot], context: ScoringContext | None=None) -> MilestoneScore:
        if not any((isinstance(constraint.metadata.get("toolsandbox"), dict) for constraint in milestone.constraints)):
            return super().score_milestone(milestone, boundary, trajectory, reference_snapshots, context=context)
        constraint_scores: list[ConstraintScore] = []
        score_product = 1.0
        non_guardrail_count = 0
        hard_pass = True
        for constraint in milestone.constraints:
            source, reference = self.constraint_sources(constraint, boundary, trajectory, reference_snapshots, context=context)
            result = self.score_constraint(constraint, source, reference, context=context)
            constraint_scores.append(result)
            constraint_score = clamp(float(result.score))
            score_product *= constraint_score
            metadata = constraint.metadata.get("toolsandbox")
            if isinstance(metadata, dict) and bool(metadata.get("guardrail")):
                if constraint_score <= 0.0:
                    hard_pass = False
            else:
                non_guardrail_count += 1
                if constraint.hard and (result.missing or constraint_score < constraint.threshold) and (not (is_semantic_emit_message_constraint(constraint) and (not result.missing))):
                    hard_pass = False
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
        missing_count = sum((1 for item in constraint_scores if item.missing))
        return MilestoneScore(milestone_id=milestone.milestone_id, boundary_id=boundary.boundary_id, score=score, status=status, evidence=[line for item in constraint_scores for line in item.evidence], missing_ratio=missing_count / len(constraint_scores), hard_constraints_all_pass=hard_pass, constraint_scores=constraint_scores)

    def score_custom_constraint(self, constraint: Constraint, source: object, reference_source: object | None, actual: JsonValue, reference_value: JsonValue, context: ScoringContext | None=None) -> ConstraintScore:
        metadata = constraint.metadata.get("toolsandbox")
        if not isinstance(metadata, dict):
            return super().score_custom_constraint(constraint, source, reference_source, actual, reference_value, context=context)
        if constraint.operator != Operator.CUSTOM:
            return super().score_constraint(constraint, source, reference_source, context=context)
        measure_name = str(metadata.get("snapshot_constraint") or "")
        try:
            score, reference_summary = self._score_toolsandbox_snapshot_constraint(measure_name=measure_name, constraint=constraint, actual=actual, context=context)
        except Exception as exc:
            return self._custom_constraint_failure_score(constraint, actual, exc, context)
        except BaseException as exc:
            exc_type = exc.__class__
            if str(getattr(exc_type, "__module__", "")) != "pyo3_runtime" and str(getattr(exc_type, "__name__", "")) != "PanicException":
                raise
            return self._custom_constraint_failure_score(constraint, actual, exc, context)
        return ConstraintScore(
            constraint_id=constraint.constraint_id,
            score=score,
            missing=actual is None,
            evidence=[
                f"ToolSandbox custom constraint {constraint.constraint_id} 得分 {score:.3f} ({measure_name})",
                *self._reference_evidence(reference_summary),
            ],
            actual=actual,
        )

    def _custom_constraint_failure_score(self, constraint: Constraint, actual: JsonValue, exc: BaseException, context: ScoringContext | None=None) -> ConstraintScore:
        exc_type = f"{exc.__class__.__module__}.{exc.__class__.__name__}"
        reference_summary = self._reference_summary(constraint, context)
        return ConstraintScore(
            constraint_id=constraint.constraint_id,
            score=0.0,
            missing=actual is None,
            evidence=[
                f"ToolSandbox custom constraint {constraint.constraint_id} 评分失败: {exc_type}: {exc}",
                *self._reference_evidence(reference_summary),
            ],
            actual=actual,
        )

    def _score_toolsandbox_snapshot_constraint(self, measure_name: str, constraint: Constraint, actual: JsonValue, context: ScoringContext | None) -> tuple[float, JsonObject | None]:
        if not measure_name:
            raise ValueError("缺少 snapshot_constraint")
        evaluation = self._module_loader("tool_sandbox.common.evaluation")
        measure = getattr(evaluation, measure_name, None)
        if not callable(measure):
            raise ValueError(f"不支持的 ToolSandbox snapshot_constraint: {measure_name}")
        metadata = constraint.metadata.get("toolsandbox")
        namespace = constraint.namespace or (str(metadata.get("database_namespace") or "") if isinstance(metadata, dict) else "")
        snapshot = self._rows_to_dataframe(actual, namespace=namespace)
        column_similarities = self._column_similarities(evaluation, constraint)
        reference_snapshot, reference_summary = self._reference_dataframe(constraint, context)
        target, target_source = self._resolved_target_dataframe(constraint=constraint, namespace=namespace, reference_snapshot=reference_snapshot)
        if isinstance(reference_summary, dict):
            reference_summary["target_source"] = target_source
        kwargs = self._snapshot_constraint_kwargs(constraint)
        score = float(measure(snapshot=snapshot, target_dataframe=target, column_similarities=column_similarities, reference_snapshot=reference_snapshot, **kwargs))
        return (score, reference_summary)

    def _resolved_target_dataframe(self, constraint: Constraint, namespace: str, reference_snapshot: pl.DataFrame | None) -> tuple[pl.DataFrame, str]:
        """解析 ToolSandbox snapshot constraint 的目标数据源。

        入参：
            constraint: 当前 ToolSandbox 结构化约束。
            namespace: 当前约束对应的 database namespace。
            reference_snapshot: 从 runtime scoring context 解析出的参考快照。
        输出：
            target dataframe 与来源标签。
        """
        semantics = constraint.stage_goal_semantics if isinstance(constraint.stage_goal_semantics, dict) else {}
        if semantics.get("kind") == StageGoalSemanticKind.PRESERVE_STATE.value:
            if reference_snapshot is None:
                raise ValueError(f"preserve_state 缺少 reference snapshot: constraint={constraint.constraint_id}")
            return (reference_snapshot, "reference_snapshot")
        return (self._rows_to_dataframe(constraint.expected, namespace=namespace, target=True), "constraint.expected")

    def _rows_to_dataframe(self, value: JsonValue, namespace: str | None=None, target: bool=False) -> pl.DataFrame:
        rows: JsonValue
        if isinstance(value, dict) and isinstance(value.get("rows"), list):
            rows = value["rows"]
        else:
            rows = value
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise ValueError("ToolSandbox snapshot rows 必须是 list")
        if target and str(namespace or "").upper() == "SANDBOX":
            rows = [{**row, "tool_trace": self._serialize_target_tool_trace(row["tool_trace"])} if isinstance(row, dict) and "tool_trace" in row else row for row in rows]
        return self._restore_namespace_schema(pl.DataFrame(rows), namespace, target=target)

    def _serialize_target_tool_trace(self, value: JsonValue) -> JsonValue:
        if value is None or isinstance(value, str):
            return value
        traces = tool_trace_items(value)
        if len(traces) == 1:
            return json.dumps(traces[0], ensure_ascii=False)
        return json.dumps(traces or value, ensure_ascii=False)

    def _restore_namespace_schema(self, dataframe: pl.DataFrame, namespace: str | None, target: bool=False) -> pl.DataFrame:
        if dataframe is None or not namespace:
            return dataframe
        schema = self._namespace_schema(namespace)
        if not schema:
            return dataframe
        result = dataframe
        for column_name, dtype in schema.items():
            if column_name not in result.columns:
                continue
            if target and column_name == "tool_trace":
                continue
            if result.schema.get(column_name) == dtype:
                continue
            if namespace.upper() != "SANDBOX" and result.schema.get(column_name) != pl.Null:
                continue
            try:
                result = result.with_columns(pl.col(column_name).cast(dtype))
            except Exception as exc:
                raise ValueError(f"ToolSandbox {namespace.upper()} schema 恢复失败: column={column_name}") from exc
        return result

    def _namespace_schema(self, namespace: str) -> dict[str, Any]:
        normalized = namespace.upper()
        try:
            execution_context = self._module_loader("tool_sandbox.common.execution_context")
            schemas = getattr(getattr(execution_context, "ExecutionContext"), "dbs_schemas", {})
            for key, value in dict(schemas).items():
                if str(key).upper().endswith(normalized):
                    return dict(value)
        except (AttributeError, ModuleNotFoundError, TypeError, ValueError):
            return _FALLBACK_TOOLSANDBOX_SCHEMAS.get(normalized, {})
        return _FALLBACK_TOOLSANDBOX_SCHEMAS.get(normalized, {})

    def _snapshot_constraint_kwargs(self, constraint: Constraint) -> dict[str, Any]:
        metadata = constraint.metadata.get("toolsandbox")
        if not isinstance(metadata, dict):
            return {}
        raw_kwargs = metadata.get("snapshot_constraint_kwargs")
        if not isinstance(raw_kwargs, dict):
            return {}
        kwargs: dict[str, Any] = {}
        for key, value in raw_kwargs.items():
            if key == "extractor" and isinstance(value, str):
                extractors = self._module_loader("tool_sandbox.common.tool_trace_extractors")
                extractor = getattr(extractors, value, None)
                if not callable(extractor):
                    raise ValueError(f"不支持的 ToolSandbox extractor: {value}")
                kwargs[key] = extractor
            else:
                kwargs[key] = value
        return kwargs

    def _column_similarities(self, evaluation: Any, constraint: Constraint) -> dict[str, Any]:
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

    def _reference_dataframe(self, constraint: Constraint, context: ScoringContext | None) -> tuple[pl.DataFrame | None, JsonObject | None]:
        metadata = constraint.metadata.get("toolsandbox")
        if not isinstance(metadata, dict):
            return (None, None)
        reference_index = metadata.get("reference_milestone_node_index")
        if reference_index is None:
            return (None, None)
        error = f"ToolSandbox reference snapshot 缺失: constraint={constraint.constraint_id}, reference_milestone_node_index={reference_index}"
        if context is None:
            raise ValueError(error)
        reference_milestone_id = "initial" if reference_index == -1 else f"m{reference_index}"
        reference_snapshot = context.matched_snapshots.get(reference_milestone_id)
        if reference_snapshot is None:
            raise ValueError(error)
        namespace = constraint.namespace or str(metadata.get("database_namespace") or "")
        rows = reference_snapshot.namespaces.get(namespace)
        row_list = rows if isinstance(rows, list) else []
        columns = sorted({str(column) for row in row_list if isinstance(row, dict) for column in row})
        summary = {
            "reference_snapshot_id": reference_snapshot.snapshot_id,
            "reference_milestone_id": reference_milestone_id,
            "namespace": namespace,
            "row_count": len(row_list),
            "columns": columns,
            "target_source": "constraint.expected",
        }
        return (self._rows_to_dataframe(rows, namespace=namespace), summary)

    def _reference_summary(self, constraint: Constraint, context: ScoringContext | None) -> JsonObject | None:
        try:
            _, summary = self._reference_dataframe(constraint, context)
        except Exception as exc:
            metadata = constraint.metadata.get("toolsandbox")
            reference_index = metadata.get("reference_milestone_node_index") if isinstance(metadata, dict) else None
            if reference_index is None:
                return None
            return {"error": str(exc), "reference_milestone_node_index": reference_index}
        return summary

    def _reference_evidence(self, summary: JsonObject | None) -> list[str]:
        if not isinstance(summary, dict):
            return []
        if summary.get("error"):
            return [f"ToolSandbox reference snapshot 诊断: error={summary.get('error')}"]
        columns = summary.get("columns")
        column_text = ",".join((str(column) for column in columns[:8])) if isinstance(columns, list) else ""
        if isinstance(columns, list) and len(columns) > 8:
            column_text = f"{column_text},..."
        return [
            (
                "ToolSandbox reference snapshot 诊断: "
                f"id={summary.get('reference_snapshot_id')}, "
                f"milestone={summary.get('reference_milestone_id')}, "
                f"namespace={summary.get('namespace')}, "
                f"rows={summary.get('row_count')}, "
                f"target_source={summary.get('target_source')}, "
                f"columns={column_text}"
            )
        ]
