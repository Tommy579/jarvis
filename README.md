# JARVIS 2.0 (Open Mind Interface)

Assistant personnel local compose d'un service central Python (`jarvis-core`), d'une interface web React/TypeScript (`web/`) et d'une couche bureautique (`jarvis-desktop`).

**Installation guidee (Linux, Windows, executable) : [INSTALL.md](INSTALL.md)**

## Architecture

- `packages/shared/` : modeles Pydantic v2, protocole WebSocket (`jarvis_shared.ws_protocol`) et generateur de types TypeScript.
- `packages/core/` : API FastAPI + WebSocket, runtime d'execution (`jarvis_core.runtime`), memoire SQLite unifiee (`jarvis.db` + FTS5) + wiki Markdown, boucle agent en streaming, approbations de risque, routeur multi-modeles (OpenRouter, Gemini, Ollama), objectifs supervises et routines planifiees (`jarvis_core.scheduler`).
- `packages/desktop/` : integration bureau Linux/Windows, capture camera/ecran et pipeline vocal.
- `web/` : application React/Vite avec sphere vocale 3D (`VoiceSphere` en chargement differe) et repli Canvas 2D.
- `evals/` : banc d'essai reproductible (`EvalHarness`).

## Installation

```bash
make install        # uv sync (Python 3.12) + npm install
make install-full   # ajoute les extras voice (faster-whisper, onnxruntime) et memory (fastembed)
```

Les dependances sont declarees dans les trois `pyproject.toml` du workspace : une installation
neuve n'a besoin d'aucun autre fichier de dependances.

## Configuration

Creez un fichier `.env` a la racine (ou copiez `.env.example`) et renseignez au moins une cle LLM :

```bash
OPENROUTER_API_KEY=...
# ou GEMINI_API_KEY=...
```

Le service exige un jeton d'acces (`JARVIS_API_TOKEN`) : sans lui il refuse de demarrer, sauf si
`JARVIS_ALLOW_INSECURE=1` est defini explicitement pour un usage local sur loopback.
`scripts/launch_jarvis_v2.sh` genere automatiquement le jeton, l'ecrit dans `.env` et l'injecte
dans l'interface, qui le memorise ensuite pour la session du navigateur. Pour un lancement manuel
sans ce script, l'interface affiche un ecran de saisie du jeton au premier appel refuse (401).

## Demarrage local

Tout-en-un (service + fenetre Chromium) :

```bash
make run
```

Backend seul (`jarvis-core`) :

```bash
PYTHONPATH=packages/shared:packages/core:packages/desktop:. .venv/bin/python -m uvicorn jarvis_core.api.server:app --host 127.0.0.1 --port 8765
```

Interface web en developpement :

```bash
cd web && npm run dev
```

Interface en ligne de commande :

```bash
jarvis eval --save
jarvis chat --model deepseek/deepseek-v4.1-flash-1017
```

## Tests et qualite

```bash
PYTHONPATH=packages/shared:packages/core:packages/desktop:. .venv/bin/python -m pytest tests -q
env -u PYTHONPATH .venv/bin/python -m ruff check packages tests evals
env -u PYTHONPATH .venv/bin/python -m ruff format --check packages tests evals
cd web && npm run build && npx vitest run && npx playwright test
```

La CI ajoute un job d'installation propre : les paquets sont installes dans un venv vierge avec
leurs seules dependances declarees, puis `jarvis_core.api.server` doit s'importer. Un job
`docker` construit l'image de production et verifie `/api/health`.

## Exploitation : version, sauvegardes, diagnostic

- `GET /api/version` expose la version installee, la version du schema SQLite et l'uptime.
  L'ecran Config > Apparence affiche cette version et permet de verifier les mises a jour
  (`GET /api/updates/check`, derniere release GitHub, cache de 6 h).
- Sauvegardes : instantane SQLite coherent (API `backup`, compatible WAL) chaque nuit a 03:20,
  au demarrage si la derniere date de plus de 24 h, et a la demande.

```bash
jarvis backup now            # sauvegarde immediate
jarvis backup list           # 7 dernieres par defaut (JARVIS_BACKUP_KEEP)
jarvis backup restore <nom>  # service jarvis-core arrete ; l'ancienne base est copiee de cote
jarvis doctor                # python, venv, .env, cles, base, disque, port, interface
```

Les journaux vivent dans `~/.local/state/jarvis/` (`launch.log`, `jarvis-core.log`) et sont
rotationnes a 5 Mo par le lanceur, pour ne pas grossir indefiniment.
