#!/usr/bin/env bash
set -euo pipefail

bootstrap_python_command() {
    local project_root="$1"
    local candidate
    for candidate in "$project_root/.venv/Scripts/python.exe" "$project_root/.venv/bin/python"; do
        if [[ -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    if command -v python >/dev/null 2>&1; then
        printf '%s\n' python
        return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        printf '%s\n' python3
        return 0
    fi
    echo "Python is required to bootstrap DynSTEER experiments." >&2
    return 127
}

mixed_path() {
    local value="$1"
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -m "$value"
    else
        printf '%s\n' "$value"
    fi
}

load_env_file() {
    local project_root="$1"
    local env_file="$2"
    if [[ -z "$env_file" ]]; then
        return 0
    fi
    if [[ "$env_file" != /* && ! "$env_file" =~ ^[A-Za-z]:[\\/] ]]; then
        env_file="$project_root/$env_file"
    fi
    if [[ ! -f "$env_file" ]]; then
        echo "Env file not found, continuing without it: $env_file" >&2
        return 0
    fi
    set -a
    # shellcheck source=/dev/null
    . "$env_file"
    set +a
}

experiment_path() {
    local project_root="$1"
    local experiment="$2"
    if [[ "$experiment" == /* || "$experiment" =~ ^[A-Za-z]:[\\/] ]]; then
        printf '%s\n' "$experiment"
    else
        printf '%s/%s\n' "$project_root" "$experiment"
    fi
}

_run_bootstrap_python() {
    local project_root="$1"
    local experiment="$2"
    shift 2
    local python_cmd
    python_cmd="$(bootstrap_python_command "$project_root")"
    MSYS2_ARG_CONV_EXCL="*" "$python_cmd" - \
        "$(mixed_path "$project_root")" \
        "$(mixed_path "$(experiment_path "$project_root" "$experiment")")" \
        "$@"
}

experiment_runtime_lines() {
    local project_root="$1"
    local experiment="$2"
    local profile="$3"
    _run_bootstrap_python "$project_root" "$experiment" "$profile" <<'PY'
import json
import sys
from pathlib import Path

project_root = Path(sys.argv[1]).resolve()
config_path = Path(sys.argv[2]).resolve()
profile = sys.argv[3]
config = json.loads(config_path.read_text(encoding="utf-8"))
benchmarks = config.get("benchmarks")
if not isinstance(benchmarks, list) or len(benchmarks) != 1 or not isinstance(benchmarks[0], dict):
    raise SystemExit("experiment config must contain exactly one benchmark spec")
spec = benchmarks[0]
benchmark = str(spec.get("benchmark") or "").strip()
data_root = Path(str(spec.get("data_root") or f"data/{benchmark}"))
if not data_root.is_absolute():
    configured_from_experiment = config_path.parent / data_root
    data_root = configured_from_experiment if configured_from_experiment.exists() else project_root / data_root
manifest_path = data_root / "benchmark.json"
if not manifest_path.is_file():
    raise SystemExit(f"benchmark manifest not found: {manifest_path}")
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
source_root = str(manifest.get("source_root") or "").strip()
if not source_root:
    raise SystemExit(f"benchmark.json source_root is empty: {manifest_path}")
runtime = {
    "toolsandbox": ("toolsandbox", ".venv-toolsandbox"),
    "swebench_pro": ("swebench_pro", ".venv-swebench-pro"),
    "skillsbench": ("skillsbench", ".venv-skillsbench"),
}.get(benchmark)
if runtime is None:
    raise SystemExit(f"unsupported benchmark: {benchmark}")
print("\t".join((benchmark, runtime[0], runtime[1], profile, source_root)))
PY
}

experiment_runtime() {
    local project_root="$1"
    local experiment="$2"
    local profile="$3"
    local line
    line="$(experiment_runtime_lines "$project_root" "$experiment" "$profile")"
    if [[ "$(printf '%s\n' "$line" | wc -l)" != "1" ]]; then
        echo "Experiment runtime resolution must return one line." >&2
        return 65
    fi
    printf '%s\n' "$line"
}

benchmark_python() {
    local project_root="$1"
    local venv_name="$2"
    local candidate
    for candidate in "$project_root/$venv_name/Scripts/python.exe" "$project_root/$venv_name/bin/python"; do
        if [[ -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    echo "Python executable not found in $venv_name." >&2
    return 127
}

_uv_sync() {
    local project_root="$1"
    local venv_name="$2"
    shift 2
    (
        cd "$project_root"
        mkdir -p "$project_root/.uv-cache"
        exec 9>"$project_root/.uv-cache/${venv_name}.lock"
        if command -v flock >/dev/null 2>&1; then
            flock 9
        fi
        UV_PROJECT_ENVIRONMENT="$project_root/$venv_name" \
        UV_CACHE_DIR="$project_root/.uv-cache" \
        UV_PYTHON_INSTALL_DIR="$project_root/.uv-python" \
        UV_LINK_MODE=copy \
        uv sync --frozen --no-dev --no-install-project --inexact "$@"
    )
}

prepare_base_venv() {
    local project_root="$1"
    _uv_sync "$project_root" ".venv"
}

prepare_benchmark_venv() {
    local project_root="$1"
    local group="$2"
    local venv_name="$3"
    _uv_sync "$project_root" "$venv_name" --group "$group"
}

prepare_test_venv() {
    local project_root="$1"
    (
        cd "$project_root"
        UV_PROJECT_ENVIRONMENT="$project_root/.venv-tests" \
        UV_CACHE_DIR="$project_root/.uv-cache" \
        UV_PYTHON_INSTALL_DIR="$project_root/.uv-python" \
        UV_LINK_MODE=copy \
        uv sync --frozen --all-groups
    )
}

assert_execution_neutral() {
    local project_root="$1"
    local experiment="$2"
    _run_bootstrap_python "$project_root" "$experiment" <<'PY'
import json
import sys
from pathlib import Path

config = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
forbidden = {"docker", "use_docker", "execution_mode", "environment", "sandbox", "backend", "agent" + "compass"}

def check(value, path):
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key)
            if name in forbidden:
                raise SystemExit(f"{path} contains forbidden execution field: {name}")
            check(item, f"{path}.{name}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            check(item, f"{path}[{index}]")

check(config, "experiment config")
PY
}

prepare_benchmark_source() {
    local project_root="$1"
    local benchmark="$2"
    local experiment="$3"
    local python_cmd
    python_cmd="$(bootstrap_python_command "$project_root")"
    MSYS2_ARG_CONV_EXCL="*" "$python_cmd" "$(mixed_path "$project_root/scripts/preflight_experiment.py")" \
        --exp "$(mixed_path "$(experiment_path "$project_root" "$experiment")")" \
        --profile source --no-prepare-images --sources-only
}

bootstrap_usage() {
    cat <<'EOF'
Usage:
  ./scripts/experiment_bootstrap.sh COMMAND

Commands:
  prepare_base_venv     Create the base project virtual environment
  prepare_test_venv     Create .venv-tests with all dependency groups
  -h, --help            Show this help
EOF
}

bootstrap_main() {
    local project_root
    project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[1]}")/.." && pwd)"
    case "${1:-}" in
        prepare_base_venv) prepare_base_venv "$project_root" ;;
        prepare_test_venv) prepare_test_venv "$project_root" ;;
        -h|--help) bootstrap_usage ;;
        "") bootstrap_usage >&2; return 64 ;;
        *) echo "Unknown command: $1" >&2; bootstrap_usage >&2; return 64 ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    bootstrap_main "$@"
fi
