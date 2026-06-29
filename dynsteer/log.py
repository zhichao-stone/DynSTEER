from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from dynsteer.model import JsonObject

_LOG_BUFFER: list[JsonObject] = []
LOG_BUFFER_LIMIT = 2000
_LOGGER_LOCK = threading.RLock()
_LOG_BUFFER_LOCK = threading.RLock()


class BufferLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        """将日志记录写入内存缓冲区。

        Args:
            record: logging 产生的日志记录。

        Returns:
            None。
        """
        try:
            entry: JsonObject = {
                "time": datetime.fromtimestamp(record.created).isoformat(timespec="seconds"),
                "level": record.levelname,
                "message": record.getMessage(),
                "module": record.module,
            }
            for key, value in record.__dict__.items():
                if key.startswith("_") or key in {
                    "args",
                    "asctime",
                    "created",
                    "exc_info",
                    "exc_text",
                    "filename",
                    "funcName",
                    "levelname",
                    "levelno",
                    "lineno",
                    "module",
                    "msecs",
                    "message",
                    "msg",
                    "name",
                    "pathname",
                    "process",
                    "processName",
                    "relativeCreated",
                    "stack_info",
                    "thread",
                    "threadName",
                }:
                    continue
                if isinstance(value, (str, int, float, bool)) or value is None:
                    entry[key] = value
            with _LOG_BUFFER_LOCK:
                _LOG_BUFFER.append(entry)
                overflow = len(_LOG_BUFFER) - LOG_BUFFER_LIMIT
                if overflow > 0:
                    del _LOG_BUFFER[:overflow]
        except Exception:
            self.handleError(record)


def configure_logger(log_dir: str | Path) -> logging.Logger:
    """配置 DynSTEER 结构化中文日志。

    Args:
        log_dir: 日志文件目录。

    Returns:
        已配置的 logger。
    """
    if log_dir is None:
        raise ValueError("log_dir 不能为空")
    with _LOGGER_LOCK:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("dynsteer")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        file_path = directory / f"{datetime.now().date().isoformat()}.log"
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        buffer_handler = BufferLogHandler()
        buffer_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        logger.addHandler(file_handler)
        logger.addHandler(buffer_handler)
        logger.info("DynSTEER 日志初始化完成", extra={"log_file": str(file_path)})
        return logger


def get_log_buffer() -> list[JsonObject]:
    """获取内存日志缓冲区。

    Returns:
        当前进程内的结构化日志列表。
    """
    with _LOG_BUFFER_LOCK:
        return list(_LOG_BUFFER)


def clear_log_buffer() -> None:
    """清空内存日志缓冲区。

    Returns:
        None。
    """
    with _LOG_BUFFER_LOCK:
        _LOG_BUFFER.clear()
