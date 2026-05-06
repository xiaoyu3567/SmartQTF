#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

source /opt/homebrew/Caskroom/miniforge/base/bin/activate QTF

export SMARTQTF_USE_PROXY="${SMARTQTF_USE_PROXY:-1}"
export PYTHONPATH="${PYTHONPATH:-.}"
export SMARTQTF_WORKER_URL="${SMARTQTF_WORKER_URL:-http://127.0.0.1:6667}"
export SMARTQTF_WEB_SMOKE_URL="${SMARTQTF_WEB_SMOKE_URL:-http://127.0.0.1:3000}"

cd "${PROJECT_ROOT}"

curl --fail --silent --show-error "${SMARTQTF_WORKER_URL}/health" >/dev/null
curl --fail --silent --show-error "${SMARTQTF_WEB_SMOKE_URL}/smartqtf" >/dev/null

python -m pytest -q tests/test_smartqtf_web_api_smoke.py

if [[ -n "${SMARTQTF_PLAYWRIGHT_CDP_URL:-}" ]]; then
  (
    cd "${PROJECT_ROOT}/web"
    pnpm exec node scripts/cdp_runtime_console_smoke.mjs
  )
fi
