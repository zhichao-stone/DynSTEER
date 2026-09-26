from __future__ import annotations

import base64
import io
import json
import shlex
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import docker

from dynsteer.agent import ToolExecutionResult
from dynsteer.utils import exclusive_file_lock, load_image_archive


@dataclass(frozen=True)
class DockerRunSpec:
    image: str
    working_dir: str
    command_timeout_seconds: int
    network_mode: str = "bridge"
    nano_cpus: int | None = None
    memory_limit: str | None = None
    platform: str | None = None
    override_entrypoint: bool = False
    image_archive: Path | None = None
    image_lock: Path | None = None


class LocalDockerExecutor:
    def __init__(self, spec: DockerRunSpec, container_name: str) -> None:
        if spec is None or not container_name.strip():
            raise ValueError('spec and container_name must not be empty.')
        self._spec = spec
        self._container_name = container_name.strip()
        self._client = docker.from_env(timeout=spec.command_timeout_seconds + 30)
        self._container = None
        self._start_error: str | None = None

    @property
    def spec(self) -> DockerRunSpec:
        """Returns the current execution configuration."""
        return self._spec

    def start(self) -> None:
        """Loads the local image cache and activates the permanent mission container."""
        image = self._spec.image
        self._start_error = None
        try:
            try:
                self._client.images.get(image)
            except docker.errors.ImageNotFound:
                self._ensure_image(image)
            run_options: dict[str, object] = {
                "image": image,
                "name": self._container_name,
                "detach": True,
                "tty": False,
                "stdin_open": False,
                "auto_remove": False,
                "command": ["/bin/sh", "-c", "sleep infinity"],
                "working_dir": self._spec.working_dir,
                "network_mode": self._spec.network_mode,
            }
            if self._spec.override_entrypoint:
                run_options["entrypoint"] = ["/bin/bash"]
                run_options["command"] = ["-c", "sleep infinity"]
            if self._spec.nano_cpus is not None:
                run_options["nano_cpus"] = self._spec.nano_cpus
            if self._spec.memory_limit is not None:
                run_options["mem_limit"] = self._spec.memory_limit
            if self._spec.platform is not None:
                run_options["platform"] = self._spec.platform
            self._container = self._client.containers.run(**run_options)
            state = self._container_state()
            if state.get("Status") != "running":
                raise RuntimeError(
                    f"Mission container is not running status after startup:{state.get('Status')}, "
                    f"exit_code={state.get('ExitCode')}"
                )
        except Exception as exc:
            self._start_error = f"{type(exc).__name__}: {exc}"
            raise

    def diagnostics(self, error: str | None = None) -> dict[str, object]:
        """Collects the bottom state and tail logs needed to start the container failed."""
        state = self._container_state()
        diagnostics: dict[str, object] = {
            "container_name": self._container_name,
            "container_state": state,
            "exit_code": state.get("ExitCode"),
            "container_error": state.get("Error"),
            "start_error": self._start_error,
        }
        if self._container is not None:
            diagnostics["container_id"] = self._container.short_id
        if error is not None:
            diagnostics["error"] = error
        try:
            diagnostics["logs_tail"] = self.collect_logs()[-8192:]
        except Exception as exc:
            diagnostics["logs_error"] = f"{type(exc).__name__}: {exc}"
        return diagnostics

    def write_diagnostics(self, *, error: str, output_dir: Path) -> dict[str, object]:
        """Collects diagnostics and writes to the specified output directory."""
        diagnostics = self.diagnostics(error)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "docker_diagnostics.json").write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=4),
            encoding="utf-8",
        )
        return diagnostics

    def set_working_dir(self, working_dir: str) -> None:
        """Updates the work directory for the follow-up exec."""
        if not working_dir.strip():
            raise ValueError('Working_dir cannot be empty')
        self._spec = DockerRunSpec(
            image=self._spec.image,
            working_dir=working_dir,
            command_timeout_seconds=self._spec.command_timeout_seconds,
            network_mode=self._spec.network_mode,
            nano_cpus=self._spec.nano_cpus,
            memory_limit=self._spec.memory_limit,
            platform=self._spec.platform,
            override_entrypoint=self._spec.override_entrypoint,
            image_archive=self._spec.image_archive,
            image_lock=self._spec.image_lock,
        )

    def execute(self, command: str, timeout_seconds: int | None = None) -> ToolExecutionResult:
        """Execute a bash command in a long-stay container."""
        timeout = int(timeout_seconds or self._spec.command_timeout_seconds)
        started = time.perf_counter()
        try:
            if self._container is None:
                raise RuntimeError('Docker executor not started')
            result = self._container.exec_run(
                ["timeout", "--signal=KILL", str(timeout), "/bin/bash", "-lc", command],
                workdir=self._spec.working_dir,
                demux=True,
            )
            latency = int((time.perf_counter() - started) * 1000)
            stdout = _decode(result.output[0])
            stderr = _decode(result.output[1] if len(result.output) > 1 else b"")
            exit_code = int(result.exit_code) if result.exit_code is not None else -1
            return ToolExecutionResult(
                command=command,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                latency_ms=latency,
                timed_out=exit_code == 124,
            )
        except Exception as exc:
            latency = int((time.perf_counter() - started) * 1000)
            return ToolExecutionResult(
                command=command,
                stdout="",
                stderr=str(exc),
                exit_code=-1,
                latency_ms=latency,
                error_type=type(exc).__name__,
            )

    def put_directory(self, source: Path, target: str) -> None:
        """Copy a directory through an in-memory tar archive without a host bind mount."""
        if self._container is None:
            raise RuntimeError('Docker executor not started')
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for path in source.rglob("*"):
                relative = path.relative_to(source).as_posix()
                _assert_safe_archive_path(relative)
                if path.is_symlink():
                    raise ValueError(f"Task image input contains a symlink: {path}")
                info = tar.gettarinfo(str(path), arcname=relative)
                info.uid = 0
                info.gid = 0
                info.uname = "root"
                info.gname = "root"
                if path.is_file():
                    with path.open("rb") as stream:
                        tar.addfile(info, stream)
                else:
                    tar.addfile(info)
        archive.seek(0)
        if not self._container.put_archive(target, archive):
            raise RuntimeError(f"failed to copy task directory: {source} -> {target}")

    def read_text_file(self, target: str) -> str:
        """Reads the container text file."""
        result = self.execute(f"base64 -w0 {shlex.quote(target)}")
        if result.exit_code != 0:
            raise RuntimeError(f"failed to read container file: {target}: {result.stderr}")
        return base64.b64decode(result.stdout, validate=True).decode("utf-8", errors="replace")

    def collect_logs(self) -> str:
        """Reads the container log."""
        if self._container is None:
            return ""
        return _decode(self._container.logs(stderr=False, stdout=True)) + "\n" + _decode(
            self._container.logs(stderr=True, stdout=False)
        )

    def stop(self) -> None:
        """Mandatory destruction of mission containers."""
        if self._container is None:
            return
        container = self._container
        self._container = None
        container.remove(force=True, v=True)

    def _container_state(self) -> dict[str, object]:
        if self._container is None:
            return {}
        try:
            self._container.reload()
            value = self._container.attrs.get("State", {})
            return dict(value) if isinstance(value, dict) else {"Value": value}
        except Exception as exc:
            return {"Status": "unknown", "Error": f"{type(exc).__name__}: {exc}"}

    def _ensure_image(self, image: str) -> None:
        """Recover the image from the local compressed cache when it is missing."""
        if self._spec.image_lock is not None:
            with exclusive_file_lock(self._spec.image_lock):
                self._ensure_image_unlocked(image)
            return
        self._ensure_image_unlocked(image)

    def _ensure_image_unlocked(self, image: str) -> None:
        """Recover images only from the local compressed cache and never contact a remote registry during experiments."""
        archive = self._spec.image_archive
        if archive is not None and archive.is_file():
            try:
                load_image_archive(self._client, image, archive)
            except Exception as exc:
                raise RuntimeError(
                    f"Local image cache not available:{image}; archive={archive}; {exc}"
                ) from exc
            return
        archive_label = str(archive) if archive is not None else 'not configured'
        raise RuntimeError(f"local image cache is missing: {image}; archive={archive_label}")


def _decode(value: bytes | None) -> str:
    return (value or b"").decode("utf-8", errors="replace")


def _assert_safe_archive_path(relative: str) -> None:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"task image input path is not secure:{relative}")



