# Journal des changements

Format inspire de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).

## [2.0.2] - 2026-11-27

### Corrige (sécurité)

- Autorisation API : cookie de session `HttpOnly` signé, `?token=` refusé sur les méthodes
  mutantes, verrouillage anti-force-brute, `Cache-Control: no-store` sur le bootstrap.
- Exécution : `run_command` n'hérite plus de l'environnement complet, refuse `sudo`,
  `rm -rf`, `eval`, `curl | sh` et tient compte du `cwd` ; approbations liées à une empreinte
  SHA-256 des arguments, à usage unique, avec expiration.
- Contenu non fiable : encadrement `<untrusted>` non contournable (espaces, caractères de largeur
  nulle, balise non fermée), outils MCP interactifs soumis à approbation, filtrage
  `LD_PRELOAD`/`NODE_OPTIONS`/`PYTHONPATH`.
- Données locales : base, WAL, wiki, sauvegardes et journaux en 0700/0600, `O_NOFOLLOW`,
  restauration atomique, refus des sauvegardes non appartenant à l'utilisateur.
- Infrastructure : unités systemd durcies (`systemd-analyze security` 9.0 -> 3.5), conteneur
  `cap_drop: ALL`, `umask 077`, lanceur avec verrou d'instance et jeton masqué.
- Chaîne d'approvisionnement : parsing RSS via `defusedxml`, révision du modèle WeSpeaker
  épinglée, `huggingface-hub` borné à `< 2.0` (contrainte `fastembed`).

### Ajoute

- Audit de sécurité en trois périmètres (API/outils, injection/navigateur, données/infra) avec
  rapports dans `docs/security/` et 28 constats corrigés, chacun couvert par un test.
- CI `security` : bandit (sévérité moyenne et plus), `pip-audit`, `npm audit` ; cible `make security`.
- Empreinte `SHA256SUMS` jointe aux releases Linux et Windows, vérifiée à l'installation.
- Gouvernance du dépôt : `SECURITY.md`, `CONTRIBUTING.md`, modèles d'issue et de PR, Dependabot.

- Documentation : captures d'ecran reelles (accueil, conversation, configuration, mobile), FAQ de
  depannage (`docs/FAQ.md`) et script de capture reproductible (`web/scripts/capture-screenshots.mjs`).
- Accessibilite : lien d'evitement « Aller au contenu principal » et cible `#main-content`.
- Serveur de simulation : endpoints `/api/version`, `/api/backups`, `/api/doctor`,
  `/api/updates/check` et `/api/system/autostart` pour des captures et tests fideles.
- Typage progressif : `mypy` en CI sur `jarvis_shared` et les modules d'exploitation
  (`backup`, `doctor`, `updates`, `scheduler/routines`, `cli`), cible `make typecheck`.
- Artefacts visuels 2D/3D synchronises a la voix (`present_visual_artifact`) : fenetre dediee,
  rendu SVG/Motion ou react-three-fiber, narration etape par etape, chargement paresseux.

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