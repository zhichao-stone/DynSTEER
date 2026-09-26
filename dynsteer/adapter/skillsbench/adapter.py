from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from dynsteer.adapter.base import BaseBenchmarkAdapter
from dynsteer.adapter.contract import normalize_tool_contract
from dynsteer.adapter.utils import resolve_source_root
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.milestone.model import GeneratorTaskView, GeneratorTurn
from dynsteer.model import JsonObject, TaskCase, TaskType
from dynsteer.utils import safe_name


EXCLUDED_TASK_IDS = frozenset({"mhc-layer-impl"})


@dataclass(frozen=True)
class SkillsBenchTask:
    case_id: str
    description: str
    category: str
    subcategory: str | None
    task_types: tuple[str, ...]
    difficulty: str | None
    agent_timeout_seconds: float
    verifier_timeout_seconds: float
    sandbox: JsonObject
    source_digest: str


class SkillsBenchAdapter(BaseBenchmarkAdapter):
    benchmark = "skillsbench"

    def list_case_ids(self, config: HarnessRunConfig) -> list[str]:
        return sorted(_tasks(config))

    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase:
        task = self.load_task(config, case_id)
        source_root = resolve_source_root(config.data_root, Path(__file__).resolve().parents[3], "skillsbench")
        return TaskCase(
            task_id=f"skillsbench::{case_id}",
            task_description=task.description,
            case_id=case_id,
            environment_schema={"working_directory": "/root", "task_image": f"dynsteer-skills:{task.source_digest[:16]}-{safe_name(case_id)}"},
            tool_schema={"tools": [{"name": "bash", "input": {"command": "string"}}]},
            initial_state={"category": task.category},
            task_types=[TaskType.GENERAL],
            metadata={
                "skills": [
                    path.relative_to(source_root / "tasks" / case_id / "environment/skills").as_posix()
                    for path in sorted((source_root / "tasks" / case_id / "environment/skills").rglob("SKILL.md"))
                ],
                "category": task.category,
                "subcategory": task.subcategory,
                "task_types": list(task.task_types),
                "difficulty": task.difficulty,
                "agent_timeout_seconds": task.agent_timeout_seconds,
                "verifier_timeout_seconds": task.verifier_timeout_seconds,
                "sandbox": task.sandbox,
                "source_digest": task.source_digest,
            },
        )

    def load_task(self, config: HarnessRunConfig, case_id: str) -> SkillsBenchTask:
        task = _tasks(config).get(case_id)
        if task is None:
            raise KeyError(f"SkillsBench task 不存在: {case_id}")
        return task

    def generator_task_view(
        self, config: HarnessRunConfig, task_case: TaskCase, case_id: str
    ) -> GeneratorTaskView:
        if task_case.case_id != case_id:
            raise ValueError("task_case.case_id 与 case_id 不一致")
        tool_schema, evidence = normalize_tool_contract(task_case.tool_schema)
        skills = task_case.metadata.get("skills") if isinstance(task_case.metadata.get("skills"), list) else []
        return GeneratorTaskView(
            benchmark=self.benchmark,
            task_id=task_case.task_id,
            case_id=case_id,
            language=str(config.metadata.get("language", "en")),
            turns=(GeneratorTurn(turn_id="instruction", instruction=task_case.task_description, source_ref="task.md"),),
            public_assets=[{"name": "skills", "entries": skills}],
            public_state=dict(task_case.environment_schema),
            simulation_state={"working_directory": "/root"},
            tool_schema=tool_schema,
            tool_contracts={"bash": {"input": "command", "batch": "single_command"}},
            environment_rules={"condition": config.metadata.get("condition"), "skills_visible": config.metadata.get("condition") == "with-skills"},
            evidence_catalog=tuple(evidence),
        )


def _tasks(config: HarnessRunConfig) -> dict[str, SkillsBenchTask]:
    source_root = resolve_source_root(config.data_root, Path(__file__).resolve().parents[3], "skillsbench")
    tasks_dir = source_root / "tasks"
    tasks: dict[str, SkillsBenchTask] = {}
    for task_dir in sorted(tasks_dir.iterdir()):
        if not task_dir.is_dir() or task_dir.name in EXCLUDED_TASK_IDS:
            continue
        task = _parse_task(task_dir)
        tasks[task.case_id] = task
    if not tasks:
        raise ValueError(f"SkillsBench tasks 目录没有可用任务: {tasks_dir}")
    return tasks


def _parse_task(task_dir: Path) -> SkillsBenchTask:
    task_path = task_dir / "task.md"
    front_matter, description = _split_front_matter(task_path.read_text(encoding="utf-8"))
    metadata = front_matter.get("metadata")
    verifier = front_matter.get("verifier")
    agent = front_matter.get("agent")
    sandbox = front_matter.get("sandbox")
    if not isinstance(metadata, dict) or not isinstance(verifier, dict) or not isinstance(agent, dict) or not isinstance(sandbox, dict):
        raise ValueError(f"SkillsBench task front matter 不完整: {task_dir}")
    task_type = metadata.get("task_type", [])
    return SkillsBenchTask(
        case_id=task_dir.name,
        description=description.strip(),
        category=str(metadata.get("category") or ""),
        subcategory=str(metadata["subcategory"]) if metadata.get("subcategory") is not None else None,
        task_types=tuple(str(item) for item in task_type) if isinstance(task_type, list) else (),
        difficulty=str(metadata["difficulty"]) if metadata.get("difficulty") is not None else None,
        agent_timeout_seconds=float(agent.get("timeout_sec", 900.0)),
        verifier_timeout_seconds=float(verifier.get("timeout_sec", 900.0)),
        sandbox={str(key): value for key, value in sandbox.items()},
        source_digest=_directory_digest(task_dir),
    )


def _split_front_matter(text: str) -> tuple[JsonObject, str]:
    if not text.startswith("---\n"):
        raise ValueError("SkillsBench task.md 缺少 front matter")
    end = text.find("\n---", 4)
    if end < 0:
        raise ValueError("SkillsBench task.md front matter 未闭合")
    payload = yaml.safe_load(text[4:end])
    if not isinstance(payload, dict):
        raise ValueError("SkillsBench task.md front matter 必须是对象")
    return {str(key): value for key, value in payload.items()}, text[end + 4:]


def _directory_digest(task_dir: Path) -> str:
    digest = hashlib.sha256()
    for relative in ("task.md", "environment", "verifier"):
        path = task_dir / relative
        if path.is_file():
            paths = [path]
        elif path.is_dir():
            paths = sorted(item for item in path.rglob("*") if item.is_file())
        else:
            raise FileNotFoundError(path)
        for item in paths:
            relative_path = item.relative_to(task_dir).as_posix().encode("utf-8")
            digest.update(relative_path + b"\0")
            digest.update(str(item.stat().st_mode).encode("ascii") + b"\0")
            with item.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()
