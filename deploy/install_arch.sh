#!/usr/bin/env bash
set -euo pipefail

# Script d'installation JARVIS 2.0 pour Arch Linux / KDE Wayland
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Installation des dependances systeme via pacman..."
sudo pacman -S --needed --noconfirm \
  portaudio \
  playerctl \
  plocate \
  xdg-desktop-portal-kde \
  tailscale \
  uv \
  nodejs \
  npm

echo "Synchronisation de l'environnement Python 3.12 via uv..."
uv python install 3.12
uv sync --python 3.12

if [ -d "$ROOT_DIR/web" ] && [ -f "$ROOT_DIR/web/package.json" ]; then
  echo "Installation et build du front-end React..."
  (cd "$ROOT_DIR/web" && npm ci && npm run build)
fi

echo "Installation de l'unite systemd utilisateur jarvis-desktop..."
mkdir -p "${HOME}/.config/systemd/user"
cp -f "$ROOT_DIR/deploy/jarvis-desktop.service" "${HOME}/.config/systemd/user/jarvis-desktop.service"
systemctl --user daemon-reload || true
systemctl --user enable jarvis-desktop.service || true

echo "Installation Arch Linux terminee. Pensez a verifier .env et Tailscale."
