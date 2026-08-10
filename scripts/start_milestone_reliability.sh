#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then echo "Usage: $0 --exp PATH [--force] [options]"; exit 0; fi
command -v docker >/dev/null 2>&1 || { echo "Docker is required" >&2; exit 127; }
command -v docker compose >/dev/null 2>&1 || { echo "Docker Compose is required" >&2; exit 127; }
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec docker compose -f "$root/docker-compose.yml" run --rm --entrypoint bash dynsteer \
  /workspace/DynSTEER/scripts/start_milestone_reliability_no_docker.sh "$@"
