from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from dynsteer.model import JsonObject, JsonValue

_LOG_BUFFER: list[JsonObject] = []
LOG_BUFFER_LIMIT = 2000
_LOGGER_LOCK = threading.RLock()
_LOG_BUFFER_LOCK = threading.RLock()
_LOG_EXTRA_TEXT_LIMIT = 160
_LOG_EXTRA_LIST_LIMIT = 12
_TERMINAL_SUPPRESSED_MESSAGES = {
    "evaluator_milestone_match_attempt",
    "evaluator_pending_milestones",
    "standard_judge_input_snapshot",
    "standard_judge_output_snapshot",
    "expensive_judge_input_snapshot",
    "expensive_judge_output_snapshot",
}
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


class TerminalLogFilter(logging.Filter):
    """过滤不需要进入终端的高频诊断日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        """判断日志记录是否应该输出到终端。

        Args:
            record: logging 产生的日志记录。

        Returns:
            `True` 表示允许终端输出，`False` 表示仅保留到文件/缓冲区。
        """
        if record is None:
            raise ValueError("record 不能为空")
        if record.name.startswith("dynsteer.judges"):
            return False
        return record.getMessage() not in _TERMINAL_SUPPRESSED_MESSAGES


class StructuredLogFormatter(logging.Formatter):
    """在日志消息后追加轻量 JSON extra 的 formatter。"""

    def format(self, record: logging.LogRecord) -> str:
        """格式化日志记录并追加结构化 extra。

        Args:
            record: logging 产生的日志记录。

        Returns:
            原始日志文本加可选 JSON extra。
        """
        message = super().format(record)
        extra = log_extra_from_record(record)
        if not extra:
            return message
        return f"{message} {json.dumps(extra, ensure_ascii=False, sort_keys=True)}"


class TerminalLogFormatter(logging.Formatter):
    """终端专用 formatter，仅展示少量摘要字段。"""

    def format(self, record: logging.LogRecord) -> str:
        """格式化终端日志并追加受控摘要。

        Args:
            record: logging 产生的日志记录。

        Returns:
            终端可读的短日志文本。
        """
        message = super().format(record)
        extra = terminal_log_extra_from_record(record)
        if not extra:
            return message
        return f"{message} {json.dumps(extra, ensure_ascii=False, sort_keys=True)}"


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
            entry.update(log_extra_from_record(record))
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
        formatter = StructuredLogFormatter("%(asctime)s %(levelname)s %(message)s")
        terminal_formatter = TerminalLogFormatter("%(asctime)s %(levelname)s %(message)s")
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(terminal_formatter)
        stream_handler.addFilter(TerminalLogFilter())
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


def log_extra_from_record(record: logging.LogRecord) -> JsonObject:
    """从 LogRecord 提取并清洗业务 extra。

    Args:
        record: logging 产生的日志记录。

    Returns:
        仅包含业务字段的 JSON 对象。
    """
    if record is None:
        raise ValueError("record 不能为空")
    extra: JsonObject = {}
    for key, value in record.__dict__.items():
        if key.startswith("_") or key in _LOG_RECORD_BUILTINS:
            continue
        extra[key] = sanitize_log_value(value)
    return extra


def terminal_log_extra_from_record(record: logging.LogRecord) -> JsonObject:
    """从 LogRecord 中提取终端展示用的精简 extra。

    Args:
        record: logging 产生的日志记录。

    Returns:
        仅包含 case、milestone、分数、诊断和证据的 JSON 对象。
    """
    if record is None:
        raise ValueError("record 不能为空")
    if record.getMessage() != "evaluator_milestone_checkpoint" and record.levelno < logging.WARNING:
        return {}
    raw_extra = log_extra_from_record(record)
    return _compact_terminal_extra(raw_extra)


def sanitize_log_value(value: object) -> JsonValue:
    """清洗日志 extra 值，避免长字段刷屏。

    Args:
        value: 任意业务日志值。

    Returns:
        JSON 可序列化且长度受控的值。
    """
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _truncate_text(value)
    if isinstance(value, list):
        return [sanitize_log_value(item) for item in value[:_LOG_EXTRA_LIST_LIMIT]]
    if isinstance(value, dict):
        return {
            str(key): sanitize_log_value(item)
            for key, item in list(value.items())[:_LOG_EXTRA_LIST_LIMIT]
        }
    return _truncate_text(str(value))


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


def _truncate_text(value: str) -> str:
    text = " ".join(str(value).split())
    if len(text) <= _LOG_EXTRA_TEXT_LIMIT:
        return text
    return text[: max(_LOG_EXTRA_TEXT_LIMIT - 3, 0)] + "..."
