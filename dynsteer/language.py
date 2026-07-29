from enum import Enum
from typing import Mapping

class TaskLanguage(str, Enum):
    """任务 prompt 支持的语言。"""
    ENGLISH = 'en'
    CHINESE = 'zh'
_LANGUAGE_ALIASES: dict[str, TaskLanguage] = {'en': TaskLanguage.ENGLISH, 'eng': TaskLanguage.ENGLISH, 'english': TaskLanguage.ENGLISH, 'zh': TaskLanguage.CHINESE, 'ch': TaskLanguage.CHINESE, 'cn': TaskLanguage.CHINESE, 'chi': TaskLanguage.CHINESE, 'chinese': TaskLanguage.CHINESE, 'zhongwen': TaskLanguage.CHINESE, '中文': TaskLanguage.CHINESE}

def normalize_task_language(value: object) -> TaskLanguage:
    """将外部语言配置归一化为 TaskLanguage。"""
    if value is None:
        return TaskLanguage.ENGLISH
    if isinstance(value, TaskLanguage):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError('language 必须是非空字符串')
    normalized = value.strip().lower()
    language = _LANGUAGE_ALIASES.get(normalized)
    if language is None:
        raise ValueError(f'不支持的 language: {value}')
    return language

def language_from_metadata(metadata: Mapping[str, object] | None) -> TaskLanguage:
    """从元数据中读取并归一化任务语言。"""
    if metadata is None:
        return TaskLanguage.ENGLISH
    return normalize_task_language(metadata.get('language'))

def language_from_task(task_case: object | None) -> TaskLanguage:
    """从 TaskCase 读取 prompt 语言。"""
    if task_case is None:
        return TaskLanguage.ENGLISH
    metadata = getattr(task_case, 'metadata', None)
    return language_from_metadata(metadata if isinstance(metadata, Mapping) else None)
