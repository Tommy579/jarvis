# Rapport d'audit de sécurité — Périmètre Opus (données, infrastructure, chaîne d'approvisionnement)

Branche : `sec/opus-data`  
Périmètre : mémoire SQLite et wiki Markdown, sauvegardes/restauration, diagnostic, outils fichiers, unités `systemd`, conteneur Docker, lanceur et installeur Linux, dépendances et configuration.

---

## 1. Synthèse des vulnérabilités et vérifications

| ID | Sévérité | Fichier:ligne | Impact | Reproduction | Correctif | Statut |
|----|----------|---------------|--------|--------------|-----------|--------|
| O-01 | Élevé | `memory/schema.py:379-420`, `doctor.py:108-127` | Base `jarvis.db` (`0644`), dossier `jarvis-data/` (`0755`) et journaux `-wal`/`-shm` lisibles par tout utilisateur local | `stat` sur `jarvis-data/` (`755`/`644` sous `umask 0022`) ; `test_security_data.py::test_new_database_and_wal_files_are_private`, `test_existing_world_readable_database_is_tightened` | Création du dossier en `0700`, pré-création atomique `O_CREAT\|O_EXCL\|O_NOFOLLOW` de `jarvis.db` en `0600` (héritée par `-wal`/`-shm`), resserrage au démarrage et contrôle dans `jarvis doctor` | corrigé |
| O-02 | Élevé | `memory/schema.py:422-436, 1122-1143` | Repli silencieux en clair si `encryption_key` est fourni sans `sqlcipher3`, et `decide_approval` acceptait d'approuver en SQLite une demande expirée | `test_security_data.py::test_encryption_key_without_sqlcipher_fails_closed`, `test_expired_approval_cannot_be_approved_in_database` | Échec fermé (`RuntimeError`) si `encryption_key` est défini sans `sqlcipher3` ; marquage `expired` si `expires_at <= now` dans `decide_approval` | corrigé |
| O-03 | Élevé | `backup.py:78-115, 186-207` | Sauvegardes `jarvis-data/backups/*.db` et dossier créés en `0644`/`0755` et sensibles au suivi d'un lien symbolique pré-posé | `ls -la jarvis-data/backups` (`-rw-r--r--`) ; `test_security_data.py::test_backup_file_and_directory_are_private`, `test_backup_does_not_follow_planted_symlink` | Dossier `0700`, création de chaque snapshot et copie de sécurité en `0600` via `os.open(..., O_NOFOLLOW)` | corrigé |
| O-04 | Élevé | `backup.py:216-265` | Restauration via `shutil.copy2` suivant un lien symbolique cible (écrasement arbitraire hors `jarvis-data`), reprenant les droits du fichier source et accumulant les `pre-restore-*` sans purge | `test_security_data.py::test_restore_refuses_symlinked_target`, `test_restore_refuses_symlinked_source`, `test_restore_result_is_private_even_if_source_is_world_readable`, `test_pre_restore_copies_are_pruned` | Refus des liens symboliques source/cible, copie via fichier de transit `0600` puis `os.replace` atomique, purge des `pre-restore-*` selon `JARVIS_BACKUP_KEEP` | corrigé |
| O-05 | Moyen | `memory/import_legacy.py:16-187` | `jarvis_profile.json` malformé ou hostile (types inattendus, taille illimitée, lien symbolique) faisait planter le démarrage ; pages wiki créées en `0644` | `test_security_data.py::test_hostile_profile_types_do_not_crash_startup`, `test_hostile_profile_sizes_are_bounded`, `test_wiki_pages_are_private_and_symlinks_not_followed` | Validation de type par section, plafond 1 Mo / 500 faits / longueurs bornées, refus des liens symboliques, wiki créé en `0700`/`0600` avec `O_NOFOLLOW` | corrigé |
| O-06 | Élevé | `tools/standard.py:120-210` | `read_file`/`write_file` bloquaient `jarvis-data` par nom uniquement : un `JARVIS_DATA_DIR` renommé, `~/.local/state/jarvis/*.log` et `~/.config/chromium-jarvis` restaient lisibles/modifiables | `test_security_data.py::test_file_tools_block_custom_data_dir_and_launcher_state` | Blocage explicite des chemins résolus sous `JARVIS_DATA_DIR`, `$XDG_STATE_HOME/jarvis` et `$XDG_CONFIG_HOME/chromium-jarvis` | corrigé |
| O-07 | Critique | `deploy/jarvis-core.service`, `deploy/jarvis-desktop.service` | `jarvis-core.service` tournait en `root` implicite sans `ProtectSystem`, `ProtectHome`, `UMask` ni restriction de capacités (`systemd-analyze`: `9.0 UNSAFE`) | `systemd-analyze security --offline=yes deploy/jarvis-core.service` (`9.0` -> `3.5 OK`) ; `test_security_infra.py::test_core_unit_never_runs_as_root_and_is_hardened`, `test_desktop_unit_has_user_compatible_hardening` | Ajout de `User=jarvis`, `UMask=0077`, `ProtectSystem=strict`, `ProtectHome=true`, `ReadWritePaths=/opt/jarvis`, `CapabilityBoundingSet=`, `RestrictAddressFamilies` en préservant `/dev/snd` et `/dev/video*` ; `UMask=0077` et durcissement compatible session sur `jarvis-desktop.service` | corrigé |
| O-08 | Élevé | `Dockerfile:46-60`, `docker-compose.yml:19-26`, `.dockerignore` | `.dockerignore` sans `**/` laissait passer `web/.env` ou `packages/**/*.db` ; volume `/data` monté avec `umask 022` (`0644` sur l'hôte) ; capacités Docker par défaut conservées | `test_security_infra.py::test_dockerignore_excludes_nested_secrets_and_data`, `test_dockerfile_runs_unprivileged_with_private_umask`, `test_compose_drops_capabilities_and_publishes_on_loopback` | Motifs récursifs `**/.env*`, `**/*.db*`, `**/*.pem`/`key` dans `.dockerignore` ; `chmod 700 /data` et `umask 077` dans `Dockerfile` ; `cap_drop: ALL`, `pids_limit: 512` et `tmpfs` dans `docker-compose.yml` | corrigé |
| O-09 | Élevé | `scripts/launch_jarvis_v2.sh:16-139`, `install_linux.sh:35-100` | Journaux `~/.local/state/jarvis/*.log` en `0644` contenant le jeton en clair (`--backend-only` / `GET /?token=`), absence de verrou anti-double-lancement ; `install_linux.sh` créait `.env` avant `chmod 600` et utilisait `uv sync` non figé | `stat ~/.local/state/jarvis/*` (`644`) ; `grep -c "token=" jarvis-core.log` (`5`) ; `test_security_infra.py::test_launcher_restricts_permissions_and_does_not_log_token`, `test_installer_uses_frozen_lock_and_private_env` | `umask 077`, `chmod 700/600` sur l'état et `JARVIS_DATA_DIR`, verrou `flock` non hérité (`9>&-`), masquage du jeton hors TTY ; `umask 077`, contrôle de `GEMINI_API_KEY` et `uv sync --frozen` dans `install_linux.sh` | corrigé |
| O-10 | Faible | `.env.example:19,43`, `.gitignore:44-57` | Gabarits de secrets fixes (`changez_ce_jeton_secret`, `un_secret_hmac_aleatoire_pour_les_liens_mobiles`) exposés en cas de copie naïve en Docker/systemd ; `.gitignore` omettait `.env.*`, `*.db-wal`, `*.db-shm`, `*.pem`, `*.key` | `test_security_infra.py::test_env_example_has_no_usable_placeholder_secrets`, `test_gitignore_covers_sqlite_side_files_and_env_variants` | Variables `JARVIS_API_TOKEN=` et `ACTION_TOKEN_SECRET=` vidées par défaut avec consigne de génération ; complétude de `.gitignore` | corrigé |

---

## 2. Détail par entrée (faits vérifiés)

### O-01 — Permissions de la base SQLite en clair et de `jarvis-data/` (`memory/schema.py`, `doctor.py`)
- **Vérification** : Sur le poste courant (`umask 0022`), `jarvis-data/` était en `0755`, `jarvis-data/jarvis.db` en `0644`, `jarvis_history.db` et `jarvis_profile.json` en `0644`. `MemoryDatabase.__init__` créait le répertoire parent sans `mode=0o700` et laissait `sqlite3.connect` créer la base, `-wal` et `-shm` avec l'`umask` courant.
- **Impact réel** : Fuite de l'intégralité de la mémoire personnelle (faits, épisodes, personnes, historique des appels outils) auprès de tout autre compte système local.
- **Correctif** : Création du dossier parent en `0700`, pré-création de `jarvis.db` en `0600` via `os.open(..., O_CREAT|O_EXCL|O_NOFOLLOW, 0o600)` avant ouverture SQLite (qui applique alors les mêmes droits à `-wal` et `-shm`), resserrage via `secure_file_permissions()` sur les bases existantes, et nouveau contrôle `permissions donnees` dans `jarvis_core/doctor.py`.
- **Tests** : `tests/test_security_data.py::test_new_database_and_wal_files_are_private`, `test_existing_world_readable_database_is_tightened`, `test_doctor_warns_on_world_readable_data`.
- **Risque résiduel** : Les fichiers historiques hors `JARVIS_DATA_DIR` créés manuellement avant la v2 (`jarvis_history.db`, `jarvis_profile.json` à la racine) restent à restreindre ou supprimer après import (`chmod 600` / suppression de `jarvis_profile.json`).

### O-02 — Chiffrement SQLite optionnel en échec ouvert et expiration SQL (`memory/schema.py`)
- **Vérification** : `MemoryDatabase._connect` interceptait `ImportError` sur `sqlcipher3`, émettait un `logger.warning` et retombait sur `sqlite3.connect` en clair. De plus, `sqlcipher3` n'est pas déclaré dans `packages/core/pyproject.toml` ni présent dans `.venv`, et `decide_approval` mettait à jour `status` sans vérifier `expires_at`.
- **Impact réel** : Un opérateur croyant activer le chiffrement au repos (`encryption_key=...`) obtenait silencieusement une base en clair ; une approbation expirée pouvait être marquée `approved` en base si appelée hors route REST durcie.
- **Correctif** : Si `encryption_key` est renseigné sans `sqlcipher3`, `_connect` lève désormais `RuntimeError` (échec fermé). `decide_approval` vérifie `expires_at` et force le statut `expired` si le délai est dépassé.
- **Tests** : `tests/test_security_data.py::test_encryption_key_without_sqlcipher_fails_closed`, `test_encryption_key_uses_sqlcipher_when_available`, `test_expired_approval_cannot_be_approved_in_database`.
- **Risque résiduel** : En configuration standard sans `encryption_key` (cas actuel de `jarvis-core`), SQLite reste non chiffré au repos sur le disque ; la confidentialité repose alors sur les droits POSIX `0600`/`0700` et le chiffrement disque système (LUKS).

### O-03 & O-04 — Permissions, liens symboliques et rétention des sauvegardes (`backup.py`)
- **Vérification** : Sur disque, `jarvis-data/backups` était en `0755` et `jarvis-20261127T192947Z.db` en `0644`. Dans `backup.py`, `restore_database` utilisait `shutil.copy2(source, target)` sans refuser les liens symboliques ni créer un fichier temporaire privé, et conservait indéfiniment les `pre-restore-*.db`. Note : `jarvis backup restore <fichier>` (`cli.py:244`) accepte volontairement un chemin explicite saisi par l'administrateur local (hors `backups/` pour restaurer une archive externe), à condition que ni la source ni la cible ne soient des liens symboliques.
- **Impact réel** : Fuite des sauvegardes en `0644` ; si `jarvis.db` ou une cible dans `backups/` était remplacé par un lien symbolique, `backup_database`/`restore_database` pouvait écraser un fichier arbitraire ; une restauration depuis un fichier `0644` rendait `jarvis.db` de nouveau lisible par tous.
- **Correctif** : `_private_dir` (`0700`), `_create_private_file` (`O_CREAT|O_TRUNC|O_NOFOLLOW`, `0600`), refus des liens symboliques source/cible dans `restore_database`, copie de restauration dans un fichier temporaire `0600` du même dossier puis `os.replace`, et purge des `pre-restore-*` via `_prune_pre_restore`.
- **Tests** : `tests/test_security_data.py::test_backup_file_and_directory_are_private`, `test_backup_does_not_follow_planted_symlink`, `test_restore_refuses_symlinked_target`, `test_restore_refuses_symlinked_source`, `test_restore_result_is_private_even_if_source_is_world_readable`, `test_pre_restore_copies_are_pruned`.
- **Risque résiduel** : Un utilisateur administrateur qui exécute manuellement `jarvis backup restore` garde le droit de restaurer un fichier externe de son choix tant qu'il ne s'agit pas d'un lien symbolique.

### O-05 — Import `jarvis_profile.json` hostile et permissions du wiki (`memory/import_legacy.py`)
- **Vérification** : `import_legacy_profile` lisait `jarvis_profile.json` sans limite de taille, sans gestion de JSON invalide et appelait `.get()` sur `identity`/`work`/`preferences` sans vérifier qu'il s'agissait d'un `dict`, faisant planter le démarrage de `JarvisCoreState` si une section valait une liste ou une chaîne. Les 4 pages Markdown créées dans `jarvis-data/wiki/` prenaient les droits `0644`.
- **Impact réel** : Déni de service au démarrage via un fichier d'import corrompu ou piégé, et exposition des notes personnelles du wiki (`0644`).
- **Correctif** : Refus des liens symboliques, plafond de 1 Mo (`MAX_PROFILE_BYTES`), capture de `ValueError`/`UnicodeDecodeError`/`RecursionError`, validation stricte de type par section, bornes sur le nombre de faits (500) et longueurs de clés/valeurs, et création du wiki en `0700`/`0600` avec `O_NOFOLLOW`.
- **Tests** : `tests/test_security_data.py::test_hostile_profile_types_do_not_crash_startup`, `test_hostile_profile_sizes_are_bounded`, `test_invalid_json_profile_is_ignored`, `test_wiki_pages_are_private_and_symlinks_not_followed`.
- **Risque résiduel** : Les pages wiki créées avant correctif restent protégées par le `chmod 700` du dossier parent (`launch_jarvis_v2.sh` / `doctor.py`).

### O-06 — Protection des dossiers privés Jarvis dans les outils fichiers (`tools/standard.py`)
- **Vérification** : `_SENSITIVE_USER_DIR_NAMES` ne bloquait que les noms littéraux `jarvis-data` et `jarvis-browser`. Si `JARVIS_DATA_DIR` pointait vers un autre nom de dossier, ou si l'agent lisait `~/.local/state/jarvis/jarvis-core.log` ou `~/.config/chromium-jarvis/`, `is_sensitive_read_path` et `is_sensitive_system_path` renvoyaient `False`.
- **Impact réel** : Lecture ou altération des journaux contenant `?token=`, de la base mémoire sous chemin personnalisé ou du profil Chromium dédié par les outils `read_file`/`write_file`.
- **Correctif** : Ajout de `_jarvis_private_roots()` et `_is_jarvis_private_path()` dans `standard.py` bloquant `JARVIS_DATA_DIR`, `$XDG_STATE_HOME/jarvis` et `$XDG_CONFIG_HOME/chromium-jarvis`.
- **Tests** : `tests/test_security_data.py::test_file_tools_block_custom_data_dir_and_launcher_state`.
- **Risque résiduel** : `list_directory` reste autorisé sur le répertoire parent et `run_command` suit la politique d'approbation/confinement d'environnement (périmètre Carbon).

### O-07 — Durcissement des unités `systemd` (`deploy/jarvis-core.service`, `deploy/jarvis-desktop.service`)
- **Vérification** : `systemd-analyze security --offline=yes deploy/jarvis-core.service` renvoyait `9.0 UNSAFE :-{` (`User=` absent donc `root`, aucun `ProtectSystem`, `ProtectHome`, `UMask`, ni restriction de capacités ou familles d'adresses).
- **Impact réel** : Élévation de privilèges immédiate en `root` si `jarvis-core.service` était déployé tel quel (d'autant que l'agent expose `run_command`), et création de fichiers système lisibles par tous.
- **Correctif** :
  - `jarvis-core.service` : `User=jarvis`, `Group=jarvis`, `SupplementaryGroups=audio video`, `UMask=0077`, `ProtectSystem=strict`, `ReadWritePaths=/opt/jarvis`, `ProtectHome=true`, `StateDirectory=jarvis` / `CacheDirectory=jarvis` (`0700`, avec `XDG_CACHE_HOME`/`XDG_STATE_HOME`), `CapabilityBoundingSet=`, `AmbientCapabilities=`, `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK`, `ProtectKernel*`, `RestrictNamespaces=true`, `RestrictSUIDSGID=true`, `LockPersonality=true`. `PrivateDevices` est volontairement omis pour garder l'accès à `/dev/snd` et `/dev/video*`. Score `systemd-analyze security --offline=yes` ramené à **`3.5 OK :-)`**.
  - `jarvis-desktop.service` (unité utilisateur `--user`) : ajout de `UMask=0077`, `NoNewPrivileges=true`, `LockPersonality=true`, `RestrictSUIDSGID=true`, `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK`, sans `ProtectHome`/`ProtectSystem` qui casseraient les capteurs KDE/D-Bus/Wayland et la lecture de `~/.local/state`.
- **Tests** : `tests/test_security_infra.py::test_core_unit_never_runs_as_root_and_is_hardened`, `test_desktop_unit_has_user_compatible_hardening`, `test_core_unit_exposure_score_is_ok`.
- **Risque résiduel** : Le déploiement sur `/opt/jarvis` suppose d'avoir exécuté les commandes prérequis commentées en tête de `jarvis-core.service` (`useradd jarvis`, `chown`/`chmod`).

### O-08 — Durcissement Docker et contexte de build (`Dockerfile`, `docker-compose.yml`, `.dockerignore`)
- **Vérification** : L'image utilise déjà un multi-stage non-root (`USER jarvis`, UID 1000), un `HEALTHCHECK` sur `127.0.0.1:8765/api/health` (port réel `8765`), une publication `127.0.0.1:8765:8765` et `no-new-privileges:true`. Trois faiblesses ont été relevées : (1) `.dockerignore` utilisait `.env` / `*.db` sans `**/` (laissant passer `web/.env` ou `packages/**/*.db` dans `COPY web/` / `COPY packages`), (2) l'image créait `/data` en `0755` et tournait avec `umask 022` (données écrites en `0644` sur le volume hôte), (3) `docker-compose.yml` ne retirait pas les capacités Linux (`cap_drop: ALL`) ni ne bornait `pids_limit`.
- **Impact réel** : Inclusion accidentelle d'un `.env` ou `.db` dans un sous-dossier lors d'un `docker build`, et exposition en `0644` du volume de données monté sur l'hôte.
- **Correctif** : Motifs récursifs `**/` dans `.dockerignore`, `chmod 700 /data` et `umask 077` dans le `CMD` du `Dockerfile`, ajout de `cap_drop: ALL`, `pids_limit: 512` et `tmpfs` dans `docker-compose.yml`.
- **Tests** : `tests/test_security_infra.py::test_dockerignore_excludes_nested_secrets_and_data`, `test_dockerfile_runs_unprivileged_with_private_umask`, `test_compose_drops_capabilities_and_publishes_on_loopback`.
- **Risque résiduel** : `docker-compose.yml` passe `env_file: .env` au conteneur (usage normal hors image) ; `jarvis` écoute sur `0.0.0.0:8765` dans son espace réseau isolé afin de répondre au proxy de port Docker publié sur `127.0.0.1:8765`.

### O-09 — Lanceur, journaux et installeur Linux (`scripts/launch_jarvis_v2.sh`, `install_linux.sh`)
- **Vérification** :
  - Sur le poste, `~/.local/state/jarvis/` était en `0755` et ses 3 journaux (`launch.log`, `launch.log.1`, `jarvis-core.log`) en `0644`. La rotation à 5 Mo (`rotate_log`) était fonctionnelle, mais `jarvis-core.log` contenait 5 lignes `GET /?token=...` (journaux d'accès uvicorn) et `launch.log.1` 2 lignes issues de `--backend-only`.
  - Le lanceur n'avait aucun verrou d'instance unique avant de lancer `uvicorn` et ouvrait Chromium avec `--app=http://localhost:8765/?token=...`, visible dans `/proc/<pid>/cmdline` (`ps aux` constaté sur l'instance active).
  - `install_linux.sh` copiait `.env.example` vers `.env` avec `umask 022` avant d'appeler `chmod 600` (fenêtre temporelle en `0644`), acceptait `GEMINI_API_KEY` en argument CLI (`$1`, visible dans `ps`) sans avertissement et l'injectait dans `sed` sans validation, et appelait `uv sync` sans `--frozen` (ni aucun `curl | sh` heureusement).
- **Impact réel** : Lecture des journaux (et du jeton `--backend-only` / access logs) par un autre utilisateur local, lancement concurrent possible de deux `uvicorn`, fenêtre `0644` sur `.env` à l'installation et injection `sed` si une clé contient `|`/`&`.
- **Correctif** : `umask 077`, `chmod 700`/`600` sur `$STATE_DIR` et ses `.log`, `chmod 700` sur `$DATA_DIR` (lu depuis `JARVIS_DATA_DIR` ou `.env`), verrou `flock -w 30 9` fermé avant le lancement d'`uvicorn` et `chromium` (`9>&-`), masquage du jeton dans `--backend-only` lorsque la sortie n'est pas un terminal. Dans `install_linux.sh` : `umask 077` autour de la création de `.env`, avertissement si clé passée en `$1`, contrôle alphanumérique avant `sed`, et `uv sync --frozen`.
- **Tests** : `tests/test_security_infra.py::test_launcher_restricts_permissions_and_does_not_log_token`, `test_shell_scripts_parse`, `test_installer_uses_frozen_lock_and_private_env`.
- **Risque résiduel (hors périmètre)** : Le passage initial du jeton via `--app=http://localhost:8765/?token=${TOKEN}` reste visible dans `/proc/<pid>/cmdline` (même UID Linux, ou autre UID si `/proc` n'est pas monté avec `hidepid=2`) et dans les logs d'accès HTTP tant que le contrat serveur (`api/server.py` / `web/src/lib/authToken.ts`, hors périmètre Opus) ne remplace pas `?token=` au lancement par un échange via fichier temporaire `0600` ou jeton one-shot échangé contre le cookie `HttpOnly` ajouté par Carbon.

### O-10 — Chaîne d'approvisionnement et configuration (`.env.example`, `.gitignore`, `config/*.yaml`, `pyproject.toml`, `web/package.json`)
- **Vérification** :
  - `uv run --frozen --with pip-audit pip-audit` : **0 vulnérabilité connue** (`No known vulnerabilities found` ; les 3 paquets locaux `jarvis-core`, `jarvis-desktop`, `jarvis-shared` sont ignorés car absents de PyPI).
  - `cd web && npm audit --omit=dev` : **0 vulnérabilité** (`found 0 vulnerabilities`).
  - `pyproject.toml` et `packages/*/pyproject.toml` utilisent des bornes `>=`, tout comme `web/package.json` (`^`), mais l'installation reproductible est garantie par `uv.lock` (`--frozen` ajouté dans `install_linux.sh`, déjà présent dans `Dockerfile`) et `web/package-lock.json` (`npm ci`).
  - `config/mcp.yaml` : les 3 serveurs (`playwright`, `notion`, `github`) sont `enabled: false` par défaut avec versions `npx` épinglées (`@playwright/mcp@0.0.28`, `@notionhq/notion-mcp-server@1.8.1`, `@modelcontextprotocol/server-github@2025.4.8`).
  - `config/models.yaml` et `config/persona.md` : aucun secret ni URL interne privée exposée.
  - `.env.example` contenait `JARVIS_API_TOKEN=changez_ce_jeton_secret` et `ACTION_TOKEN_SECRET=un_secret_hmac_aleatoire_pour_les_liens_mobiles` (valeurs connues publiquement si reprises sans modification en Docker/systemd). `.gitignore` ne listait pas `.env.*`, `*.db-wal`, `*.db-shm`, `*.pem`, `*.key`, `secrets.enc`.
- **Correctif** : Vidage des deux gabarits secrets dans `.env.example` (avec commande `secrets.token_urlsafe(32)` pour Docker/systemd) et ajout des motifs manquants dans `.gitignore`.
- **Tests** : `tests/test_security_infra.py::test_env_example_has_no_usable_placeholder_secrets`, `test_gitignore_covers_sqlite_side_files_and_env_variants`, `test_mcp_servers_are_disabled_and_pinned`.
- **Risque résiduel** : Si un serveur MCP est activé dans `config/mcp.yaml`, il télécharge et exécute un paquet npm via `npx -y` sans vérification de somme locale hors cache npm.

---

## 3. Ce que je n'ai pas pu vérifier

1. **Build et inspection de l'image Docker réelle** (`docker build` / `docker history`) : binaire `docker` absent sur la machine (`which: no docker`). Les vérifications sur `Dockerfile`, `docker-compose.yml` et `.dockerignore` sont statiques.
2. **Exécution réelle de `jarvis-core.service` sous `systemd` avec utilisateur `jarvis` et matériel audio/vidéo** : `systemd-analyze security --offline=yes` a validé le profil (`3.5 OK`), mais pas un démarrage effectif avec microphone (`/dev/snd`) et webcam (`/dev/video*`).
3. **Chiffrement effectif via `sqlcipher3`** : la bibliothèque `sqlcipher3` n'est pas installée dans `.venv` ; la branche nominale avec `sqlcipher3` a été testée par simulateur (`monkeypatch`), et la branche d'échec fermé sans `sqlcipher3` a été testée en réel.
4. **Exécution complète d'`install_linux.sh` et du lanceur graphique** (`sudo pacman`/`apt-get`, autostart KDE, Chromium).

---

## 4. Relecture croisée (`sec/carbon-api` et `sec/neon-injection`)

### Méthode et résultats vérifiés
- Comparaison de `git diff --name-only main..sec/carbon-api` et `main..sec/neon-injection` par rapport aux §3 de `SECURITY_CARBON.md` et `SECURITY_NEON.md`.
- Vérification de l'absence de collision de migration SQLite (`memory/schema.py` n'est modifié ni par Carbon ni par Neon).
- Test d'échec avant correctif (sur `main`) et de réussite après correctif dans des worktrees isolés.
- Test de fusion à trois voies (`sec/opus-data` + `sec/carbon-api` + `sec/neon-injection`) : fusion automatique sans aucun conflit Git, et **49/49 tests de sécurité passés** (`test_security_data.py`, `test_security_infra.py`, `test_security_api.py`, `test_security_injection.py`).

### A. Branche `sec/carbon-api` — Avis : **À fusionner** (avec 2 remarques mineures)

- **Périmètre** :
  - Respecté sur `security.py`, `tools/registry.py`, `tools/approvals.py`, `tools/autonomy.py`, `tools/standard.py` (uniquement `run_command` / `is_dangerous_command` / `_build_sanitized_command_env`), `mcp/client.py`, `integrations/base.py`, `integrations/lifestyle.py`, `tests/test_security_api.py`, `docs/security/carbon-report.md`.
  - Remarque mineure sur `api/server.py` : en plus des routes prévues (auth, middlewares, `/actions/*`, `/api/oauth/*`, `/api/webhooks/*`, `/api/approvals*`), Carbon a légitimement durci `/ws` (verrouillage anti-force-brute) et masqué les détails `502` sur `/api/voice/transcribe`, `/api/voice/wake-check` et `/api/integrations/{id}/sync`.
  - Écart mineur justifié : `integrations/apple_calendar.py` et `integrations/github_probes.py` ne sont pas des intégrations OAuth, mais leur modification se limite strictement au masquage des URLs secrètes (`.ics` / jetons) dans les exceptions HTTP (`redact_sensitive_text`).
- **Conflits / lacune résiduelle repérée** :
  - **Conflit avec Opus / Neon** : aucun conflit Git ni d'intention ; `tools/standard.py` et `api/server.py` fusionnent proprement.
  - **Point d'attention sur WebSocket `action.approve` (`api/server.py:3805` sur `sec/carbon-api`)** : Carbon a sécurisé `POST /api/approvals/{id}` et `POST /actions/{token}`, mais le handler WebSocket `action.approve` appelle toujours `core_state.db.decide_approval` et `runtime.approvals.resolve` sans vérifier au préalable si la demande est déjà résolue ou expirée avant `_broadcast_approval_resolved`. Le correctif Opus dans `memory/schema.py` empêche désormais la base SQLite d'accepter l'expiration, mais l'événement WS pourrait encore diffuser un statut incohérent si appelé après expiration.
- **Preuve rouge/vert des tests** :
  - Sur `main`, `pytest tests/test_security_api.py` échoue dès la collecte par `ImportError: cannot import name 'write_secret_env_0600' from 'jarvis_core.integrations.base'` (import de nouveaux symboles en tête de module). Sur `sec/carbon-api`, les **10 tests** de `test_security_api.py` et l'ensemble de la suite (`189 passed, 3 skipped`, `ruff`, `mypy`) passent. Remarque mineure : importer `write_secret_env_0600` et `redact_sensitive_text` via module/fallback dans `test_security_api.py` permettrait de constater individuellement les 10 échecs sur `main`.

### B. Branche `sec/neon-injection` — Avis : **À fusionner** (avec remarques de périmètre à documenter au merge)

- **Périmètre** :
  - Respecté sur `security.py` (`wrap_untrusted_content` et `contains_untrusted_tags`), `memory/engine.py`, `tools/browser.py`, `integrations/*` (`gmail.py`, `outlook.py`, `weather_transport.py`), `channels/telegram.py`, `runtime.py`, `web/index.html`, `web/src/components/VisualArtifactWindow.tsx`, `web/src/lib/authToken.ts`, `tests/test_security_injection.py`, `web/src/tests/security.test.tsx`, `docs/security/neon-report.md`.
  - **Fichiers hors liste stricte du §3** (tous techniquement cohérents avec les hypothèses du §6, mais dépassant la liste du §3) :
    - `api/server.py` (+2/-1 lignes sur la CSP) : nécessaire pour supprimer `'unsafe-inline'` de `script-src` (§6.8), bien que `api/server.py` soit réservé à Carbon (à fusionner sans conflit, déjà vérifié en fusion 3 voies).
    - `web/public/theme-init.js` et `web/public/sw.js` : accompagnent `web/index.html` pour externaliser le script de thème.
    - `agent/loop.py`, `agent/orchestrator.py`, `agent/supervisor.py`, `attention/engine.py`, `tools/registry.py`, `tools/standard.py`, `tools/visual_artifact.py`, `tts.py` : chacun corrige une faille réelle du §6 (propagation `had_untrusted_context` des sous-agents, triage `AttentionEngine`, marquage `read_file`/`get_clipboard` non fiables, `svg_path`, clé TTS dans l'en-tête). À noter que `4596ede` a bien isolé `registry.py` et évité un faux positif avec le persona.
- **Conflits d'intention** :
  - Aucun conflit avec Carbon ni Opus sur `tools/registry.py`, `tools/standard.py`, `security.py` ou `api/server.py`.
  - Synergie à poursuivre après fusion : Carbon fournit désormais le cookie `HttpOnly` `jarvis_session` ; Neon pourra retirer complètement le stockage du jeton en `sessionStorage`/`localStorage` dans `web/src/lib/authToken.ts` lors d'une passe ultérieure.
- **Preuve rouge/vert des tests** :
  - Vérifié sur `main` : **`10 failed` sur `tests/test_security_injection.py`** et **`3 failed | 1 passed` sur `web/src/tests/security.test.tsx`** (le test `RichText` vérifiant une propriété déjà sûre avant correctif).
  - Vérifié sur `sec/neon-injection` : **`10 passed` sur `tests/test_security_injection.py`**, **`4 passed` sur `web/src/tests/security.test.tsx`**, suite complète **`189 passed, 3 skipped`**, `ruff` et `mypy` verts.
