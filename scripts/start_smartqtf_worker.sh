#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

source /opt/homebrew/Caskroom/miniforge/base/bin/activate QTF

export SMARTQTF_USE_PROXY="${SMARTQTF_USE_PROXY:-1}"
export PYTHONPATH="${PYTHONPATH:-.}"
export SMARTQTF_WORKER_HOST="${SMARTQTF_WORKER_HOST:-127.0.0.1}"
export SMARTQTF_WORKER_PORT="${SMARTQTF_WORKER_PORT:-6667}"
export SMARTQTF_WORKER_CONFIG="${SMARTQTF_WORKER_CONFIG:-config/examples/paper-runtime.example.json}"

cd "${PROJECT_ROOT}"

python scripts/smartqtf_worker.py \
  --host "${SMARTQTF_WORKER_HOST}" \
  --port "${SMARTQTF_WORKER_PORT}" \
  --config "${SMARTQTF_WORKER_CONFIG}" \
  "$@"
