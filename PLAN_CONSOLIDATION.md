# OMI 2.0 — Plan de consolidation du cœur (implémentation)

> **Pour Hermes :** utiliser la skill `subagent-driven-development` pour exécuter ce plan tâche par tâche
> (un sous-agent par tâche, revue de conformité au spec puis revue de qualité avant de passer à la suivante).

**Objectif :** rendre le service `omi-core` réellement fonctionnel de bout en bout — un message envoyé
depuis l'interface web doit traverser le routeur multi-modèles, la boucle agent, les outils avec
approbation, la mémoire persistante, et revenir en streaming — puis assainir le dépôt (données
personnelles, lint, doublons, CI).

**Architecture cible :** `packages/core/omi_core/api/server.py` reste le seul point d'entrée HTTP/WS ;
il instancie un objet `OmiRuntime` (nouveau) qui possède les providers LLM, le `LLMRouter`, le
`ToolRegistry`, le `MemoryEngine`, l'`AttentionEngine` et les canaux de notification. Le WebSocket
devient un adaptateur mince entre le protocole `omi_shared.ws_protocol` et `AgentLoop.run_turn()`.
Une seule couche SQLite (`MemoryDatabase`, migrations versionnées) écrit dans `omi.db`.

**Stack :** Python 3.12 (workspace `uv`), FastAPI + uvicorn, Pydantic v2, SQLite/SQLCipher, APScheduler,
React 19 + Vite 6 + Zustand + react-three-fiber, Vitest + Playwright.

**État de référence mesuré le 2026-11-14 (à re-vérifier avant de commencer) :**

| Porte | Commande | Résultat mesuré |
|---|---|---|
| Tests Python | `PYTHONPATH=packages/shared:packages/core:packages/desktop:. .venv/bin/python -m pytest tests -q` | 28 passed en 7,7 s |
| Tests web | `cd web && npx vitest run` | 8 passed |
| E2E | `cd web && npx playwright test` | 4 passed (contre `web/mock-server.mjs`, pas contre le vrai serveur) |
| Build web | `cd web && npm run build` | OK, chunk unique 1 404 Ko (398 Ko gzip) |
| Ruff | `.venv/bin/python -m ruff check packages tests` | 46 erreurs (25 auto-fixables) |
| Ruff format | `.venv/bin/python -m ruff format --check packages tests` | 37 fichiers à reformater |

Sondes exécutées (script jetable, venv du projet) :

```
/api/evals/latest sans evals/latest.json  -> HTTP 200, total=2, passed=2, latences 420/510 ms  (données fabriquées)
WS user.text "Quel temps fait-il a Nice ?" -> assistant.delta "Bien recu : Quel temps fait-il a Nice ?."  (echo, aucun LLM)
PUT /api/models/role glm-5.3 puis redemarrage -> deepseek/deepseek-v4.1-flash-1017  (non persiste)
GET /api/messages sans OMI_API_TOKEN      -> 200  (auth ouverte)
ToolRegistry.execute(write_file, risk=2)  -> "Approbation requise (statut : expired)" en 51 ms
```

---

## 0. Rectificatifs par rapport au premier rapport d'audit

Trois points annoncés oralement sont faux ou inexacts ; le plan corrige :

1. **Wayland n'est pas à écrire.** `packages/desktop/omi_desktop/wayland_sensors.py` gère déjà
   `spectacle -b -n -o`, le repli `grim`, le `restore_token` du portail ScreenCast, l'OCR local et la
   liste noire de fenêtres. Il n'est simplement **jamais instancié** par un service en production.
2. **Les unités systemd existent déjà** : `deploy/omi-core.service`, `deploy/omi-desktop.service`,
   `deploy/install_arch.sh` (pacman + uv + build web + `systemctl --user enable`). Ce qui manque n'est
   pas le fichier, c'est le contenu qu'il lance (aujourd'hui `omi_core.api.server:app` sans runtime).
3. **Les bases `.db` ne sont plus suivies par git dans HEAD** (staging `D omi_history.db`,
   `D omi-windows/omi_history.db`), mais elles restent **7 commits + 2 commits** dans l'historique du
   remote public `github.com/Tommy579/omi`. La purge n'a donc pas été exécutée.

---

## 1. Décisions d'architecture à valider avant de coder

| Décision | Choix proposé | Pourquoi |
|---|---|---|
| Où vit le câblage runtime | Nouveau module `packages/core/omi_core/runtime.py` (`OmiRuntime`) | `server.py` fait déjà 534 lignes de routes ; y ajouter la construction des providers le rendrait intestable |
| Couche mémoire | `MemoryDatabase` (schema.py) unique propriétaire de `omi.db` ; `MemoryStorage` devient un adaptateur | Les deux classes créent les mêmes tables `facts`/`episodes`/`people`/`events`/`notifications`/`llm_calls` avec des **colonnes différentes** ; pointer les deux sur `omi.db` casse la seconde (`CREATE TABLE IF NOT EXISTS` garde le premier schéma) |
| Persistance des réglages | Table `settings(key TEXT PRIMARY KEY, value_json TEXT)` dans `omi.db` + réécriture de `config/models.yaml` pour les rôles | Un `PUT /api/models/role` doit survivre au redémarrage sans dépendre d'un fichier édité à la main |
| Approbations | `ApprovalManager` unique porté par `OmiRuntime`, `on_approval_requested` pousse un événement dans une `asyncio.Queue` consommée par le WS | Le callback est synchrone et appelé depuis la boucle agent ; il faut un pont vers l'async sans bloquer |
| Auth | Refus de démarrer sans `OMI_API_TOKEN` sauf `OMI_ALLOW_INSECURE=1` + bind loopback | Le mode ouvert actuel est silencieux : une variable oubliée expose tout le service |
| Évals | `omi eval --save` écrit `evals/latest.json` ; l'API renvoie 404 « jamais exécuté » si absent | Ne jamais servir de résultats inventés à l'UI |

---

## Phase 0 — Sécurité et dépôt (P0, à faire avant tout le reste)

### Tâche 0.1 — Purger les données personnelles de l'historique Git

**Objectif :** aucun blob `omi_history.db` / `omi_profile.json` ne doit rester accessible sur le remote public.

**Fichiers :** `scripts/purge_git_history.sh` (existant, à relire avant exécution), `.gitignore`

**Étapes :**

1. Sauvegarder les bases locales hors du dépôt :
   ```bash
   mkdir -p ~/omi-data/backups
   cp -f omi_history.db omi_profile.json ~/omi-data/backups/ 2>/dev/null || true
   cp -f omi-windows/omi_history.db omi-windows/omi_profile.json ~/omi-data/backups/ 2>/dev/null || true
   ```
2. Vérifier l'état avant purge :
   ```bash
   git log --all --oneline -- omi_history.db omi-windows/omi_history.db omi_profile.json | wc -l
   ```
   Attendu : `9` (7 + 2 commits constatés).
3. Installer l'outil si absent : `sudo pacman -S --needed git-filter-repo` (ou `pipx install git-filter-repo`).
4. Exécuter la purge :
   ```bash
   bash scripts/purge_git_history.sh
   ```
5. Vérifier après purge :
   ```bash
   git log --all --oneline -- omi_history.db omi-windows/omi_history.db omi_profile.json | wc -l   # attendu : 0
   git count-objects -vH
   ```
6. Forcer la réécriture du remote **après** avoir prévenu tout autre clone :
   ```bash
   git push origin --force --all
   git push origin --force --tags
   ```
7. Re-cloner dans un dossier temporaire et confirmer :
   ```bash
   git clone https://github.com/Tommy579/omi /tmp/omi-verify
   cd /tmp/omi-verify && git log --all --oneline -- omi_history.db | wc -l   # attendu : 0
   ```

**Validation :** la commande de l'étape 7 renvoie `0` sur un clone neuf.

**Risque :** `filter-repo` réécrit tous les SHA ; les PR ouvertes et les forks deviennent invalides.
À faire une fois, quand personne d'autre ne travaille sur le dépôt.

### Tâche 0.2 — Rotation des clés exposées

**Objectif :** invalider toute clé qui aurait transité dans l'historique.

**Étapes :**

1. Auditer l'historique avant purge (si la purge n'est pas encore faite) :
   ```bash
   git log --all -p | grep -nE "AIza[0-9A-Za-z_-]{35}|sk-or-v1-[0-9a-f]{64}" | head
   ```
2. Révoquer et régénérer la clé Gemini sur `aistudio.google.com/apikey`.
3. Révoquer et régénérer la clé OpenRouter sur `openrouter.ai/keys`.
4. Mettre à jour `.env` (jamais commité) et le trousseau : `keyring set omi-core GEMINI_API_KEY`.

**Validation :** `grep -rn "AIza" . --include=*.py --include=*.md --include=*.sh` ne renvoie rien hors `.env`.

### Tâche 0.3 — Garde-fou sur l'exécution shell du code legacy

**Objectif :** l'ancien `core/tools.py` ne doit plus pouvoir lancer `shell=True` sans confirmation, même
s'il reste dans le dépôt pendant la transition.

**Fichiers :**
- Modifier : `core/tools.py:323` (`execute_command`)
- Test : `tests/test_tools.py`

**Étape 1 — écrire le test qui échoue**

```python
def test_execute_command_requires_confirmation(monkeypatch):
    import core.tools as tools

    called = {"n": 0}

    def fake_run(*a, **k):
        called["n"] += 1
        raise AssertionError("subprocess.run ne doit pas etre appele sans confirmation")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    monkeypatch.setattr(tools, "confirm_action", lambda *a, **k: False, raising=False)
    out = tools.execute_command("rm -rf /tmp/inexistant")
    assert called["n"] == 0
    assert "refus" in str(out).lower() or "confirm" in str(out).lower()
```

**Étape 2 — vérifier l'échec**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_tools.py::test_execute_command_requires_confirmation -v
```
Attendu : FAIL (`AssertionError: subprocess.run ne doit pas etre appele sans confirmation`).

**Étape 3 — implémentation minimale**

Dans `core/tools.py`, au-dessus de `execute_command` :

```python
def confirm_action(description: str) -> bool:
    """Confirmation explicite obligatoire avant toute action destructive."""
    try:
        answer = input(f"[OMI] Confirmer : {description} ? [o/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("o", "oui", "y", "yes")
```

Et dans `execute_command`, avant le `subprocess.run` :

```python
    if not confirm_action(f"executer la commande shell : {command}"):
        return {"ok": False, "error": "Execution refusee : confirmation absente."}
```

**Étape 4 — vérifier le vert**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_tools.py -v
```
Attendu : 9 passed.

**Étape 5 — commit**

```bash
git add core/tools.py tests/test_tools.py
git commit -m "fix(legacy): exiger une confirmation avant execute_command"
```

### Tâche 0.4 — Hygiène du dépôt

**Objectif :** plus aucun artefact local dans l'arbre de travail.

**Étapes :**

1. Ajouter au `.gitignore` : `.hermes/`, `web/test-results/`, `web/tsconfig.tsbuildinfo`, `web/dist/`,
   `*.log`, `omi-data/backups/`.
2. Déplacer les trois copies du legacy vers une seule référence :
   ```bash
   git rm -r --cached omi-windows 2>/dev/null || true
   mv omi-windows legacy/omi_windows_ref   # omi-windows/ n'est plus qu'une archive
   ```
3. Vérifier : `git status --porcelain | grep -v '^??' | wc -l` doit tomber à 0 hors modifications voulues.

**Validation :** `git status` ne montre plus `omi-windows/` ni `web/dist/`.

---

## Phase 1 — Unifier la couche mémoire (prérequis du câblage agent)

> Sans cette phase, brancher `MemoryEngine` sur `omi.db` casse l'API : `MemoryStorage` insère
> `embedding_json` et `use_count`, colonnes absentes du schéma de `MemoryDatabase`.

### Tâche 1.1 — Test de collision de schéma (preuve du problème)

**Fichiers :** `tests/test_memory_unification.py` (créer)

```python
from pathlib import Path

from omi_core.memory.schema import MemoryDatabase
from omi_core.memory.storage import MemoryStorage


def test_single_writer_on_same_file(tmp_path: Path) -> None:
    db_path = tmp_path / "omi.db"
    db = MemoryDatabase(db_path)
    storage = MemoryStorage(db_path, database=db)  # API cible
    assert storage.get_today_spend_usd() == 0.0
    assert "settings" in db.table_names()
```

**Exécution :** `.venv/bin/python -m pytest tests/test_memory_unification.py -v`
Attendu : FAIL — `TypeError: __init__() got an unexpected keyword argument 'database'`.

### Tâche 1.2 — Migration 2 : colonnes manquantes + table `settings`

**Fichier :** `packages/core/omi_core/memory/schema.py` (ajouter à `MIGRATIONS`)

```python
    (
        2,
        """
        ALTER TABLE facts ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE facts ADD COLUMN embedding_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE notifications ADD COLUMN dedup_key TEXT;
        ALTER TABLE events ADD COLUMN dedup_key TEXT;
        ALTER TABLE llm_calls ADD COLUMN provider TEXT NOT NULL DEFAULT '';
        ALTER TABLE llm_calls ADD COLUMN cached_tokens INTEGER NOT NULL DEFAULT 0;

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """,
    ),
```

Ajouter dans `MemoryDatabase` :

```python
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value_json"] if row else default

    def set_setting(self, key: str, value_json: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO settings (key, value_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, "
                "updated_at = excluded.updated_at",
                (key, value_json, utc_now_iso()),
            )
```

**Validation :** `.venv/bin/python -m pytest tests/test_frontend_repetitif.py::test_memory_schema_crud_search_and_legacy_import -v` reste vert (migration idempotente).

### Tâche 1.3 — `MemoryStorage` devient un adaptateur

**Fichier :** `packages/core/omi_core/memory/storage.py`

Remplacer `__init__`, `_connect`, `_init_schema` et les 14 méthodes par une délégation :

```python
class MemoryStorage:
    """Adaptateur fin : expose l'API attendue par MemoryEngine au-dessus de MemoryDatabase."""

    def __init__(self, db_path: Path | str, encryption_key: str | None = None,
                 database: MemoryDatabase | None = None) -> None:
        self.db = database or MemoryDatabase(db_path, encryption_key=encryption_key)

    def insert_fact(self, fact: Fact, embedding: list[float]) -> Fact: ...
    def mark_fact_superseded(self, old_fact_id: str, new_fact_id: str) -> None: ...
    def mark_fact_used(self, fact_id: str) -> None: ...
    def list_active_facts(self) -> list[tuple[Fact, list[float], int]]: ...
    def get_fact_by_id(self, fact_id: str) -> Fact | None: ...
    def insert_episode(self, ep: Episode, embedding: list[float]) -> Episode: ...
    def list_episodes(self, limit: int = 200) -> list[tuple[Episode, list[float]]]: ...
    def purge_old_raw_episodes(self, cutoff_iso: str) -> int: ...
    def upsert_person(self, person: Person) -> Person: ...
    def insert_event(self, event: SystemEvent) -> SystemEvent: ...
    def insert_notification(self, notif: NotificationRecord) -> NotificationRecord: ...
    def log_llm_call(self, role: str, usage: Usage) -> None: ...
    def get_today_spend_usd(self) -> float: ...
```

Supprimer `SCHEMA_SQL` (le schéma vit uniquement dans `schema.py`).

**Validation :**
```bash
.venv/bin/python -m pytest tests/ -q
```
Attendu : 29 passed (28 + le nouveau test 1.1).

### Tâche 1.4 — Mesurer la déduplication

**Objectif :** prouver que la fusion a bien eu lieu.

```bash
wc -l packages/core/omi_core/memory/storage.py packages/core/omi_core/memory/schema.py
```
Attendu : `storage.py` passe de 369 lignes à moins de 200 ; à noter dans le message de commit.

**Commit :** `refactor(memory): un seul proprietaire SQLite, MemoryStorage en adaptateur`

---

## Phase 2 — Câbler le runtime réel (le cœur du travail)

### Tâche 2.1 — `OmiRuntime` : construction des providers depuis la config

**Fichiers :**
- Créer : `packages/core/omi_core/runtime.py`
- Test : `tests/test_runtime.py`

**Étape 1 — test qui échoue**

```python
from pathlib import Path

from omi_core.runtime import build_runtime


def test_runtime_builds_router_and_tools(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    rt = build_runtime(data_dir=tmp_path / "data", config_dir=Path("config"))
    assert set(rt.router.providers) == {"openrouter"}
    assert rt.router.get_role_route("agent").model == "deepseek/deepseek-v4.1-flash-1017"
    assert rt.tool_registry.list_schemas(), "au moins un outil doit etre expose"
    assert rt.agent.max_iterations == 8
```

**Étape 2 — échec attendu :** `ModuleNotFoundError: omi_core.runtime`

**Étape 3 — implémentation**

```python
"""Construction du runtime OMI 2.0 : providers, routeur, outils, memoire, agent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from omi_core.agent.loop import AgentLoop
from omi_core.llm.base import LLMProvider
from omi_core.llm.gemini import GeminiProvider
from omi_core.llm.openrouter import OpenRouterProvider
from omi_core.llm.router import DEFAULT_ROLE_ROUTES, LLMRouter, RoleRoute
from omi_core.memory.engine import MemoryEngine
from omi_core.memory.schema import MemoryDatabase
from omi_core.memory.storage import MemoryStorage
from omi_core.memory.wiki import MarkdownWiki
from omi_core.security import SecretStore
from omi_core.tools.approvals import ApprovalManager
from omi_core.tools.registry import ToolRegistry, detect_platform
from omi_core.tools.standard import StandardToolkit


@dataclass
class OmiRuntime:
    database: MemoryDatabase
    router: LLMRouter
    tool_registry: ToolRegistry
    memory_engine: MemoryEngine
    approvals: ApprovalManager
    agent: AgentLoop


def _load_roles(config_dir: Path) -> dict[str, RoleRoute]:
    models_file = config_dir / "models.yaml"
    if not models_file.exists():
        return DEFAULT_ROLE_ROUTES
    raw = yaml.safe_load(models_file.read_text(encoding="utf-8")) or {}
    roles: dict[str, RoleRoute] = {}
    for name, cfg in (raw.get("roles") or {}).items():
        if not isinstance(cfg, dict):
            continue
        roles[name] = RoleRoute(
            role_name=name,
            model=str(cfg.get("model", "")),
            provider=str(cfg.get("provider", "openrouter")),
            fallback=[str(f) for f in cfg.get("fallback", [])],
        )
    return roles or DEFAULT_ROLE_ROUTES


def build_runtime(
    data_dir: Path,
    config_dir: Path,
    secrets: SecretStore | None = None,
    on_state_change=None,
    on_sentence_ready=None,
) -> OmiRuntime:
    store = secrets or SecretStore()
    providers: dict[str, LLMProvider] = {}
    or_key = store.get_secret("OPENROUTER_API_KEY")
    if or_key:
        providers["openrouter"] = OpenRouterProvider(api_key=or_key)
    gem_key = store.get_secret("GEMINI_API_KEY")
    if gem_key:
        providers["gemini"] = GeminiProvider(api_key=gem_key)
    if not providers:
        raise RuntimeError("Aucune cle LLM configuree (OPENROUTER_API_KEY ou GEMINI_API_KEY).")

    database = MemoryDatabase(data_dir / "omi.db")
    storage = MemoryStorage(data_dir / "omi.db", database=database)
    wiki = MarkdownWiki(data_dir / "wiki", auto_git=False)

    router = LLMRouter(
        providers=providers,
        roles=_load_roles(config_dir),
        daily_budget_usd=float(database.get_setting("daily_budget_usd", "5.0") or 5.0),
        usage_logger=lambda role, usage: storage.log_llm_call(role, usage),
        daily_spend_getter=storage.get_today_spend_usd,
    )

    approvals = ApprovalManager(ttl_seconds=120.0)
    registry = ToolRegistry(approval_manager=approvals, current_platform=detect_platform())
    StandardToolkit(database=database).register_all(registry)

    memory_engine = MemoryEngine(storage=storage, wiki=wiki)
    agent = AgentLoop(
        router=router,
        tool_registry=registry,
        memory_engine=memory_engine,
        on_state_change=on_state_change,
        on_sentence_ready=on_sentence_ready,
    )
    return OmiRuntime(database, router, registry, memory_engine, approvals, agent)
```

**Note :** si `StandardToolkit` n'expose pas encore de `register_all(registry)`, l'ajouter dans
`packages/core/omi_core/tools/standard.py` — c'est le seul point d'entrée manquant pour publier les
outils niveaux 0/1 dans le registre avec leurs `RiskLevel`.

**Étape 4 — vert :** `.venv/bin/python -m pytest tests/test_runtime.py -v` → 1 passed.

### Tâche 2.2 — WebSocket : remplacer l'echo par l'agent en streaming

**Fichiers :**
- Modifier : `packages/core/omi_core/api/server.py:454-508` (`websocket_endpoint`)
- Test : `tests/test_ws_agent.py` (créer)

**Étape 1 — test qui échoue (avec un faux provider)**

```python
from pathlib import Path

from fastapi.testclient import TestClient

from omi_core.api.server import create_app, OmiCoreState
from omi_core.llm.base import LLMProvider
from omi_shared.models import ModelCapabilities, StreamDelta, Usage


class EchoProvider(LLMProvider):
    name = "fake"

    async def fetch_available_models(self) -> dict[str, ModelCapabilities]:
        return {}

    async def complete(self, model, messages, tools=None, fallback_models=None, temperature=0.2):
        return StreamDelta(text="Bonjour Tom.", usage=Usage(model=model, provider=self.name,
                                                            tokens_in=10, tokens_out=3))

    async def stream(self, model, messages, tools=None, fallback_models=None, temperature=0.2):
        for part in ("Bonjour ", "Tom."):
            yield StreamDelta(text=part)


def test_ws_uses_agent_not_echo(tmp_path: Path) -> None:
    state = OmiCoreState(data_dir=tmp_path / "data", config_dir=Path("config"),
                         legacy_profile_path=tmp_path / "none.json")
    state.runtime = _runtime_with(state, EchoProvider())
    client = TestClient(create_app(state))
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # state idle
        ws.send_json({"type": "user.text", "payload": {"text": "Salut"}})
        deltas = []
        while True:
            ev = ws.receive_json()
            if ev["type"] == "assistant.delta":
                deltas.append(ev["payload"]["text"])
            if ev["type"] == "assistant.done":
                break
    assert "".join(deltas) == "Bonjour Tom."
    assert state.db.list_messages(limit=2)[-1].role == "assistant"
```

**Étape 2 — échec attendu :** `assert 'Bien recu : Salut.' == 'Bonjour Tom.'`

**Étape 3 — implémentation (extrait à substituer dans `websocket_endpoint`)**

```python
                if event.type == "user.text":
                    msg_id = f"msg_{uuid.uuid4().hex[:12]}"
                    core_state.db.add_message("user", event.payload.text)
                    await websocket.send_json({"type": "state", "payload": {"value": "thinking"}})

                    sent_parts: list[str] = []

                    async def _forward(delta) -> None:
                        if delta.text:
                            sent_parts.append(delta.text)
                            await websocket.send_json(
                                {"type": "assistant.delta",
                                 "payload": {"text": delta.text, "message_id": msg_id}}
                            )

                    result = await core_state.runtime.agent.run_turn(
                        user_input=event.payload.text, channel="web"
                    )
                    # run_turn renvoie le texte complet : on emet un delta unique si le streaming
                    # interne n'a pas deja ete relaye (voir tache 2.3 pour le streaming vrai).
                    if not sent_parts and result.text:
                        await websocket.send_json(
                            {"type": "assistant.delta",
                             "payload": {"text": result.text, "message_id": msg_id}}
                        )
                    usage = result.usages[-1] if result.usages else None
                    core_state.db.add_message(
                        "assistant",
                        result.text,
                        usage=usage.model_dump() if usage else None,
                        message_id=msg_id,
                    )
                    await websocket.send_json(
                        {"type": "assistant.done",
                         "payload": {"message_id": msg_id,
                                     "usage": usage.model_dump() if usage else {}}}
                    )
                    await websocket.send_json({"type": "state", "payload": {"value": "idle"}})
```

Ajouter dans `OmiCoreState.__init__` :

```python
        self.runtime = None  # injecte par create_app() ou par les tests
```

et dans `create_app`, après `core_state = state or OmiCoreState()` :

```python
    if core_state.runtime is None:
        from ..runtime import build_runtime

        core_state.runtime = build_runtime(
            data_dir=core_state.data_dir,
            config_dir=core_state.config_dir,
            on_state_change=lambda s: None,
        )
```

**Étape 4 — vert :** `.venv/bin/python -m pytest tests/test_ws_agent.py -v` → 1 passed.

**Étape 5 — mettre à jour le test existant** `tests/test_frontend_repetitif.py::test_fastapi_endpoints_auth_and_ws`
qui affirme aujourd'hui le comportement d'echo : remplacer l'assertion sur `"Bien recu"` par une
assertion sur la présence de `assistant.delta` + `assistant.done` avec un provider factice injecté.

**Commit :** `feat(core): brancher AgentLoop sur le WebSocket`

### Tâche 2.3 — Streaming token par token

**Objectif :** le premier `assistant.delta` doit arriver avant la fin de la génération (indispensable
pour le TTS et pour la sphère qui passe en `speaking`).

**Fichiers :** `packages/core/omi_core/agent/loop.py:107-136`, `packages/core/omi_core/api/server.py`

**Approche :** `AgentLoop.run_turn` accepte un paramètre `on_delta: Callable[[str], Awaitable[None]] | None`.
Dans la boucle `async for delta in self.router.stream(...)`, appeler `await on_delta(delta.text)` au lieu
de seulement accumuler. Le WS passe une coroutine qui `send_json` l'événement.

**Test :** deux `assistant.delta` distincts pour une réponse en deux fragments (le test 2.2 le couvre
déjà si `EchoProvider.stream` yield deux morceaux et que l'assertion porte sur `len(deltas) == 2`).

**Validation :** `len(deltas) == 2` dans `test_ws_uses_agent_not_echo`.

### Tâche 2.4 — Événements `tool.call` / `tool.result` vers l'UI

**Fichiers :** `packages/core/omi_core/agent/loop.py`, `packages/core/omi_core/api/server.py`

**Approche :** ajouter deux callbacks `on_tool_call(ToolCall)` et `on_tool_result(ToolResult)` à
`AgentLoop.__init__`, appelés respectivement avant `asyncio.gather` et après chaque résultat. Le WS les
traduit en `{"type": "tool.call", "payload": {"id","name","args","risk"}}` et
`{"type": "tool.result", "payload": {"id","ok","summary"}}`.

**Test :** `tests/test_ws_agent.py::test_ws_emits_tool_events` avec un provider qui renvoie un
`ToolCall(name="system_stats", arguments={})` au premier tour puis un texte au second.
Attendu : réception de `tool.call`, `tool.result`, puis `assistant.done`.

**Validation :** le store front (`useOmiStore.handleServerEvent`, cas `tool.call`/`tool.result` lignes 47
et 96 du test Vitest) reçoit déjà ces événements — aucun changement front nécessaire.

### Tâche 2.5 — Pont d'approbation WS ↔ ApprovalManager

**Objectif :** un outil de niveau 2/3 doit faire apparaître une demande dans l'UI et attendre la réponse
humaine, au lieu d'expirer en 50 ms.

**Fichiers :**
- Modifier : `packages/core/omi_core/tools/registry.py:116` (`approval_timeout: float | None = 0.05`)
- Modifier : `packages/core/omi_core/runtime.py` (brancher `on_approval_requested`)
- Modifier : `packages/core/omi_core/api/server.py` (consommer la file)

**Étape 1 — test qui échoue**

```python
def test_sensitive_tool_waits_for_human_approval(tmp_path):
    import asyncio
    from omi_core.tools.approvals import ApprovalManager
    from omi_core.tools.registry import ToolRegistry, ToolSpec
    from omi_shared.models import RiskLevel, ToolCall

    async def scenario():
        mgr = ApprovalManager(ttl_seconds=5)
        reg = ToolRegistry(approval_manager=mgr, current_platform="linux")
        reg.register(ToolSpec(name="write_file", description="ecrit",
                              handler=lambda path="": {"ok": True},
                              args_model=_Args, risk=RiskLevel.SENSITIVE))

        async def approve_later():
            await asyncio.sleep(0.2)
            pending = mgr.list_pending()
            mgr.resolve(pending[0].id, approved=True)

        asyncio.create_task(approve_later())
        res = await reg.execute(ToolCall(name="write_file", arguments={"path": "/tmp/x"}))
        return res

    res = asyncio.run(scenario())
    assert res.is_error is False
```

**Étape 2 — échec attendu :** `assert True is False` (statut `expired` après 50 ms).

**Étape 3 — implémentation**

1. Dans `registry.py`, changer la signature en `approval_timeout: float | None = None` (le `None` fait
   utiliser la TTL complète de `ApprovalManager`, soit 120 s).
2. Dans `runtime.py`, créer la file et le pont :

```python
    approval_queue: asyncio.Queue = asyncio.Queue()

    def _on_request(req) -> None:
        approval_queue.put_nowait(req)

    approvals = ApprovalManager(ttl_seconds=120.0, on_approval_requested=_on_request)
```

3. Dans le WS, au moment de `run_turn`, lancer une tâche concurrente qui vide la file et envoie
   `approval.request` (le schéma existe déjà : `omi_shared.ws_protocol.ApprovalRequestEvent`), et router
   `action.approve` vers `core_state.runtime.approvals.resolve(id, approved)`.

**Étape 4 — vert :** `.venv/bin/python -m pytest tests/test_ws_agent.py -v` → 3 passed.

**Validation manuelle :** depuis l'UI, demander « écris un fichier /tmp/test.txt », constater la demande
d'approbation sur `/alerts`, approuver, vérifier la création du fichier.

### Tâche 2.6 — Persister les réglages (modèles, règles, liste noire)

**Fichiers :** `packages/core/omi_core/api/server.py:333-350, 412-415, 447-450`

**Étape 1 — test qui échoue**

```python
def test_role_model_survives_restart(tmp_path):
    state = OmiCoreState(data_dir=tmp_path / "d", config_dir=Path("config"),
                         legacy_profile_path=tmp_path / "none.json")
    client = TestClient(create_app(state))
    client.put("/api/models/role", json={"role": "agent", "model": "z-ai/glm-5.3"})

    state2 = OmiCoreState(data_dir=tmp_path / "d", config_dir=Path("config"),
                          legacy_profile_path=tmp_path / "none.json")
    client2 = TestClient(create_app(state2))
    agent = [m for m in client2.get("/api/models").json() if m["role"] == "agent"][0]
    assert agent["model"] == "z-ai/glm-5.3"
```

**Étape 2 — échec attendu :** `assert 'deepseek/deepseek-v4.1-flash-1017' == 'z-ai/glm-5.3'`

**Étape 3 — implémentation**

Dans `update_role_model`, après mutation :

```python
        core_state.db.set_setting("role_models", json.dumps([m.model_dump() for m in core_state.models]))
        core_state.runtime.router.set_role_model(payload.role, payload.model, payload.provider)
        return item
```

Dans `OmiCoreState.__init__`, après `self.models = self._load_models_config()` :

```python
        stored = self.db.get_setting("role_models")
        if stored:
            self.models = [RoleModelConfig.model_validate(m) for m in json.loads(stored)]
```

Idem pour `update_rules` (`set_setting("attention_rules", payload.model_dump_json())`) et
`update_sensors_blacklist` (`set_setting("blacklist", payload.model_dump_json())`), relus au démarrage.

**Étape 4 — vert :** `.venv/bin/python -m pytest tests/test_ws_agent.py -v` → 4 passed.

**Commit :** `feat(core): persister modeles, regles et liste noire`

---

## Phase 3 — Fiabilité du service (P1)

### Tâche 3.1 — Auth non silencieuse

**Fichiers :** `packages/core/omi_core/api/server.py:179-184`

**Test :**

```python
def test_refuses_to_start_without_token(tmp_path, monkeypatch):
    monkeypatch.delenv("OMI_API_TOKEN", raising=False)
    monkeypatch.delenv("OMI_ALLOW_INSECURE", raising=False)
    with pytest.raises(RuntimeError, match="OMI_API_TOKEN"):
        create_app(OmiCoreState(data_dir=tmp_path / "d", config_dir=Path("config"),
                                legacy_profile_path=tmp_path / "n.json"))
```

**Implémentation :** au début de `create_app` :

```python
    if not os.environ.get("OMI_API_TOKEN", "").strip():
        if os.environ.get("OMI_ALLOW_INSECURE") == "1":
            logger.warning("OMI_API_TOKEN absent : mode non authentifie (loopback uniquement).")
        else:
            raise RuntimeError(
                "OMI_API_TOKEN manquant. Definissez-le dans .env, "
                "ou OMI_ALLOW_INSECURE=1 pour un usage local sans jeton."
            )
```

Adapter les tests existants (`tests/test_frontend_repetitif.py` et `tests/test_ws_agent.py`) avec
`monkeypatch.setenv("OMI_ALLOW_INSECURE", "1")`.

**Validation :** suite complète verte.

### Tâche 3.2 — Évals réelles, plus de résultats fabriqués

**Fichiers :**
- Modifier : `packages/core/omi_core/api/server.py:356-386` (supprimer le bloc de repli inventé)
- Modifier : `packages/core/omi_core/cli.py` (option `--save`)
- Modifier : `evals/harness.py` (écrire `latest.json`)

**Étape 1 — test qui échoue**

```python
def test_evals_endpoint_returns_404_when_never_run(tmp_path, monkeypatch):
    monkeypatch.setenv("OMI_ALLOW_INSECURE", "1")
    state = OmiCoreState(data_dir=tmp_path / "d", config_dir=Path("config"),
                         legacy_profile_path=tmp_path / "n.json")
    client = TestClient(create_app(state))
    assert client.get("/api/evals/latest").status_code == 404
```

**Étape 2 — échec attendu :** `assert 200 == 404`

**Étape 3 — implémentation**

```python
    @application.get("/api/evals/latest", response_model=EvalSuiteResult)
    def get_latest_evals() -> EvalSuiteResult:
        latest_file = Path(__file__).resolve().parents[4] / "evals" / "latest.json"
        if not latest_file.exists():
            raise HTTPException(
                status_code=404,
                detail="Aucune evaluation enregistree. Lancez `omi eval --save`.",
            )
        return EvalSuiteResult.model_validate_json(latest_file.read_text(encoding="utf-8"))
```

Dans `evals/harness.py`, ajouter :

```python
    def save_report(self, results: list[EvalScenarioResult], out: Path) -> Path:
        payload = {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
            "scenarios": [
                {"id": r.scenario_id, "title": r.category, "model": "eval_mock",
                 "passed": r.passed, "latency_ms": 0.0,
                 "score": 1.0 if r.passed else 0.0, "notes": r.details}
                for r in results
            ],
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return out
```

**Étape 4 — côté front** (`web/src/pages/ConfigPage.tsx:98`) : afficher « Banc d'essai : jamais exécuté
— lancez `omi eval --save` » quand la requête renvoie 404, au lieu de laisser `evalsQuery.data` indéfini.

**Validation :** `omi eval --save` puis `curl -s localhost:8765/api/evals/latest | jq .passed` renvoie un
nombre réel ; sans le fichier, l'API renvoie 404.

### Tâche 3.3 — Point d'entrée `omi` et commande `chat`

**Fichiers :** `packages/core/pyproject.toml`, `packages/core/omi_core/cli.py:47-53`

**Problème mesuré :** `packages/core/pyproject.toml` ne déclare aucun `[project.scripts]`, donc `omi eval`
et `omi chat` n'existent pas ; et `main()` renvoie `0` sans rien faire pour `chat`.

**Implémentation :**

```toml
[project.scripts]
omi = "omi_core.cli:main"
```

```python
    if args.command == "chat":
        return asyncio.run(_run_chat_cmd(args.model))
```

avec `_run_chat_cmd` qui construit `build_runtime(...)`, boucle sur `input("> ")`, appelle
`runtime.agent.run_turn(...)` et imprime le texte + le coût du tour.

**Validation :** `uv sync && omi --help` liste `chat` et `eval` ; `omi chat --model glm` répond.

### Tâche 3.4 — Canaux de notification réellement branchés

**Fichiers :** `packages/core/omi_core/runtime.py`, `packages/core/omi_core/channels/*.py`

**Constat :** `TelegramChannel`, `TwilioVoiceChannel`, `NtfyChannel` existent mais aucun n'est instancié
hors tests ; `AttentionEngine._dispatch_decision` appelle `self.on_dispatch(notif, decision)` qui n'est
jamais fourni.

**Implémentation :** dans `build_runtime`, construire les canaux dont les secrets sont présents et passer
un `on_dispatch` qui route selon `decision.channels` :

```python
    def _dispatch(notif, decision) -> None:
        for channel in decision.channels:
            sender = senders.get(channel)
            if sender:
                asyncio.create_task(sender.send(notif.message, urgency=int(notif.urgency)))
```

**Test :** `tests/test_runtime.py::test_dispatch_routes_to_configured_channels` avec un faux sender qui
enregistre les appels.

**Validation :** suite verte + un envoi ntfy réel si `NTFY_TOPIC` est défini dans `.env`.

---

## Phase 4 — Front-end et performance (P2)

### Tâche 4.1 — Code splitting de la sphère 3D

**Fichiers :** `web/src/App.tsx`, `web/src/pages/HomePage.tsx`, `web/vite.config.ts`

**Mesure de départ :** un chunk `index-*.js` de 1 404 Ko (398 Ko gzip), avertissement Vite > 500 Ko.

**Implémentation :**

```tsx
const VoiceSphere = lazy(() => import("./sphere/VoiceSphere").then((m) => ({ default: m.VoiceSphere })));
```

enveloppé dans `<Suspense fallback={<SphereFallback2D />}>` (le composant de repli existe déjà).

**Validation :**
```bash
cd web && npm run build 2>&1 | grep -E "dist/assets|chunks are larger"
```
Attendu : au moins deux chunks JS, le principal sous 500 Ko.

### Tâche 4.2 — E2E contre le vrai serveur

**Fichiers :** `web/playwright.config.ts`, `web/tests/app.spec.ts`

**Constat :** les 4 tests E2E tournent contre `web/mock-server.mjs`, donc ils ne prouvent rien sur
`omi-core`.

**Implémentation :** ajouter un second projet Playwright `real-core` dont le `webServer` lance
`uvicorn omi_core.api.server:app` sur le port 8766 avec `OMI_ALLOW_INSECURE=1` et un `OMI_DATA_DIR`
temporaire, et un test qui envoie un message et vérifie un `assistant.delta` non-echo.

**Validation :** `cd web && npx playwright test --project=real-core` → vert.

### Tâche 4.3 — Hygiène lint et CI

**Fichiers :** `.github/workflows/ci.yml` (créer)

**Étapes :**

1. `env -u PYTHONPATH .venv/bin/python -m ruff check packages tests --fix`
2. `env -u PYTHONPATH .venv/bin/python -m ruff format packages tests`
3. Corriger à la main les `E402` de `tests/test_core_v2.py` (imports après `sys.path.insert` : déplacer
   la manipulation de `sys.path` dans un `conftest.py` et importer normalement).
4. Workflow CI :

```yaml
name: ci
on: [push, pull_request]
jobs:
  python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --python 3.12
      - run: uv run ruff check packages tests
      - run: uv run ruff format --check packages tests
      - run: uv run pytest tests -q
  web:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - run: cd web && npm ci && npm run build && npx vitest run
```

**Validation :** `ruff check` et `ruff format --check` renvoient 0 ; le workflow passe sur GitHub.

### Tâche 4.4 — README à jour

**Fichier :** `README.md` (actuellement 222 lignes décrivant la v1 : Tkinter, Gemini seul, `install_linux.sh` apt)

**Contenu cible :** architecture v2 (packages, ports 8765/5173), `deploy/install_arch.sh`, variables
`.env` réelles, `omi chat` / `omi eval`, sécurité (jeton obligatoire, approbations, `<untrusted>`),
limites connues. Supprimer les tableaux de la v1 ou les déplacer sous « historique ».

**Validation :** chaque commande du README exécutée une fois sur une machine propre (ou conteneur).

---

## Fichiers touchés (récapitulatif)

Créés :
- `packages/core/omi_core/runtime.py`
- `tests/test_runtime.py`, `tests/test_ws_agent.py`, `tests/test_memory_unification.py`
- `.github/workflows/ci.yml`

Modifiés :
- `packages/core/omi_core/api/server.py` (WS, auth, évals, persistance)
- `packages/core/omi_core/agent/loop.py` (callbacks delta/outils)
- `packages/core/omi_core/memory/storage.py` (adaptateur), `memory/schema.py` (migration 2 + settings)
- `packages/core/omi_core/tools/registry.py` (timeout d'approbation)
- `packages/core/omi_core/tools/standard.py` (`register_all`)
- `packages/core/omi_core/cli.py`, `packages/core/pyproject.toml` (script `omi`)
- `evals/harness.py` (rapport), `web/src/pages/ConfigPage.tsx`, `web/src/App.tsx`,
  `web/playwright.config.ts`, `README.md`, `.gitignore`
- `core/tools.py` (garde-fou legacy), `tests/test_tools.py`, `tests/test_frontend_repetitif.py`

Supprimés / déplacés :
- `SCHEMA_SQL` dans `memory/storage.py`
- `omi-windows/` (archive dans `legacy/`)

---

## Validation de bout en bout (à exécuter après la phase 3)

```bash
# 1. Portes statiques
env -u PYTHONPATH .venv/bin/python -m ruff check packages tests
env -u PYTHONPATH .venv/bin/python -m ruff format --check packages tests

# 2. Tests
PYTHONPATH=packages/shared:packages/core:packages/desktop:. .venv/bin/python -m pytest tests -q
cd web && npx vitest run && npx playwright test

# 3. Service réel
export OMI_API_TOKEN=$(openssl rand -hex 24)
PYTHONPATH=packages/shared:packages/core:packages/desktop:. \
  .venv/bin/python -m uvicorn omi_core.api.server:app --host 127.0.0.1 --port 8765 &
curl -s -H "Authorization: Bearer $OMI_API_TOKEN" localhost:8765/api/health | jq .

# 4. Conversation réelle (consomme des tokens)
#    Dans l'UI : poser une question -> vérifier assistant.delta en streaming,
#    l'entrée dans /api/messages, et une ligne dans llm_calls.

# 5. Outil sensible
#    Demander « écris /tmp/omi-test.txt avec bonjour » -> approval.request dans /alerts,
#    approuver -> fichier créé.

# 6. Coût
curl -s -H "Authorization: Bearer $OMI_API_TOKEN" localhost:8765/api/usage/daily | jq .
```

Critères d'acceptation :
- un message web produit une réponse du modèle réel (pas « Bien recu ») ;
- le premier delta arrive en moins de 2 s sur DeepSeek ;
- un outil de niveau 2 attend l'humain et s'exécute après approbation ;
- un changement de modèle survit à un redémarrage ;
- `/api/evals/latest` renvoie 404 ou un rapport réellement produit, jamais un faux ;
- `git log --all -- omi_history.db` renvoie 0 ligne sur un clone neuf.

---

## Risques et questions ouvertes

1. **Purge Git irréversible** — réécrit tous les SHA. Question : d'autres clones/PR existent-ils ?
   Si oui, prévoir une fenêtre et prévenir avant `push --force`.
2. **Coût réel des évals** — `omi eval` utilise aujourd'hui `_EvalMockProvider`. Passer à un modèle réel
   pour `oral_register` et `prompt_injection` coûte quelques centimes par run ; à décider (option
   `--live`).
3. **Streaming et `AgentLoop`** — `run_turn` accumule aujourd'hui tout le texte avant de rendre la main ;
   la tâche 2.3 ajoute le callback mais ne change pas la valeur de retour. Vérifier qu'aucun appelant ne
   dépend de l'ordre exact.
4. **`StandardToolkit.register_all`** — la signature exacte des handlers (`Callable[[Any], Awaitable]`
   dans `standard.py` contre `Callable[..., Any]` dans `registry.ToolSpec`) impose peut-être un adaptateur ;
   à trancher à l'implémentation.
5. **SQLCipher** — `MemoryDatabase` accepte une `encryption_key` mais rien ne la fournit ; brancher
   `SecretStore.get_secret("OMI_DB_KEY")` est une tâche à part (chiffrement au repos, plan §2.2).
6. **TTS / voix temps réel** — `GeminiLiveBridge` et `RealtimeVoicePipeline` ne sont pas dans ce plan ;
   ils dépendent de la tâche 2.3 (streaming) et méritent leur propre plan.
7. **Python 3.14 dans `.venv`** — le venv local tourne en 3.14.7 alors que `.python-version` et
   `pyproject.toml` visent 3.12. `uv sync --python 3.12` recréera l'environnement ; à faire avant la CI.
