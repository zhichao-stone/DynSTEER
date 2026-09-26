#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "$script_dir/.." && pwd)"

# shellcheck source=scripts/experiment_bootstrap.sh
. "$script_dir/experiment_bootstrap.sh"

python_executable="$project_root/.venv-tests/Scripts/python.exe"
[[ -x "$python_executable" ]] || python_executable="$project_root/.venv-tests/bin/python"
if [[ ! -x "$python_executable" ]]; then
    prepare_test_venv "$project_root"
    python_executable="$(benchmark_python "$project_root" .venv-tests)"
fi

export UV_NO_SYNC=1
export UV_CACHE_DIR="$project_root/.uv-cache"
export UV_PYTHON_INSTALL_DIR="$project_root/.uv-python"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$(mixed_path "$project_root")"
exec "$python_executable" "$(mixed_path "$project_root/scripts/get_cases.py")" "$@"
