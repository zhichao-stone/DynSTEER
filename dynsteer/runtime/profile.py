import os


EXECUTION_PROFILES = frozenset({"docker", "host"})


class UnsupportedBenchmarkProfile(RuntimeError):
    """当前执行 profile 不支持该 benchmark 能力。"""


def execution_profile_from_environment() -> str:
    """读取启动脚本注入的 benchmark 执行 profile。"""
    profile = str(os.environ.get("DYNSTEER_BENCHMARK_EXECUTION", "")).strip().lower()
    return profile if profile in EXECUTION_PROFILES else ""


def require_native_execution_profile() -> str:
    """读取并校验 SWE/Skills 必需的 benchmark 执行 profile。"""
    profile = execution_profile_from_environment()
    if not profile:
        raise RuntimeError(
            "缺少 DYNSTEER_BENCHMARK_EXECUTION；请通过 start_experiment.sh 或 start_experiment_no_docker.sh 启动"
        )
    return profile
