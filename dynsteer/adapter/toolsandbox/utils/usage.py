from __future__ import annotations

from dataclasses import dataclass

import httpx

from dynsteer.model import JsonObject
from dynsteer.utils import compact_text
import logging


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderUsageDelta:
    """一个 ToolSandbox 批次累计的 agent provider usage。"""

    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


class ProviderUsageRecorder:
    """观察 provider HTTP response usage 的单 case 记录器。"""

    def __init__(self) -> None:
        self._records: list[JsonObject] = []
        self._pending_records: list[JsonObject] = []
        self._missing_agent_target_count = 0

    def record_openai_response(self, response: httpx.Response) -> None:
        """记录 OpenAI response 的最小 usage 结构。"""
        self._record(response, ("prompt_tokens", "completion_tokens", "total_tokens"))

    def record_anthropic_response(self, response: httpx.Response) -> None:
        """记录 Anthropic response 的最小 usage 结构。"""
        self._record(response, ("input_tokens", "output_tokens"))

    def take_delta(self) -> ProviderUsageDelta | None:
        """消费并汇总上次调用以来的 provider responses。"""
        records, self._pending_records = self._pending_records, []
        if not records:
            return None
        if any(record.get("available") is not True for record in records):
            return None
        prompt = sum(int(record["prompt_tokens"]) for record in records)
        completion = sum(int(record["completion_tokens"]) for record in records)
        total = sum(int(record["total_tokens"]) for record in records)
        return ProviderUsageDelta(prompt, completion, total)

    def note_missing_agent_target(self) -> None:
        """记录 provider 消耗无法归属到本批次 agent outbound 的异常。"""
        self._missing_agent_target_count += 1

    def summary(self) -> JsonObject:
        """输出累计 token、请求数和可用性，缺失不伪造为 0。"""
        available_records = [record for record in self._records]
        complete = bool(available_records) and all(record.get("available") is True for record in available_records)
        return {
            "available": complete and self._missing_agent_target_count == 0,
            "request_count": len(available_records),
            "usage_response_count": sum(record.get("available") is True for record in available_records),
            "missing_response_count": sum(record.get("available") is not True for record in available_records),
            "missing_agent_target_count": self._missing_agent_target_count,
            "prompt_tokens": sum(int(record["prompt_tokens"]) for record in available_records if record.get("available") is True),
            "completion_tokens": sum(int(record["completion_tokens"]) for record in available_records if record.get("available") is True),
            "total_tokens": sum(int(record["total_tokens"]) for record in available_records if record.get("available") is True),
        }

    def _record(self, response: httpx.Response, token_fields: tuple[str, ...]) -> None:
        """只读取 response JSON usage，解析异常不改写 provider 结果。"""
        try:
            usage = response.json().get("usage")
            usage = usage if isinstance(usage, dict) else {}
            record: JsonObject = {"available": all(isinstance(usage.get(key), int) for key in token_fields)}
            if token_fields[0] == "input_tokens":
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")
                record.update(
                    prompt_tokens=input_tokens if isinstance(input_tokens, int) else 0,
                    completion_tokens=output_tokens if isinstance(output_tokens, int) else 0,
                    total_tokens=input_tokens + output_tokens if isinstance(input_tokens, int) and isinstance(output_tokens, int) else 0,
                )
            else:
                record.update({
                    key: usage.get(key) if isinstance(usage.get(key), int) else 0
                    for key in token_fields
                })
            self._records.append(record)
            self._pending_records.append(record)
        except Exception as exc:
            logger.warning(
                "toolsandbox_usage_parse_failed",
                extra={"事件": "ToolSandbox provider usage 解析失败", "error": compact_text(str(exc), 160)},
            )
            missing: JsonObject = {"available": False, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            self._records.append(missing)
            self._pending_records.append(missing)
