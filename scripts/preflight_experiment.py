from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import docker

from dynsteer.adapter.skillsbench.runtime import build_task_image, mirror_task
from dynsteer.adapter.swebench_pro.runtime import swe_image_uri
from dynsteer.utils import (
    DockerProgressPrinter,
    docker_image_archive_path,
    exclusive_file_lock,
    load_image_archive,
    optional_str,
    read_json_file,
    safe_name,
    save_image_archive,
)
from dynsteer.experiment.config import load_experiment_config


NATIVE_BENCHMARKS = frozenset({"swebench_pro", "skillsbench"})


class DockerImagePreparationError(RuntimeError):
    """单个任务镜像准备失败时抛出，运行期可继续生成逐 case 失败记录。"""

    def __init__(self, failures: list[str]) -> None:
        self.failures = tuple(failures)
        super().__init__("; ".join(self.failures))


@dataclass(frozen=True)
class PreflightContext:
    """实验预检所需的解析结果。"""

    config_path: Path
    project_root: Path
    profile: str
    benchmark: str
    data_root: Path
    source_root: Path
    case_ids: tuple[str, ...]
    models: tuple[str, ...]
    methods: tuple[str, ...]
    repeats: int
    runs_dir: Path
    only_adapt: bool
    force_eval: bool
    prepare_images: bool
    sources_only: bool


def main(argv: list[str] | None = None) -> int:
    """执行启动前预检并输出 JSON 摘要。"""
    args = _parse_args(argv)
    failures: list[str] = []
    image_failures: list[str] = []
    checks: dict[str, str] = {}
    context: PreflightContext | None = None
    try:
        context = load_context(args.exp, args.profile)
        context = PreflightContext(**{**context.__dict__, "only_adapt": args.only_adapt})
        context = PreflightContext(**{**context.__dict__, "force_eval": args.force_eval})
        context = PreflightContext(**{**context.__dict__, "prepare_images": args.prepare_runtime_images})
        context = PreflightContext(**{**context.__dict__, "sources_only": args.sources_only})
    except Exception as exc:
        _fail_load(args, exc, failures)
    if context is not None:
        checks["config"] = "passed"
        if not context.sources_only:
            _run_check("python_dependencies", failures, checks, lambda: validate_python_dependencies(context))
            _run_check("model_credentials", failures, checks, lambda: validate_model_credentials(read_json_file(context.config_path, "experiment config", dict)))
            _run_check("cases", failures, checks, lambda: validate_cases(context))
            if context.profile == "docker":
                try:
                    validate_docker(context, context.prepare_images)
                    checks["docker"] = "passed"
                except DockerImagePreparationError as exc:
                    checks["docker"] = "passed_with_image_failures"
                    image_failures.extend(exc.failures)
                except Exception as exc:
                    checks["docker"] = "failed"
                    failures.append(f"docker: {type(exc).__name__}: {exc}")
            elif context.profile == "host":
                _run_check("host_capability", failures, checks, lambda: validate_host_capability(context))
    print(json.dumps({
        "benchmark": context.benchmark if context is not None else None,
        "profile": args.profile,
        "case_count": len(context.case_ids) if context is not None else 0,
        "models": list(context.models) if context is not None else [],
        "methods": list(context.methods) if context is not None else [],
        "checks": checks,
        "failures": failures,
        "image_failures": image_failures,
    }, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


def load_context(path: Path, profile: str) -> PreflightContext:
    """读取实验配置并解析唯一 benchmark 运行上下文。"""
    config_path = path.resolve()
    project_root = Path(__file__).resolve().parents[1]
    config = load_experiment_config(config_path)
    benchmarks = config.get("benchmarks")
    if not isinstance(benchmarks, list) or len(benchmarks) != 1 or not isinstance(benchmarks[0], dict):
        raise ValueError("experiment config must contain exactly one benchmark spec")
    spec = benchmarks[0]
    benchmark = optional_str(spec.get("benchmark"))
    if benchmark is None:
        raise ValueError("benchmark cannot be empty")
    data_root = Path(str(spec.get("data_root") or f"data/{benchmark}"))
    if not data_root.is_absolute():
        configured_from_experiment = config_path.parent / data_root
        data_root = (
            configured_from_experiment
            if configured_from_experiment.exists()
            else project_root / data_root
        )
    manifest = read_json_file(data_root / "benchmark.json", "benchmark manifest", dict)
    source_value = optional_str(manifest.get("source_root"))
    if source_value is None:
        raise ValueError("benchmark manifest source_root cannot be empty")
    source_root = Path(source_value)
    if not source_root.is_absolute():
        source_root = project_root / source_root
    raw_cases = spec.get("case_ids")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("formal experiment case_ids must be a non-empty list")
    case_ids = tuple(str(item) for item in raw_cases)
    raw_models = config.get("models")
    if not isinstance(raw_models, list) or not raw_models:
        raise ValueError("experiment models cannot be empty")
    models = tuple(str(item.get("model_id")) for item in raw_models if isinstance(item, dict))
    raw_methods = config.get("methods")
    if not isinstance(raw_methods, list) or not raw_methods:
        raise ValueError("experiment methods cannot be empty")
    repeats = int(config.get("repeats", 1))
    if repeats < 1:
        raise ValueError("experiment repeats must be positive")
    experiment_id = optional_str(config.get("experiment_id")) or config_path.stem
    runs_value = config.get("runs_dir", Path("runs") / "exp" / experiment_id)
    runs_dir = Path(str(runs_value))
    if not runs_dir.is_absolute():
        runs_dir = project_root / runs_dir
    return PreflightContext(
        config_path=config_path,
        project_root=project_root,
        profile=profile,
        benchmark=benchmark,
        data_root=data_root.resolve(),
        source_root=source_root.resolve(),
        case_ids=case_ids,
        models=models,
        methods=tuple(_method_name(item) for item in raw_methods),
        repeats=repeats,
        runs_dir=runs_dir.resolve(),
        only_adapt=False,
        force_eval=False,
        prepare_images=False,
        sources_only=False,
    )


def validate_python_dependencies(context: PreflightContext) -> None:
    """用当前解释器验证 benchmark 运行依赖。"""
    required = ["openai", "dynsteer.adapter.registry"]
    if context.benchmark == "toolsandbox":
        required.extend(("tool_sandbox", "tool_sandbox.roles.anthropic_api_agent"))
    elif context.benchmark == "swebench_pro":
        required.extend(("docker", "pandas", "polars"))
    elif context.benchmark == "skillsbench":
        required.extend(("docker", "yaml"))
    missing: list[str] = []
    source_root = str(context.source_root)
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    for module_name in required:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            missing.append(f"{module_name}: {type(exc).__name__}: {exc}")
    if missing:
        raise RuntimeError("; ".join(missing))


def validate_model_credentials(config: dict[str, object]) -> None:
    """校验实验矩阵中的明文模型与 Judge 凭据。"""
    benchmark = str(config["benchmarks"][0]["benchmark"])
    for model in config.get("models", []):
        if not isinstance(model, dict):
            raise ValueError("model spec must be an object")
        model_id = str(model.get("model_id") or "")
        metadata = model.get("harness_metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        client_key = "agent_client" if benchmark == "toolsandbox" else "client"
        client = metadata.get(client_key)
        if not isinstance(client, dict):
            raise ValueError(f"model {model_id} lacks harness_metadata.{client_key}")
        if benchmark != "toolsandbox" and str(client.get("model") or "") != model_id:
            raise ValueError(f"model {model_id} client.model must match model_id")
        for field in ("api_key", "base_url"):
            if optional_str(client.get(field)) is None:
                raise ValueError(f"model {model_id} client.{field} cannot be empty")
        if not str(client["base_url"]).startswith("https://"):
            raise ValueError(f"model {model_id} base_url must use https")
    judge_profiles = config.get("judge_profiles")
    if judge_profiles is None:
        return
    if not isinstance(judge_profiles, dict):
        raise ValueError("judge_profiles must be an object")
    for name, profile in judge_profiles.items():
        if not isinstance(profile, dict):
            raise ValueError(f"judge profile {name} must be an object")
        for field in ("provider", "model", "api_key", "base_url"):
            if optional_str(profile.get(field)) is None:
                raise ValueError(f"judge profile {name}.{field} cannot be empty")
        if not str(profile["base_url"]).startswith("https://"):
            raise ValueError(f"judge profile {name} base_url must use https")


def _method_name(item: object) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and optional_str(item.get("method")) is not None:
        return str(item["method"])
    raise ValueError("experiment method must be a string or an object with method")



def validate_sources(context: PreflightContext) -> None:
    """按 benchmark 校验本地源码与快照的只读输入。"""
    if not context.source_root.is_dir():
        raise FileNotFoundError(f"source root not found: {context.source_root}")
    if context.benchmark == "toolsandbox":
        if not (context.source_root / "tool_sandbox").is_dir():
            raise FileNotFoundError("ToolSandbox package entry not found")
        return
    if context.benchmark == "swebench_pro":
        required = (context.source_root / "swe_bench_pro_eval.py", context.source_root / "helper_code" / "image_uri.py")
        missing = [str(path) for path in required if not path.is_file()]
        samples_path = context.data_root / "source" / "eval_samples.jsonl"
        if not samples_path.is_file():
            missing.append("data/swebench_pro/source/eval_samples.jsonl (run scripts/prepare_swebench_pro.py)")
        else:
            sample_ids = _swe_sample_ids(samples_path)
            unknown = sorted(set(context.case_ids) - sample_ids)
            if unknown:
                missing.append(f"unknown SWE cases: {', '.join(unknown[:10])}")
            for case_id in context.case_ids:
                missing.extend(
                    str(path.relative_to(context.source_root))
                    for path in (
                        context.source_root / "run_scripts" / case_id / "run_script.sh",
                        context.source_root / "run_scripts" / case_id / "parser.py",
                    )
                    if not path.is_file()
                )
        if missing:
            raise FileNotFoundError("; ".join(missing[:30]))
        return
    if context.benchmark != "skillsbench":
        raise ValueError(f"unsupported benchmark: {context.benchmark}")
    missing: list[str] = []
    for case_id in context.case_ids:
        task_dir = context.source_root / "tasks" / case_id
        required = (task_dir / "task.md", task_dir / "environment" / "Dockerfile", task_dir / "verifier" / "test.sh")
        missing.extend(f"{case_id}/{path.name}" for path in required if not path.is_file())
        if (task_dir / "verifier" / "test.sh").is_file():
            missing.extend(_missing_verifier_scripts(task_dir))
    if missing:
        raise FileNotFoundError("; ".join(missing[:30]))


def validate_cases(context: PreflightContext) -> None:
    """校验 case 列表唯一且属于当前 benchmark。"""
    if not context.case_ids:
        raise ValueError("case_ids cannot be empty")
    if len(context.case_ids) != len(set(context.case_ids)):
        raise ValueError("case_ids contains duplicates")
    if context.benchmark == "swebench_pro":
        sample_ids = _swe_sample_ids(context.data_root / "source" / "eval_samples.jsonl")
        unknown = sorted(set(context.case_ids) - sample_ids)
        if unknown:
            raise ValueError(f"unknown SWE cases: {', '.join(unknown[:10])}")


def validate_docker(context: PreflightContext, prepare_images: bool) -> None:
    """校验 Docker 能力并按需准备任务镜像。"""
    if context.benchmark not in NATIVE_BENCHMARKS:
        return
    client = docker.from_env(timeout=60)
    client.ping()
    if prepare_images and context.benchmark == "swebench_pro":
        prepare_swe_images(context)
    elif prepare_images and context.benchmark == "skillsbench":
        prepare_skills_images(context)


def validate_host_capability(context: PreflightContext) -> None:
    """校验 host profile 是否只请求可离线执行的能力。"""
    if context.benchmark not in NATIVE_BENCHMARKS or context.only_adapt:
        return
    if "default" not in context.methods:
        return
    missing = _missing_default_trajectories(context)
    if missing:
        preview = ", ".join(missing[:30])
        suffix = "" if len(missing) <= 30 else f" and {len(missing) - 30} more"
        raise RuntimeError(f"host profile lacks default trajectories: {preview}{suffix}")


def prepare_swe_images(context: PreflightContext) -> None:
    """预准备选中的 SWE 官方镜像，单个失败不阻断其他 case。"""
    namespace = _swe_namespace(context)
    client = docker.from_env(timeout=600)
    samples = _swe_samples(context.data_root / "source" / "eval_samples.jsonl")
    platform = os.environ.get("DYNSTEER_DOCKER_PLATFORM") or None
    lock_dir = context.project_root / "runs" / "swebench_pro"
    total = len(context.case_ids)
    failures: list[str] = []
    for index, case_id in enumerate(context.case_ids, start=1):
        sample = samples[case_id]
        image = swe_image_uri(case_id, namespace, str(sample["repo"]))
        archive = docker_image_archive_path(
            context.project_root.parent / "docker_images",
            image,
            case_id,
        )
        progress = DockerProgressPrinter(f"SWE-bench-Pro 镜像 {index}/{total}", single_line=True)
        progress.start(f"等待 case 镜像锁: {case_id}")
        try:
            with exclusive_file_lock(_image_lock_path(lock_dir, image, case_id)):
                progress.start(f"检查 {image}")
                try:
                    try:
                        client.images.get(image)
                        if archive.is_file():
                            progress.finish("本地缓存命中")
                        else:
                            save_image_archive(client, image, archive)
                            progress.finish("本地缓存命中，已保存压缩包")
                    except docker.errors.ImageNotFound:
                        if archive.is_file():
                            load_image_archive(client, image, archive)
                            progress.finish("压缩包载入完成")
                        else:
                            for event in client.api.pull(
                                repository=image,
                                stream=True,
                                decode=True,
                                platform=platform,
                            ):
                                progress.event(event)
                            client.images.get(image)
                            save_image_archive(client, image, archive)
                            progress.finish("拉取完成，已保存压缩包")
                except Exception as exc:
                    progress.fail(f"{type(exc).__name__}: {exc}")
                    failures.append(f"{case_id}: {type(exc).__name__}: {exc}")
        except Exception as exc:
            failures.append(f"{case_id}: {type(exc).__name__}: {exc}")
    if failures:
        raise DockerImagePreparationError(failures)


def prepare_skills_images(context: PreflightContext) -> None:
    """在 DynSTEER mirror 中构建选中的 Skills 任务镜像，单个失败不阻断其他 case。"""
    total = len(context.case_ids)
    failures: list[str] = []
    for index, case_id in enumerate(context.case_ids, start=1):
        source = context.source_root / "tasks" / case_id
        mirror = context.project_root / "runs" / "skillsbench" / "preflight-images" / context.config_path.stem / case_id
        mirror_task(source, mirror)
        digest = _directory_digest(source)
        tag = f"dynsteer-skills:{digest[:16]}-{safe_name(case_id)}"
        try:
            build_task_image(
                mirror,
                tag,
                timeout_seconds=900,
                platform=os.environ.get("DYNSTEER_DOCKER_PLATFORM"),
                progress_label=f"SkillsBench 镜像 {index}/{total}: {case_id}",
            )
        except Exception as exc:
            failures.append(f"{case_id}: {type(exc).__name__}: {exc}")
    if failures:
        raise DockerImagePreparationError(failures)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preflight a DynSTEER benchmark experiment")
    parser.add_argument("--exp", required=True, type=Path)
    parser.add_argument("--profile", required=True, choices=("docker", "host", "source"))
    parser.add_argument("--prepare-runtime-images", action="store_true")
    parser.add_argument("--no-prepare-images", action="store_true")
    parser.add_argument("--only-adapt", action="store_true")
    parser.add_argument("--force-eval", action="store_true")
    parser.add_argument("--sources-only", action="store_true")
    args = parser.parse_args(argv)
    if args.prepare_runtime_images and args.no_prepare_images:
        parser.error("--prepare-runtime-images and --no-prepare-images are mutually exclusive")
    return args


def _run_check(name: str, failures: list[str], checks: dict[str, str], operation) -> None:
    try:
        operation()
        checks[name] = "passed"
    except Exception as exc:
        checks[name] = "failed"
        failures.append(f"{name}: {type(exc).__name__}: {exc}")


def _fail_load(args: argparse.Namespace, exc: Exception, failures: list[str]) -> NoReturn:
    failures.append(f"config: {type(exc).__name__}: {exc}")
    print(json.dumps({
        "benchmark": None,
        "profile": args.profile,
        "case_count": 0,
        "models": [],
        "methods": [],
        "checks": {"config": "failed"},
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    raise SystemExit(1)


def _swe_sample_ids(path: Path) -> set[str]:
    return set(_swe_samples(path))


def _swe_samples(path: Path) -> dict[str, dict[str, object]]:
    samples: dict[str, dict[str, object]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        sample = json.loads(line)
        samples[str(sample["instance_id"])] = sample
    return samples


def _swe_namespace(context: PreflightContext) -> str:
    manifest = read_json_file(context.data_root / "benchmark.json", "benchmark manifest", dict)
    return str(manifest["dockerhub_username"])


def _image_lock_path(lock_dir: Path, image: str, case_id: str) -> Path:
    digest = hashlib.sha256(image.encode("utf-8")).hexdigest()[:16]
    return lock_dir / ".image-pull-locks" / f"{safe_name(case_id)}-{digest}.lock"


def _missing_default_trajectories(context: PreflightContext) -> list[str]:
    missing: list[str] = []
    for model_id in context.models:
        for repeat_index in range(context.repeats):
            for case_id in context.case_ids:
                trajectory = (
                    context.runs_dir / context.benchmark / model_id / f"r{repeat_index}"
                    / "default" / case_id / "trajectory.json"
                )
                if context.force_eval or not trajectory.is_file():
                    missing.append(f"({model_id}, {repeat_index}, {case_id})")
    return missing


def _directory_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _missing_verifier_scripts(task_dir: Path) -> list[str]:
    script = task_dir / "verifier" / "test.sh"
    missing: list[str] = []
    for name in sorted(set(re.findall(r"(?<=/verifier/)[A-Za-z0-9_./-]+\.py", script.read_text(encoding="utf-8")))):
        if not (task_dir / "verifier" / Path(name).name).is_file():
            missing.append(f"{task_dir.name}/verifier/{Path(name).name}")
    return missing


if __name__ == "__main__":
    raise SystemExit(main())
