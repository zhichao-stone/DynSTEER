#!/usr/bin/env bash
set -euo pipefail
usage() { echo "Usage: $0 --exp PATH [--workers NUM] [--ged-solver MODE] [--fgw]"; }
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then usage; exit 0; fi
[[ $# -gt 0 ]] || { usage >&2; exit 64; }
case "$*" in
  *"--exp "*|*"--exp="*|*"--experiment-config "*|*"--experiment-config="*) ;;
  *) echo "--exp is required" >&2; exit 64;;
esac
cd "$root"
script_dir="$root/scripts"
# 复用统一实验 bootstrap：从 benchmark.json 解析 source_root，避免运行时缺少
# ToolSandbox/SkillsBench 等 benchmark 包。
# shellcheck source=scripts/experiment_bootstrap.sh
. "$script_dir/experiment_bootstrap.sh"
if [[ "${DYNSTEER_SKIP_UV_SYNC:-0}" != "1" ]]; then
    command -v uv >/dev/null 2>&1 || { echo "uv is required" >&2; exit 127; }
    UV_CACHE_DIR="${UV_CACHE_DIR:-$root/.uv-cache}" uv sync --frozen --no-dev --no-install-project --inexact
fi
python_executable="${UV_PROJECT_ENVIRONMENT:-$root/.venv}/Scripts/python.exe"
[[ -x "$python_executable" ]] || python_executable="${UV_PROJECT_ENVIRONMENT:-$root/.venv}/bin/python"
[[ -x "$python_executable" ]] || { echo "Python executable not found" >&2; exit 127; }

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
declare -A installed_sources=()
while IFS=$'\t' read -r benchmark data_root source_root container_source_root max_workers; do
    [[ -n "$benchmark" ]] || continue
    [[ "$source_root" != "-" && -n "$source_root" ]] || continue
    if [[ "$source_root" != /* && ! "$source_root" =~ ^[A-Za-z]:[\\/] ]]; then
        source_root="$root/$source_root"
    fi
    if [[ "$source_root" =~ ^[A-Za-z]:[\\/] ]] && command -v cygpath >/dev/null 2>&1; then
        source_root="$(cygpath -u "$source_root")"
    fi
    source_root="$(cd -- "$source_root" 2>/dev/null && pwd)" || {
        echo "Benchmark source not found: $source_root" >&2
        exit 66
    }
    [[ -f "$source_root/pyproject.toml" || -f "$source_root/setup.py" ]] || {
        echo "Benchmark source must contain pyproject.toml or setup.py: $source_root" >&2
        exit 66
    }
    if [[ -z "${installed_sources[$source_root]+x}" ]]; then
        echo "Installing benchmark source for reliability experiment: $source_root"
        export DYNSTEER_BENCHMARK_SOURCE_ROOT="$source_root"
        uv pip install --python "$python_executable" --editable "$source_root"
        installed_sources["$source_root"]=1
    fi
done <<< "$bootstrap_lines"
unset DYNSTEER_BENCHMARK_SOURCE_ROOT
entrypoint_args=()
raw_args=("$@")
env_file="$root/.env"
load_env_file=1
index=0
while [[ $index -lt ${#raw_args[@]} ]]; do
    arg="${raw_args[$index]}"
    case "$arg" in
        --env-file)
            index=$((index + 1)); env_file="${raw_args[$index]:-}"; [[ -n "$env_file" ]] || exit 64
            ;;
        --env-file=*) env_file="${arg#*=}"; [[ -n "$env_file" ]] || exit 64 ;;
        --no-env-file|--no-dotenv) load_env_file=0 ;;
        *) entrypoint_args+=("$arg") ;;
    esac
    index=$((index + 1))
done
if [[ $load_env_file == 1 && -f "$env_file" ]]; then
    set -a
    # shellcheck source=/dev/null
    . "$env_file"
    set +a
fi
exec "$python_executable" milestone_reliability.py "${entrypoint_args[@]}"
