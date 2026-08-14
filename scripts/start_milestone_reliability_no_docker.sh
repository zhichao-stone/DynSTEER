#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 --exp PATH [--force] [--workers NUM] [--ged-solver MODE] [--fgw]"
}

runtime_python() {
    local environment="$1"
    local candidate
    for candidate in \
        "$environment/Scripts/python.exe" \
        "$environment/bin/python" \
        "$environment/Scripts/python"; do
        if [[ -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

environment_is_healthy() {
    local python_executable="$1"
    local requires_toolsandbox="$2"
    shift 2
    "$python_executable" - "$requires_toolsandbox" "$@" <<'PY'
from importlib import metadata
from collections import Counter
from pathlib import Path
import re
import site
import sys

invalid = [
    str(path)
    for root in site.getsitepackages()
    for path in Path(root).glob("*.dist-info")
    if not (path / "METADATA").is_file()
]
if invalid:
    raise SystemExit(f"invalid dist-info directories: {invalid}")

distribution_names = [
    re.sub(r"[-_.]+", "-", name).lower()
    for distribution in metadata.distributions()
    if (name := distribution.metadata.get("Name"))
]
duplicates = sorted(
    name for name, count in Counter(distribution_names).items() if count > 1
)
if duplicates:
    raise SystemExit(f"duplicate installed distributions: {duplicates}")

pydantic_version = metadata.version("pydantic")
if not pydantic_version.strip():
    raise SystemExit("pydantic distribution version is empty")

if sys.argv[1] == "1":
    for source_root in reversed(sys.argv[2:]):
        sys.path.insert(0, source_root)
    import tool_sandbox.scenarios  # noqa: F401
PY
}

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi
[[ $# -gt 0 ]] || { usage >&2; exit 64; }
command -v uv >/dev/null 2>&1 || { echo "uv is required" >&2; exit 127; }
cd "$root"

script_dir="$root/scripts"
# shellcheck source=scripts/experiment_bootstrap.sh
. "$script_dir/experiment_bootstrap.sh"

experiment_config=""
case_args=("$@")
for ((index=0; index<${#case_args[@]}; index++)); do
    case "${case_args[$index]}" in
        --exp|--experiment-config)
            index=$((index + 1))
            experiment_config="${case_args[$index]:-}"
            ;;
        --exp=*|--experiment-config=*)
            experiment_config="${case_args[$index]#*=}"
            ;;
    esac
done
[[ -n "$experiment_config" ]] || { echo "--exp is required" >&2; exit 64; }

bootstrap_lines="$(experiment_bootstrap_lines "$root" "$experiment_config")" || exit $?
declare -A known_sources=()
source_roots=()
requires_toolsandbox=0
requires_agentcompass=0
while IFS=$'\t' read -r benchmark data_root source_root container_source_root max_workers; do
    [[ -n "$benchmark" ]] || continue
    if [[ "$benchmark" == "toolsandbox" ]]; then
        requires_toolsandbox=1
    fi
    if [[ "$benchmark" == "swebench_pro" || "$benchmark" == "skillsbench" ]]; then
        requires_agentcompass=1
    fi
    [[ "$source_root" != "-" && -n "$source_root" ]] || continue
    if [[ "$source_root" != /* && ! "$source_root" =~ ^[A-Za-z]:[\\/] ]]; then
        source_root="$root/$source_root"
    fi
    if [[ "$source_root" =~ ^[A-Za-z]:[\\/] ]] && command -v cygpath >/dev/null 2>&1; then
        source_root="$(cygpath -u "$source_root")"
    fi
    source_candidate="$source_root"
    source_root="$(cd -- "$source_candidate" 2>/dev/null && pwd)" || {
        echo "Benchmark source not found: $source_candidate" >&2
        exit 66
    }
    [[ -f "$source_root/pyproject.toml" || -f "$source_root/setup.py" ]] || {
        echo "Benchmark source must contain pyproject.toml or setup.py: $source_root" >&2
        exit 66
    }
    if [[ -z "${known_sources[$source_root]+x}" ]]; then
        source_roots+=("$source_root")
        known_sources["$source_root"]=1
    fi
done <<< "$bootstrap_lines"

export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$root/.venv}"
export UV_CACHE_DIR="${DYNSTEER_RELIABILITY_UV_CACHE:-$root/.uv-cache}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
export UV_NO_CACHE="${DYNSTEER_RELIABILITY_UV_NO_CACHE:-1}"

sync_args=(--frozen --no-install-project)
if [[ "$requires_toolsandbox" == "1" ]]; then
    sync_args+=(--group toolsandbox)
fi
if [[ "$requires_agentcompass" == "1" ]]; then
    sync_args+=(--extra agentcompass)
fi

echo "Synchronizing the shared DynSTEER environment: $UV_PROJECT_ENVIRONMENT"
uv sync "${sync_args[@]}"
python_executable="$(runtime_python "$UV_PROJECT_ENVIRONMENT")" || {
    echo "Python executable not found in shared environment: $UV_PROJECT_ENVIRONMENT" >&2
    exit 127
}
environment_is_healthy \
    "$python_executable" \
    "$requires_toolsandbox" \
    "${source_roots[@]}"

entrypoint_args=()
raw_args=("$@")
env_file="$root/.env"
load_env_file=1
index=0
while [[ $index -lt ${#raw_args[@]} ]]; do
    arg="${raw_args[$index]}"
    case "$arg" in
        --env-file)
            index=$((index + 1))
            env_file="${raw_args[$index]:-}"
            [[ -n "$env_file" ]] || exit 64
            ;;
        --env-file=*)
            env_file="${arg#*=}"
            [[ -n "$env_file" ]] || exit 64
            ;;
        --no-env-file|--no-dotenv)
            load_env_file=0
            ;;
        *)
            entrypoint_args+=("$arg")
            ;;
    esac
    index=$((index + 1))
done
if [[ "$load_env_file" == "1" && -f "$env_file" ]]; then
    set -a
    # shellcheck source=/dev/null
    . "$env_file"
    set +a
fi

exec "$python_executable" milestone_reliability.py "${entrypoint_args[@]}"
