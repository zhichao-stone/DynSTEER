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
    parser = argparse.ArgumentParser(description='Prepare SWE-bench Pro local data snapshot')
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
        _write_error(f"SWE-bench Pro data preparation failed:{exc}")
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
    """Generate and verify local data snapshots and evaluator inputs."""
    if not archive.is_file():
        raise FileNotFoundError(f"local dataset archive is missing: {archive}")
    source_dir = data_root / "source"
    digest_path = source_dir / ".archive_digest"
    digest = _archive_digest(archive)
    source_commit = _source_commit(source_root)
    metadata = {"sha256": digest, "source_git_commit": source_commit}
    if not force and digest_path.is_file():
        try:
            current = json.loads(digest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"SWE-bench Pro archive digest is not valid JSON: {digest_path}") from exc
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
            print('SWE-bench Pro snapshot ready and skipping reconstruction')
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
                raise ValueError(f"The data package contains duplicate paths:{member.filename}")
            seen_names.add(member.filename)
            target = _safe_zip_destination(destination, member.filename)
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"The packet contains symlink:{member.filename}")
            _write_zip_member(archive, member, target)


def _safe_zip_destination(destination: Path, member_name: str) -> Path:
    if "\\" in member_name:
        raise ValueError(f"The data pack path is not safe:{member_name}")
    path = PurePosixPath(member_name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"The data pack path is not safe:{member_name}")
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
        raise ValueError('SWE-bench Pro mark does not contain data')
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
        raise ValueError(f"Instance_id must start with instance_:{instance_id}")
    sample: dict[str, object] = {
        "instance_id": instance_id,
        "problem_statement": str(row.get("problem_statement") or row.get("issue") or "").strip(),
    }
    for field in EVALUATOR_FIELDS[1:]:
        value = row.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"{instance_id} is missing field {field}")
        sample[field] = _python_literal(value) if field in {
            "fail_to_pass", "pass_to_pass", "selected_test_files_to_run"
        } else value
    return sample


def _python_literal(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return repr([str(item) for item in value])
    raise ValueError(f"The evaluator list field must be a list or string: {type(value)!r}")


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
        raise FileNotFoundError('SWE-bench Pro source or snapshot missing file:' + ", ".join(missing[:20]))


def _remove_directory(path: Path, project_root: Path) -> None:
    if not path.exists():
        return
    resolved = path.resolve()
    marker = (project_root / "data").resolve()
    if marker not in resolved.parents:
        raise ValueError(f"refusing to delete a path outside the data directory: {resolved}")
    shutil.rmtree(resolved)


if __name__ == "__main__":
    raise SystemExit(main())
