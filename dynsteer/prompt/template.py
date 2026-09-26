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
            "Challenge every node rigorously: retain it only if omitting it strictly prevents task success. "
            "Never inject auxiliary shift_timestamp, timestamp_diff, or unit_conversion unless explicitly demanded. "
            "Prefer direct public literals over intermediate getter/conversion tools."
        ),
        "alternative": (
            "Actively explore valid alternative realizations: direct use of public state values, batch set_state goals, "
            "or direct answer tools. Do not invent non-essential steps to manufacture superficial diversity."
        ),
        "dependency_safety": (
            "Audit producer-consumer dataflow and executability. Keep independent producers strictly unordered. "
            "If the instruction lacks sufficient context to identify target records, declare response_only, "
            "return an empty operation graph or clarification message, and register fatal minefields for unsafe tool calls."
        ),
    },
    TaskLanguage.CHINESE: {
        "minimality": (
            "极其严格地质疑每一个节点：只有缺少该节点任务在逻辑上必然失败时才予保留；"
            "严禁插入非必需的 shift_timestamp、timestamp_diff 或单位转换工具；公开 literal 足矣时优先直接采用。"
        ),
        "alternative": (
            "主动寻找真正合法的不同实现：直接利用公开状态值、合法的批量 set_state 目标或直接答复工具；"
            "不得通过随意省略必需前置条件或捏造无用工具来制造虚假差异。"
        ),
        "dependency_safety": (
            "严格核查数据流依赖与任务可执行性；独立 producer 之间绝不连边；"
            "若公开输入不足以确定唯一操作目标，果断判定为 response_only 并输出空操作图或澄清消息，并对危险工具调用注册 fatal minefield。"
        ),
    },
}


def _safe_format(template: str, **kwargs: object) -> str:
    """Secure replacement simple`{key}`Placeholder to avoid executing expressions or damaging JSON brackets."""
    if template is None:
        raise ValueError('Template cannot be empty')
    left_marker, right_marker = ("\x00L\x00", "\x00R\x00")
    protected = template.replace("{{", left_marker).replace("}}", right_marker)

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return str(kwargs[key]) if key in kwargs else match.group(0)
    result = re.sub("\\{([a-zA-Z_][a-zA-Z0-9_]*)\\}", replace, protected)
    return result.replace(left_marker, "{").replace(right_marker, "}")

class PromptTemplate:
    """Multilingual prompt template."""

    def __init__(self, **lang_templates: str) -> None:
        if not lang_templates:
            raise ValueError('At least one language template needs to be provided')
        for language, template in lang_templates.items():
            if not isinstance(language, str) or not language.strip():
                raise ValueError('Language code cannot be empty')
            if not isinstance(template, str) or not template.strip():
                raise ValueError(f"The {language} prompt template cannot be empty")
        self._templates = {key: value.strip() for key, value in lang_templates.items()}

    def render(self, language: TaskLanguage=TaskLanguage.ENGLISH, **kwargs: object) -> str:
        """Practise text according to language."""
        if not isinstance(language, TaskLanguage):
            raise ValueError('Language must be TaskLanguage enumerate')
        template = self._templates.get(language.value)
        if template is None:
            template = self._templates.get(TaskLanguage.ENGLISH.value)
        if template is None:
            template = next(iter(self._templates.values()))
            logger.warning("Prompt language %s is not found. Fall back to first template.", language.value)
        return _safe_format(template, **kwargs)

def load_prompt_template(domain: str, name: str) -> PromptTemplate:
    """Reads all existing language templates for the specified prompt."""
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError('Prompt domain cannot be empty')
    if not isinstance(name, str) or not name.strip():
        raise ValueError("prompt name must not be empty.")
    templates: dict[str, str] = {}
    for language in TaskLanguage:
        path = _TEMPLATE_DIR / domain / f"{name}.{language.value}.md"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"prompt template must not be empty: {path}")
        templates[language.value] = text
    if not templates:
        template_pattern = _TEMPLATE_DIR / domain / f"{name}.<language>.md"
        raise FileNotFoundError(f"missing prompt template: {template_pattern}")
    return PromptTemplate(**templates)


class MilestonePromptBuilder:
    """Focuss on a separate candidate for milestone profile."""

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
            "tool_output_contracts": _safe_tool_output_contracts(view),
            "evidence_catalog": json_safe(view.evidence_catalog),
        }

    def generation(self, batch_index: int, focus: str) -> str:
        """Renders a source word for a separate double-chart batch."""
        language = normalize_task_language(self.view.language)
        instructions = _MILESTONE_FOCUS_INSTRUCTIONS.get(language, {})
        if focus not in instructions:
            raise ValueError(f"Unsupported emestone batch review focus:{focus}")
        return load_prompt_template("milestone", "generation").render(
            language,
            batch_index=batch_index + 1,
            focus_code=focus,
            focus_instruction=instructions[focus],
            task=json.dumps(self.payload, ensure_ascii=False, sort_keys=True),
        )


def _safe_tool_output_contracts(view: GeneratorTaskView) -> JsonObject:
    """Only project output syntax and publicly available state effect fields, without leaking a specific evaluation contract."""
    result: JsonObject = {}
    for evidence in view.evidence_catalog:
        tool_name = evidence.metadata.get("tool_name")
        contract = view.tool_contracts.get(str(tool_name), {})
        outputs = contract.get("outputs", {}) if isinstance(contract, dict) else {}
        if not isinstance(outputs, dict):
            continue
        safe_outputs: JsonObject = {}
        for name, output in outputs.items():
            if not isinstance(output, dict):
                continue
            safe_outputs[str(name)] = {
                "selector": output.get("selector"),
                "type": output.get("type"),
                "cardinality": output.get("cardinality", []),
            }
        safe_contract: JsonObject = {
            "tool_name": tool_name,
            "outputs": safe_outputs,
        }
        effect = contract.get("effect") if isinstance(contract, dict) else None
        state_fields = contract.get("state_fields") if isinstance(contract, dict) else {}
        if (
            contract.get("state_evaluator") == "toolsandbox_snapshot"
            and isinstance(effect, dict) and isinstance(state_fields, dict)
        ):
            safe_contract["state_effect"] = {
                "namespace": effect.get("namespace"),
                "operation": effect.get("operation"),
                "fields": sorted(str(field) for field in state_fields),
            }
            executor_arguments = contract.get("executor_arguments", {})
            if isinstance(executor_arguments, dict):
                safe_contract["executor_arguments"] = {
                    str(argument): state_field
                    for argument, state_field in sorted(executor_arguments.items())
                }
        result[evidence.evidence_id] = safe_contract
    return result


def _public_state_prompt_json(public_state: JsonObject) -> JsonObject:
    """Attach a stable reference to the open state leaves node."""
    def convert(value: object, path: tuple[str, ...]) -> object:
        if isinstance(value, dict):
            return {str(key): convert(item, (*path, str(key))) for key, item in value.items()}
        if isinstance(value, list):
            return [convert(item, (*path, str(index))) for index, item in enumerate(value)]
        return {"source_ref": f"public_state:{'.'.join(path)}", "value": value}

    result = convert(public_state, ())
    return result if isinstance(result, dict) else {}
