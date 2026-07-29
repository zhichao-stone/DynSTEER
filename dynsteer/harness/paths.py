from pathlib import Path
from dynsteer.harness.model import HarnessRunConfig

def case_output_dir(base_dir: Path, config: HarnessRunConfig, run_id: str, case_id: str, method_fallback: str) -> Path:
    """构造单个 benchmark case 的输出目录。"""
    if base_dir is None or config is None or (not run_id.strip()) or (not case_id.strip()):
        raise ValueError('base_dir, config, run_id 和 case_id 不能为空')
    method = output_method(config, method_fallback)
    return base_dir / config.benchmark / method / run_id / case_id

def output_method(config: HarnessRunConfig, fallback: str) -> str:
    """从 metadata 读取输出 method 目录名。"""
    if config is None or not fallback.strip():
        raise ValueError('config 和 fallback 不能为空')
    raw_method = config.metadata.get('method')
    method = str(raw_method).strip() if raw_method is not None else fallback.strip()
    if not method:
        raise ValueError('method 不能为空')
    return method
