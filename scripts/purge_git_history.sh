#!/usr/bin/env bash
set -euo pipefail

# Purge des donnees personnelles (bases SQLite, profils JSON, backups) de tout l'historique Git.
# A executer depuis la racine du depot apres avoir sauvegarde vos fichiers locaux dans jarvis-data/backups/.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p jarvis-data/backups
for f in jarvis_history.db jarvis_profile.json; do
    if [[ -f "$f" ]]; then
        cp -f "$f" "jarvis-data/backups/$f"
    fi
done

if command -v git-filter-repo &>/dev/null; then
    git filter-repo --invert-paths \
        --path jarvis_history.db \
        --path jarvis_profile.json \
        --path jarvis-windows/jarvis_history.db \
        --path jarvis-windows/jarvis_profile.json \
        --path core/assistant.py.bak \
        --force
else
    git filter-branch --force --index-filter \
        "git rm --cached --ignore-unmatch jarvis_history.db jarvis_profile.json jarvis-windows/jarvis_history.db jarvis-windows/jarvis_profile.json core/assistant.py.bak" \
        --prune-empty --tag-name-filter cat -- --all
    rm -rf .git/refs/original/
    git reflog expire --expire=now --all
    git gc --prune=now --aggressive
fi

# Restauration des copies locales non suivies par git
for f in jarvis_history.db jarvis_profile.json; do
    if [[ -f "jarvis-data/backups/$f" && ! -f "$f" ]]; then
        cp -f "jarvis-data/backups/$f" "$f"
    fi
done

echo "Purge locale terminee. Verification :"
git log --all --oneline -- jarvis_history.db jarvis_profile.json
echo "Pour pousser la purge sur GitHub : git push origin --force --all && git push origin --force --tags"
