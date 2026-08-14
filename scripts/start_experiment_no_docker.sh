#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  ./scripts/start_experiment_no_docker.sh --exp PATH [--source PATH] [options]

Options:
  --exp PATH                 Unified experiment config JSON. Required.
  --experiment-config PATH   Same as --exp.
  --source PATH              Optional source tree override. Defaults to benchmark.json source_root.
  --workers NUM              Optional worker override. Defaults to benchmark.json max_workers or 1.
  --random_seed NUM          Python random seed. Defaults to 202608.
  --only_adapt               Only adapt benchmark data into data-root; do not run evaluation.
  --force_adapt              Rebuild adapted cases even if cached data already exists.
  --force_eval               Re-run evaluation and overwrite existing case outputs.
  --no_sum                   Skip experiment-level index/scores/metrics files.
  --env-file PATH            Env file to source before running. Defaults to .env.
  --no-env-file              Do not source an env file.
  -h, --help                 Show this help.

Examples:
  ./scripts/start_experiment_no_docker.sh --exp data/experiments/double_benchmark_initial.json
  ./scripts/start_experiment_no_docker.sh --exp data/experiments/double_benchmark_initial.json --no-env-file
EOF
}

script_dir() {
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd
}

require_value() {
    local option_name="$1"
    local option_value="${2:-}"
    if [[ -z "$option_value" ]]; then
        echo "$option_name requires a non-empty value." >&2
        exit 64
    fi
}

absolute_host_path() {
    local base_dir="$1"
    local input_path="$2"
    if [[ "$input_path" == /* || "$input_path" =~ ^[A-Za-z]:[\\/] ]]; then
        cd -- "$input_path" && pwd
    else
        cd -- "$base_dir/$input_path" && pwd
    fi
}

source_env_file() {
    local project_root="$1"
    local env_file="$2"

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

ensure_uv_environment() {
    local project_root="$1"
    local venv_dir="${UV_PROJECT_ENVIRONMENT:-.venv}"
    local cache_dir="${UV_CACHE_DIR:-.uv-cache}"
    export UV_PROJECT_ENVIRONMENT="$venv_dir"
    export UV_CACHE_DIR="$cache_dir"

    if [[ "${DYNSTEER_SKIP_UV_SYNC:-0}" == "1" ]]; then
        return 0
    fi

    cd "$project_root"
    uv sync --frozen --no-dev --no-install-project --inexact
}

ensure_agentcompass_extra() {
    if [[ "${DYNSTEER_SKIP_UV_SYNC:-0}" == "1" ]]; then
        return 0
    fi
    if experiment_uses_agentcompass "$1" "$2"; then
        echo "Enabling optional AgentCompass bridge for experiment"
        uv sync --frozen --no-dev --no-install-project --inexact --extra agentcompass
    fi
}

ensure_toolsandbox_group() {
    if [[ "${DYNSTEER_SKIP_UV_SYNC:-0}" == "1" ]]; then
        return 0
    fi
    if experiment_uses_toolsandbox "$1" "$2"; then
        echo "Enabling locked ToolSandbox dependencies for experiment"
        uv sync --frozen --no-dev --no-install-project --inexact --group toolsandbox
    fi
}

project_python() {
    local venv_dir="${UV_PROJECT_ENVIRONMENT:-.venv}"
    local candidate
    for candidate in "$venv_dir/bin/python" "$venv_dir/Scripts/python.exe" "$venv_dir/Scripts/python"; do
        if [[ -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    echo "Python executable not found in uv environment: $venv_dir" >&2
    exit 127
}

prepare_benchmark_source() {
    local source_path="$1"
    local invocation_dir="$2"

    local source_abs
    if ! source_abs="$(absolute_host_path "$invocation_dir" "$source_path")"; then
        echo "Benchmark source not found: $source_path" >&2
        exit 66
    fi
    if [[ ! -f "$source_abs/pyproject.toml" && ! -f "$source_abs/setup.py" ]]; then
        echo "Benchmark source must contain pyproject.toml or setup.py: $source_abs" >&2
        exit 66
    fi

    export DYNSTEER_BENCHMARK_SOURCE_ROOT="$source_abs"
    echo "Using benchmark source for experiment: $source_abs"
}

main() {
    local invocation_dir
    invocation_dir="$(pwd)"
    local script_root
    script_root="$(script_dir)"
    # shellcheck source=scripts/experiment_bootstrap.sh
    . "$script_root/experiment_bootstrap.sh"
    local project_root
    project_root="$(cd -- "$script_root/.." && pwd)"

    local experiment_config=""
    local source_path=""
    local workers=""
    local random_seed=""
    local only_adapt="0"
    local force_adapt="0"
    local force_eval="0"
    local no_sum="0"
    local env_file="${DYNSTEER_ENV_FILE:-.env}"
    local load_env_file="1"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --exp|--experiment-config)
                require_value "$1" "${2:-}"
                experiment_config="$2"
                shift 2
                ;;
            --exp=*)
                experiment_config="${1#*=}"
                require_value "--exp" "$experiment_config"
                shift
                ;;
            --experiment-config=*)
                experiment_config="${1#*=}"
                require_value "--experiment-config" "$experiment_config"
                shift
                ;;
            --source)
                require_value "$1" "${2:-}"
                source_path="$2"
                shift 2
                ;;
            --source=*)
                source_path="${1#*=}"
                require_value "--source" "$source_path"
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
            --random_seed|--random-seed)
                require_value "$1" "${2:-}"
                random_seed="$2"
                shift 2
                ;;
            --random_seed=*|--random-seed=*)
                random_seed="${1#*=}"
                require_value "--random_seed" "$random_seed"
                shift
                ;;
            --only_adapt|--only-adapt)
                only_adapt="1"
                shift
                ;;
            --force_adapt|--force-adapt)
                force_adapt="1"
                shift
                ;;
            --force_eval|--force-eval)
                force_eval="1"
                shift
                ;;
            --no_sum|--no-sum)
                no_sum="1"
                shift
                ;;
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
            --no-env-file|--no-dotenv)
                load_env_file="0"
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                echo "Unknown option: $1" >&2
                usage >&2
                exit 64
                ;;
        esac
    done

    if [[ -z "$experiment_config" ]]; then
        usage >&2
        exit 64
    fi

    if [[ "$load_env_file" == "1" ]]; then
        source_env_file "$project_root" "$env_file"
    fi

    cd "$project_root"
    ensure_uv_environment "$project_root"
    ensure_agentcompass_extra "$project_root" "$experiment_config"
    ensure_toolsandbox_group "$project_root" "$experiment_config"
    local python_executable
    python_executable="$(project_python)"
    if [[ -n "$source_path" ]]; then
        prepare_benchmark_source "$source_path" "$invocation_dir"
    else
        local bootstrap_lines
        bootstrap_lines="$(experiment_bootstrap_lines "$project_root" "$experiment_config")"
        local benchmark
        local data_root
        local source_root
        local container_source_root
        local max_workers
        local derived_workers=""
        declare -A installed_sources=()
        while IFS=$'\t' read -r benchmark data_root source_root container_source_root max_workers; do
            if [[ -z "$benchmark" ]]; then
                continue
            fi
            if [[ "$source_root" != "-" && -n "$source_root" && -z "${installed_sources[$source_root]+x}" ]]; then
                prepare_benchmark_source "$source_root" "$project_root"
                installed_sources["$source_root"]=1
            fi
            if [[ "$max_workers" != "-" && -n "$max_workers" ]]; then
                if [[ -z "$derived_workers" || "$max_workers" -lt "$derived_workers" ]]; then
                    derived_workers="$max_workers"
                fi
            fi
        done <<< "$bootstrap_lines"
        unset DYNSTEER_BENCHMARK_SOURCE_ROOT
        if [[ -z "$workers" ]]; then
            workers="${derived_workers:-1}"
        fi
    fi

    if [[ -n "$source_path" && -z "$workers" ]]; then
        local manual_bootstrap_lines
        manual_bootstrap_lines="$(experiment_bootstrap_lines "$project_root" "$experiment_config")"
        local manual_benchmark
        local manual_data_root
        local manual_source_root
        local manual_container_source_root
        local manual_max_workers
        local manual_derived_workers=""
        while IFS=$'\t' read -r manual_benchmark manual_data_root manual_source_root manual_container_source_root manual_max_workers; do
            if [[ "$manual_max_workers" != "-" && -n "$manual_max_workers" ]]; then
                if [[ -z "$manual_derived_workers" || "$manual_max_workers" -lt "$manual_derived_workers" ]]; then
                    manual_derived_workers="$manual_max_workers"
                fi
            fi
        done <<< "$manual_bootstrap_lines"
        workers="${manual_derived_workers:-1}"
    fi

    local worker_args=()
    if [[ -n "$workers" ]]; then
        worker_args=(--workers "$workers")
    fi
    local force_adapt_args=()
    if [[ "$force_adapt" == "1" ]]; then
        force_adapt_args=(--force_adapt)
    fi
    local force_eval_args=()
    if [[ "$force_eval" == "1" ]]; then
        force_eval_args=(--force_eval)
    fi
    local no_sum_args=()
    if [[ "$no_sum" == "1" ]]; then
        no_sum_args=(--no_sum)
    fi
    local random_seed_args=()
    if [[ -n "$random_seed" ]]; then
        random_seed_args=(--random_seed "$random_seed")
    fi
    local only_adapt_args=()
    if [[ "$only_adapt" == "1" ]]; then
        only_adapt_args=(--only_adapt)
    fi

    exec "$python_executable" main.py \
        --exp "$experiment_config" \
        "${worker_args[@]}" \
        "${random_seed_args[@]}" \
        "${only_adapt_args[@]}" \
        "${force_adapt_args[@]}" \
        "${force_eval_args[@]}" \
        "${no_sum_args[@]}"
}

main "$@"
