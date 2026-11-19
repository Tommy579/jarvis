#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════
#   JARVIS 2.0.0 — Automated Linux Installer
#   Usage: bash install_linux.sh [GEMINI_API_KEY]
# ═══════════════════════════════════════════════════════

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

step()  { echo -e "\n${CYAN}${BOLD}──────────────────────────────────────────────────${NC}"; echo -e "  ${BOLD}$1${NC}"; echo -e "${CYAN}──────────────────────────────────────────────────${NC}"; }
ok()    { echo -e "  ${GREEN}✓${NC} $1"; }
warn()  { echo -e "  ${YELLOW}⚠${NC}  $1"; }
fail()  { echo -e "  ${RED}✗${NC} $1"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
ENV_FILE="$SCRIPT_DIR/.env"
LAUNCHER="$SCRIPT_DIR/scripts/launch_jarvis_v2.sh"
ICON_PATH="$SCRIPT_DIR/jarvis_icon.png"

echo -e "\n${BOLD}═══════════════════════════════════════════════════${NC}"
echo -e "   ${BOLD}JARVIS 2.0.0 — AUTOMATED LINUX INSTALLER${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}\n"

# ── Step 1: Configuration (.env) ────────────────────────
step "1/5 · Configuration (.env)"

API_KEY="${1:-${GEMINI_API_KEY:-}}"
if [ ! -f "$ENV_FILE" ] && [ -f "$SCRIPT_DIR/.env.example" ]; then
    cp "$SCRIPT_DIR/.env.example" "$ENV_FILE"
    ok ".env créé depuis .env.example"
fi

if [ -n "$API_KEY" ]; then
    if [ -f "$ENV_FILE" ] && grep -q '^GEMINI_API_KEY=' "$ENV_FILE"; then
        sed -i "s|^GEMINI_API_KEY=.*|GEMINI_API_KEY=$API_KEY|" "$ENV_FILE"
    else
        echo "GEMINI_API_KEY=$API_KEY" >> "$ENV_FILE"
    fi
    ok "GEMINI_API_KEY enregistrée dans .env"
elif [ ! -f "$ENV_FILE" ]; then
    touch "$ENV_FILE"
    warn "Aucun .env existant : pensez à renseigner vos clés API dans $ENV_FILE"
else
    ok ".env existant conservé"
fi

# ── Step 2: Dépendances système ─────────────────────────
step "2/5 · Dépendances système"

if command -v pacman &>/dev/null; then
    sudo pacman -S --needed --noconfirm \
        portaudio playerctl plocate xdg-desktop-portal-kde nodejs npm python uv \
        || warn "Certains paquets pacman n'ont pas pu être installés"
    ok "Dépendances Arch Linux vérifiées"
elif command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y python3 python3-pip python3-venv git curl libportaudio2 nodejs npm \
        || warn "Certains paquets apt n'ont pas pu être installés"
    ok "Dépendances Debian/Ubuntu vérifiées"
else
    warn "Gestionnaire de paquets non reconnu — vérifiez python3, nodejs, npm et portaudio manuellement."
fi

# ── Step 3: Environnement Python (workspace JARVIS 2.0) ─
step "3/5 · Environnement Python JARVIS 2.0"

cd "$SCRIPT_DIR"
if command -v uv &>/dev/null; then
    uv sync || uv sync --python 3.12
    ok "Workspace Python synchronisé via uv (.venv)"
else
    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
    fi
    "$VENV_DIR/bin/pip" install --upgrade pip -q
    "$VENV_DIR/bin/pip" install -e packages/shared -e packages/core -e packages/desktop -q
    ok "Packages JARVIS 2.0 installés dans .venv"
fi

# ── Step 4: Interface Web React (web/dist) ──────────────
step "4/5 · Compilation de l'interface Web 2.0"

if [ -d "$SCRIPT_DIR/web" ] && [ -f "$SCRIPT_DIR/web/package.json" ]; then
    cd "$SCRIPT_DIR/web"
    npm ci --no-audit --no-fund
    npm run build
    ok "Frontend compilé dans web/dist"
else
    fail "Dossier web/ introuvable dans $SCRIPT_DIR"
fi

# ── Step 5: Raccourcis et démarrage automatique ─────────
step "5/5 · Raccourcis bureau et autostart"

chmod +x "$LAUNCHER"
mkdir -p "$HOME/.local/share/applications" "$HOME/.config/autostart"

cat > "$HOME/.local/share/applications/jarvis.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=JARVIS
GenericName=Assistant IA personnel
Comment=Interface web JARVIS 2.0
Exec=$LAUNCHER
Path=$SCRIPT_DIR
Icon=$ICON_PATH
Terminal=false
Categories=Utility;Application;
StartupNotify=true
StartupWMClass=chrome-localhost__-Default
NoDisplay=false
EOF

cat > "$HOME/.config/autostart/JarvisAssistant.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=JARVIS
GenericName=Assistant IA personnel
Comment=Interface web JARVIS 2.0
Exec=$LAUNCHER
Path=$SCRIPT_DIR
Icon=$ICON_PATH
Terminal=false
Categories=Utility;Application;
StartupNotify=true
StartupWMClass=chrome-localhost__-Default
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF

update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
kbuildsycoca6 2>/dev/null || kbuildsycoca5 2>/dev/null || true
systemctl --user daemon-reload 2>/dev/null || true
ok "Raccourcis JARVIS 2.0.0 installés et caches actualisés"

echo -e "\n${GREEN}${BOLD}Installation JARVIS 2.0.0 terminée !${NC}"
echo -e "  Lancer maintenant : ${CYAN}$LAUNCHER${NC}\n"
