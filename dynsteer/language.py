from enum import Enum
from typing import Mapping


class TaskLanguage(str, Enum):
    """Language supported by task prompts."""
    ENGLISH = "en"
    CHINESE = "zh"

_LANGUAGE_ALIASES: dict[str, TaskLanguage] = {
    "en": TaskLanguage.ENGLISH,
    "eng": TaskLanguage.ENGLISH,
    "english": TaskLanguage.ENGLISH,
    "zh": TaskLanguage.CHINESE,
    "ch": TaskLanguage.CHINESE,
    "cn": TaskLanguage.CHINESE,
    "chi": TaskLanguage.CHINESE,
    "chinese": TaskLanguage.CHINESE,
    "zhongwen": TaskLanguage.CHINESE,
    "中文": TaskLanguage.CHINESE,
}

def normalize_task_language(value: object) -> TaskLanguage:
    """Normalize a task-language value, defaulting to English."""
    if value is None:
        return TaskLanguage.ENGLISH
    if isinstance(value, TaskLanguage):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError('language must be a non-empty string')
    normalized = value.strip().lower()
    language = _LANGUAGE_ALIASES.get(normalized)
    if language is None:
        raise ValueError(f"unsupported language: {value}")
    return language

def language_from_metadata(metadata: Mapping[str, object] | None) -> TaskLanguage:
    """Read and normalize the task language from metadata."""
    if metadata is None:
        return TaskLanguage.ENGLISH
    return normalize_task_language(metadata.get("language"))

def language_from_task(task_case: object | None) -> TaskLanguage:
    """Read the prompt language from a TaskCase."""
    if task_case is None:
        return TaskLanguage.ENGLISH
    metadata = getattr(task_case, "metadata", None)
    return language_from_metadata(metadata if isinstance(metadata, Mapping) else None)
