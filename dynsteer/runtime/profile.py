import os


EXECUTION_PROFILES = frozenset({"docker", "host"})


class UnsupportedBenchmarkProfile(RuntimeError):
    """The current execution profile does not support this benchmark capability."""


def execution_profile_from_environment() -> str:
    """Read the execution profile injected by the benchmark launcher."""
    profile = str(os.environ.get("DYNSTEER_BENCHMARK_EXECUTION", "")).strip().lower()
    return profile if profile in EXECUTION_PROFILES else ""


def require_native_execution_profile() -> str:
    """Read and verify the required native benchmark execution profile."""
    profile = execution_profile_from_environment()
    if not profile:
        raise RuntimeError(
            'DYNSTEER_BENCHMARK_EXECUTION is unset; start the run with start_experiment.sh or start_experiment_no_docker.sh'
        )
    return profile
