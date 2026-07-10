from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from dynsteer.model import JsonObject, JsonValue
from dynsteer.utils import compact_text

_LOG_BUFFER: list[JsonObject] = []
LOG_BUFFER_LIMIT = 2000
_LOGGER_LOCK = threading.RLock()
_LOG_BUFFER_LOCK = threading.RLock()
_LOG_EXTRA_TEXT_LIMIT = 160
_LOG_EXTRA_LIST_LIMIT = 12
_LOG_RECORD_BUILTINS = {
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
}


class StructuredLogFormatter(logging.Formatter):
    """在日志消息后追加轻量 JSON extra 的 formatter。"""

    def format(self, record: logging.LogRecord) -> str:
        """格式化日志记录并追加结构化 extra。"""
        message = super().format(record)
        extra = log_extra_from_record(record)
        if not extra:
            return message
        return f"{message} {json.dumps(extra, ensure_ascii=False, sort_keys=True)}"


class TerminalLogFormatter(logging.Formatter):
    """终端专用 formatter，仅展示少量摘要字段。"""

    def format(self, record: logging.LogRecord) -> str:
        """格式化终端日志并追加受控摘要。"""
        message = super().format(record)
        extra = terminal_log_extra_from_record(record)
        if not extra:
            return message
        return f"{message} {json.dumps(extra, ensure_ascii=False, sort_keys=True)}"


class BufferLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        """将日志记录写入内存缓冲区。"""
        try:
            entry: JsonObject = {
                "time": datetime.fromtimestamp(record.created).isoformat(timespec="seconds"),
                "level": record.levelname,
                "message": record.getMessage(),
                "module": record.module,
            }
            entry.update(log_extra_from_record(record))
            with _LOG_BUFFER_LOCK:
                _LOG_BUFFER.append(entry)
                overflow = len(_LOG_BUFFER) - LOG_BUFFER_LIMIT
                if overflow > 0:
                    del _LOG_BUFFER[:overflow]
        except Exception:
            self.handleError(record)


def configure_logger(log_dir: str | Path) -> logging.Logger:
    """配置 DynSTEER 结构化中文日志。"""
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
        formatter = StructuredLogFormatter("%(asctime)s %(levelname)s %(message)s")
        terminal_formatter = TerminalLogFormatter("%(asctime)s %(levelname)s %(message)s")
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(terminal_formatter)
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
    """获取内存日志缓冲区。"""
    with _LOG_BUFFER_LOCK:
        return list(_LOG_BUFFER)


def clear_log_buffer() -> None:
    """清空内存日志缓冲区。"""
    with _LOG_BUFFER_LOCK:
        _LOG_BUFFER.clear()


def log_extra_from_record(record: logging.LogRecord) -> JsonObject:
    """从 LogRecord 提取并清洗业务 extra。"""
    if record is None:
        raise ValueError("record 不能为空")
    extra: JsonObject = {}
    for key, value in record.__dict__.items():
        if key.startswith("_") or key in _LOG_RECORD_BUILTINS:
            continue
        extra[key] = sanitize_log_value(value)
    return extra


def terminal_log_extra_from_record(record: logging.LogRecord) -> JsonObject:
    """从 LogRecord 中提取终端展示用的精简 extra。"""
    if record is None:
        raise ValueError("record 不能为空")
    if record.levelno < logging.WARNING:
        return {}
    raw_extra = log_extra_from_record(record)
    return _compact_terminal_extra(raw_extra)


def sanitize_log_value(value: object) -> JsonValue:
    """清洗日志 extra 值，避免长字段刷屏。"""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return compact_text(value, _LOG_EXTRA_TEXT_LIMIT)
    if isinstance(value, list):
        return [sanitize_log_value(item) for item in value[:_LOG_EXTRA_LIST_LIMIT]]
    if isinstance(value, dict):
        return {
            str(key): sanitize_log_value(item)
            for key, item in list(value.items())[:_LOG_EXTRA_LIST_LIMIT]
        }
    return compact_text(value, _LOG_EXTRA_TEXT_LIMIT)


def _compact_terminal_extra(extra: JsonObject) -> JsonObject:
    result: JsonObject = {}
    for key in ("case_id", "milestone_id", "milestone_score", "milestone_status"):
        value = extra.get(key)
        if value is not None:
            result[key] = value
    diagnosis = extra.get("diagnosis")
    if diagnosis is None:
        diagnosis = extra.get("stage_first_diagnosis")
    if diagnosis is None:
        diagnosis = extra.get("judge_first_diagnosis")
    if diagnosis is not None:
        result["diagnosis"] = diagnosis
    evidence = extra.get("evidence")
    if evidence is None:
        evidence = extra.get("stage_first_evidence")
    if evidence is None:
        evidence = extra.get("judge_first_evidence")
    if evidence is not None:
        result["evidence"] = evidence
    return result

