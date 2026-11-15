#!/usr/bin/env bash
set -euo pipefail

# Purge des donnees personnelles (bases SQLite, profils JSON, backups) de tout l'historique Git.
# A executer depuis la racine du depot apres avoir sauvegarde vos fichiers locaux dans omi-data/backups/.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p omi-data/backups
for f in omi_history.db omi_profile.json; do
    if [[ -f "$f" ]]; then
        cp -f "$f" "omi-data/backups/$f"
    fi
done

if command -v git-filter-repo &>/dev/null; then
    git filter-repo --invert-paths \
        --path omi_history.db \
        --path omi_profile.json \
        --path omi-windows/omi_history.db \
        --path omi-windows/omi_profile.json \
        --path core/assistant.py.bak \
        --force
else
    git filter-branch --force --index-filter \
        "git rm --cached --ignore-unmatch omi_history.db omi_profile.json omi-windows/omi_history.db omi-windows/omi_profile.json core/assistant.py.bak" \
        --prune-empty --tag-name-filter cat -- --all
    rm -rf .git/refs/original/
    git reflog expire --expire=now --all
    git gc --prune=now --aggressive
fi

# Restauration des copies locales non suivies par git
for f in omi_history.db omi_profile.json; do
    if [[ -f "omi-data/backups/$f" && ! -f "$f" ]]; then
        cp -f "omi-data/backups/$f" "$f"
    fi
done

echo "Purge locale terminee. Verification :"
git log --all --oneline -- omi_history.db omi_profile.json
echo "Pour pousser la purge sur GitHub : git push origin --force --all && git push origin --force --tags"
