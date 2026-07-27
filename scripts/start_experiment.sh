#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  ./scripts/start_experiment.sh --experiment-config PATH [--source PATH] [options]

Options:
  --experiment-config PATH   Unified experiment config JSON. Required.
  --source PATH              Host/container benchmark source tree to install editable.
  --workers NUM              Max parallel experiment workers. Defaults to main.py default.
  --env-file PATH            Env file to source before running. Defaults to .env.
  --no-env-file              Do not source an env file.
  -h, --help                 Show this help.

Examples:
  ./scripts/start_experiment.sh --experiment-config data/experiments/double_benchmark_initial.json --source ../ToolSandbox --workers 1
  docker compose run --rm --entrypoint bash -v ../ToolSandbox:/workspace/benchmark-sources/toolsandbox dynsteer /workspace/DynSTEER/scripts/start_experiment.sh --experiment-config data/experiments/double_benchmark_initial.json --source /workspace/benchmark-sources/toolsandbox
EOF
}

script_dir() {
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd
}

detect_compose() {
    if docker compose version >/dev/null 2>&1; then
        echo "docker compose"
        return 0
    fi
    if command -v docker-compose >/dev/null 2>&1; then
        echo "docker-compose"
        return 0
    fi
    return 1
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

docker_mount_path() {
    local host_path="$1"
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -w "$host_path"
        return 0
    fi
    printf '%s\n' "$host_path"
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

    local source_path=""
    local arg
    local -a raw_args=("$@")
    local index=0
    while [[ $index -lt ${#raw_args[@]} ]]; do
        arg="${raw_args[$index]}"
        case "$arg" in
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

install_benchmark_source() {
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
    echo "Installing benchmark source for experiment: $source_path"
    uv pip install --python "${UV_PROJECT_ENVIRONMENT:-.venv}/bin/python" --editable "$source_path"
}

main() {
    local invocation_dir
    invocation_dir="$(pwd)"
    local script_root
    script_root="$(script_dir)"
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
    local env_file="${DYNSTEER_ENV_FILE:-.env}"
    local load_env_file="1"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --experiment-config)
                require_value "$1" "${2:-}"
                experiment_config="$2"
                shift 2
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
    if [[ -n "$source_path" ]]; then
        install_benchmark_source "$source_path"
    else
        unset DYNSTEER_BENCHMARK_SOURCE_ROOT
    fi

    local worker_args=()
    if [[ -n "$workers" ]]; then
        worker_args=(--workers "$workers")
    fi

    exec python main.py \
        --experiment-config "$experiment_config" \
        "${worker_args[@]}"
}

main "$@"
