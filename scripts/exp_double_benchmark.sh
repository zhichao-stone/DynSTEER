#!/usr/bin/env bash
set -euo pipefail

python main.py \
  --experiment-config data/experiments/double_benchmark_initial.json \
  --workers "${DYNSTEER_EXPERIMENT_WORKERS:-1}"
