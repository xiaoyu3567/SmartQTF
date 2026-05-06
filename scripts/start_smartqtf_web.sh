#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WEB_ROOT="${PROJECT_ROOT}/web"

export SMARTQTF_WORKER_URL="${SMARTQTF_WORKER_URL:-http://127.0.0.1:6667}"
export HOSTNAME="${HOSTNAME:-127.0.0.1}"
export PORT="${PORT:-3000}"

cd "${WEB_ROOT}"

pnpm dev --hostname "${HOSTNAME}" --port "${PORT}" "$@"
