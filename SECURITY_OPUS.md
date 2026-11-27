# Brief sécurité — Claude Opus 5.5 (données, infrastructure, chaîne d'approvisionnement)

> Lis ce document en entier avant de toucher au code. Tu travailles sur une copie locale de
> JARVIS 2.0. Deux autres modèles auditent l'API/l'exécution (Carbon) et la chaîne
> d'injection/navigateur/frontend (Neon) : **ne sors pas de ton périmètre**. Ta seconde mission
> est de relire leurs PR à la fin.

## 1. Contexte

Jarvis stocke toute la mémoire personnelle dans SQLite (`jarvis-data/jarvis.db`, mode WAL),
plus un wiki Markdown (`jarvis-data/wiki/`). Les sauvegardes sont des instantanés SQLite dans
`jarvis-data/backups/`. L'application existe sous trois formes : installation locale
(`install_linux.sh` + `scripts/launch_jarvis_v2.sh`), service systemd (`deploy/*.service`) et
conteneur Docker (image multi-étapes non-root). Les dépendances sont verrouillées (`uv.lock`,
`web/package-lock.json`).

Le risque principal de ton périmètre : perte ou fuite de données personnelles, permissions
trop larges, secret embarqué dans une image, dépendance compromise, et durcissement
d'exploitation insuffisant.

## 2. Ta mission

Auditer la confidentialité et l'intégrité des données, les permissions fichiers, le
durcissement des unités systemd et du conteneur, la chaîne d'approvisionnement, puis relire les
deux autres PR de sécurité.

## 3. Périmètre (branch `sec/opus-data`)

- `packages/core/jarvis_core/memory/schema.py`, `storage.py`, `import_legacy.py`
- `packages/core/jarvis_core/backup.py`, `doctor.py`
- `packages/core/jarvis_core/tools/standard.py` (opérations fichiers uniquement)
- `scripts/launch_jarvis_v2.sh`, `install_linux.sh`
- `deploy/jarvis-core.service`, `deploy/jarvis-desktop.service`
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- `.gitignore`, `pyproject.toml`, `uv.lock`, `web/package.json`, `web/package-lock.json`
- `config/*.yaml`, `.env.example`
- `tests/` : nouveaux `tests/test_security_data.py`, `tests/test_security_infra.py`

Hors périmètre : `api/server.py`, `security.py`, `tools/registry.py`, `mcp/`, OAuth, webhooks,
`tools/browser.py`, `channels/telegram.py`, `web/src/`. Signale, ne modifie pas.

## 4. Commandes de référence

```bash
# tests et qualité
PYTHONPATH=packages/shared:packages/core:packages/desktop:. .venv/bin/python -m pytest tests -q
.venv/bin/python -m ruff check packages tests evals packaging
.venv/bin/python -m ruff format --check packages tests evals packaging
PYTHONPATH=packages/shared:packages/core:packages/desktop:. .venv/bin/python -m mypy

# audit de dépendances (isolé, sans toucher au venv)
uv run --with pip-audit pip-audit
cd web && npm audit --omit=dev

# image (si Docker est disponible)
docker build -t jarvis:audit .
docker history --no-trunc jarvis:audit | head -40
```

## 5. Règles de correction

- Ne casse ni l'installation locale, ni le service systemd, ni le conteneur : teste ce que tu
  peux tester, documente ce que tu ne peux pas.
- Pas de nouvelle dépendance sans justification ; si tu en ajoutes une, mets `uv.lock` à jour.
- Migrations SQLite : jamais de modification d'une migration déjà appliquée, toujours une
  nouvelle version.
- Ne touche jamais `.env`, `jarvis-data/`, ni aux secrets. Pas de commande destructive.
- Commentaires en français, cohérents avec le fichier modifié.

## 6. Hypothèses à vérifier

1. **Base en clair.** Le chiffrement SQLite est optionnel (`encryption_key`) et retombe en clair
   avec un simple `warning` si `sqlcipher3` manque. Vérifie le comportement réel, les permissions
   de `jarvis.db`, `-wal`, `-shm`, et documente clairement le niveau de protection.
2. **Sauvegardes.** Vérifie les permissions des fichiers créés dans `jarvis-data/backups/`
   (umask), la rétention, et le chemin accepté par `jarvis backup restore` : peut-on restaurer
   depuis un chemin arbitraire, écraser un fichier hors du dossier de données, ou suivre un lien
   symbolique ?
3. **Wiki et imports.** Les pages Markdown et `jarvis_profile.json` contiennent des données
   personnelles : permissions, emplacement, et ce que fait `import_legacy_profile` avec un
   fichier hostile (clés, tailles, chemins).
4. **Journaux.** `~/.local/state/jarvis/*.log` : permissions, contenu (transcriptions, chemins,
   erreurs), rotation effective.
5. **systemd.** Les unités ont `NoNewPrivileges` et `PrivateTmp`. Vérifie l'absence de
   `ProtectSystem`, `ProtectHome`, `ReadWritePaths`, `RestrictAddressFamilies`, `UMask`, et
   propose un durcissement qui ne casse pas l'accès à `~/.local/state`, `jarvis-data` et au
   matériel audio/vidéo.
6. **Docker.** Vérifie que l'image ne contient aucun secret (`.env`, clés, `jarvis-data`), que
   l'utilisateur est non-root, que les capacités sont réduites, que le montage de données est
   explicite, et que le healthcheck correspond au port réel.
7. **Lanceur.** Jeton écrit dans `.env` (permissions), jeton passé dans l'URL de la fenêtre,
   journal de lancement, verrou d'instance unique. Vérifie ce qui est lisible par un autre
   utilisateur local.
8. **Installation.** `install_linux.sh` exécute `sudo pacman`/`apt-get`, `npm ci`, et installe un
   venv : vérifie les entrées non contrôlées, les téléchargements, et l'absence d'exécution de
   code distant non vérifié.
9. **Chaîne d'approvisionnement.** `pip-audit` et `npm audit` : liste les CVE connues, les
   dépendances non épinglées (`>=`), et les serveurs MCP déclarés dans `config/mcp.yaml` (qui
   exécute des commandes locales).
10. **Configuration.** `.env.example` contient-il des valeurs par défaut dangereuses ?
    `config/models.yaml` ou `config/persona.md` exposent-ils des secrets ou des URL internes ?

## 7. Relecture croisée (à la fin)

Quand les branches `sec/carbon-api` et `sec/neon-injection` sont disponibles :

- vérifie qu'aucune des deux ne modifie un fichier hors de son périmètre ;
- cherche les conflits d'intention (deux correctifs qui se neutralisent, un middleware modifié
  des deux côtés) ;
- vérifie qu'aucune migration n'entre en collision ;
- vérifie que les tests ajoutés échouent réellement sans le correctif ;
- rends un avis par PR : à fusionner, à corriger, à rejeter (avec la raison).

## 8. Format du rapport

Écris `docs/security/opus-report.md` :

```markdown
| ID | Sévérité | Fichier:ligne | Impact | Reproduction | Correctif | Statut |
|----|----------|---------------|--------|--------------|-----------|--------|
| O-01 | Élevé | backup.py:190 | ... | ... | ... | corrigé |
```

Puis, pour chaque entrée : vérification, impact réel (fuite, perte, élévation), correctif,
test, risque résiduel. Termine par « ce que je n'ai pas pu vérifier » et la relecture croisée.

## 9. Definition of done

- Tests ajoutés, suite verte, ruff et mypy verts.
- Un commit par correctif (`fix(security): ...`), messages en français.
- Rapport écrit, faits et hypothèses séparés.
- Aucune modification hors périmètre, aucun secret affiché.