from __future__ import annotations

import importlib
import json

import pytest

from dynsteer.judges import CheapJudge, ExpensiveJudge, StandardJudge
from dynsteer.judges.base import LLMJudgeConfigurationError, LLMJudgeResponseError
from dynsteer.language import TaskLanguage
from dynsteer.llm.base import BaseLLM, LLMMessage
from dynsteer.model import (
    Dimension,
    EvaluationLevel,
    MilestoneScore,
    StageInterval,
    StageStatus,
    TaskCase,
    Trajectory,
)


class FakeLLM(BaseLLM):
    """记录调用并返回预设响应的 fake BaseLLM。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.messages: list[list[LLMMessage]] = []
        self.infer_params: list[dict[str, object]] = []

    def chat(self, messages: list[LLMMessage], **infer_params: object) -> str:
        self.messages.append(list(messages))
        self.infer_params.append(dict(infer_params))
        return self._responses.pop(0)

    def _create_client(self) -> object:
        return object()

    def _get_response_from_client(
        self,
        messages: list[LLMMessage],
        request_params: dict[str, object],
    ) -> object:
        return None

    def _response_text(self, response: object) -> str:
        return ""


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


def _task_case_with_language(language: str) -> TaskCase:
    return TaskCase(task_id="task-1", task_description="测试任务", metadata={"language": language})


def _trajectory() -> Trajectory:
    return Trajectory(run_id="run-1", task_id="task-1", steps=[])


def _weights() -> dict[Dimension, float]:
    return {dimension: 1 / len(Dimension) for dimension in Dimension}


def test_standard_judge_parses_single_json_response() -> None:
    llm = FakeLLM([_valid_response()])
    judge = StandardJudge(llm=llm)

    result = judge.evaluate_stage(_interval(), _task_case(), _trajectory(), _weights())

    assert result.evaluator_level == EvaluationLevel.STANDARD
    assert result.status == StageStatus.PASS
    assert result.stage_score == pytest.approx(0.82)
    assert len(llm.messages) == 1
    assert llm.messages[0][0].role == "system"
    assert llm.messages[0][-1].role == "user"


def test_prompt_template_defaults_to_english_and_renders_task_language() -> None:
    prompt_module = importlib.import_module("dynsteer.judges.prompt")
    template = prompt_module.PromptTemplate(en="English prompt for {name}", zh="中文提示 {name}")

    assert template.supported_languages == ["en", "zh"]
    assert template.render(name="judge") == "English prompt for judge"
    assert template.render(language=TaskLanguage.CHINESE, name="judge") == "中文提示 judge"


def test_safe_format_preserves_json_braces_and_unknown_placeholders() -> None:
    prompt_module = importlib.import_module("dynsteer.judges.prompt")

    rendered = prompt_module._safe_format(
        'Return JSON: {{"status": "pass", "value": "{value}"}} and keep {missing}',
        value="ok",
    )

    assert rendered == 'Return JSON: {"status": "pass", "value": "ok"} and keep {missing}'


def test_standard_prompt_contains_rubric_schema_and_evidence_rules() -> None:
    llm = FakeLLM([_valid_response()])
    judge = StandardJudge(llm=llm)

    judge.evaluate_stage(_interval(), _task_case_with_language("en"), _trajectory(), _weights())

    prompt = llm.messages[0][-1].content
    assert "Standard Judge" in prompt
    assert "Evaluation objective" in prompt
    assert "Evidence rules" in prompt
    assert "dimension_scores" in prompt
    assert "progress" in prompt
    assert not prompt.lstrip().startswith("{")


def test_standard_prompt_uses_chinese_output_schema_for_chinese_task() -> None:
    llm = FakeLLM([_valid_response()])
    judge = StandardJudge(llm=llm)

    judge.evaluate_stage(_interval(), _task_case_with_language("zhongwen"), _trajectory(), _weights())

    prompt = llm.messages[0][-1].content
    context = json.loads(prompt.rsplit("Context:\n", 1)[1])
    required_output = context["required_output"]
    assert required_output["dimension_scores"].startswith("dict[str,float]，覆盖")
    assert required_output["stage_score"] == "0 到 1 之间的浮点数"
    assert "dict[str,float] covering" not in required_output["dimension_scores"]


def test_llm_judge_system_prompt_uses_task_language() -> None:
    llm = FakeLLM([_valid_response()])
    judge = StandardJudge(llm=llm)

    judge.evaluate_stage(_interval(), _task_case_with_language("english"), _trajectory(), _weights())

    assert "DynSTEER LLM-as-a-Judge" in llm.messages[0][0].content
    assert "only output JSON" in llm.messages[0][0].content


def test_standard_judge_rejects_invalid_json_response() -> None:
    judge = StandardJudge(llm=FakeLLM(["not-json"]))

    with pytest.raises(LLMJudgeResponseError):
        judge.evaluate_stage(_interval(), _task_case(), _trajectory(), _weights())


def test_expensive_judge_records_pass_metadata() -> None:
    llm = FakeLLM([
        _valid_response(0.7),
        _valid_response(0.8),
        _valid_response(0.9),
        _valid_response(0.6),
        _valid_response(0.86),
    ])
    judge = ExpensiveJudge(llm=llm, expensive_passes=3)

    result = judge.evaluate_stage(_interval(), _task_case(), _trajectory(), _weights())

    assert result.evaluator_level == EvaluationLevel.EXPENSIVE
    assert result.stage_score == pytest.approx(0.86)
    assert len(result.metadata["judge_passes"]) == 4
    assert [item["prompt_type"] for item in result.metadata["judge_passes"]] == [
        "focus",
        "focus",
        "focus",
        "risk",
    ]
    # 3 轮聚焦评估 + 1 轮风险复核 + 1 轮汇总裁决
    assert len(llm.messages) == 5


def test_expensive_judge_rejects_invalid_focus_pass_before_adjudication() -> None:
    invalid = _valid_response().replace('"needs_expensive": false', '"needs_expensive": "false"')
    llm = FakeLLM([invalid, _valid_response(0.6), _valid_response(0.86)])
    judge = ExpensiveJudge(llm=llm, expensive_passes=1)

    with pytest.raises(LLMJudgeResponseError, match="needs_expensive"):
        judge.evaluate_stage(_interval(), _task_case(), _trajectory(), _weights())

    assert len(llm.messages) == 1


def test_expensive_prompt_uses_focus_risk_and_adjudication_templates() -> None:
    llm = FakeLLM([_valid_response(0.7), _valid_response(0.8), _valid_response(0.6), _valid_response(0.86)])
    judge = ExpensiveJudge(llm=llm, expensive_passes=2)

    judge.evaluate_stage(_interval(), _task_case_with_language("en"), _trajectory(), _weights())

    prompts = [messages[-1].content for messages in llm.messages]
    assert "Expensive Focus Review" in prompts[0]
    assert "Expensive Focus Review" in prompts[1]
    assert "Expensive Risk Review" in prompts[2]
    assert "Expensive Final Adjudication" in prompts[3]
    assert "previous_passes" in prompts[3]


def test_expensive_judge_rejects_non_positive_passes() -> None:
    with pytest.raises(LLMJudgeConfigurationError):
        ExpensiveJudge(llm=FakeLLM([]), expensive_passes=0)


def test_llm_judge_rejects_non_bool_flags() -> None:
    invalid = _valid_response().replace('"needs_expensive": false', '"needs_expensive": "false"')
    judge = StandardJudge(llm=FakeLLM([invalid]))

    with pytest.raises(LLMJudgeResponseError, match="needs_expensive"):
        judge.evaluate_stage(_interval(), _task_case(), _trajectory(), _weights())


def test_llm_judge_accepts_single_json_fenced_block() -> None:
    judge = StandardJudge(llm=FakeLLM([f"```json\n{_valid_response()}\n```"]))

    result = judge.evaluate_stage(_interval(), _task_case(), _trajectory(), _weights())

    assert result.stage_score == pytest.approx(0.82)


def test_cheap_judge_uses_milestone_score() -> None:
    milestone_score = MilestoneScore(
        milestone_id="m1",
        boundary_id="b0",
        score=0.9,
        status=StageStatus.PASS,
        evidence=["命中 milestone"],
        missing_ratio=0.0,
        hard_constraints_all_pass=True,
    )
    interval = StageInterval(
        "stage:m1",
        "m1",
        0,
        1,
        StageStatus.PASS,
        milestone_score=milestone_score,
        evidence=["命中 milestone"],
    )
    judge = CheapJudge()

    result = judge.evaluate_stage(interval, _task_case(), _trajectory(), _weights())

    assert result.evaluator_level == EvaluationLevel.CHEAP
    assert result.stage_score == pytest.approx(0.9)
    assert result.required_fields_missing_ratio == pytest.approx(0.0)
    assert result.hard_constraints_all_pass is True


def test_cheap_judge_marks_missing_milestone() -> None:
    interval = StageInterval("stage:m2", "m2", 0, 0, StageStatus.MISSING)
    judge = CheapJudge()

    result = judge.evaluate_stage(interval, _task_case(), _trajectory(), _weights())

    assert result.stage_score == pytest.approx(0.0)
    assert "阶段缺少 milestone 匹配" in result.evidence
