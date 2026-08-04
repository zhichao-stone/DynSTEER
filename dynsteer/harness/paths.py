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
    """构建单个 benchmark case 的输出目录。"""
    if base_dir is None or config is None or not case_id.strip():
        raise ValueError("base_dir, config 和 case_id 不能为空")
    raw_method = config.metadata.get("method")
    method = str(raw_method).strip() if raw_method is not None else method_fallback.strip()
    if not method:
        raise ValueError("method 不能为空")
    case_dir = base_dir / config.benchmark
    experiment_id = str(config.metadata.get("experiment_id") or "").strip()
    if experiment_id:
        model_id = str(config.metadata.get("model_id") or "").strip()
        if not model_id:
            raise ValueError("experiment_id 存在时 model_id 不能为空")
        case_dir = case_dir / model_id
        repeat_index = config.metadata.get("repeat_index")
        if not isinstance(repeat_index, int) or repeat_index < 0:
            raise ValueError("experiment 输出必须包含非负 repeat_index")
        case_dir = case_dir / f"r{repeat_index}"
    return case_dir / method / case_id


def case_identity_from_output_dir(case_dir: Path) -> CaseOutputIdentity:
    """解析 benchmark-only 或 canonical experiment case 目录。"""
    parts = case_dir.parts
    exp_indexes = [index for index, part in enumerate(parts) if part == "exp"]
    if exp_indexes:
        tail = parts[exp_indexes[-1]:]
        if len(tail) != 7 or not tail[4].startswith("r") or not tail[4][1:].isdigit():
            raise ValueError(f"非规范 experiment case 目录: {case_dir}")
        return CaseOutputIdentity(tail[2], tail[1], tail[3], int(tail[4][1:]), tail[5], tail[6])
    if len(parts) < 3:
        raise ValueError(f"非规范 benchmark case 目录: {case_dir}")
    benchmark, method, case_id = parts[-3:]
    return CaseOutputIdentity(benchmark, None, None, None, method, case_id)
