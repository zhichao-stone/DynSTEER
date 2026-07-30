import logging
from pathlib import Path
import re
from dynsteer.language import TaskLanguage
logger = logging.getLogger(__name__)
_TEMPLATE_DIR = Path(__file__).with_name("templates")

def _safe_format(template: str, **kwargs: object) -> str:
    """安全替换简单 `{key}` 占位符，避免执行表达式或破坏 JSON 大括号。"""
    if template is None:
        raise ValueError("template 不能为空")
    left_marker, right_marker = ("\x00L\x00", "\x00R\x00")
    protected = template.replace("{{", left_marker).replace("}}", right_marker)

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return str(kwargs[key]) if key in kwargs else match.group(0)
    result = re.sub("\\{([a-zA-Z_][a-zA-Z0-9_]*)\\}", replace, protected)
    return result.replace(left_marker, "{").replace(right_marker, "}")

class PromptTemplate:
    """多语言 prompt 模板。"""

    def __init__(self, **lang_templates: str) -> None:
        if not lang_templates:
            raise ValueError("至少需要提供一种语言模板")
        for language, template in lang_templates.items():
            if not isinstance(language, str) or not language.strip():
                raise ValueError("语言代码不能为空")
            if not isinstance(template, str) or not template.strip():
                raise ValueError(f"{language} prompt 模板不能为空")
        self._templates = {key: value.strip() for key, value in lang_templates.items()}

    @property
    def supported_languages(self) -> list[str]:
        """返回当前模板支持的语言列表。"""
        return list(self._templates.keys())

    def render(self, language: TaskLanguage=TaskLanguage.ENGLISH, **kwargs: object) -> str:
        """按语言渲染 prompt。"""
        if not isinstance(language, TaskLanguage):
            raise ValueError("language 必须是 TaskLanguage 枚举类")
        template = self._templates.get(language.value)
        if template is None:
            template = self._templates.get(TaskLanguage.ENGLISH.value)
        if template is None:
            template = next(iter(self._templates.values()))
            logger.warning("Prompt language %s is not found. Fall back to first template.", language.value)
        return _safe_format(template, **kwargs)

def load_prompt_text(domain: str, name: str, language: TaskLanguage) -> str:
    """读取指定语言的 prompt 模板文本。"""
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError("prompt domain 不能为空")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("prompt name 不能为空")
    language_code = language.value if isinstance(language, TaskLanguage) else TaskLanguage.ENGLISH.value
    path = _TEMPLATE_DIR / domain / f"{name}.{language_code}.md"
    if not path.exists() and language_code != TaskLanguage.ENGLISH.value:
        path = _TEMPLATE_DIR / domain / f"{name}.{TaskLanguage.ENGLISH.value}.md"
    if not path.exists():
        raise FileNotFoundError(f"缺少 prompt 模板: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"prompt 模板不能为空: {path}")
    return text

def load_prompt_template(domain: str, name: str) -> PromptTemplate:
    """读取指定 prompt 的全部已存在语言模板。"""
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError("prompt domain 不能为空")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("prompt name 不能为空")
    templates: dict[str, str] = {}
    for language in TaskLanguage:
        path = _TEMPLATE_DIR / domain / f"{name}.{language.value}.md"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"prompt 模板不能为空: {path}")
        templates[language.value] = text
    if not templates:
        template_pattern = _TEMPLATE_DIR / domain / f"{name}.<language>.md"
        raise FileNotFoundError(f"缺少 prompt 模板: {template_pattern}")
    return PromptTemplate(**templates)
