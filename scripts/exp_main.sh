#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  ./scripts/exp_main.sh [--benchmarks a,b,c] [--benchmark NAME ...] [-- start.sh args]

Examples:
  ./scripts/exp_main.sh --benchmarks toolsandbox
  ./scripts/exp_main.sh --benchmark toolsandbox -- --source ../ToolSandbox --runs-dir runs/batch

If no benchmark is provided, this script runs every data/* directory that contains
benchmark.json.
EOF
}

script_dir() {
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd
}

trim() {
    local value="$1"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "$value"
}

append_csv_benchmarks() {
    local csv="$1"
    local -n target="$2"
    local item
    local -a parsed

    IFS=',' read -r -a parsed <<< "$csv"
    for item in "${parsed[@]}"; do
        item="$(trim "$item")"
        if [[ -n "$item" ]]; then
            target+=("$item")
        fi
    done
}

discover_benchmarks() {
    local project_root="$1"
    local -n target="$2"
    local benchmark_dir

    if [[ ! -d "$project_root/data" ]]; then
        return 0
    fi

    for benchmark_dir in "$project_root"/data/*; do
        if [[ -d "$benchmark_dir" && -f "$benchmark_dir/benchmark.json" ]]; then
            target+=("$(basename "$benchmark_dir")")
        fi
    done
}

main() {
    local script_root
    script_root="$(script_dir)"
    local project_root
    project_root="$(cd -- "$script_root/.." && pwd)"

    local benchmarks=()
    local start_args=()

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --benchmarks)
                if [[ -z "${2:-}" ]]; then
                    echo "--benchmarks requires a comma-separated value." >&2
                    exit 64
                fi
                append_csv_benchmarks "$2" benchmarks
                shift 2
                ;;
            --benchmarks=*)
                append_csv_benchmarks "${1#*=}" benchmarks
                shift
                ;;
            --benchmark)
                if [[ -z "${2:-}" ]]; then
                    echo "--benchmark requires a value." >&2
                    exit 64
                fi
                benchmarks+=("$2")
                shift 2
                ;;
            --benchmark=*)
                benchmarks+=("${1#*=}")
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            --)
                shift
                start_args+=("$@")
                break
                ;;
            -*)
                echo "Unknown exp_main.sh option: $1" >&2
                echo "Pass start.sh options after --." >&2
                exit 64
                ;;
            *)
                benchmarks+=("$1")
                shift
                ;;
        esac
    done

    if [[ ${#benchmarks[@]} -eq 0 ]]; then
        discover_benchmarks "$project_root" benchmarks
    fi

    if [[ ${#benchmarks[@]} -eq 0 ]]; then
        echo "No benchmarks provided and no data/*/benchmark.json files found." >&2
        exit 64
    fi

    local benchmark
    for benchmark in "${benchmarks[@]}"; do
        echo "=== Running benchmark: $benchmark ==="
        "$script_root/start.sh" --benchmark "$benchmark" "${start_args[@]}"
    done
}

main "$@"
