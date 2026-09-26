from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from dynsteer.model import JsonObject


@dataclass(frozen=True)
class NativeEvaluationResult:
    score: float | None
    resolved: bool | None
    available: bool
    output_path: Path | None
    returncode: int | None
    failure_type: str | None
    summary: JsonObject


def evaluate_patch(
    *,
    source_root: Path,
    data_root: Path,
    instance_id: str,
    patch: str,
    output_dir: Path,
    image_namespace: str,
    execution_profile: str,
    docker_platform: str | None,
) -> NativeEvaluationResult:
    """以子进程调用外部源码 evaluator，并返回原生评测结果。"""
    source_root = source_root.resolve()
    data_root = data_root.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir = output_dir.resolve()
    patch_path = output_dir / "patch.json"
    native_dir = output_dir / "native"
    try:
        patch_path.write_text(json.dumps([{"instance_id": instance_id, "patch": patch}]), encoding="utf-8")
    except OSError as exc:
        return _failure(None, None, "patch_write_failed", str(exc))
    command = [
        sys.executable,
        str(source_root / "swe_bench_pro_eval.py"),
        "--raw_sample_path", "eval_samples.jsonl",
        "--patch_path", str(patch_path),
        "--output_dir", str(native_dir),
        "--dockerhub_username", image_namespace,
        "--scripts_dir", str(source_root / "run_scripts"),
        "--num_workers", "1",
    ]
    if execution_profile == "docker":
        command.append("--use_local_docker")
    if docker_platform:
        command.extend(["--docker_platform", docker_platform])
    try:
        process = subprocess.run(
            command,
            cwd=data_root / "source",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _failure(None, None, "evaluator_process_failed", str(exc), command)
    (output_dir / "native_stdout.log").write_text(process.stdout, encoding="utf-8")
    (output_dir / "native_stderr.log").write_text(process.stderr, encoding="utf-8")
    case_output = native_dir / instance_id / "_output.json"
    results_path = native_dir / "eval_results.json"
    if not case_output.is_file():
        failure = "evaluator_process_failed" if process.returncode != 0 else "native_output_missing"
        return _failure(process.returncode, None, failure, process.stderr, command)
    try:
        resolved_value = json.loads(results_path.read_text(encoding="utf-8")).get(instance_id)
        json.loads(case_output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _failure(process.returncode, str(case_output), "native_output_invalid", str(exc), command)
    stdout_path = output_dir / "native_stdout.log"
    stderr_path = output_dir / "native_stderr.log"
    if resolved_value is not True and process.returncode != 0:
        return _failure(
            process.returncode,
            str(case_output),
            "evaluator_process_failed",
            process.stderr,
            command,
        )
    if resolved_value is not True and not (stdout_path.is_file() and stderr_path.is_file()):
        return _failure(
            process.returncode,
            str(case_output),
            "native_output_incomplete",
            "evaluator stdout/stderr missing",
            command,
        )
    resolved = resolved_value is True
    score = 1.0 if resolved else 0.0
    return NativeEvaluationResult(
        score=score,
        resolved=resolved,
        available=True,
        output_path=case_output,
        returncode=process.returncode,
        failure_type=None,
        summary={
            "evaluator": "swe_bench_pro_eval.py",
            "returncode": process.returncode,
            "output_path": str(case_output.relative_to(output_dir)),
            "results_path": str(results_path.relative_to(output_dir)),
        },
    )


def _failure(
    returncode: int | None,
    output_path: str | None,
    failure_type: str,
    detail: str,
    command: list[str] | None = None,
) -> NativeEvaluationResult:
    return NativeEvaluationResult(
        score=None,
        resolved=None,
        available=False,
        output_path=Path(output_path) if output_path else None,
        returncode=returncode,
        failure_type=failure_type,
        summary={"detail": detail, "command": command or []},
    )
