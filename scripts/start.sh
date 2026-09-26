#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat <<'EOF'
Usage:
  ./scripts/start.sh --exp data/experiments/{benchmark}_{experiment_type}.json [options]

Compatibility entry for start_experiment.sh.
EOF
    exit 0
fi

exec "$script_dir/start_experiment.sh" "$@"
