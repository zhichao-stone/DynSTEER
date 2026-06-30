from __future__ import annotations

import json
import logging

from dynsteer.log import (
    BufferLogHandler,
    StructuredLogFormatter,
    TerminalLogFilter,
    TerminalLogFormatter,
    clear_log_buffer,
    configure_logger,
    get_log_buffer,
)


def test_log_buffer_keeps_latest_entries_with_limit(monkeypatch) -> None:
    import dynsteer.log as log_module

    monkeypatch.setattr(log_module, "LOG_BUFFER_LIMIT", 3, raising=False)
    clear_log_buffer()
    handler = BufferLogHandler()
    logger = logging.getLogger("dynsteer.test.buffer")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    for index in range(5):
        logger.info("message-%s", index)

    messages = [entry["message"] for entry in get_log_buffer()]
    assert messages == ["message-2", "message-3", "message-4"]


def test_structured_log_formatter_outputs_json_extra_with_chinese(tmp_path) -> None:
    clear_log_buffer()
    logger = configure_logger(tmp_path)

    logger.warning(
        "evaluator_policy_stop",
        extra={
            "事件": "策略提前终止",
            "case_id": "case-1",
            "pending_required_milestone_ids": ["m3", "m4"],
            "detail": {"诊断": "中文可读"},
            "large": "x" * 500,
        },
    )

    log_file = next(tmp_path.glob("*.log"))
    lines = log_file.read_text(encoding="utf-8").splitlines()
    last_line = lines[-1]
    assert "evaluator_policy_stop" in last_line
    assert '"事件": "策略提前终止"' in last_line
    assert '"pending_required_milestone_ids": ["m3", "m4"]' in last_line
    assert "中文可读" in last_line
    assert "xxx..." in last_line

    entry = get_log_buffer()[-1]
    assert entry["pending_required_milestone_ids"] == ["m3", "m4"]
    assert entry["detail"] == {"诊断": "中文可读"}
    assert isinstance(entry["large"], str)
    assert len(entry["large"]) < 200


def test_structured_log_formatter_skips_logging_builtin_fields() -> None:
    formatter = StructuredLogFormatter("%(message)s")
    record = logging.LogRecord(
        name="dynsteer.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="message",
        args=(),
        exc_info=None,
    )
    record.case_id = "case-1"

    rendered = formatter.format(record)
    extra_json = rendered.split(" ", 1)[1]
    extra = json.loads(extra_json)

    assert extra == {"case_id": "case-1"}


def test_terminal_log_formatter_keeps_only_milestone_summary_fields() -> None:
    formatter = TerminalLogFormatter("%(message)s")
    record = logging.LogRecord(
        name="dynsteer.evaluate.evaluator",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="evaluator_milestone_checkpoint",
        args=(),
        exc_info=None,
    )
    record.case_id = "cellular_off"
    record.milestone_id = "m1"
    record.milestone_score = 0.899
    record.milestone_status = "pass"
    record.stage_score = 0.208
    record.stage_status = "fail"
    record.stage_first_diagnosis = "overall: 目标错位"
    record.stage_first_evidence = "step 18: agent message"
    record.ready_milestone_ids_before_match = ["m1"]

    rendered = formatter.format(record)
    extra = json.loads(rendered.split(" ", 1)[1])

    assert extra == {
        "case_id": "cellular_off",
        "milestone_id": "m1",
        "milestone_score": 0.899,
        "milestone_status": "pass",
        "diagnosis": "overall: 目标错位",
        "evidence": "step 18: agent message",
    }


def test_terminal_log_filter_hides_noisy_runtime_and_judge_records() -> None:
    filter_ = TerminalLogFilter()

    noisy_messages = [
        "evaluator_milestone_match_attempt",
        "evaluator_pending_milestones",
        "standard_judge_input_snapshot",
        "standard_judge_output_snapshot",
        "expensive_judge_input_snapshot",
        "expensive_judge_output_snapshot",
    ]
    for message in noisy_messages:
        record = logging.LogRecord(
            name="dynsteer.judges.standard" if "judge" in message else "dynsteer.evaluate.evaluator",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=message,
            args=(),
            exc_info=None,
        )
        assert not filter_.filter(record)

    visible = logging.LogRecord(
        name="dynsteer.evaluate.evaluator",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="evaluator_milestone_checkpoint",
        args=(),
        exc_info=None,
    )
    assert filter_.filter(visible)
