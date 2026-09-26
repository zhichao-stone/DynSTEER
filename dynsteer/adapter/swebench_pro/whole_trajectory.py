from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping, Sequence

from dynsteer.adapter.swebench_pro.adapter import SWEBenchProSample
from dynsteer.adapter.swebench_pro.evaluator import NativeEvaluationResult
from dynsteer.llm import build_llm_from_config
from dynsteer.llm.base import BaseLLM
from dynsteer.model import JsonObject, LLMMessage, TrajectoryStep

AUDITOR_MODEL = "qwen3-max-2026-01-23"
AUDITOR_TEMPERATURE = 0.2


@dataclass(frozen=True)
class WholeTrajectoryEvaluationResult:
    """Outcome-informed holistic audit result on the common 0-100 scale."""

    score: float | None
    available: bool
    failure_type: str | None
    summary: JsonObject
    metrics: JsonObject


class SWEBenchProWholeTrajectoryEvaluator:
    """Audit a complete SWE-bench Pro trajectory as one evaluation unit."""

    def __init__(self, judge_profile: Mapping[str, object]) -> None:
        if not isinstance(judge_profile, Mapping):
            raise ValueError("judge_profile must be a JSON object")
        auditor_profile = {
            **judge_profile,
            "provider": "openai_compatible",
            "model": AUDITOR_MODEL,
            "temperature": AUDITOR_TEMPERATURE,
        }
        self.llm: BaseLLM = build_llm_from_config(auditor_profile)
        if self.llm is None:
            raise ValueError("whole-trajectory auditor is not configured")

    def evaluate(
        self,
        *,
        sample: SWEBenchProSample,
        steps: Sequence[TrajectoryStep],
        patch: str,
        native_result: NativeEvaluationResult,
    ) -> WholeTrajectoryEvaluationResult:
        """Return one holistic score conditioned on the terminal patch outcome."""
        try:
            messages = [
                LLMMessage(role="system", content=_SYSTEM_PROMPT),
                LLMMessage(
                    role="user",
                    content=_user_prompt(sample, steps, patch, native_result),
                ),
            ]
            response = self.llm.chat(
                messages,
                response_format={"type": "json_object"},
                temperature=AUDITOR_TEMPERATURE,
            )
            payload = _parse_payload(response)
            score_100 = _parse_score(payload["score"])
            return WholeTrajectoryEvaluationResult(
                score=score_100 / 100.0,
                available=True,
                failure_type=None,
                summary=payload,
                metrics={
                    "score_100": score_100,
                    "auditor_model": AUDITOR_MODEL,
                    "auditor_temperature": AUDITOR_TEMPERATURE,
                    "terminal_outcome_used": bool(payload.get("terminal_outcome_used")),
                },
            )
        except Exception as exc:
            return WholeTrajectoryEvaluationResult(
                score=None,
                available=False,
                failure_type="whole_trajectory_audit_failed",
                summary={"error": f"{type(exc).__name__}: {exc}"},
                metrics={"auditor_model": AUDITOR_MODEL, "auditor_temperature": AUDITOR_TEMPERATURE},
            )


def _user_prompt(
    sample: SWEBenchProSample,
    steps: Sequence[TrajectoryStep],
    patch: str,
    native_result: NativeEvaluationResult,
) -> str:
    payload = {
        "task": {
            "instance_id": sample.instance_id,
            "problem_statement": sample.problem_statement,
            "repository": sample.repo,
            "base_commit": sample.base_commit,
        },
        "repository_context": {
            "dockerhub_tag": sample.dockerhub_tag,
            "image_probe_context": "Official task image with persistent repository state",
        },
        "trajectory": [_trajectory_item(step) for step in steps],
        "final_patch": patch,
        "terminal_outcome": {
            "official_evaluator_available": native_result.available,
            "resolved": native_result.resolved,
            "native_score": native_result.score,
            "returncode": native_result.returncode,
            "failure_type": native_result.failure_type,
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _trajectory_item(step: TrajectoryStep) -> JsonObject:
    item: JsonObject = {
        "index": step.index,
        "event_type": step.event_type.value,
        "actor": step.actor.value,
        "content": step.content,
    }
    if step.tool_call is not None:
        item["tool_call"] = {"name": step.tool_call.name, "arguments": step.tool_call.arguments}
    if step.tool_result is not None:
        item["tool_result"] = {
            "success": step.tool_result.success,
            "content": step.tool_result.content,
            "exception": step.tool_result.exception,
        }
    if step.cost.latency_ms is not None:
        item["latency_ms"] = step.cost.latency_ms
    return item


def _parse_payload(response: str) -> JsonObject:
    try:
        payload = json.loads(response)
    except json.JSONDecodeError as exc:
        raise ValueError("auditor response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("auditor response must be a JSON object")
    if "score" not in payload or "rationale" not in payload:
        raise ValueError("auditor response lacks score or rationale")
    return payload


def _parse_score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("auditor score must be numeric")
    return min(max(float(value), 0.0), 100.0)


_SYSTEM_PROMPT = (
    "You are the fixed SWE-bench Pro whole-trajectory auditor. "
    "Treat the complete execution as one unit and audit command selection, evidence gathering, reasoning, "
    "patch construction, recovery, and efficiency conditioned on the official terminal patch/test outcome. "
    "Return only compact JSON with keys score, rationale, execution_quality, and terminal_outcome_used. "
    "score must be a number from 0 to 100."
)
