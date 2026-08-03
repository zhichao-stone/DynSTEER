import copy
import hashlib
import json

from dynsteer.model import JsonObject
from dynsteer.utils import json_safe


class JudgeCache:
    """保存单个 case、单次 evaluator 生命周期内的 judge 结果。"""

    def __init__(self) -> None:
        self._results: dict[str, JsonObject] = {}
        self._cache_hit_count = 0
        self._semantic_review_cache_hit_count = 0

    def get(self, key: str, *, semantic_review: bool = False) -> JsonObject | None:
        """返回缓存结果的深拷贝，并记录命中统计。"""
        result = self._results.get(key)
        if result is None:
            return None
        self._cache_hit_count += 1
        if semantic_review:
            self._semantic_review_cache_hit_count += 1
        return copy.deepcopy(result)

    def put(self, key: str, result: JsonObject) -> None:
        """写入 judge 结果的深拷贝。"""
        if not key or result is None:
            raise ValueError("key 和 result 不能为空")
        self._results[key] = copy.deepcopy(result)

    def clear(self) -> None:
        """清理 case 边界缓存和 telemetry。"""
        self._results.clear()
        self._cache_hit_count = 0
        self._semantic_review_cache_hit_count = 0

    def telemetry(self) -> JsonObject:
        """返回 judge 去重 telemetry。"""
        return {
            "llm_unique_prompt_count": len(self._results),
            "llm_cache_hit_count": self._cache_hit_count,
            "llm_duplicate_prompt_avoided_count": self._cache_hit_count,
            "semantic_review_cache_hit_count": self._semantic_review_cache_hit_count,
        }


def judge_context_digest(context: JsonObject) -> str:
    """计算稳定的 judge 上下文摘要。"""
    if context is None:
        raise ValueError("context 不能为空")
    payload = json.dumps(json_safe(context), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
