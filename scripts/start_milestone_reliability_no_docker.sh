#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "$script_dir/.." && pwd)"

# shellcheck source=scripts/experiment_bootstrap.sh
. "$script_dir/experiment_bootstrap.sh"

experiment=""
args=("$@")
for ((index=0; index<${#args[@]}; index++)); do
    case "${args[$index]}" in
        --exp|--experiment-config)
            experiment="${args[$((index + 1))]:-}"
            ;;
        --exp=*|--experiment-config=*)
            experiment="${args[$index]#*=}"
            ;;
    esac
done
[[ -n "$experiment" ]] || { echo "--exp is required" >&2; exit 64; }

prepare_base_venv "$project_root"
runtime_line="$(experiment_runtime "$project_root" "$experiment" host)"
IFS=$'\t' read -r benchmark group venv_name _ _ <<< "$runtime_line"
prepare_benchmark_venv "$project_root" "$group" "$venv_name"
prepare_benchmark_venv "$project_root" "$benchmark" "$venv_name"

export DYNSTEER_BENCHMARK_EXECUTION=host
export UV_NO_SYNC=1
export UV_CACHE_DIR="$project_root/.uv-cache"
export UV_PYTHON_INSTALL_DIR="$project_root/.uv-python"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$(mixed_path "$project_root")"
prepare_benchmark_source "$project_root" "$benchmark" "$experiment"
python_executable="$(benchmark_python "$project_root" "$venv_name")"
exec "$python_executable" "$(mixed_path "$project_root/milestone_reliability.py")" "${args[@]}"
