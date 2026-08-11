import logging
import json
from pathlib import Path
import re
from dynsteer.language import TaskLanguage, normalize_task_language
from dynsteer.milestone.model import GeneratorTaskView, MilestoneGenerationConfig
from dynsteer.model import JsonObject
from dynsteer.utils import json_safe
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


class MilestonePromptBuilder:
    """集中构造多路径 generation/refinement 提示词与公开 payload。"""

    def __init__(
        self, view: GeneratorTaskView, config: MilestoneGenerationConfig
    ) -> None:
        self.view = view
        self.config = config
        self.payload: JsonObject = {
            "benchmark": view.benchmark,
            "task_id": view.task_id,
            "case_id": view.case_id,
            "language": view.language,
            "turns": json_safe(view.turns),
            "public_assets": view.public_assets,
            "initial_state": view.initial_state,
            "tool_schema": view.tool_schema,
            "environment_rules": view.environment_rules,
            "evidence_catalog": json_safe(view.evidence_catalog),
        }

    def generation(self) -> str:
        """渲染第一轮多样化路径生成提示词。"""
        return load_prompt_template("milestone", "generation").render(
            normalize_task_language(self.view.language),
            max_candidate_path_count=self.config.max_candidate_path_count,
            task=json.dumps(self.payload, ensure_ascii=False, sort_keys=True),
        )

    def refinement(
        self,
        original_response: str,
        violations: list[JsonObject],
        simulated_paths: list[object],
        common_operations: list[str],
    ) -> str:
        """渲染结构修复与共同 operation 反例搜索提示词。"""
        return load_prompt_template("milestone", "refinement").render(
            normalize_task_language(self.view.language),
            max_candidate_path_count=self.config.max_candidate_path_count,
            task=json.dumps(self.payload, ensure_ascii=False, sort_keys=True),
            original_response=original_response,
            validation_violations=json.dumps(violations, ensure_ascii=False),
            simulated_paths=json.dumps(simulated_paths, ensure_ascii=False),
            common_operations=json.dumps(common_operations, ensure_ascii=False),
        )
