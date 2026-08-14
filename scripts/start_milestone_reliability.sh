#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    echo "Usage: $0 --exp PATH [--force] [--workers NUM] [options]"
    exit 0
fi
command -v docker >/dev/null 2>&1 || { echo "Docker is required" >&2; exit 127; }

# shellcheck source=scripts/experiment_bootstrap.sh
. "$root/scripts/experiment_bootstrap.sh"
compose_cmd="$(detect_compose)" || {
    echo "Docker Compose is required" >&2
    exit 127
}
# shellcheck disable=SC2206
compose_parts=($compose_cmd)

experiment_config=""
raw_args=("$@")
for ((index=0; index<${#raw_args[@]}; index++)); do
    case "${raw_args[$index]}" in
        --exp|--experiment-config)
            index=$((index + 1))
            experiment_config="${raw_args[$index]:-}"
            ;;
        --exp=*|--experiment-config=*)
            experiment_config="${raw_args[$index]#*=}"
            ;;
    esac
done
[[ -n "$experiment_config" ]] || { echo "--exp is required" >&2; exit 64; }

bootstrap_lines="$(
    experiment_bootstrap_lines "$root" "$experiment_config" "/workspace/DynSTEER"
)" || exit $?
run_options=(--rm --entrypoint bash)
declare -A mounted_sources=()
while IFS=$'\t' read -r benchmark data_root source_root container_source_root max_workers; do
    if [[ -z "$benchmark" || "$source_root" == "-" || "$container_source_root" == "-" ]]; then
        continue
    fi
    if [[ -n "${mounted_sources[$source_root]+x}" ]]; then
        continue
    fi
    if ! source_abs="$(absolute_host_path "$root" "$source_root")"; then
        echo "Benchmark source not found: $source_root" >&2
        exit 66
    fi
    docker_source="$(docker_mount_path "$source_abs")"
    run_options+=(--volume "${docker_source}:${container_source_root}:rw")
    mounted_sources["$source_root"]=1
done <<< "$bootstrap_lines"

export DYNSTEER_HOST_UID="${DYNSTEER_HOST_UID:-$(id -u)}"
export DYNSTEER_HOST_GID="${DYNSTEER_HOST_GID:-$(id -g)}"
exec "${compose_parts[@]}" -f "$root/docker-compose.yml" run \
    "${run_options[@]}" dynsteer \
    /workspace/DynSTEER/scripts/start_milestone_reliability_no_docker.sh "$@"
