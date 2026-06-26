from __future__ import annotations

import pytest

from dynsteer.language import TaskLanguage, language_from_metadata, normalize_task_language


def test_normalize_task_language_accepts_english_aliases() -> None:
    assert normalize_task_language("en") == TaskLanguage.ENGLISH
    assert normalize_task_language("English") == TaskLanguage.ENGLISH
    assert normalize_task_language(None) == TaskLanguage.ENGLISH


def test_normalize_task_language_accepts_chinese_aliases() -> None:
    assert normalize_task_language("zh") == TaskLanguage.CHINESE
    assert normalize_task_language("CH") == TaskLanguage.CHINESE
    assert normalize_task_language("zhongwen") == TaskLanguage.CHINESE
    assert normalize_task_language("中文") == TaskLanguage.CHINESE


def test_normalize_task_language_rejects_unknown_language() -> None:
    with pytest.raises(ValueError, match="language"):
        normalize_task_language("fr")


def test_language_from_metadata_reads_language_field() -> None:
    assert language_from_metadata({"language": "chinese"}) == TaskLanguage.CHINESE
