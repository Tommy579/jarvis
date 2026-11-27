# Brief sécurité — Gemini 4 Carbon (audit profond : API, outils, MCP, OAuth)

> Lis ce document en entier avant de toucher au code. Tu travailles sur une copie locale de
> JARVIS 2.0 (workspace Python 3.12 + interface React/TypeScript). Deux autres modèles auditent
> en parallèle deux autres périmètres : **ne sors pas du tien** (voir §3). Tu peux lire tout le
> dépôt, tu ne modifies que ton périmètre.

## 1. Contexte

Jarvis est un assistant personnel local. `jarvis-core` (FastAPI + WebSocket) expose ~90 routes
sur `127.0.0.1:8765`, protégées par un jeton `JARVIS_API_TOKEN`. Il exécute des outils
(terminal, fichiers, navigateur CDP, sous-agents CLI, serveurs MCP), planifie des tâches
autonomes et se connecte à des services externes (Google, Microsoft, Notion, Home Assistant,
Spotify, Telegram).

Modèle de menace réel : un contenu externe non fiable (page web, mail, transcription) peut
tenter de détourner les outils ; un processus local non privilégié peut tenter d'appeler l'API ;
une intégration OAuth peut fuiter des jetons. Les secrets vivent dans `.env`, le keyring ou
`/run/credentials`.

## 2. Ta mission

Trouver et corriger les vulnérabilités **exploitables** dans la couche d'autorisation et
d'exécution : authentification de l'API, jetons d'action, approbations, exécution de commandes,
cycle de vie MCP, OAuth et webhooks. Pour chaque hypothèse du §6 : vérifier dans le code, écrire
un test qui prouve le problème, corriger, prouver que le test passe.

## 3. Périmètre (branch `sec/carbon-api`)

Fichiers que tu peux modifier :

- `packages/core/jarvis_core/api/server.py` (uniquement auth, middlewares, routes `/actions/*`,
  `/api/oauth/*`, `/api/webhooks/*`, `/api/approvals*`)
- `packages/core/jarvis_core/security.py`
- `packages/core/jarvis_core/tools/registry.py`, `approvals.py`, `autonomy.py`
- `packages/core/jarvis_core/tools/standard.py` (outil `run_command` seulement)
- `packages/core/jarvis_core/mcp/client.py`
- `packages/core/jarvis_core/integrations/base.py` et les intégrations OAuth
- `tests/` : nouveaux fichiers `tests/test_security_*.py`

Hors périmètre : `web/`, `memory/`, `backup.py`, `doctor.py`, `install_linux.sh`, Docker,
`tools/browser.py`, `channels/telegram.py` (autres auditeurs). Si un correctif l'exige, écris-le
dans ton rapport au lieu de modifier.

## 4. Commandes de référence

```bash
# environnement
uv sync                       # ou : .venv/bin/python -m pip install -e packages/shared -e packages/core -e packages/desktop

# tests et qualité (doivent rester verts)
PYTHONPATH=packages/shared:packages/core:packages/desktop:. .venv/bin/python -m pytest tests -q
.venv/bin/python -m ruff check packages tests evals packaging
.venv/bin/python -m ruff format --check packages tests evals packaging
PYTHONPATH=packages/shared:packages/core:packages/desktop:. .venv/bin/python -m mypy
```

Pour un test ciblé : `pytest tests/test_security_api.py -q`.

## 5. Règles de correction

- Aucun changement de comportement fonctionnel non justifié par la sécurité.
- Pas de nouvelle dépendance sans justification écrite dans le rapport (et `uv.lock` mis à jour).
- Préserve les contrats : modèles Pydantic de `packages/shared`, protocole WS, types TS générés.
- Commentaires et messages en français, sans accents dans le code existant si le fichier n'en a pas.
- Toute correction de sécurité significative est accompagnée d'un test de non-régression.
- Ne touche jamais `.env`, `jarvis-data/`, ni les secrets. Ne lance aucune commande destructive.

## 6. Hypothèses à vérifier (pas des conclusions : prouve ou réfute)

1. **Jeton en query string.** Le middleware accepte `?token=` (`api/server.py`, ~2126). Un jeton
   dans l'URL finit dans l'historique navigateur, les journaux d'accès et le `Referer`. Vérifie
   les fuites ; propose une voie sûre (cookie de session court, en-tête seul, ou échange
   one-shot) sans casser le lanceur qui ouvre `/?token=...`.
2. **Webhook sans jeton.** `POST /api/webhooks/trading` n'exige un Bearer que si
   `TRADER_WEBHOOK_TOKEN` est défini. Vérifie l'exposition réelle (loopback, Docker, système) et
   rends le refus explicite quand le jeton manque.
3. **Callback OAuth hors authentification.** `/api/oauth/{provider}/callback` contourne le jeton
   API. Vérifie que `state` est à usage unique, lié au fournisseur, expirant, et que l'échange de
   code ne peut pas être rejoué.
4. **Stockage des jetons d'intégration.** Vérifie où les access/refresh tokens sont persistés,
   avec quelles permissions, et s'ils apparaissent dans les réponses API ou les logs.
5. **`SecretStore.set_secret`.** En cas d'échec keyring, il écrit dans `os.environ` en mémoire et
   renvoie `False` : vérifie qu'aucun appelant ne croit à une persistance réelle.
6. **Approbations et TOCTOU.** Un jeton d'action signe `(approval_id, action, expiration)`.
   Vérifie qu'une approbation ne peut pas être rejouée pour une autre commande, un autre
   argument, ou après expiration, et que la décision est liée aux arguments exacts.
7. **`run_command`.** Vérifie la liste noire, le `cwd` par défaut, le timeout, l'absence de
   `shell=True`, et qu'aucun chemin (JSON d'arguments, sortie de modèle) ne permet de contourner
   l'approbation via `ask`.
8. **MCP.** `create_subprocess_exec` est utilisé : vérifie l'absence de shell, la provenance de
   la configuration (`config/mcp.yaml`), les variables d'environnement transmises, les timeouts
   et ce qui se passe si un serveur configuré est malveillant.
9. **Rate limiting.** Le limiteur à fenêtre glissante protège-t-il les jetons d'action et
   l'authentification API, ou seulement `/actions/*` ? Vérifie le brute-force sur 8765.
10. **Fuite d'information.** Messages d'erreur 500, `/api/doctor`, `/api/version` : chemins
    absolus, noms d'hôtes, versions de dépendances. Sous authentification, c'est acceptable ;
    hors authentification, non.

## 7. Format du rapport

Écris `docs/security/carbon-report.md` :

```markdown
| ID | Sévérité | Fichier:ligne | Scénario d'exploitation | Preuve | Correctif | Statut |
|----|----------|---------------|--------------------------|--------|-----------|--------|
| C-01 | Élevé | api/server.py:2126 | ... | test_security_x.py::test_y | ... | corrigé |
```

Puis, pour chaque entrée : hypothèse, vérification, impact réel, correctif (avant/après),
test ajouté, risque résiduel. Termine par « ce que je n'ai pas pu vérifier ».

## 8. Definition of done

- Tests ajoutés, suite complète verte, ruff et mypy verts.
- Un commit par correctif, messages en français (`fix(security): ...`).
- Rapport écrit, sections « vérifié » et « non vérifié » distinctes.
- Aucune modification hors périmètre, aucun secret affiché.