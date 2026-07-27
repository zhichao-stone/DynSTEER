#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  ./scripts/start_no_docker.sh --benchmark BENCHMARK [--source PATH] [options] [-- extra main.py args]

Options:
  --benchmark NAME      Benchmark name, for example toolsandbox. Required.
  --source PATH         Local benchmark source tree to install editable.
  --data-root PATH      Benchmark config directory. Defaults to data/NAME.
  --runs-dir PATH       Runtime artifacts directory. Defaults to runs.
  --results-dir PATH    DynSTEER reports directory. Defaults to results.
  --workers NUM         Max parallel benchmark workers. Defaults to main.py default.
  --only_adapt          Only adapt benchmark data into data-root, do not run evaluation.
  --force_adapt         Rebuild adapted cases even if cached data already exists.
  --env-file PATH       Env file to source before running. Defaults to .env.
  --no-env-file         Do not source an env file.
  -h, --help            Show this help.

Examples:
  ./scripts/start_no_docker.sh --benchmark toolsandbox --source ../ToolSandbox --workers 3
  ./scripts/start_no_docker.sh --benchmark toolsandbox --source ../ToolSandbox --only_adapt
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

    cd "$project_root"
    uv sync --frozen --no-dev --no-install-project --inexact
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

install_benchmark_source() {
    local benchmark="$1"
    local source_path="$2"
    local invocation_dir="$3"
    local python_executable="$4"

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
    echo "Installing benchmark source for $benchmark: $source_abs"
    uv pip install --python "$python_executable" --editable "$source_abs"
}

main() {
    local invocation_dir
    invocation_dir="$(pwd)"
    local script_root
    script_root="$(script_dir)"
    local project_root
    project_root="$(cd -- "$script_root/.." && pwd)"

    local benchmark=""
    local source_path=""
    local data_root=""
    local runs_dir="${DYNSTEER_RUNS_DIR:-runs}"
    local results_dir="${DYNSTEER_RESULTS_DIR:-results}"
    local workers=""
    local only_adapt="0"
    local force_adapt="0"
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

    if [[ "$load_env_file" == "1" ]]; then
        source_env_file "$project_root" "$env_file"
    fi

    cd "$project_root"
    ensure_uv_environment "$project_root"
    local python_executable
    python_executable="$(project_python)"
    if [[ -n "$source_path" ]]; then
        install_benchmark_source "$benchmark" "$source_path" "$invocation_dir" "$python_executable"
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

    exec "$python_executable" main.py \
        --benchmark "$benchmark" \
        --data-root "$data_root" \
        --runs-dir "$runs_dir" \
        --results-dir "$results_dir" \
        "${worker_args[@]}" \
        "${force_adapt_args[@]}" \
        "${only_adapt_args[@]}" \
        "${extra_args[@]}"
}

main "$@"
