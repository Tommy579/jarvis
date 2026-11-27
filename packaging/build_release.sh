#!/usr/bin/env bash
# Construit une archive de distribution Linux autonome : sources + interface deja compilee.
# L'utilisateur final n'a alors besoin ni de Node ni de git, seulement de Python 3.12 et uv.
#
# Usage : bash packaging/build_release.sh [--skip-frontend] [--version X.Y.Z]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SKIP_FRONTEND=0
VERSION=""

while [ $# -gt 0 ]; do
    case "$1" in
        --skip-frontend) SKIP_FRONTEND=1 ;;
        --version)
            VERSION="${2:-}"
            [ -n "$VERSION" ] || { echo "Erreur : --version attend une valeur." >&2; exit 1; }
            shift
            ;;
        -h | --help)
            sed -n '2,7p' "$0"
            exit 0
            ;;
        *)
            echo "Option inconnue : $1" >&2
            exit 1
            ;;
    esac
    shift
done

if [ -z "$VERSION" ]; then
    VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' packages/core/pyproject.toml | head -1)"
fi
[ -n "$VERSION" ] || { echo "Erreur : version introuvable." >&2; exit 1; }

if [ "$SKIP_FRONTEND" -eq 0 ]; then
    echo "Compilation de l'interface web..."
    (cd web && npm ci --no-audit --no-fund && npm run build)
fi

[ -f web/dist/index.html ] || {
    echo "Erreur : web/dist/index.html absent. Lance sans --skip-frontend." >&2
    exit 1
}

NAME="jarvis-$VERSION"
DIST_DIR="$ROOT_DIR/dist"
STAGE="$DIST_DIR/$NAME"
ARCHIVE="$DIST_DIR/$NAME-linux-x86_64.tar.gz"

rm -rf "$STAGE"
mkdir -p "$STAGE"

copy_tree() {
    local source="$1" target="$2"
    mkdir -p "$target"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a \
            --exclude '__pycache__' \
            --exclude '*.pyc' \
            --exclude '*.egg-info' \
            --exclude '.pytest_cache' \
            --exclude '.ruff_cache' \
            "$source"/ "$target"/
    else
        cp -a "$source"/. "$target"/
        find "$target" -type d -name '__pycache__' -prune -exec rm -rf {} +
        find "$target" -type f -name '*.pyc' -delete
        find "$target" -type d -name '*.egg-info' -prune -exec rm -rf {} +
    fi
}

copy_tree packages "$STAGE/packages"
copy_tree config "$STAGE/config"
copy_tree scripts "$STAGE/scripts"
mkdir -p "$STAGE/web"
copy_tree web/dist "$STAGE/web/dist"

for file in pyproject.toml uv.lock README.md INSTALL.md LICENSE CHANGELOG.md \
    install_linux.sh install_windows.ps1 jarvis_icon.png; do
    [ -f "$ROOT_DIR/$file" ] && cp -a "$ROOT_DIR/$file" "$STAGE/$file"
done

chmod +x "$STAGE/scripts/launch_jarvis_v2.sh" "$STAGE/install_linux.sh" 2>/dev/null || true

tar -czf "$ARCHIVE" -C "$DIST_DIR" "$NAME"
rm -rf "$STAGE"

SIZE="$(du -h "$ARCHIVE" | cut -f1)"
echo "Archive creee : $ARCHIVE ($SIZE)"