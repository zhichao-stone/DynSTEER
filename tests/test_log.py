from __future__ import annotations

import json
import logging

from dynsteer.log import BufferLogHandler, StructuredLogFormatter, clear_log_buffer, configure_logger, get_log_buffer


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
