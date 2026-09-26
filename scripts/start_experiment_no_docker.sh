#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  ./scripts/start_experiment_no_docker.sh --exp PATH [options]

ToolSandbox supports full execution. SWE-bench Pro supports
adaptation, replay from existing default trajectories, and offline summaries.

Options:
  --exp PATH, --experiment-config PATH
                              Unified single-benchmark experiment JSON. Required.
  --workers NUM               Case worker override.
  --only_adapt, --only-adapt Only adapt benchmark data.
  --force_adapt, --force-adapt
                              Rebuild adapted case files.
  --force_eval, --force-eval Re-run case outputs.
  --no_sum, --no-sum          Skip experiment-level summaries.
  --env-file PATH             Environment file to load. Defaults to .env.
  --no-env-file               Do not load an environment file.
  -h, --help                  Show this help.
EOF
}

script_dir() {
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd
}

require_value() {
    if [[ -z "${2:-}" ]]; then
        echo "$1 requires a non-empty value." >&2
        exit 64
    fi
}

main() {
    local script_root
    script_root="$(script_dir)"
    # shellcheck source=scripts/experiment_bootstrap.sh
    . "$script_root/experiment_bootstrap.sh"
    local project_root
    project_root="$(cd -- "$script_root/.." && pwd)"

    local experiment="" workers="" only_adapt=0 force_adapt=0 force_eval=0 no_sum=0
    local env_file="${DYNSTEER_ENV_FILE:-.env}" load_env=1
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --exp|--experiment-config)
                require_value "$1" "${2:-}"
                experiment="$2"
                shift 2
                ;;
            --exp=*|--experiment-config=*)
                experiment="${1#*=}"
                require_value "--exp" "$experiment"
                shift
                ;;
            --workers)
                require_value "$1" "${2:-}"
                workers="$2"
                shift 2
                ;;
            --workers=*)
                workers="${1#*=}"
                require_value "--workers" "$workers"
                shift
                ;;
            --only_adapt|--only-adapt) only_adapt=1; shift ;;
            --force_adapt|--force-adapt) force_adapt=1; shift ;;
            --force_eval|--force-eval) force_eval=1; shift ;;
            --no_sum|--no-sum) no_sum=1; shift ;;
            --env-file)
                require_value "$1" "${2:-}"
                env_file="$2"
                shift 2
                ;;
            --env-file=*)
                env_file="${1#*=}"
                require_value "--env-file" "$env_file"
                shift
                ;;
            --no-env-file|--no-dotenv) load_env=0; shift ;;
            -h|--help) usage; exit 0 ;;
            *) echo "Unknown option: $1" >&2; usage >&2; exit 64 ;;
        esac
    done
    if [[ -z "$experiment" ]]; then
        usage >&2
        exit 64
    fi
    if [[ "$load_env" == "1" ]]; then
        load_env_file "$project_root" "$env_file"
    fi

    prepare_base_venv "$project_root"
    assert_execution_neutral "$project_root" "$experiment"
    local runtime_line benchmark group venv_name
    runtime_line="$(experiment_runtime "$project_root" "$experiment" host)"
    IFS=$'\t' read -r benchmark group venv_name _ _ <<< "$runtime_line"
    prepare_benchmark_venv "$project_root" "$group" "$venv_name"

    export DYNSTEER_BENCHMARK_EXECUTION=host
    export UV_NO_SYNC=1
    export UV_CACHE_DIR="$project_root/.uv-cache"
    export UV_PYTHON_INSTALL_DIR="$project_root/.uv-python"
    export PYTHONDONTWRITEBYTECODE=1
    export PYTHONPATH="$(mixed_path "$project_root")"
    export PYTHONIOENCODING=utf-8
    export PYTHONUTF8=1
    prepare_benchmark_source "$project_root" "$benchmark" "$experiment"

    local python_executable preflight_args=(--no-prepare-images)
    python_executable="$(benchmark_python "$project_root" "$venv_name")"
    [[ "$only_adapt" == "0" ]] || preflight_args+=(--only-adapt)
    if [[ "$force_eval" != "0" || "$force_adapt" != "0" ]]; then
        preflight_args+=(--force-eval)
    fi
    "$python_executable" "$(mixed_path "$project_root/scripts/preflight_experiment.py")" \
        --exp "$(mixed_path "$(experiment_path "$project_root" "$experiment")")" \
        --profile host "${preflight_args[@]}"

    local main_args=(--exp "$experiment")
    [[ -z "$workers" ]] || main_args+=(--workers "$workers")
    [[ "$only_adapt" == "0" ]] || main_args+=(--only_adapt)
    [[ "$force_adapt" == "0" ]] || main_args+=(--force_adapt)
    [[ "$force_eval" == "0" ]] || main_args+=(--force_eval)
    [[ "$no_sum" == "0" ]] || main_args+=(--no_sum)
    "$python_executable" "$(mixed_path "$project_root/main.py")" "${main_args[@]}"
}

main "$@"
