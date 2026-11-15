#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR/packages/shared:$ROOT_DIR/packages/core:$ROOT_DIR/packages/desktop:$ROOT_DIR"
export OMI_WEB_URL="${OMI_WEB_URL:-http://localhost:8765}"
export OMI_ALLOW_INSECURE="${OMI_ALLOW_INSECURE:-1}"
export OMI_WINDOW_SIZE="${OMI_WINDOW_SIZE:-1307,824}"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

if ! curl -fsS "${OMI_WEB_URL}/api/health" >/dev/null 2>&1; then
    nohup "$PYTHON_BIN" -m uvicorn omi_core.api.server:app --host 127.0.0.1 --port 8765 >/tmp/omi-core.log 2>&1 &
    for _ in {1..60}; do
        if curl -fsS "${OMI_WEB_URL}/api/health" >/dev/null 2>&1; then
            break
        fi
        sleep 0.25
    done
fi

if [[ "${1:-}" == "--backend-only" ]]; then
    exit 0
fi

if command -v chromium >/dev/null 2>&1; then
    exec chromium --class=jarvis-app --app="$OMI_WEB_URL" --window-size="$OMI_WINDOW_SIZE"
else
    exec xdg-open "$OMI_WEB_URL"
fi
