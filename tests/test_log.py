from pathlib import Path

from dynsteer.log import clear_log_buffer, configure_logger, get_log_buffer


def test_configure_logger_writes_buffer_and_file(tmp_path: Path) -> None:
    clear_log_buffer()
    logger = configure_logger(tmp_path)

    logger.info("测试日志", extra={"run_id": "r1"})

    buffer = get_log_buffer()
    assert any(item["message"] == "测试日志" for item in buffer)
    assert any(path.suffix == ".log" for path in tmp_path.iterdir())

