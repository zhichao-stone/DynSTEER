from __future__ import annotations

import pytest

from dynsteer.judges.llm import LLMJudge, LLMJudgeResponseError
from dynsteer.model import (
    Dimension,
    EvaluationLevel,
    StageInterval,
    StageStatus,
    TaskCase,
    Trajectory,
)


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)


class _Response:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


class FakeChatCompletions:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def create(self, **kwargs: object) -> _Response:
        messages = kwargs["messages"]
        assert isinstance(messages, list)
        self.prompts.append(str(messages[-1]["content"]))
        return _Response(self.responses.pop(0))


class FakeClient:
    def __init__(self, responses: list[str]) -> None:
        self.chat = type("Chat", (), {"completions": FakeChatCompletions(responses)})()


def _valid_response(score: float = 0.82) -> str:
    return (
        "{"
        f'"stage_score": {score},'
        '"status": "pass",'
        '"judge_confidence": 0.91,'
        '"dimension_scores": {'
        '"progress": 0.8, "state_consistency": 0.8, "tool_quality": 0.8,'
        '"efficiency": 0.8, "safety": 0.8, "interaction_quality": 0.8, "recovery": 0.8'
        "},"
        '"evidence": ["证据"],'
        '"diagnosis": ["诊断"],'
        '"needs_expensive": false,'
        '"first_error_location_required": false'
        "}"
    )


def _interval() -> StageInterval:
    return StageInterval("stage:m1", "m1", 0, 0, StageStatus.PASS)


def _task_case() -> TaskCase:
    return TaskCase(task_id="task-1", task_description="测试任务")


def _trajectory() -> Trajectory:
    return Trajectory(run_id="run-1", task_id="task-1", steps=[])


def _weights() -> dict[Dimension, float]:
    return {dimension: 1 / len(Dimension) for dimension in Dimension}


def test_llm_judge_parses_standard_json_response_without_secret_in_prompt() -> None:
    client = FakeClient([_valid_response()])
    judge = LLMJudge(
        model="judge-model",
        api_key="secret-token",
        base_url="https://example.invalid/v1",
        client=client,
    )

    result = judge.evaluate_stage(_interval(), _task_case(), _trajectory(), EvaluationLevel.STANDARD, _weights())

    assert result.evaluator_level == EvaluationLevel.STANDARD
    assert result.status == StageStatus.PASS
    assert result.stage_score == pytest.approx(0.82)
    assert "secret-token" not in client.chat.completions.prompts[0]


def test_llm_judge_rejects_invalid_json_response() -> None:
    judge = LLMJudge(model="judge-model", api_key="secret-token", client=FakeClient(["not-json"]))

    with pytest.raises(LLMJudgeResponseError):
        judge.evaluate_stage(_interval(), _task_case(), _trajectory(), EvaluationLevel.STANDARD, _weights())


def test_expensive_judge_records_pass_metadata() -> None:
    client = FakeClient([_valid_response(0.7), _valid_response(0.8), _valid_response(0.9), _valid_response(0.86)])
    judge = LLMJudge(model="judge-model", api_key="secret-token", client=client, expensive_passes=3)

    result = judge.evaluate_stage(_interval(), _task_case(), _trajectory(), EvaluationLevel.EXPENSIVE, _weights())

    assert result.evaluator_level == EvaluationLevel.EXPENSIVE
    assert result.stage_score == pytest.approx(0.86)
    assert len(result.metadata["judge_passes"]) == 3
