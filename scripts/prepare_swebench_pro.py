from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import polars


EVALUATOR_FIELDS = (
    "instance_id", "repo", "base_commit", "before_repo_set_cmd",
    "fail_to_pass", "pass_to_pass", "selected_test_files_to_run", "dockerhub_tag",
)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="准备 SWE-bench Pro 本地数据快照")
    parser.add_argument("--project-root", default=PROJECT_ROOT, type=Path)
    parser.add_argument("--data-root", default="data/swebench_pro", type=Path)
    parser.add_argument("--source-root", default="../SWE-bench_Pro-os", type=Path)
    parser.add_argument("--archive", default="../../swebench_pro_data.zip", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        project_root = args.project_root.resolve()
        data_root = _resolve_relative(args.data_root, project_root)
        prepare_snapshot(
            project_root=project_root,
            data_root=data_root,
            source_root=_resolve_relative(args.source_root, project_root),
            archive=_resolve_relative(args.archive, data_root),
            force=args.force,
        )
    except Exception as exc:
        _write_error(f"SWE-bench Pro 数据准备失败: {exc}")
        return 1
    return 0


def _resolve_relative(path: Path, base: Path) -> Path:
    return path if path.is_absolute() else base / path


def _write_error(message: str) -> None:
    sys.stderr.buffer.write(message.encode("utf-8", errors="replace") + b"\n")


def prepare_snapshot(
    *,
    project_root: Path,
    data_root: Path,
    source_root: Path,
    archive: Path,
    force: bool,
) -> None:
    """生成并校验本地数据快照与 evaluator 输入。"""
    if not archive.is_file():
        raise FileNotFoundError(f"缺少本地数据包: {archive}")
    source_dir = data_root / "source"
    digest_path = source_dir / ".archive_digest"
    digest = _archive_digest(archive)
    source_commit = _source_commit(source_root)
    metadata = {"sha256": digest, "source_git_commit": source_commit}
    if not force and digest_path.is_file():
        try:
            current = json.loads(digest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"SWE-bench Pro archive digest 不是合法 JSON: {digest_path}") from exc
        required = [
            source_dir / "data/test-00000-of-00001.parquet",
            source_dir / "dockerfiles/base_dockerfile",
            source_dir / "dockerfiles/instance_dockerfile",
            source_dir / "eval_samples.jsonl",
        ]
        if (
            current.get("sha256") == digest
            and current.get("source_git_commit") == source_commit
            and all(path.exists() for path in required)
        ):
            print("SWE-bench Pro 快照已就绪，跳过重建")
            return
        _remove_directory(source_dir, project_root)
    else:
        _remove_directory(source_dir, project_root)
    _extract_archive(archive, source_dir)
    samples = _write_eval_samples(source_dir)
    _validate_source(source_root, source_dir, [sample["instance_id"] for sample in samples])
    metadata["created_at"] = datetime.now(timezone.utc).isoformat()
    digest_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=4), encoding="utf-8")


def _archive_digest(archive: Path) -> str:
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_archive(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        seen_names: set[str] = set()
        for member in bundle.infolist():
            if member.filename in seen_names:
                raise ValueError(f"数据包包含重复路径: {member.filename}")
            seen_names.add(member.filename)
            target = _safe_zip_destination(destination, member.filename)
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"数据包包含 symlink: {member.filename}")
            _write_zip_member(archive, member, target)


def _safe_zip_destination(destination: Path, member_name: str) -> Path:
    if "\\" in member_name:
        raise ValueError(f"数据包路径不安全: {member_name}")
    path = PurePosixPath(member_name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"数据包路径不安全: {member_name}")
    return destination.joinpath(*path.parts)


def _write_zip_member(archive: Path, member: zipfile.ZipInfo, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if member.is_dir():
        destination.mkdir(exist_ok=True)
        return
    with zipfile.ZipFile(archive) as bundle, bundle.open(member) as source, destination.open("wb") as target:
        shutil.copyfileobj(source, target)
    mode = (member.external_attr >> 16) & 0o777
    if mode & 0o111:
        destination.chmod(mode)


def _write_eval_samples(data_root: Path) -> list[dict[str, object]]:
    parquet = data_root / "data/test-00000-of-00001.parquet"
    frame = polars.read_parquet(parquet)
    rows = frame.to_dicts()
    if not rows:
        raise ValueError("SWE-bench Pro parquet 不包含数据")
    samples = [_eval_sample(row) for row in rows]
    sample_path = data_root / "eval_samples.jsonl"
    sample_path.write_text(
        "".join(json.dumps(sample, ensure_ascii=False) + "\n" for sample in samples),
        encoding="utf-8",
    )
    return samples


def _eval_sample(row: Mapping[str, object]) -> dict[str, object]:
    instance_id = str(row.get("instance_id") or "").strip()
    if not instance_id.startswith("instance_"):
        raise ValueError(f"instance_id 必须以 instance_ 开头: {instance_id}")
    sample: dict[str, object] = {
        "instance_id": instance_id,
        "problem_statement": str(row.get("problem_statement") or row.get("issue") or "").strip(),
    }
    for field in EVALUATOR_FIELDS[1:]:
        value = row.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"{instance_id} 缺少字段 {field}")
        sample[field] = _python_literal(value) if field in {
            "fail_to_pass", "pass_to_pass", "selected_test_files_to_run"
        } else value
    return sample


def _python_literal(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return repr([str(item) for item in value])
    raise ValueError(f"evaluator 列表字段必须是 list 或字符串: {type(value)!r}")


def _source_commit(source_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_root), "-c", f"safe.directory={source_root.resolve()}", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _validate_source(source_root: Path, data_root: Path, sample_ids: list[str]) -> None:
    missing: list[str] = []
    required_source = [source_root / "swe_bench_pro_eval.py", source_root / "helper_code/image_uri.py"]
    missing.extend(str(path) for path in required_source if not path.is_file())
    for instance_id in sample_ids:
        for relative in (
            Path("run_scripts") / instance_id / "run_script.sh",
            Path("run_scripts") / instance_id / "parser.py",
            Path("dockerfiles/base_dockerfile") / instance_id / "Dockerfile",
            Path("dockerfiles/instance_dockerfile") / instance_id / "Dockerfile",
        ):
            if not (source_root / relative).is_file() and not (data_root / relative).is_file():
                missing.append(str(relative))
    if missing:
        raise FileNotFoundError("SWE-bench Pro 源码或快照缺少文件: " + ", ".join(missing[:20]))


def _remove_directory(path: Path, project_root: Path) -> None:
    if not path.exists():
        return
    resolved = path.resolve()
    marker = (project_root / "data").resolve()
    if marker not in resolved.parents:
        raise ValueError(f"拒绝删除 data 目录之外的路径: {resolved}")
    shutil.rmtree(resolved)


if __name__ == "__main__":
    raise SystemExit(main())
