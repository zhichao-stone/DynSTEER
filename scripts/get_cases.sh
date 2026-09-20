#!/usr/bin/env bash
set -euo pipefail

script_dir="${BASH_SOURCE[0]%/*}"
if [[ "$script_dir" == "${BASH_SOURCE[0]}" ]]; then
    script_dir="."
fi
script_dir="$(cd -- "$script_dir" && pwd)"
project_root="$(cd -- "$script_dir/.." && pwd)"

export UV_CACHE_DIR="${UV_CACHE_DIR:-$project_root/.uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$project_root/.uv-python}"

source_path="${DYNSTEER_AGENTCOMPASS_SOURCE:-../AgentCompass}"
data_dir="${1:-}"
if [[ -z "$data_dir" ]]; then
    data_dir="${DYNSTEER_AGENTCOMPASS_DATA_DIR:-data/agentcompass}"
fi

# shellcheck source=agentcompass_environment.sh
source "$script_dir/agentcompass_environment.sh"
ensure_agentcompass_environment "$project_root" "$source_path"

run_command=(uv run --no-sync python get_cases.py --data-dir "$data_dir")
cd "$project_root"
echo "Exporting AgentCompass case lists: ${run_command[*]}"
"${run_command[@]}"
