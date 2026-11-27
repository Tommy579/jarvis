#!/usr/bin/env bash
# Lance jarvis-core (si besoin) puis ouvre l'interface dans une fenetre Chromium.
# Usage : bash scripts/launch_jarvis_v2.sh [--backend-only]
#
# Ce script est appele par le raccourci du menu applications (.desktop) : il n'a donc
# AUCUNE console. Toute erreur doit finir dans un journal et, si possible, dans une
# notification bureau, sinon le lancement echoue silencieusement.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="$ROOT_DIR/.env"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/jarvis"
LOG_FILE="$STATE_DIR/launch.log"
mkdir -p "$STATE_DIR"
if [ ! -t 1 ]; then
    # Lancement par raccourci : tout va dans le journal.
    exec >>"$LOG_FILE" 2>&1
fi
echo "=== $(date '+%Y-%m-%d %H:%M:%S') lancement ($*) ==="

fail() {
    echo "ERREUR: $1" >&2
    if command -v notify-send >/dev/null 2>&1; then
        notify-send -a JARVIS -u critical "JARVIS n'a pas demarre" "$1 (journal : $LOG_FILE)" || true
    fi
    exit 1
}

export PYTHONPATH="$ROOT_DIR/packages/shared:$ROOT_DIR/packages/core:$ROOT_DIR/packages/desktop:$ROOT_DIR"
export JARVIS_WEB_URL="${JARVIS_WEB_URL:-http://localhost:8765}"
export JARVIS_WINDOW_SIZE="${JARVIS_WINDOW_SIZE:-1307,824}"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

[ -x "$PYTHON_BIN" ] || fail "Environnement Python absent ($PYTHON_BIN). Lance 'make install' d'abord."

# --- Jeton d'acces --------------------------------------------------------------
# Le service refuse de demarrer sans JARVIS_API_TOKEN. Le lanceur garantit un jeton :
# repris de .env, sinon genere et persiste, puis transmis a l'interface via ?token=...
TOKEN="${JARVIS_API_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -f "$ENV_FILE" ]; then
    # sed -n ne renvoie pas 1 quand la cle manque (contrairement a grep sous pipefail).
    TOKEN="$(sed -n 's/^JARVIS_API_TOKEN=//p' "$ENV_FILE" | head -1 | tr -d '\r' | sed -E "s/^[\"']//; s/[\"']\$//")"
fi
# .env.example fournit un gabarit : on ne le prend pas pour un vrai jeton.
case "$TOKEN" in
    changez_ce_jeton_secret | change-me-local-token) TOKEN="" ;;
esac

if [ -z "$TOKEN" ]; then
    TOKEN="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(32))')" ||
        fail "Generation du jeton impossible."
    touch "$ENV_FILE" || fail "Impossible d'ecrire dans $ENV_FILE."
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
    if [ ! -f "$ROOT_DIR/web/dist/index.html" ]; then
        echo "ATTENTION: web/dist absent, l'interface sera vide. Compile avec 'make install'."
    fi
    nohup "$PYTHON_BIN" -m uvicorn jarvis_core.api.server:app --host 127.0.0.1 --port 8765 \
        >"$STATE_DIR/jarvis-core.log" 2>&1 &
    for _ in {1..60}; do
        if curl -fsS "${JARVIS_WEB_URL}/api/health" >/dev/null 2>&1; then
            break
        fi
        sleep 0.25
    done
fi

if ! curl -fsS "${JARVIS_WEB_URL}/api/health" >/dev/null 2>&1; then
    tail -n 20 "$STATE_DIR/jarvis-core.log" >&2 2>/dev/null || true
    fail "jarvis-core ne repond pas sur ${JARVIS_WEB_URL} (journal : $STATE_DIR/jarvis-core.log)."
fi

APP_URL="${JARVIS_WEB_URL}/?token=${TOKEN}"

if [[ "${1:-}" == "--backend-only" ]]; then
    echo "jarvis-core actif sur ${JARVIS_WEB_URL}"
    echo "Interface : ${APP_URL}"
    exit 0
fi

if [ -x /usr/lib/chromium/chromium ]; then
    exec /usr/lib/chromium/chromium --user-data-dir="${XDG_CONFIG_HOME:-$HOME/.config}/chromium-jarvis" \
        --app="$APP_URL" --window-size="$JARVIS_WINDOW_SIZE"
elif command -v chromium >/dev/null 2>&1; then
    exec chromium --user-data-dir="${XDG_CONFIG_HOME:-$HOME/.config}/chromium-jarvis" \
        --app="$APP_URL" --window-size="$JARVIS_WINDOW_SIZE"
elif command -v google-chrome-stable >/dev/null 2>&1; then
    exec google-chrome-stable --user-data-dir="${XDG_CONFIG_HOME:-$HOME/.config}/chromium-jarvis" \
        --app="$APP_URL" --window-size="$JARVIS_WINDOW_SIZE"
else
    exec xdg-open "$APP_URL"
fi
