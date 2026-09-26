from dataclasses import dataclass
from pathlib import Path

from dynsteer.harness.model import HarnessRunConfig


@dataclass(frozen=True)
class CaseOutputIdentity:
    benchmark: str
    experiment_id: str | None
    model_id: str | None
    repeat_index: int | None
    method: str
    case_id: str


def case_output_dir(base_dir: Path, config: HarnessRunConfig, case_id: str, method_fallback: str) -> Path:
    """Builds an output directory of individual benchmark case."""
    if base_dir is None or config is None or not case_id.strip():
        raise ValueError('Base_dir, contact and case_id cannot be empty')
    raw_method = config.metadata.get("method")
    method = str(raw_method).strip() if raw_method is not None else method_fallback.strip()
    if not method:
        raise ValueError('method must not be empty.')
    case_dir = base_dir / config.benchmark
    experiment_id = str(config.metadata.get("experiment_id") or "").strip()
    if experiment_id:
        model_id = str(config.metadata.get("model_id") or "").strip()
        if not model_id:
            raise ValueError('model_id is required when experiment_id is present.')
        case_dir = case_dir / model_id
        repeat_index = config.metadata.get("repeat_index")
        if not isinstance(repeat_index, int) or repeat_index < 0:
            raise ValueError('Output must contain non-negative repeak_index')
        case_dir = case_dir / f"r{repeat_index}"
    return case_dir / method / case_id


def case_identity_from_output_dir(case_dir: Path) -> CaseOutputIdentity:
    """Parse a benchmark-only or canonical experiment case directory."""
    parts = case_dir.parts
    exp_indexes = [index for index, part in enumerate(parts) if part == "exp"]
    if exp_indexes:
        tail = parts[exp_indexes[-1]:]
        if len(tail) != 7 or not tail[4].startswith("r") or not tail[4][1:].isdigit():
            raise ValueError(f"Non-regulatory case directory:{case_dir}")
        return CaseOutputIdentity(tail[2], tail[1], tail[3], int(tail[4][1:]), tail[5], tail[6])
    if len(parts) < 3:
        raise ValueError(f"Unregulated benchmark case directory:{case_dir}")
    benchmark, method, case_id = parts[-3:]
    return CaseOutputIdentity(benchmark, None, None, None, method, case_id)
