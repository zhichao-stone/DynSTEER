from pathlib import Path

from dynsteer.harness.model import HarnessRunConfig


def case_output_dir(base_dir: Path, config: HarnessRunConfig, case_id: str, method_fallback: str) -> Path:
    """构建单个 benchmark case 的输出目录。"""
    if base_dir is None or config is None or not case_id.strip():
        raise ValueError("base_dir, config 和 case_id 不能为空")
    method = output_method(config, method_fallback)
    case_dir = base_dir / config.benchmark
    if output_experiment_id(config) is not None:
        model_id = output_model_id(config)
        if model_id is None:
            raise ValueError("experiment_id 存在时 model_id 不能为空")
        case_dir = case_dir / model_id
        repeat_index = config.metadata.get("repeat_index")
        if repeat_index is not None:
            case_dir = case_dir / f"r{repeat_index}"
    return case_dir / method / case_id


def output_method(config: HarnessRunConfig, fallback: str) -> str:
    """从 metadata 读取输出 method 目录名。"""
    if config is None or not fallback.strip():
        raise ValueError("config 和 fallback 不能为空")
    raw_method = config.metadata.get("method")
    method = str(raw_method).strip() if raw_method is not None else fallback.strip()
    if not method:
        raise ValueError("method 不能为空")
    return method


def output_experiment_id(config: HarnessRunConfig) -> str | None:
    """从 metadata 读取 experiment_id。"""
    raw_experiment_id = config.metadata.get("experiment_id")
    experiment_id = str(raw_experiment_id).strip() if raw_experiment_id is not None else ""
    return experiment_id or None


def output_model_id(config: HarnessRunConfig) -> str | None:
    """从 metadata 读取 model_id。"""
    raw_model_id = config.metadata.get("model_id")
    model_id = str(raw_model_id).strip() if raw_model_id is not None else ""
    return model_id or None
