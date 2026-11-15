# OMI 2.0 (Open Mind Interface)

Assistant personnel local compose d'un service central Python (`omi-core`), d'une interface web React/TypeScript (`web/`) et d'une couche bureautique (`omi-desktop`).

## Architecture

- `packages/shared/` : modeles Pydantic v2, protocole WebSocket (`omi_shared.ws_protocol`) et generateur de types TypeScript.
- `packages/core/` : API FastAPI + WebSocket, runtime d'execution (`omi_core.runtime`), memoire SQLite unifiee (`omi.db` + FTS5) + wiki Markdown, boucle agent en streaming, approbations de risque et routeur multi-modeles (OpenRouter, Gemini).
- `packages/desktop/` : integration bureau Linux/Windows, capture camera/ecran et pipeline vocal.
- `web/` : application React/Vite avec sphere vocale 3D (`VoiceSphere` en chargement differe) et repli Canvas 2D.
- `evals/` : banc d'essai reproductible (`EvalHarness`).
- `legacy/` : reference historique conservee hors du flux principal.

## Configuration

Creez un fichier `.env` a la racine avec un jeton d'acces pour l'API et au moins une cle LLM :

```bash
OMI_API_TOKEN=change-me-local-token
OPENROUTER_API_KEY=...
# ou GEMINI_API_KEY=...
```

Sans `OMI_API_TOKEN`, le serveur refuse de demarrer sauf si `OMI_ALLOW_INSECURE=1` est defini explicitement pour un usage local sur loopback.

## Demarrage local

Backend (`omi-core`) :

```bash
PYTHONPATH=packages/shared:packages/core:packages/desktop:. .venv/bin/python -m uvicorn omi_core.api.server:app --host 127.0.0.1 --port 8765
```

Interface web :

```bash
cd web && npm install && npm run dev
```

Interface en ligne de commande :

```bash
omi eval --save
omi chat --model deepseek/deepseek-v4.1-flash-1017
```

## Tests et qualite

```bash
PYTHONPATH=packages/shared:packages/core:packages/desktop:. .venv/bin/python -m pytest tests -q
env -u PYTHONPATH .venv/bin/python -m ruff check packages tests
env -u PYTHONPATH .venv/bin/python -m ruff format --check packages tests
cd web && npm run build && npx vitest run && npx playwright test
```
