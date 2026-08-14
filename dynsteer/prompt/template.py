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

_MILESTONE_FOCUS_INSTRUCTIONS: dict[TaskLanguage, dict[str, str]] = {
    TaskLanguage.ENGLISH: {
        "minimality": (
            "Challenge every search/getter/conversion/recovery node: retain it only if "
            "the task cannot be completed correctly without its output or effect. Prefer "
            "public literals and direct goals when sufficient."
        ),
        "alternative": (
            "Actively seek a genuinely different complete realization: alternative visible "
            "tools, direct use of public information, or a correct bulk set_state. Do not "
            "create variation by omitting prerequisites."
        ),
        "dependency_safety": (
            "Audit producer-consumer dependencies, executability, and fatal side effects. "
            "Keep independent producers unordered; use empty/non-executable graphs and "
            "minefields when the visible inputs or tools cannot safely complete the task."
        ),
    },
    TaskLanguage.CHINESE: {
        "minimality": (
            "逐一质疑 search、getter、conversion、recovery 节点：只有缺少其输出或效果时任务"
            "确实无法正确完成，才保留该节点；公开 literal 或直接 goal 已足够时优先采用。"
        ),
        "alternative": (
            "主动寻找真正不同且完整的实现：替代的可见工具、直接利用公开信息，或正确的批量 "
            "set_state；不得通过省略前置条件制造差异。"
        ),
        "dependency_safety": (
            "核查 producer-consumer 的真实依赖、任务可执行性和 fatal 副作用；独立 producer "
            "之间不排序；公开输入或工具不能安全完成任务时，使用空的不可执行图和 minefield。"
        ),
    },
}

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
    """集中构造相互独立的候选 milestone graph 提示词。"""

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
            "public_assets": [
                item for item in view.public_assets
                if item.get("visibility", "agent") == "agent"
            ],
            "public_state": _public_state_prompt_json(view.public_state),
            "tool_schema": view.tool_schema,
            "evidence_catalog": json_safe(view.evidence_catalog),
        }

    def generation(self, batch_index: int, focus: str) -> str:
        """渲染一个独立双图批次的生成提示词。"""
        language = normalize_task_language(self.view.language)
        instructions = _MILESTONE_FOCUS_INSTRUCTIONS.get(language, {})
        if focus not in instructions:
            raise ValueError(f"不支持的 milestone 批次审查重点: {focus}")
        return load_prompt_template("milestone", "generation").render(
            language,
            batch_index=batch_index + 1,
            focus_code=focus,
            focus_instruction=instructions[focus],
            task=json.dumps(self.payload, ensure_ascii=False, sort_keys=True),
        )


def _public_state_prompt_json(public_state: JsonObject) -> JsonObject:
    """为公开状态叶节点附加可供 provenance 引用的稳定 reference。"""
    def convert(value: object, path: tuple[str, ...]) -> object:
        if isinstance(value, dict):
            return {str(key): convert(item, (*path, str(key))) for key, item in value.items()}
        if isinstance(value, list):
            return [convert(item, (*path, str(index))) for index, item in enumerate(value)]
        return {"source_ref": f"public_state:{'.'.join(path)}", "value": value}

    result = convert(public_state, ())
    return result if isinstance(result, dict) else {}
