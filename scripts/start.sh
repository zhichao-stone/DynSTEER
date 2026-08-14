#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  ./scripts/start.sh --benchmark BENCHMARK [--source PATH] [options] [-- extra main.py args]

Options:
  --benchmark NAME      Benchmark name, for example toolsandbox. Required.
  --source PATH         Host/container path to the benchmark source tree to install editable.
  --data-root PATH      Benchmark config directory. Defaults to data/NAME.
  --runs-dir PATH       Runtime artifacts directory. Defaults to runs.
  --results-dir PATH    DynSTEER reports directory. Defaults to results.
  --workers NUM         Max parallel benchmark workers. Defaults to main.py default.
  --only_adapt          Only adapt benchmark data into data-root, do not run evaluation.
  --force_adapt         Rebuild adapted cases even if cached data already exists.
  --force_eval          Re-run evaluation and overwrite existing case outputs.
  --env-file PATH       Env file to source before running. Defaults to .env.
  --no-env-file         Do not source an env file.
  -h, --help            Show this help.

Examples:
  ./scripts/start.sh --benchmark toolsandbox --source ../ToolSandbox --workers 3
  docker compose run --rm -v ../ToolSandbox:/workspace/benchmark-sources/toolsandbox dynsteer --benchmark toolsandbox --source /workspace/benchmark-sources/toolsandbox
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
    if [[ "$input_path" == /* ]]; then
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
        echo "Docker is required when running start.sh outside the container." >&2
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

    local benchmark=""
    local source_path=""
    local arg
    local -a raw_args=("$@")
    local index=0
    while [[ $index -lt ${#raw_args[@]} ]]; do
        arg="${raw_args[$index]}"
        case "$arg" in
            --benchmark)
                benchmark="${raw_args[$((index + 1))]:-}"
                index=$((index + 2))
                ;;
            --benchmark=*)
                benchmark="${arg#*=}"
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
            --)
                break
                ;;
            *)
                index=$((index + 1))
                ;;
        esac
    done

    local -a run_options=(--rm)
    local -a container_args=()
    local container_source=""
    if [[ -n "$source_path" ]]; then
        require_value "--benchmark" "$benchmark"
        local source_abs
        if ! source_abs="$(absolute_host_path "$invocation_dir" "$source_path")"; then
            echo "Benchmark source not found: $source_path" >&2
            exit 66
        fi
        local docker_source
        docker_source="$(docker_mount_path "$source_abs")"
        container_source="/workspace/benchmark-sources/$benchmark"
        run_options+=(--volume "${docker_source}:${container_source}:rw")
    fi

    index=0
    while [[ $index -lt ${#raw_args[@]} ]]; do
        arg="${raw_args[$index]}"
        case "$arg" in
            --source)
                if [[ -n "$container_source" ]]; then
                    container_args+=(--source "$container_source")
                else
                    container_args+=(--source "${raw_args[$((index + 1))]:-}")
                fi
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

    exec "${compose_parts[@]}" -f "$project_root/docker-compose.yml" run "${run_options[@]}" dynsteer "${container_args[@]}"
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
    local benchmark="$1"
    if [[ "${DYNSTEER_SKIP_UV_SYNC:-0}" == "1" ]]; then
        return 0
    fi
    case "$benchmark" in
        swebench_pro|skillsbench)
            echo "Enabling optional AgentCompass bridge for benchmark: $benchmark"
            (cd "$project_root" && uv sync --frozen --no-dev --no-install-project --inexact --extra agentcompass)
            ;;
    esac
}

ensure_toolsandbox_group() {
    local project_root="$1"
    local benchmark="$2"
    if [[ "${DYNSTEER_SKIP_UV_SYNC:-0}" == "1" ]]; then
        return 0
    fi
    if [[ "$benchmark" == "toolsandbox" ]]; then
        echo "Enabling locked ToolSandbox dependencies for benchmark: $benchmark"
        (cd "$project_root" && uv sync --frozen --no-dev --no-install-project --inexact --group toolsandbox)
    fi
}

prepare_benchmark_source() {
    local project_root="$1"
    local benchmark="$2"
    local source_path="$3"

    if [[ "$source_path" != /* ]]; then
        source_path="$project_root/$source_path"
    fi
    if [[ ! -d "$source_path" ]]; then
        echo "Benchmark source not found in container: $source_path" >&2
        exit 66
    fi
    if [[ ! -f "$source_path/pyproject.toml" && ! -f "$source_path/setup.py" ]]; then
        echo "Benchmark source must contain pyproject.toml or setup.py: $source_path" >&2
        exit 66
    fi

    export DYNSTEER_BENCHMARK_SOURCE_ROOT="$source_path"
    echo "Using benchmark source for $benchmark: $source_path"
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

    local benchmark=""
    local source_path=""
    local data_root=""
    local runs_dir="${DYNSTEER_RUNS_DIR:-runs}"
    local results_dir="${DYNSTEER_RESULTS_DIR:-results}"
    local workers=""
    local only_adapt="0"
    local force_adapt="0"
    local force_eval="0"
    local env_file="${DYNSTEER_ENV_FILE:-.env}"
    local load_env_file="1"
    local extra_args=()

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --benchmark)
                require_value "$1" "${2:-}"
                benchmark="$2"
                shift 2
                ;;
            --benchmark=*)
                benchmark="${1#*=}"
                require_value "--benchmark" "$benchmark"
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
            --data-root)
                require_value "$1" "${2:-}"
                data_root="$2"
                shift 2
                ;;
            --data-root=*)
                data_root="${1#*=}"
                require_value "--data-root" "$data_root"
                shift
                ;;
            --runs-dir)
                require_value "$1" "${2:-}"
                runs_dir="$2"
                shift 2
                ;;
            --runs-dir=*)
                runs_dir="${1#*=}"
                require_value "--runs-dir" "$runs_dir"
                shift
                ;;
            --results-dir)
                require_value "$1" "${2:-}"
                results_dir="$2"
                shift 2
                ;;
            --results-dir=*)
                results_dir="${1#*=}"
                require_value "--results-dir" "$results_dir"
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
            --)
                shift
                extra_args+=("$@")
                break
                ;;
            *)
                extra_args+=("$1")
                shift
                ;;
        esac
    done

    if [[ -z "$benchmark" ]]; then
        usage >&2
        exit 64
    fi

    if [[ -z "$data_root" ]]; then
        data_root="data/$benchmark"
    fi
    local effective_data_root="$data_root"

    if [[ "$load_env_file" == "1" ]]; then
        source_env_file "$project_root" "$env_file"
    fi

    cd "$project_root"
    ensure_uv_environment "$project_root"
    ensure_agentcompass_extra "$benchmark"
    ensure_toolsandbox_group "$project_root" "$benchmark"
    if [[ -n "$source_path" ]]; then
        prepare_benchmark_source "$project_root" "$benchmark" "$source_path"
    else
        unset DYNSTEER_BENCHMARK_SOURCE_ROOT
    fi

    local worker_args=()
    if [[ -n "$workers" ]]; then
        worker_args=(--workers "$workers")
    fi
    local only_adapt_args=()
    if [[ "$only_adapt" == "1" ]]; then
        only_adapt_args=(--only_adapt)
    fi
    local force_adapt_args=()
    if [[ "$force_adapt" == "1" ]]; then
        force_adapt_args=(--force_adapt)
    fi
    local force_eval_args=()
    if [[ "$force_eval" == "1" ]]; then
        force_eval_args=(--force_eval)
    fi

    exec python main.py \
        --benchmark "$benchmark" \
        --data-root "$effective_data_root" \
        --runs-dir "$runs_dir" \
        --results-dir "$results_dir" \
        "${worker_args[@]}" \
        "${force_adapt_args[@]}" \
        "${force_eval_args[@]}" \
        "${only_adapt_args[@]}" \
        "${extra_args[@]}"
}

main "$@"
