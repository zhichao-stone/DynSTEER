#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  ./scripts/start_experiment.sh --exp PATH [--source PATH] [options]

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
  ./scripts/start_experiment.sh --exp data/experiments/double_benchmark_initial.json
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

run_in_container() {
    local project_root="$1"
    local invocation_dir="$2"
    shift 2

    if ! command -v docker >/dev/null 2>&1; then
        echo "Docker is required when running start_experiment.sh outside the container." >&2
        exit 127
    fi

    local compose_cmd
    if ! compose_cmd="$(detect_compose)"; then
        echo "Docker Compose is required. Install the docker compose plugin or docker-compose." >&2
        exit 127
    fi
    # shellcheck disable=SC2206
    local compose_parts=($compose_cmd)
    export DYNSTEER_HOST_UID="${DYNSTEER_HOST_UID:-$(id -u)}"
    export DYNSTEER_HOST_GID="${DYNSTEER_HOST_GID:-$(id -g)}"

    local experiment_config=""
    local source_path=""
    local arg
    local -a raw_args=("$@")
    local index=0
    while [[ $index -lt ${#raw_args[@]} ]]; do
        arg="${raw_args[$index]}"
        case "$arg" in
            --exp|--experiment-config)
                experiment_config="${raw_args[$((index + 1))]:-}"
                index=$((index + 2))
                ;;
            --exp=*|--experiment-config=*)
                experiment_config="${arg#*=}"
                index=$((index + 1))
                ;;
            --source)
                source_path="${raw_args[$((index + 1))]:-}"
                index=$((index + 2))
                ;;
            --source=*)
                source_path="${arg#*=}"
                index=$((index + 1))
                ;;
            *)
                index=$((index + 1))
                ;;
        esac
    done

    local -a run_options=(--rm --entrypoint bash)
    local -a container_args=()
    local container_source=""
    if [[ -n "$source_path" ]]; then
        local source_abs
        if ! source_abs="$(absolute_host_path "$invocation_dir" "$source_path")"; then
            echo "Benchmark source not found: $source_path" >&2
            exit 66
        fi
        local docker_source
        docker_source="$(docker_mount_path "$source_abs")"
        container_source="/workspace/benchmark-sources/experiment-benchmark"
        run_options+=(--volume "${docker_source}:${container_source}:rw")
    elif [[ -n "$experiment_config" ]]; then
        local bootstrap_lines
        bootstrap_lines="$(experiment_bootstrap_lines "$project_root" "$experiment_config" "/workspace/DynSTEER")"
        local bootstrap_benchmark
        local bootstrap_data_root
        local bootstrap_source_root
        local bootstrap_container_source_root
        local bootstrap_max_workers
        declare -A mounted_sources=()
        while IFS=$'\t' read -r bootstrap_benchmark bootstrap_data_root bootstrap_source_root bootstrap_container_source_root bootstrap_max_workers; do
            if [[ -z "$bootstrap_benchmark" || "$bootstrap_source_root" == "-" || "$bootstrap_container_source_root" == "-" ]]; then
                continue
            fi
            if [[ -n "${mounted_sources[$bootstrap_source_root]+x}" ]]; then
                continue
            fi
            local source_abs
            if ! source_abs="$(absolute_host_path "$project_root" "$bootstrap_source_root")"; then
                echo "Benchmark source not found: $bootstrap_source_root" >&2
                exit 66
            fi
            local docker_source
            docker_source="$(docker_mount_path "$source_abs")"
            run_options+=(--volume "${docker_source}:${bootstrap_container_source_root}:rw")
            mounted_sources["$bootstrap_source_root"]=1
        done <<< "$bootstrap_lines"
    fi

    index=0
    while [[ $index -lt ${#raw_args[@]} ]]; do
        arg="${raw_args[$index]}"
        case "$arg" in
            --source)
                container_args+=(--source "${container_source:-${raw_args[$((index + 1))]:-}}")
                index=$((index + 2))
                ;;
            --source=*)
                if [[ -n "$container_source" ]]; then
                    container_args+=("--source=$container_source")
                else
                    container_args+=("$arg")
                fi
                index=$((index + 1))
                ;;
            *)
                container_args+=("$arg")
                index=$((index + 1))
                ;;
        esac
    done

    local image_name="${DYNSTEER_DOCKER_IMAGE:-dynsteer:local}"
    if ! docker image inspect "$image_name" >/dev/null 2>&1; then
        echo "Image $image_name not found; building it once..."
        "${compose_parts[@]}" -f "$project_root/docker-compose.yml" build dynsteer
    fi

    exec "${compose_parts[@]}" -f "$project_root/docker-compose.yml" run \
        "${run_options[@]}" \
        dynsteer \
        /workspace/DynSTEER/scripts/start_experiment.sh \
        "${container_args[@]}"
}

source_env_file() {
    local project_root="$1"
    local env_file="$2"

    if [[ "$env_file" != /* ]]; then
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
    local bootstrap_dir="${DYNSTEER_BOOTSTRAP_VENV:-/opt/bootstrap-venv}"
    export UV_PROJECT_ENVIRONMENT="$venv_dir"
    export UV_CACHE_DIR="$cache_dir"

    if [[ "${DYNSTEER_SKIP_UV_SYNC:-0}" == "1" ]]; then
        return 0
    fi

    if [[ ! -x "$venv_dir/bin/python" ]]; then
        mkdir -p "$venv_dir"
        if [[ -d "$bootstrap_dir" && -x "$bootstrap_dir/bin/python" ]]; then
            cp -R "$bootstrap_dir"/. "$venv_dir"/
        else
            (cd "$project_root" && uv sync --frozen --no-dev --no-install-project --inexact)
        fi
    fi

    (cd "$project_root" && uv sync --frozen --no-dev --no-install-project --inexact)
}

ensure_agentcompass_extra() {
    if [[ "${DYNSTEER_SKIP_UV_SYNC:-0}" == "1" ]]; then
        return 0
    fi
    if experiment_uses_agentcompass "$1" "$2"; then
        echo "Enabling optional AgentCompass bridge for experiment"
        (cd "$1" && uv sync --frozen --no-dev --no-install-project --inexact --extra agentcompass)
    fi
}

ensure_toolsandbox_group() {
    if [[ "${DYNSTEER_SKIP_UV_SYNC:-0}" == "1" ]]; then
        return 0
    fi
    if experiment_uses_toolsandbox "$1" "$2"; then
        echo "Enabling locked ToolSandbox dependencies for experiment"
        (cd "$1" && uv sync --frozen --no-dev --no-install-project --inexact --group toolsandbox)
    fi
}

prepare_benchmark_source() {
    local source_path="$1"

    if [[ ! -d "$source_path" ]]; then
        echo "Benchmark source not found in container: $source_path" >&2
        exit 66
    fi
    if [[ ! -f "$source_path/pyproject.toml" && ! -f "$source_path/setup.py" ]]; then
        echo "Benchmark source must contain pyproject.toml or setup.py: $source_path" >&2
        exit 66
    fi

    export DYNSTEER_BENCHMARK_SOURCE_ROOT="$source_path"
    echo "Using benchmark source for experiment: $source_path"
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

    if [[ "${DYNSTEER_IN_DOCKER:-}" != "1" && ! -f /.dockerenv ]]; then
        if [[ $# -eq 1 && ( "$1" == "-h" || "$1" == "--help" ) ]]; then
            usage
            exit 0
        fi
        run_in_container "$project_root" "$invocation_dir" "$@"
    fi

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
    if [[ -n "$source_path" ]]; then
        prepare_benchmark_source "$source_path"
    else
        local bootstrap_lines
        bootstrap_lines="$(experiment_bootstrap_lines "$project_root" "$experiment_config")"
        local bootstrap_benchmark
        local bootstrap_data_root
        local bootstrap_source_root
        local bootstrap_container_source_root
        local bootstrap_max_workers
        local derived_workers=""
        declare -A installed_sources=()
        while IFS=$'\t' read -r bootstrap_benchmark bootstrap_data_root bootstrap_source_root bootstrap_container_source_root bootstrap_max_workers; do
            if [[ -z "$bootstrap_benchmark" ]]; then
                continue
            fi
            if [[ "$bootstrap_source_root" != "-" && -n "$bootstrap_source_root" && -z "${installed_sources[$bootstrap_source_root]+x}" ]]; then
                prepare_benchmark_source "$bootstrap_source_root"
                installed_sources["$bootstrap_source_root"]=1
            fi
            if [[ "$bootstrap_max_workers" != "-" && -n "$bootstrap_max_workers" ]]; then
                if [[ -z "$derived_workers" || "$bootstrap_max_workers" -lt "$derived_workers" ]]; then
                    derived_workers="$bootstrap_max_workers"
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

    exec python main.py \
        --exp "$experiment_config" \
        "${worker_args[@]}" \
        "${random_seed_args[@]}" \
        "${only_adapt_args[@]}" \
        "${force_adapt_args[@]}" \
        "${force_eval_args[@]}" \
        "${no_sum_args[@]}"
}

main "$@"
