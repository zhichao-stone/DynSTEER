from __future__ import annotations

import hashlib
from pathlib import Path

from dynsteer.adapter.swebench_pro.adapter import SWEBenchProSample
from dynsteer.agent import ToolExecutionResult
from dynsteer.runtime import DockerRunSpec, LocalDockerExecutor
from dynsteer.model import JsonObject
from dynsteer.utils import docker_image_archive_path, safe_name


class SWEBenchProRuntime:
    def __init__(
        self,
        *,
        sample: SWEBenchProSample,
        raw_output_dir: Path,
        image_namespace: str,
        command_timeout_seconds: int,
        platform: str | None,
    ):
        self.sample = sample
        self.raw_output_dir = raw_output_dir
        image = swe_image_uri(sample.instance_id, image_namespace, sample.repo)
        image_archive = docker_image_archive_path(
            Path(__file__).resolve().parents[3].parent / "docker_images",
            image,
            sample.instance_id,
        )
        image_lock = _image_lock_path(image, sample.instance_id)
        self.executor = LocalDockerExecutor(
            DockerRunSpec(
                image=image,
                working_dir="/app",
                command_timeout_seconds=command_timeout_seconds,
                platform=platform,
                override_entrypoint=True,
                image_archive=image_archive,
                image_lock=image_lock,
            ),
            container_name=f"dynsteer-swe-{safe_name(sample.instance_id)}-{_run_digest(raw_output_dir)}",
        )
        self.probe: JsonObject = {}

    def start(self) -> None:
        """Activate the official image and record the initial repository status."""
        try:
            self.executor.start()
            self.probe = self.probe_environment()
            if self.probe.get("status") != "valid":
                raise RuntimeError(f"The official image initial state is not legal:{self.probe.get('status')}")
        except Exception as exc:
            if not self.probe:
                self.probe = {"status": "infrastructure_failure"}
            diagnostics = self.executor.write_diagnostics(
                error=f"{type(exc).__name__}: {exc}",
                output_dir=self.raw_output_dir,
            )
            self.probe.setdefault("error", str(exc))
            self.probe["docker"] = diagnostics
            self.executor.stop()
            raise

    def probe_environment(self) -> JsonObject:
        """Explores inside the image the root of the repository, the HEAD and the initial dirty state."""
        result = self.executor.execute(
            "printf '%s\\n' \"$PWD\"; git rev-parse --show-toplevel; git rev-parse HEAD; git status --short"
        )
        if result.exit_code != 0:
            return {"status": "image_probe_failed", "exit_code": result.exit_code, "stderr": result.stderr}
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if len(lines) < 3:
            return {"status": "image_probe_failed", "stdout_lines": len(lines)}
        working_directory, repository_root, head, *status = [line.strip() for line in lines]
        self.executor.set_working_dir(repository_root)
        valid = head == self.sample.base_commit
        return {
            "status": "valid" if valid else "image_state_invalid",
            "container_working_directory": working_directory,
            "working_directory": repository_root,
            "head": head,
            "expected_head": self.sample.base_commit,
            "initial_status": status,
        }

    def execute(self, command: str) -> ToolExecutionResult:
        return self.executor.execute(command)

    def collect_patch(self) -> str:
        """Collects all staged changes of the current repeat."""
        add = self.executor.execute("git add -A")
        diff = self.executor.execute("git diff --cached --binary")
        if add.exit_code != 0 or diff.exit_code != 0:
            raise RuntimeError(f"Catch collection failed: add={add.exit_code}, diff={diff.exit_code}")
        return diff.stdout

    def close(self) -> None:
        self.executor.stop()


def swe_image_uri(instance_id: str, namespace: str, repo: str) -> str:
    """The official re-engineer URI rule does not import external help_code."""
    repo_base, repo_name_only = repo.lower().split("/", maxsplit=1)
    value_hash = instance_id.replace("instance_", "")
    if instance_id == "instance_element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan":
        repo_name_only = "element-web"
    elif "element-hq" in repo.lower() and "element-web" in repo.lower():
        repo_name_only = "element"
        if value_hash.endswith("-vnan"):
            value_hash = value_hash[:-5]
    elif value_hash.endswith("-vnan"):
        value_hash = value_hash[:-5]
    tag = f"{repo_base}.{repo_name_only}-{value_hash}"[:128]
    return f"{namespace}/sweap-images:{tag}"


def patch_summary(patch: str) -> JsonObject:
    return {
        "bytes": len(patch.encode("utf-8")),
        "line_count": len(patch.splitlines()),
        "non_empty": bool(patch.strip()),
        "sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        "changed_file_count": patch.count("\ndiff --git ") + (1 if patch.startswith("diff --git ") else 0),
    }


def task_prompt(sample: SWEBenchProSample) -> str:
    return (
        f"{sample.problem_statement.strip()}\n\n"
        f"Repository: {sample.repo}\n"
        f"Base commit: {sample.base_commit}\n\n"
        "Use the bash tool to inspect and modify the repository. "
        "When the work is complete, return a short summary without calling bash again."
    )


def _run_digest(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:10]


def _image_lock_path(image: str, case_id: str) -> Path:
    lock_dir = Path(__file__).resolve().parents[3] / "runs" / "swebench_pro" / ".image-pull-locks"
    digest = hashlib.sha256(image.encode("utf-8")).hexdigest()[:16]
    return lock_dir / f"{safe_name(case_id)}-{digest}.lock"
