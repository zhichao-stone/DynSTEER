from __future__ import annotations

import logging

from dynsteer.log import BufferLogHandler, clear_log_buffer, get_log_buffer


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
