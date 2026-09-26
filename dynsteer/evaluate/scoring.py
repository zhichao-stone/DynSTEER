from dataclasses import asdict, is_dataclass
from difflib import SequenceMatcher
from typing import Any
from dynsteer.graph import START_NODE_ID
from dynsteer.model import Constraint, ConstraintScore, ConstraintTarget, Dimension, EventType, JsonObject, JsonValue, MISSING, Milestone, MilestoneScore, Operator, ScoringContext, StageEvaluationResult, StageStatus, StateSnapshot, Trajectory, TrajectoryStep
from dynsteer.utils import clamp, json_subsumes, read_token

class GeneralScorer:

    def select_value(self, source: JsonValue, selector: str) -> JsonValue:
        if selector is None or selector == "" or source is None:
            return None
        if selector == "$":
            return source
        if not selector.startswith("$."):
            return None
        current: Any = source
        for token in selector[2:].split("."):
            if token == "":
                return None
            current = read_token(current, token)
            if current is MISSING:
                return None
        return current

    def score_operator(self, actual: JsonValue, operator: Operator, expected: JsonValue) -> float:
        if operator == Operator.CUSTOM:
            raise ValueError('Superator. CUSTOM must be handled by score_custom_constraint() or benchmark coorer')
        if operator == Operator.EQUALS:
            return 1.0 if actual == expected else 0.0
        if operator == Operator.CONTAINS:
            if actual is None or expected is None:
                return 0.0
            if isinstance(actual, str):
                return 1.0 if str(expected) in actual else 0.0
            if isinstance(actual, list):
                return 1.0 if expected in actual else 0.0
            if isinstance(actual, dict) and isinstance(expected, str):
                return 1.0 if expected in actual else 0.0
            return 0.0
        if operator == Operator.ONE_OF:
            return 1.0 if isinstance(expected, list) and actual in expected else 0.0
        if operator == Operator.JSON_SUBSUMES:
            return 1.0 if json_subsumes(actual, expected) else 0.0
        if operator == Operator.FUZZY_MATCH:
            if actual is None or expected is None:
                return 0.0
            return clamp(SequenceMatcher(None, str(actual), str(expected)).ratio())
        if operator == Operator.ADDED:
            return 1.0 if actual is not None and expected is None else 0.0
        if operator == Operator.UPDATED:
            return 1.0 if actual is not None and expected is not None and (actual != expected) else 0.0
        if operator == Operator.REMOVED:
            return 1.0 if actual is None and expected is not None else 0.0
        if operator == Operator.UNCHANGED_SINCE:
            return 1.0 if actual == expected else 0.0
        return 0.0

    def score_custom_constraint(self, constraint: Constraint, source: object, reference_source: object | None, actual: JsonValue, reference_value: JsonValue, context: ScoringContext | None=None) -> ConstraintScore:
        evidence = f"Constraint {constraint.constraint_id} uses Operator.CUSTOM, but scorer {self.__class__.__name__} does not support evaluator_hint={constraint.evaluator_hint}"
        return ConstraintScore(constraint_id=constraint.constraint_id, score=0.0, missing=actual is None, evidence=[evidence], actual=actual)

    def score_constraint(self, constraint: Constraint, source: object, reference_source: object | None=None, context: ScoringContext | None=None) -> ConstraintScore:
        current_source = self._resolve_source(constraint, source)
        actual = self.select_value(current_source, constraint.selector)
        missing = actual is None
        reference_value = constraint.expected
        if constraint.expected_template is not None:
            reference_value, binding_evidence = self._resolve_expected_template(
                constraint.expected_template, context
            )
            if reference_value is MISSING:
                return ConstraintScore(
                    constraint_id=constraint.constraint_id,
                    score=0.0,
                    missing=True,
                    evidence=[binding_evidence],
                    actual=actual,
                )
        if constraint.operator in {Operator.ADDED, Operator.UPDATED, Operator.REMOVED, Operator.UNCHANGED_SINCE}:
            if constraint.reference_milestone_id is not None and reference_source is None:
                return ConstraintScore(constraint_id=constraint.constraint_id, score=0.0, missing=True, evidence=[f"Reference milestone was not matched: {constraint.reference_milestone_id}"], actual=actual)
            reference_data = self._resolve_source(constraint, reference_source)
            reference_value = self.select_value(reference_data, constraint.selector)
        if constraint.operator == Operator.CUSTOM:
            return self.score_custom_constraint(constraint, source, reference_source, actual, reference_value, context=context)
        score = 0.0 if missing and constraint.operator != Operator.REMOVED else self.score_operator(actual, constraint.operator, reference_value)
        evidence = [f"Constraint {constraint.constraint_id} score: {score:.3f}"]
        if missing:
            evidence.append(f"Selector did not match: {constraint.selector}")
        return ConstraintScore(constraint_id=constraint.constraint_id, score=score, missing=missing, evidence=evidence, actual=actual)

    def _resolve_expected_template(
        self, template: JsonValue, context: ScoringContext | None
    ) -> tuple[JsonValue | object, str]:
        """parsing binting while running in constraining expected_templatate."""
        if isinstance(template, dict) and set(template) == {"$binding"}:
            binding = template["$binding"]
            return self._resolve_binding(binding, context) if isinstance(binding, dict) else (MISSING, 'Binding must be an object')
        if isinstance(template, dict):
            result: JsonObject = {}
            for key, value in template.items():
                resolved, evidence = self._resolve_expected_template(value, context)
                if resolved is MISSING:
                    return MISSING, evidence
                result[key] = resolved
            return result, 'Templates parsed successfully'
        if isinstance(template, list):
            result_list: list[JsonValue] = []
            for value in template:
                resolved, evidence = self._resolve_expected_template(value, context)
                if resolved is MISSING:
                    return MISSING, evidence
                result_list.append(resolved)
            return result_list, 'Templates parsed successfully'
        return template, 'Static Template Values'

    def _resolve_binding(
        self, binding: JsonObject, context: ScoringContext | None
    ) -> tuple[JsonValue | object, str]:
        """parsing one/all binting from a true tool that matches the producer."""
        milestone_id = binding.get("source_milestone_id")
        selector = binding.get("selector")
        cardinality = binding.get("cardinality")
        if not isinstance(milestone_id, str) or not isinstance(selector, str) or cardinality not in {"one", "all"}:
            return MISSING, "Invalid binding fields"
        result, evidence = self._producer_tool_result(milestone_id, context)
        if result is MISSING:
            return MISSING, evidence
        if cardinality == "all" and isinstance(result, list):
            values = [self.select_value(item, selector) for item in result]
            if any(item is None for item in values):
                return MISSING, f"Producer {milestone_id} selector did not match."
            return values, f"Resolved all results from producer {milestone_id}"
        if cardinality == "all":
            return MISSING, f"Producer {milestone_id} requires an array result for all cardinality"
        if isinstance(result, list):
            if len(result) != 1:
                return MISSING, f"Producer {milestone_id} with one cardinality is not unique"
            result = result[0]
        selected = self.select_value(result, selector)
        if selected is None:
            return MISSING, f"Producer {milestone_id} selector did not match: {selector}"
        return selected, f"Resolved producer {milestone_id}"

    def _producer_tool_result(
        self, source_milestone_id: str, context: ScoringContext | None
    ) -> tuple[JsonValue | object, str]:
        """The result of a successful tool to locate a producer based on the call ID priority and the only adjacent result."""
        if context is None or context.task_case is None or context.trajectory is None:
            return MISSING, 'Missing tab_case or trajectory'
        boundary = context.matched_step_indexes.get(source_milestone_id)
        graph = context.task_case.milestone_graph
        if boundary is None or graph is None:
            return MISSING, f"Producer milestone does not match:{source_milestone_id}"
        milestone = next((item for item in graph.nodes if item.milestone_id == source_milestone_id), None)
        if milestone is None:
            return MISSING, f"Producer milestone does not exist:{source_milestone_id}"
        tool_name = next((constraint.expected for constraint in milestone.constraints
                          if constraint.target == ConstraintTarget.TOOL_CALL and constraint.selector == "$.name"), None)
        if not isinstance(tool_name, str):
            return MISSING, f"Cannot uniquely attribute a producer tool result: producer={source_milestone_id}"
        topology = graph.topology
        anchor_id = topology.stage_anchor_by_id.get(source_milestone_id, "__start__") if topology is not None else "__start__"
        stage_start = context.trajectory.first_step_index - 1 if anchor_id == "__start__" else context.matched_step_indexes.get(anchor_id, context.trajectory.first_step_index - 1)
        calls = [step for step in context.trajectory.steps if stage_start < step.index <= boundary and step.tool_call is not None and step.tool_call.name == tool_name]
        if not calls:
            return MISSING, f"Producer tool call not found:{tool_name}"
        call = calls[-1]
        call_id = call.raw.get("openai_tool_call_id")
        candidates = [step for step in context.trajectory.steps if call.index < step.index <= boundary and step.tool_result is not None and step.tool_result.success]
        if isinstance(call_id, str) and call_id:
            candidates = [step for step in candidates if step.raw.get("openai_tool_call_id") == call_id]
        else:
            candidates = [step for step in candidates if step.index == call.index + 1]
        if len(candidates) != 1:
            return MISSING, f"producer tool result cannot be uniquely attributed: {tool_name}"
        return candidates[0].tool_result.content, f"Located producer tool result: {tool_name}"

    def score_milestone(self, milestone: Milestone, scoring_step: TrajectoryStep, trajectory: Trajectory, context: ScoringContext) -> MilestoneScore:
        boundary_id = f"runtime:b{scoring_step.index}"
        if len(milestone.constraints) == 0:
            return MilestoneScore(milestone_id=milestone.milestone_id, boundary_id=boundary_id, score=0.0, status=StageStatus.INVALID, evidence=['milestone missing contacts'], missing_ratio=1.0, hard_constraints_all_pass=False)
        constraint_scores: list[ConstraintScore] = []
        stage_start_index = self._milestone_start_index(milestone, trajectory, context)
        for constraint in milestone.constraints:
            source, reference = self.constraint_sources(constraint, scoring_step, trajectory, context, stage_start_index=stage_start_index)
            result = self.score_constraint(constraint, source, reference, context=context)
            constraint_scores.append(result)
        score, hard_pass = self._aggregate_constraints(milestone, constraint_scores)
        missing_count = sum((1 for item in constraint_scores if item.missing))
        missing_ratio = missing_count / len(constraint_scores)
        threshold = milestone.pass_threshold if milestone.pass_threshold is not None else 0.8
        if not hard_pass:
            status = StageStatus.FAIL
        elif score >= threshold:
            status = StageStatus.PASS
        elif score >= 0.6:
            status = StageStatus.WARN
        else:
            status = StageStatus.FAIL
        evidence = [line for item in constraint_scores for line in item.evidence]
        return MilestoneScore(milestone_id=milestone.milestone_id, boundary_id=boundary_id, score=clamp(score), status=status, evidence=evidence, missing_ratio=missing_ratio, hard_constraints_all_pass=hard_pass, constraint_scores=constraint_scores)

    def _aggregate_constraints(self, milestone: Milestone, scores: list[ConstraintScore]) -> tuple[float, bool]:
        """Returns whether all the bound weighted scores and hard points are passed."""
        weights = [max(float(constraint.weight), 0.0) for constraint in milestone.constraints]
        weight_sum = sum(weights)
        hard_pass = all(
            not constraint.hard or score.score >= constraint.threshold
            for constraint, score in zip(milestone.constraints, scores, strict=True)
        )
        weighted_sum = sum(score.score * weight for score, weight in zip(scores, weights, strict=True))
        return (weighted_sum / weight_sum if hard_pass and weight_sum > 0 else 0.0, hard_pass)

    def _resolve_source(self, constraint: Constraint, source: object | None) -> JsonValue:
        if source is None:
            return None
        if isinstance(source, StateSnapshot):
            selected_namespace = constraint.namespace or "default"
            return source.namespaces.get(selected_namespace, source.namespaces)
        if isinstance(source, TrajectoryStep):
            data: dict[str, JsonValue] = {
                "step_id": source.step_id,
                "index": source.index,
                "actor": source.actor.value,
                "recipient": source.recipient.value if source.recipient is not None else None,
                "event_type": source.event_type.value,
                "timestamp": source.timestamp,
                "content": source.content,
                "state_delta_refs": list(source.state_delta_refs),
            }
            if source.tool_call is not None:
                data["tool_call"] = asdict(source.tool_call)
            if source.tool_result is not None:
                data["tool_result"] = asdict(source.tool_result)
            data.update(source.raw)
            if constraint.target == ConstraintTarget.TOOL_CALL:
                return data.get("tool_call")
            if constraint.target == ConstraintTarget.TOOL_RESULT:
                return data.get("tool_result")
            return data
        if is_dataclass(source):
            return asdict(source)
        if isinstance(source, dict):
            return source
        return None

    def constraint_sources(self, constraint: Constraint, scoring_step: TrajectoryStep, trajectory: Trajectory, context: ScoringContext, stage_start_index: int | None=None) -> tuple[object, StateSnapshot | None]:
        """Parsing scores on bound target source and reference."""
        if constraint.target == ConstraintTarget.STATE_SNAPSHOT:
            source: object = trajectory.snapshot_at_or_before(scoring_step.index)
        elif constraint.target == ConstraintTarget.METRIC:
            source = trajectory.metrics
        elif constraint.target in {ConstraintTarget.TOOL_CALL, ConstraintTarget.TOOL_RESULT}:
            source = self._interval_step_source(constraint, scoring_step, trajectory, stage_start_index)
        else:
            source = scoring_step
        if constraint.reference_milestone_id is None:
            return (source, None)
        return (source, context.matched_snapshots.get(constraint.reference_milestone_id))

    def _interval_step_source(self, constraint: Constraint, scoring_step: TrajectoryStep, trajectory: Trajectory, stage_start_index: int | None) -> TrajectoryStep | None:
        """Looking for the most recent target step in the current stage of milestone."""
        start_index = stage_start_index if stage_start_index is not None else trajectory.first_step_index - 1
        if scoring_step.index <= start_index:
            return scoring_step
        for step in reversed(trajectory.get_interval(start_index, scoring_step.index)):
            if constraint.target == ConstraintTarget.TOOL_CALL and (step.tool_call is not None or step.event_type == EventType.TOOL_CALL):
                return step
            if constraint.target == ConstraintTarget.TOOL_RESULT and (step.tool_result is not None or step.event_type == EventType.TOOL_RESULT):
                return step
        return scoring_step

    def _milestone_start_index(self, milestone: Milestone, trajectory: Trajectory, context: ScoringContext) -> int:
        graph = context.task_case.milestone_graph
        topology = graph.topology
        anchor_id = topology.stage_anchor_by_id[milestone.milestone_id]
        return trajectory.first_step_index - 1 if anchor_id == START_NODE_ID else context.matched_step_indexes.get(anchor_id, trajectory.first_step_index - 1)

def stage_score_from_dimensions(dimension_scores: dict[Dimension, float], weights: dict[Dimension, float]) -> float:
    """Combining the points for the calculation stage based on dimensions and dynamic weights."""
    if not dimension_scores:
        return 0.0
    weighted_score = 0.0
    total_weight = 0.0
    present_dimensions = [dimension for dimension in Dimension if dimension in dimension_scores]
    for dimension in present_dimensions:
        score = dimension_scores.get(dimension)
        weight = float(weights.get(dimension, 0.0))
        if weight > 0.0:
            weighted_score += clamp(float(score)) * weight
            total_weight += weight
    if total_weight == 0.0:
        return sum((clamp(float(dimension_scores[dimension])) for dimension in present_dimensions)) / len(present_dimensions)
    return weighted_score / total_weight

def overall_score(stage_reports: list[StageEvaluationResult], minefield_score: float) -> float:
    if not stage_reports:
        return 0.0
    raw = sum((stage.stage_score for stage in stage_reports)) / len(stage_reports)
    return clamp(raw * (1.0 - clamp(minefield_score)))

def minefield_penalty_score(matches: list[JsonObject]) -> float:
    """Calculates the total final deduction rate based on the minefield hit record."""
    max_penalty = 0.0
    for match in matches:
        if not isinstance(match, dict):
            continue
        raw_score = match.get("score", 0.0)
        score = float(raw_score) if isinstance(raw_score, int | float) else 0.0
        penalty = match.get("penalty")
        if isinstance(penalty, dict) and penalty.get("mode") == "fixed":
            raw_value = penalty.get("value", 0.0)
            value = float(raw_value) if isinstance(raw_value, int | float) else 0.0
            max_penalty = max(max_penalty, score * value)
        else:
            max_penalty = max(max_penalty, score)
    return clamp(max_penalty)
