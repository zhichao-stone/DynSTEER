from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from dynsteer.adapter.base import BaseBenchmarkAdapter
from dynsteer.adapter.contract import normalize_tool_contract
from dynsteer.adapter.utils import resolve_source_root
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.milestone.model import GeneratorTaskView, GeneratorTurn
from dynsteer.model import JsonObject, TaskCase, TaskType
from dynsteer.utils import stable_json_digest


@dataclass(frozen=True)
class SWEBenchProSample:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    dockerhub_tag: str
    metadata: JsonObject


class SWEBenchProAdapter(BaseBenchmarkAdapter):
    benchmark = "swebench_pro"

    def list_case_ids(self, config: HarnessRunConfig) -> list[str]:
        return sorted(_samples(config))

    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase:
        sample = self.load_sample(config, case_id)
        eval_sample_digest = stable_json_digest({
            "instance_id": sample.instance_id,
            "repo": sample.repo,
            "base_commit": sample.base_commit,
            "problem_statement": sample.problem_statement,
            "dockerhub_tag": sample.dockerhub_tag,
        })
        return TaskCase(
            task_id=f"swebench_pro::{case_id}",
            task_description=_public_problem_statement(sample),
            case_id=case_id,
            environment_schema={
                "repo": sample.repo,
                "base_commit": sample.base_commit,
                "dockerhub_tag": sample.dockerhub_tag,
                "image_namespace": config.metadata.get("dockerhub_username"),
                "working_directory_probe": "git rev-parse --show-toplevel",
            },
            tool_schema={"tools": [{"name": "bash", "input": {"command": "string"}}]},
            initial_state={"repo": sample.repo, "base_commit": sample.base_commit},
            task_types=[TaskType.ARTIFACT],
            metadata={
                "repo": sample.repo,
                "archive_digest": sample.metadata.get("archive_digest"),
                "source_git_commit": sample.metadata.get("source_git_commit"),
                "eval_sample_digest": eval_sample_digest,
            },
        )

    def load_sample(self, config: HarnessRunConfig, case_id: str) -> SWEBenchProSample:
        """Reads individual case public with evaluator metadata."""
        return _sample(config, case_id)

    def generator_task_view(
        self, config: HarnessRunConfig, task_case: TaskCase, case_id: str
    ) -> GeneratorTaskView:
        if task_case.case_id != case_id:
            raise ValueError('tab_case. case_id does not match case_id')
        tool_schema, evidence = normalize_tool_contract(task_case.tool_schema)
        return GeneratorTaskView(
            benchmark=self.benchmark,
            task_id=task_case.task_id,
            case_id=case_id,
            language=str(config.metadata.get("language", "en")),
            turns=(GeneratorTurn(
                turn_id="instruction",
                instruction=task_case.task_description,
                source_ref="task_description",
            ),),
            public_assets=[],
            public_state=dict(task_case.environment_schema),
            simulation_state={"working_directory": "container_repository_root"},
            tool_schema=tool_schema,
            tool_contracts={"bash": {"input": "command", "batch": "single_command"}},
            environment_rules={"repository_isolation": "official_image", "state": "persistent_container"},
            evidence_catalog=tuple(evidence),
        )


def _samples(config: HarnessRunConfig) -> dict[str, SWEBenchProSample]:
    path = config.data_root / "source/eval_samples.jsonl"
    if not path.is_file():
        raise FileNotFoundError('SWE-bench Pro missing; runs/prepare_swebench_pro.py')
    digest_data = _archive_digest(config)
    samples: dict[str, SWEBenchProSample] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        sample = _sample_from_row(row, digest_data)
        if sample.instance_id in samples:
            raise ValueError(f"Instance_id Repeat:{sample.instance_id}")
        samples[sample.instance_id] = sample
    if not samples:
        raise ValueError('The eval_samples.jsonl cannot be empty.')
    return samples


def _archive_digest(config: HarnessRunConfig) -> dict[str, object]:
    digest_path = config.data_root / "source/.archive_digest"
    if not digest_path.is_file():
        raise FileNotFoundError('Missing SWE-bench Pro archive digest; run scripts/prepare_swebench_pro.py first')
    try:
        digest_data = json.loads(digest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"SWE-bench Pro archive digest is not valid JSON: {digest_path}") from exc
    if not isinstance(digest_data, dict):
        raise ValueError(f"SWE-bench Pro archive digest must be a JSON object: {digest_path}")
    if not str(digest_data.get("sha256") or "").strip():
        raise ValueError(f"SWE-bench Pro archive digest is missing sha256: {digest_path}")
    if not str(digest_data.get("source_git_commit") or "").strip():
        raise ValueError(f"SWE-bench Pro archive digest is missing source_git_commit: {digest_path}")
    return digest_data


def _sample(config: HarnessRunConfig, case_id: str) -> SWEBenchProSample:
    sample = _samples(config).get(case_id)
    if sample is None:
        raise KeyError(f"SWE-bench Pro case does not exist: {case_id}")
    return sample


def _sample_from_row(row: dict[str, object], digest_data: dict[str, object]) -> SWEBenchProSample:
    return SWEBenchProSample(
        instance_id=str(row["instance_id"]),
        repo=str(row["repo"]),
        base_commit=str(row["base_commit"]),
        problem_statement=str(row.get("problem_statement") or ""),
        dockerhub_tag=str(row["dockerhub_tag"]),
        metadata={
            "archive_digest": digest_data.get("sha256"),
            "source_git_commit": digest_data.get("source_git_commit"),
        },
    )


def _public_problem_statement(sample: SWEBenchProSample) -> str:
    text = sample.problem_statement.strip()
    if not text:
        raise ValueError(f"SWE-bench Pro's public statement is empty:{sample.instance_id}")
    return f"{text}\n\nRepository: {sample.repo}\nBase commit: {sample.base_commit}"


def source_root(config: HarnessRunConfig) -> Path:
    """Returns read-only SWE-bench Pro source root."""
    return resolve_source_root(config.data_root, Path(__file__).resolve().parents[3], "swebench_pro")
