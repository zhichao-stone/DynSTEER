from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import time
from pathlib import Path

import docker

from dynsteer.adapter.skillsbench.adapter import SkillsBenchTask
from dynsteer.agent import ToolExecutionResult
from dynsteer.model import JsonObject
from dynsteer.runtime import DockerRunSpec, LocalDockerExecutor
from dynsteer.utils import (
    DockerProgressPrinter,
    docker_event_text,
    docker_image_archive_path,
    exclusive_file_lock,
    load_image_archive,
    safe_name,
    save_image_archive,
)


class SkillsBenchRuntime:
    def __init__(
        self,
        *,
        task: SkillsBenchTask,
        source_task_dir: Path,
        mirror_dir: Path,
        condition: str,
        command_timeout_seconds: int,
        platform: str | None,
    ):
        self.task = task
        self.source_task_dir = source_task_dir
        self.mirror_dir = mirror_dir
        self.condition = condition
        self.image_tag = f"dynsteer-skills:{task.source_digest[:16]}-{safe_name(task.case_id)}"
        self.start_state: JsonObject = {}
        resources = _docker_resources(task.sandbox)
        resources["platform"] = platform
        working_dir = _dockerfile_working_dir(source_task_dir / "environment" / "Dockerfile")
        command_timeout = max(
            command_timeout_seconds,
            int(task.agent_timeout_seconds),
            int(task.verifier_timeout_seconds),
        )
        self.executor = LocalDockerExecutor(
            DockerRunSpec(
                image=self.image_tag,
                working_dir=working_dir,
                command_timeout_seconds=command_timeout,
                **resources,
            ),
            container_name=f"dynsteer-skills-{safe_name(task.case_id)}-{_run_digest(mirror_dir)}",
        )

    def start(self) -> None:
        """镜像 task mirror，构建并启动本地任务容器。"""
        try:
            mirror_task(self.source_task_dir, self.mirror_dir)
            build_task_image(
                self.mirror_dir,
                self.image_tag,
                timeout_seconds=int(self.task.sandbox.get("build_timeout_sec", 600)),
                platform=self.executor.spec.platform,
            )
            self.executor.start()
            if self.condition == "with-skills":
                self.execute("mkdir -p /root/.dynsteer/skills")
                self.executor.put_directory(self.mirror_dir / "environment/skills", "/root/.dynsteer/skills")
            probe = self.executor.execute("pwd")
            self.start_state = {
                "status": "valid" if probe.exit_code == 0 else "container_probe_failed",
                "working_directory": probe.stdout.strip(),
            }
            if probe.exit_code != 0:
                raise RuntimeError(f"Skills 任务容器探测失败: {probe.stderr}")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            diagnostics = self.executor.write_diagnostics(
                error=error,
                output_dir=self.mirror_dir.parent,
            )
            self.start_state.setdefault("status", "infrastructure_failure")
            self.start_state.setdefault("error", error)
            self.start_state["docker"] = diagnostics
            self.executor.stop()
            raise

    def task_prompt(self) -> str:
        return self.task.description

    def system_prompt(self) -> str:
        skills = _skill_manifest(self.mirror_dir) if self.condition == "with-skills" else {}
        skill_text = ""
        if skills:
            entries = "\n".join(f"- {path}: {summary}" for path, summary in skills.items())
            skill_text = f"\nAvailable skill files are under /root/.dynsteer/skills:\n{entries}\n"
        return (
            "You are an agent working in a persistent Linux task container. "
            "Call at most one bash command per turn; stdout, stderr, and exit code are returned verbatim. "
            f"{skill_text}When the task is complete, return a short answer without calling bash again."
        )

    def execute(self, command: str) -> ToolExecutionResult:
        return self.executor.execute(command, timeout_seconds=int(self.task.agent_timeout_seconds))

    def run_verifier(self) -> ToolExecutionResult:
        """复制并执行官方 verifier。"""
        started = time.perf_counter()
        command = "/bin/bash /verifier/test.sh"
        try:
            self.execute("mkdir -p /verifier /logs/verifier")
            self.executor.put_directory(self.mirror_dir / "verifier", "/verifier")
            result = self.executor.execute(
                command,
                timeout_seconds=int(self.task.verifier_timeout_seconds),
            )
        except Exception as exc:
            result = ToolExecutionResult(
                command=command,
                stdout="",
                stderr=str(exc),
                exit_code=-1,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_type=type(exc).__name__,
            )
        raw_dir = self.mirror_dir.parent
        (raw_dir / "verifier.stdout.log").write_text(result.stdout, encoding="utf-8", errors="replace")
        (raw_dir / "verifier.stderr.log").write_text(result.stderr, encoding="utf-8", errors="replace")
        (raw_dir / "verifier_result.json").write_text(
            json.dumps({
                "exit_code": result.exit_code,
                "latency_ms": result.latency_ms,
                "timed_out": result.timed_out,
                "error_type": result.error_type,
            }, ensure_ascii=False, indent=4),
            encoding="utf-8",
        )
        return result

    def reward(self) -> float | None:
        try:
            value = self.executor.read_text_file("/logs/verifier/reward.txt").strip()
        except Exception:
            return None
        if value == "0":
            return 0.0
        if value == "1":
            return 1.0
        return None

    def metadata(self) -> JsonObject:
        return {
            "image": self.image_tag,
            "condition": self.condition,
            "source_digest": self.task.source_digest,
            "sandbox": self.task.sandbox,
            "start_state": self.start_state,
            "working_directory": self.executor.spec.working_dir,
            "storage_limit_applied": False,
        }

    def close(self) -> None:
        self.executor.stop()


def mirror_task(source: Path, destination: Path) -> None:
    """把只读任务内容复制到 DynSTEER run 目录。"""
    resolved_source = source.resolve()
    resolved_destination = destination.resolve()
    if not source.is_dir():
        raise FileNotFoundError(source)
    project_runs = Path(__file__).resolve().parents[3] / "runs"
    if not resolved_destination.is_relative_to(project_runs.resolve()):
        raise ValueError(f"Skills task mirror must stay under DynSTEER runs: {resolved_destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(resolved_destination)
    for relative in ("task.md", "environment", "verifier"):
        item = resolved_source / relative
        target = resolved_destination / relative
        if item.is_symlink():
            raise ValueError(f"Skills 任务包含 symlink: {item}")
        if item.is_file():
            if item.stat().st_nlink > 1:
                raise ValueError(f"Skills 任务包含 hardlink: {item}")
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_regular_file(item, target)
        elif item.is_dir():
            _copy_safe_directory(item, target)
        else:
            raise FileNotFoundError(item)


def _copy_safe_directory(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        destination = target / relative
        if item.is_symlink() or (item.is_file() and item.stat().st_nlink > 1):
            raise ValueError(f"Skills task input is not a regular file: {item}")
        if any(part == "groundtruth" for part in relative.parts):
            continue
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            _copy_regular_file(item, destination)


def _copy_regular_file(source: Path, target: Path) -> None:
    shutil.copyfile(source, target)
    if target.suffix not in {".sh", ".bash"}:
        return
    content = target.read_bytes()
    normalized = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if normalized != content:
        target.write_bytes(normalized)


def build_task_image(
    mirror: Path,
    tag: str,
    timeout_seconds: int,
    platform: str | None,
    progress_label: str | None = None,
) -> None:
    """构建任务镜像并保存构建日志。

    入参：
        mirror: 任务镜像的构建上下文目录。
        tag: 本地镜像 tag。
        timeout_seconds: Docker API 超时时间。
        platform: 目标平台，None 表示使用 daemon 默认值。
        progress_label: 预检进度标题；None 时保持静默。
    """
    project_runs = Path(__file__).resolve().parents[3] / "runs"
    archive = docker_image_archive_path(
        Path(__file__).resolve().parents[3].parent / "skillsbench_docker_images",
        tag,
    )
    logs: list[str] = []
    cache_hit = False
    archive_existed = True
    progress = DockerProgressPrinter(progress_label, single_line=True) if progress_label is not None else None
    if progress is not None:
        progress.start(f"等待镜像构建锁: {tag}")
    try:
        with exclusive_file_lock(_image_lock_path(project_runs / "skillsbench", tag)):
            archive_existed = archive.is_file()
            client = docker.from_env(timeout=timeout_seconds + 30)
            try:
                client.images.get(tag)
                cache_hit = True
                if not archive_existed:
                    save_image_archive(client, tag, archive)
                logs.append("image cache hit")
            except docker.errors.ImageNotFound:
                if archive_existed:
                    load_image_archive(client, tag, archive)
                else:
                    if progress is not None:
                        progress.start(f"构建 {tag}")
                    response = client.api.build(
                        path=str(mirror / "environment"),
                        tag=tag,
                        rm=True,
                        platform=platform,
                        decode=True,
                    )
                    events = [response] if isinstance(response, str) else response
                    for event in events:
                        text = docker_event_text(event)
                        logs.extend(text.splitlines())
                        if progress is not None:
                            progress.event(event)
                        if isinstance(event, dict):
                            detail = event.get("errorDetail")
                            message = detail.get("message") if isinstance(detail, dict) else None
                            error = event.get("error") or message
                            if error:
                                raise docker.errors.BuildError(str(error), iter(logs))
                    client.images.get(tag)
                    save_image_archive(client, tag, archive)
            if progress is not None:
                if cache_hit:
                    progress.finish("本地缓存命中，已保存压缩包" if not archive_existed else "本地缓存命中")
                else:
                    progress.finish("压缩包载入完成" if archive_existed else "构建完成，已保存压缩包")
    except Exception as exc:
        if progress is not None:
            progress.fail(f"{type(exc).__name__}: {exc}")
        (mirror.parent / "docker-build.log").write_text("\n".join(logs + [str(exc)]), encoding="utf-8")
        raise
    (mirror.parent / "docker-build.log").write_text("\n".join(logs), encoding="utf-8")


def _image_lock_path(lock_dir: Path, tag: str) -> Path:
    digest = hashlib.sha256(tag.encode("utf-8")).hexdigest()[:16]
    return lock_dir / ".image-build-locks" / f"{safe_name(tag)}-{digest}.lock"


def _docker_resources(sandbox: JsonObject) -> dict[str, object]:
    if float(sandbox.get("gpus", 0) or 0) != 0 or str(sandbox.get("os", "linux")).lower() != "linux":
        raise RuntimeError("当前 SkillsBench runtime 不支持 GPU 或非 Linux sandbox")
    options: dict[str, object] = {"network_mode": _network_mode(sandbox.get("network_mode"))}
    cpus = sandbox.get("cpus")
    if cpus is not None:
        options["nano_cpus"] = int(float(cpus) * 1_000_000_000)
    memory_mb = sandbox.get("memory_mb")
    if memory_mb is not None:
        options["memory_limit"] = f"{int(memory_mb)}m"
    return options


def _network_mode(value: object) -> str:
    return "none" if str(value or "").lower() in {"none", "no-network"} else "bridge"


def _dockerfile_working_dir(path: Path) -> str:
    matches = re.findall(r"(?im)^\s*WORKDIR\s+(.+)$", path.read_text(encoding="utf-8"))
    if not matches:
        return "/root"
    arguments = shlex.split(matches[-1])
    if not arguments:
        raise ValueError(f"Skills Dockerfile WORKDIR 为空: {path}")
    return arguments[-1]


def _skill_manifest(mirror: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for path in sorted((mirror / "environment/skills").rglob("SKILL.md")):
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        summary = next((line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")), "")
        manifest[path.relative_to(mirror / "environment/skills").as_posix()] = summary or hashlib.sha256(text.encode()).hexdigest()[:12]
    return manifest


def _run_digest(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:10]
