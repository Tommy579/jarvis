#!/usr/bin/env bash
# Lance jarvis-core (si besoin) puis ouvre l'interface dans une fenetre Chromium.
# Usage : bash scripts/launch_jarvis_v2.sh [--backend-only]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="$ROOT_DIR/.env"
export PYTHONPATH="$ROOT_DIR/packages/shared:$ROOT_DIR/packages/core:$ROOT_DIR/packages/desktop:$ROOT_DIR"
export JARVIS_WEB_URL="${JARVIS_WEB_URL:-http://localhost:8765}"
export JARVIS_WINDOW_SIZE="${JARVIS_WINDOW_SIZE:-1307,824}"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

# --- Jeton d'acces --------------------------------------------------------------
# Le service refuse de demarrer sans JARVIS_API_TOKEN (sauf JARVIS_ALLOW_INSECURE=1).
# Le lanceur garantit donc un jeton : repris de .env, sinon genere et persiste, puis
# transmis a l'interface via ?token=... (la page le memorise et nettoie l'URL).
TOKEN="${JARVIS_API_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -f "$ENV_FILE" ]; then
    TOKEN="$(grep -E '^JARVIS_API_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"'"'"' \r')"
    # .env.example fournit un gabarit : on ne le prend pas pour un vrai jeton.
    case "$TOKEN" in
        changez_ce_jeton_secret|change-me-local-token) TOKEN="" ;;
    esac
fi

if [ -z "$TOKEN" ]; then
    if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
        echo "Environnement Python absent : lance 'make install' d'abord." >&2
        exit 1
    fi
    TOKEN="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    touch "$ENV_FILE"
    if grep -qE '^JARVIS_API_TOKEN=' "$ENV_FILE"; then
        sed -i "s|^JARVIS_API_TOKEN=.*|JARVIS_API_TOKEN=${TOKEN}|" "$ENV_FILE"
    else
        printf '\n# Jeton genere automatiquement par scripts/launch_jarvis_v2.sh\nJARVIS_API_TOKEN=%s\n' \
            "$TOKEN" >>"$ENV_FILE"
    fi
    echo "Jeton d'acces genere et enregistre dans .env (JARVIS_API_TOKEN)."
fi
export JARVIS_API_TOKEN="$TOKEN"

# --- Service central ------------------------------------------------------------
if ! curl -fsS "${JARVIS_WEB_URL}/api/health" >/dev/null 2>&1; then
    nohup "$PYTHON_BIN" -m uvicorn jarvis_core.api.server:app --host 127.0.0.1 --port 8765 >/tmp/jarvis-core.log 2>&1 &
    for _ in {1..60}; do
        if curl -fsS "${JARVIS_WEB_URL}/api/health" >/dev/null 2>&1; then
            break
        fi
        sleep 0.25
    done
fi

if ! curl -fsS "${JARVIS_WEB_URL}/api/health" >/dev/null 2>&1; then
    echo "jarvis-core ne repond pas sur ${JARVIS_WEB_URL} (voir /tmp/jarvis-core.log)." >&2
    exit 1
fi

APP_URL="${JARVIS_WEB_URL}/?token=${TOKEN}"

if [[ "${1:-}" == "--backend-only" ]]; then
    echo "jarvis-core actif sur ${JARVIS_WEB_URL}"
    echo "Interface : ${APP_URL}"
    exit 0
fi

if command -v chromium >/dev/null 2>&1; then
    exec chromium --user-data-dir="${XDG_CONFIG_HOME:-$HOME/.config}/chromium-jarvis" --app="$APP_URL" --window-size="$JARVIS_WINDOW_SIZE"
else
    exec xdg-open "$APP_URL"
fi
