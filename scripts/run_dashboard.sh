#!/usr/bin/env bash
# Real Time Desk on one Linux box. No sudo, no Docker, no Temporal.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  rm -rf .venv
  if ! python3 -m venv .venv; then
    # Some images ship Python without ensurepip (no python3-venv package).
    rm -rf .venv
    python3 -m venv --without-pip .venv
    curl -fsSL https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
  fi
fi
if [[ ! -x .venv/bin/streamlit ]]; then
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export SETUP_COMPLETE="${SETUP_COMPLETE:-true}"
export DESK_BIND="${DESK_BIND:-0.0.0.0}"
export DESK_PORT="${DESK_PORT:-8501}"
export DESK_TIMEZONE="${DESK_TIMEZONE:-America/Chicago}"
export OLLAMA_HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3.5:9b}"

exec .venv/bin/python -m streamlit run dashboard/app.py \
  --server.address "$DESK_BIND" \
  --server.port "$DESK_PORT" \
  --server.headless true \
  --browser.gatherUsageStats false
