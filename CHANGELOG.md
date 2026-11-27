# Journal des changements

Format inspire de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).

## [2.0.1] - 2026-11-27

### Ajoute

- Endpoint `GET /api/version` (version installee, schema SQLite, uptime, dossier de donnees) et
  version exposee dans `/api/health`.
- Verification des mises a jour via la derniere release GitHub, avec cache de 6 h et tolerance
  hors-ligne (`GET /api/updates/check`).
- Sauvegardes SQLite coherentes (API `sqlite3.backup`, compatible WAL) : tache nocturne a 03:20,
  rattrapage au demarrage si la derniere sauvegarde a plus de 24 h, purge configurable
  (`JARVIS_BACKUP_KEEP`, defaut 7).
- CLI : `jarvis backup now|list|restore` (copie de securite automatique avant restauration) et
  `jarvis doctor` (Python, venv, `.env`, cles LLM, base, disque, port, interface).
- Interface : carte « Version et sauvegardes » et diagnostic de l'installation dans
  Config > Apparence.
- Filet de securite global de l'interface : `AppErrorBoundary` (ecran de secours, rechargement,
  copie du detail) et capture des erreurs de script et promesses rejetees.
- Licence MIT.
- `.dockerignore` et image Docker de production multi-etapes (Python 3.12, front compile,
  utilisateur non-root, healthcheck), verifiee par un job CI.
- Archive de distribution Linux autonome (`packaging/build_release.sh`, `make release`) avec
  interface precompilee, et workflow de release qui l'attache aux tags `v*`.
- `jarvis backup restore` refuse de s'executer si jarvis-core repond encore (`--force` pour
  outrepasser).

### Modifie

- CI : lint etendu a `evals`, nouveau job `docker` (build + `/api/health`).
- Lanceur : rotation des journaux a 5 Mo (`launch.log`, `jarvis-core.log`).
- Installation : `.env` cree en permissions 600.

### Corrige

- `GET /openapi.json` renvoyait 500 : `UpdateThinkingModeRequest` etait defini dans la fonction
  de route et n'etait pas resolu par Pydantic.
- 6 erreurs ruff et 7 fichiers non formates qui maintenaient la CI rouge.
- Icone du raccourci bureau : theme hicolor (`Icon=jarvis`) au lieu d'un chemin mal compose.

### Retire

- Role inutilise `voice_realtime`.
- `src-rust/target/` (57 artefacts de compilation) retire du suivi Git.
- `deploy/docker-compose.yml` obsolete, remplace par le `docker-compose.yml` racine.

## [2.0.0] - 2026-06-30

- Version publique initiale : service `jarvis-core`, interface web React/Three.js, couche
  bureautique `jarvis-desktop`, memoire SQLite + wiki, orchestration multi-modeles.