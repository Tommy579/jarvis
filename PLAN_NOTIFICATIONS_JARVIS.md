# Jarvis, le téléphone et les ordres — Plan d'implémentation

**Objectif** — Trois capacités concrètes, avec une seule source de vérité :

1. **Demander l'autorisation par téléphone.** Quand un outil exige une approbation (niveau 2 ou 3) et que
   l'utilisateur n'est pas devant l'écran, Jarvis l'envoie en push, puis en SMS, puis **l'appelle**.
   La réponse (bouton, code, PIN vocal, ou « non ») revient dans `ApprovalManager` et débloque l'agent.
2. **Enregistrer un ordre de notification.** « Préviens-moi quand le build échoue », « envoie-moi un SMS
   quand un client VIP écrit » : l'ordre **persiste après redémarrage**, s'applique aux événements
   suivants, et reste révocable.
3. **Moduler l'intrusion.** « Ne m'appelle pas sauf en cas d'urgence extrême », « rien entre 22 h et 7 h
   sauf alarme » : une **politique de notification** persistée, appliquée par le moteur d'attention,
   jamais contournable par le LLM sans trace.

Le fil conducteur : **le LLM propose, le code décide**. Les ordres et la politique sont des données
structurées relues par du code déterministe ; le modèle ne fait que les écrire via des outils validés.

---

## 0. État des lieux (ce qui existe déjà, ce qui est faux)

Tout ce qui suit a été vérifié dans le dépôt, pas supposé.

| Élément | État réel | Emplacement |
|---|---|---|
| Moteur d'attention (urgence 0-4, canaux, dédup) | Écrit et testé, mais **canaux codés en dur** | `packages/core/omi_core/attention/engine.py:111-203` |
| Escalade vers l'appel | `check_escalations()` existe… **jamais appelée hors tests** | `engine.py:344-358` ; seul appel : `tests/test_core_v2.py:382` |
| Plafond d'appels/jour | Compteur en mémoire, remis à zéro à chaque redémarrage | `engine.py:61-75` |
| Présence / heures calmes | Hardcodé 23 h-8 h, non lu depuis la politique | `attention/presence.py:22-30` |
| Canaux construits au runtime | ntfy, Telegram branchés ; Twilio seulement déclaré | `runtime.py:148-181` |
| Envoi réel d'un appel Twilio | **Stub** : renvoie `{"ok": True}` sans appeler l'API | `runtime.py:108-113` |
| SMS | **Aucun** émetteur | — |
| Webhooks Twilio (voix, gather, statut) | Classe prête, **aucune route FastAPI** | `channels/twilio_voice.py` ; absent de `api/server.py` |
| Approbations | File asyncio + WebSocket, TTL 120 s, PIN géré | `tools/approvals.py:29-101`, `runtime.py:229-246` |
| Approbation depuis le téléphone | **Impossible** : rien n'envoie la demande, rien ne la reçoit | — |
| Notifications persistées | Table `notifications` (canal, urgence, statut, `dedup_key`, `acked_at`) | `memory/schema.py:120-131` |
| Règles « ordres » | Texte Markdown statique, **non interprété** | `omi-data/wiki/rules.md`, `regles.md` |
| Outil LLM de notification | Seulement `send_notification` (toast local) | `tools/standard.py:60-62, 336-343` |
| Réglages persistés | Table `settings` (clé/valeur JSON), utilisée pour `attention_rules` | `memory/schema.py:196-200`, `api/server.py:289-305` |
| Incohérence d'env | `TWILIO_ALLOWED_USER_NUMBERS` (code) vs `USER_PHONE_NUMBER` (`.env.example`) ; `PUBLIC_WEBHOOK_BASE_URL` absent | `runtime.py:168`, `.env.example:20-31` |
| Planificateur de routines | Existe, **non instancié** au démarrage | `scheduler/routines.py` (aucun appel hors tests) |

**Conséquence** : aujourd'hui, Jarvis sait décider qu'il faudrait appeler. Il ne sait ni appeler, ni
recevoir la réponse, ni retenir un ordre. C'est exactement le périmètre de ce plan.

---

## 1. Modèle de données (à trancher avant de coder)

Trois notions distinctes, trois stockages, un seul point d'entrée pour les lire.

### 1.1 `NotificationPolicy` — les réglages durables (une seule ligne)

Stockée dans `settings` sous la clé `notification_policy` (JSON), avec `schema_version` pour migrer.
Modèle Pydantic dans un nouveau module `packages/shared/omi_shared/notifications.py` :

```python
class NotificationPolicy(BaseModel):
    schema_version: int = 1
    enabled: bool = True
    timezone: str = "Europe/Paris"

    # Heures calmes
    quiet_start: str = "23:00"
    quiet_end: str = "08:00"
    quiet_allow_urgency: UrgencyLiteral = 4      # ce qui passe quand même la nuit

    # Appels
    allow_calls: bool = True
    call_min_urgency: UrgencyLiteral = 3         # niveau minimum pour décrocher le téléphone
    call_require_pin: bool = True
    max_calls_per_day: int = 3                    # 4 = urgence, ne consomme pas le quota
    min_seconds_between_calls: int = 1800

    # Canaux autorisés, dans l'ordre de l'échelle
    channel_ladder: list[Literal["ntfy", "telegram", "sms", "call"]] = ["ntfy", "telegram", "sms", "call"]
    escalation_seconds: dict[str, int] = {"ntfy": 120, "telegram": 300, "sms": 300}

    # Approbations
    approval_ttl_seconds: int = 900               # 15 min, pas 2 : on parle d'un téléphone
    approval_escalation: bool = True
    approval_min_risk_for_call: HighRiskLiteral = 2   # niveau 2+ seulement

    # Réduction de bruit
    dedupe_window_seconds: int = 3600
    snooze_until: str | None = None               # ISO 8601, « ne me dérange pas avant »
    emergency_override: bool = True               # le niveau 4 ignore tout sauf enabled=False
```

Défauts = comportement actuel (23-8, plafond 3, appel à partir du niveau 3), donc **aucune régression**
pour les tests existants (`tests/test_core_v2.py:356-410`).

### 1.2 `NotificationOrder` — l'ordre ponctuel ou récurrent

Nouvelle table `notification_orders` (migration 3) :

```sql
CREATE TABLE IF NOT EXISTS notification_orders (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    -- Déclencheur : ce qu'on écoute
    source_glob TEXT NOT NULL DEFAULT '*',        -- ex. 'github:*', 'build', 'calendar'
    event_type_glob TEXT NOT NULL DEFAULT '*',    -- ex. 'probe.failed', 'message.received'
    filter_json TEXT NOT NULL DEFAULT '{}',       -- ex. {"sender_contains": "maman"}
    text_match TEXT,                              -- sous-chaîne insensible à la casse
    -- Effet
    min_urgency INTEGER NOT NULL DEFAULT 1,
    channels_json TEXT NOT NULL DEFAULT '[]',     -- [] = laisser la politique décider
    message_template TEXT NOT NULL DEFAULT '{summary}',
    -- Cycle de vie
    expires_at TEXT,                              -- NULL = jusqu'à révocation
    max_fires INTEGER,                            -- NULL = illimité
    fired_count INTEGER NOT NULL DEFAULT 0,
    last_fired_at TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    origin TEXT NOT NULL DEFAULT 'user',          -- user | agent | onboarding
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_orders_active ON notification_orders(active);
```

### 1.3 `NotificationAttempt` — la trace d'envoi

Une ligne par tentative et par canal, pour répondre à « est-ce qu'il a vraiment été prévenu ? » :

```sql
CREATE TABLE IF NOT EXISTS notification_attempts (
    id TEXT PRIMARY KEY,
    notification_id TEXT NOT NULL,
    approval_id TEXT,
    order_id TEXT,
    channel TEXT NOT NULL,           -- ntfy | telegram | sms | call | ws
    target TEXT NOT NULL,            -- topic / chat_id / numéro masqué
    ts TEXT NOT NULL,
    status TEXT NOT NULL,            -- queued | sent | delivered | failed | answered | expired
    error TEXT,
    external_id TEXT,                -- SID Twilio, id ntfy
    FOREIGN KEY(notification_id) REFERENCES notifications(id)
);
```

**Règle d'or** : un ordre ou une politique ne modifie jamais `notifications` (historique) ; il modifie la
*décision*. L'historique reste append-only.

---

## 2. Phase 1 — Rendre la politique réellement appliquée (P0, sans téléphone)

But : « ne m'appelle pas sauf urgence extrême » doit fonctionner **avant** de savoir émettre un appel.
C'est aussi la phase qui supprime les canaux codés en dur.

### Tâche 1.1 — Migration 3 et modèles partagés

**Fichiers** : `packages/core/omi_core/memory/schema.py`, `packages/shared/omi_shared/notifications.py`,
`packages/shared/omi_shared/rest_models.py`

- Ajouter la migration 3 (trois tables ci-dessus + `ALTER TABLE approvals ADD COLUMN delivered_channels TEXT NOT NULL DEFAULT ''` et `answered_via TEXT`).
- Exporter `NotificationPolicy` et `NotificationOrder` dans `omi_shared` pour que l'API, la CLI et le front partagent les types.
- `MemoryDatabase` : `get_policy()/set_policy()`, `list_orders()/upsert_order()/revoke_order()`, `insert_attempt()`.

**Test** : `tests/test_notifications.py::test_migration_3_is_idempotent` (ouvrir deux fois la base, pas d'erreur, `schema_version == 3`).

### Tâche 1.2 — `PolicyStore` : une seule lecture, un défaut sûr

**Fichier** : `packages/core/omi_core/notifications/policy.py` (nouveau paquet, aux côtés de `channels/`)

```python
class PolicyStore:
    def __init__(self, db: MemoryDatabase) -> None:
        self.db = db
        self._cache: NotificationPolicy | None = None

    def get(self) -> NotificationPolicy:
        raw = self.db.get_setting("notification_policy")
        if raw:
            try:
                return NotificationPolicy.model_validate_json(raw)
            except ValidationError:
                logger.warning("Politique de notification illisible, retour aux defauts")
        return NotificationPolicy()

    def update(self, **changes) -> NotificationPolicy:
        current = self.get().model_copy(update=changes)
        self.db.set_setting("notification_policy", current.model_dump_json())
        self._mirror_to_wiki(current)     # lisibilite humaine, jamais relu par le code
        return current
```

- `_mirror_to_wiki` écrit `omi-data/wiki/politique-notifications.md` (même style que `rules.md`) : c'est ce
  que l'utilisateur relit, mais **la base reste la source de vérité**. Le wiki est un rendu.
- Ne jamais relire le Markdown pour décider.

**Test** : `test_policy_defaults_match_legacy_behaviour`, `test_policy_survives_restart` (instance neuve,
même `data_dir`, `allow_calls=False` conservé).

### Tâche 1.3 — `AttentionEngine` consomme la politique

**Fichier** : `packages/core/omi_core/attention/engine.py`

Remplacer les listes de canaux codées en dur (`engine.py:120-203`) par un calcul unique :

```python
def decide_channels(self, urgency, snap, policy, now) -> list[str]:
    if not policy.enabled:
        return []
    if policy.snooze_until and now < parse(policy.snooze_until):
        urgency = min(urgency, UrgencyLevel.INFO)
    quiet = snap.in_quiet_hours
    if quiet and urgency < policy.quiet_allow_urgency:
        urgency = UrgencyLevel.INFO

    base = ["sphere", "voice"] if snap.is_at_pc else ["telegram"]
    if urgency >= policy.call_min_urgency and policy.allow_calls:
        base += [c for c in policy.channel_ladder if c in ("sms", "call")]
    return base
```

Points d'attention :

- `route_channels` garde sa signature publique (les tests patchent `presence.snapshot`) mais délègue à
  `decide_channels` ; `deferred_to_digest` reste identique.
- `_can_place_call` lit `policy.max_calls_per_day` et `min_seconds_between_calls`, et **persiste** le
  compteur (`settings` clé `calls_today` = `{"date": ..., "count": n, "last_ts": ...}`) pour survivre
  aux redémarrages.
- `emergency_override` : niveau 4 passe `quiet_allow_urgency` et le plafond, mais jamais `enabled=False`.
- Niveau 4 sans `allow_calls` (ordre « ne m'appelle pas sauf urgence extrême ») : on garde `allow_calls`
  comme borne *générale* et on ajoute `allow_emergency_calls: bool = True` — c'est ce booléen que
  l'ordre « ne m'appelle sauf urgence extrême » positionne à `False`, faisant du niveau 4 le seul appel
  possible. Les deux champs sont nécessaires : `allow_calls` coupe tout, `allow_emergency_calls` coupe
  tout sauf le niveau 4.

**Tests** (`tests/test_attention_policy.py`) :

1. `test_no_calls_except_extreme_emergency` : politique `allow_calls=False, allow_emergency_calls=True` ;
   urgence 3 → aucun canal `call`/`sms` ; urgence 4 → `call` présent.
2. `test_quiet_hours_swallow_level_3` : 2 h du matin, urgence 3 → digest, aucun envoi.
3. `test_daily_call_budget_survives_restart` : deux instances successives, le quatrième appel est refusé.
4. `test_policy_disabled_sends_nothing` : `enabled=False` → `channels == []` même en niveau 4.

### Tâche 1.4 — Le tick qui n'existe pas

**Fichiers** : `packages/core/omi_core/api/server.py`, `scheduler/routines.py`

Aujourd'hui `check_escalations` et `flush_triage_batch` ne tournent jamais. Ajouter dans le `lifespan`
du serveur une boucle unique (pas APScheduler pour ça, la granularité est de 30 s) :

```python
async def _attention_tick(runtime, interval=30):
    while True:
        await asyncio.sleep(interval)
        for notif in runtime.attention.check_escalations(elapsed_seconds=interval):
            await runtime.notifications.escalate(notif)   # cf. phase 2
        if int(time.time()) % 300 < interval:
            await runtime.attention.flush_triage_batch(current_hour=datetime.now().hour)
```

- `elapsed_seconds` : passer le **temps écoulé depuis l'envoi initial** (`now - notif.ts`), pas
  l'intervalle — sinon l'escalade ne déclenche jamais (bug actuel masqué par le test qui passe 1200 en dur).
- Arrêt propre sur `CancelledError`, tâche unique par process (garde `asyncio.Lock`).

**Test** : `test_attention_tick_escalates_after_timeout` avec une horloge injectée.

**Validation phase 1** :

```bash
PYTHONPATH=packages/shared:packages/core:packages/desktop:. .venv/bin/python -m pytest tests/test_attention_policy.py tests/test_core_v2.py -q
env -u PYTHONPATH .venv/bin/python -m ruff check packages tests
```

---

## 3. Phase 2 — Appeler et écrire pour de vrai (Twilio, SMS, webhooks)

### Tâche 2.1 — `TwilioClient` réel (fin du stub)

**Fichier** : `packages/core/omi_core/channels/twilio_voice.py`, nouveau `channels/twilio_api.py`

```python
class TwilioRest:
    """Appels REST Twilio : voix et SMS, avec retry et idempotence."""

    def __init__(self, account_sid, auth_token, from_number, http: httpx.AsyncClient):
        ...

    async def create_call(self, to: str, twiml_url: str, status_callback: str) -> str:
        resp = await self.http.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Calls.json",
            auth=(self.account_sid, self.auth_token),
            data={"To": to, "From": self.from_number, "Url": twiml_url,
                  "StatusCallback": status_callback, "MachineDetection": "Enable"},
        )
        resp.raise_for_status()
        return resp.json()["sid"]

    async def send_sms(self, to: str, body: str) -> str: ...
```

- `_TwilioSender.send` (runtime.py:108-113) devient : 1) persiste une `NotificationAttempt`, 2) appelle
  `create_call`, 3) stocke le SID. Ne jamais prétendre `ok: True` sans SID.
- `MachineDetection` : si répondeur, on ne compte pas l'alerte comme délivrée → un SMS de repli.
- Corps du message borné à 300 caractères (coût + lisibilité), préfixe `[JARVIS]`, urgence, et lien court
  d'acquittement signé.

### Tâche 2.2 — Webhooks FastAPI (le chemin retour)

**Fichier** : `packages/core/omi_core/api/server.py`

Quatre routes, toutes avec vérification de signature Twilio (`verify_twilio_signature`, déjà écrite) et
filtrage du numéro appelant (`is_caller_allowed`) :

| Route | Rôle |
|---|---|
| `POST /webhooks/twilio/voice` | TwiML d'entrée : `<Gather>` Palier A (1 = acquitter, 2 = rappel 10 min, PIN) |
| `POST /webhooks/twilio/gather` | Retour DTMF/voix → `handle_gather_callback` → `AttentionEngine.acknowledge` **ou** `ApprovalManager.resolve` |
| `POST /webhooks/twilio/status` | `queued/ringing/in-progress/completed/busy/no-answer/failed` → `NotificationAttempt.status` |
| `POST /webhooks/twilio/sms` | Réponse SMS (`OUI`/`NON` + code) → approbation |

- Toutes **exemptées du `OMI_API_TOKEN`** (Twilio ne le connaît pas) mais **jamais** sans signature.
- Réponse toujours en `text/xml` valide, même en erreur (`<Say>`), pour éviter les reprises Twilio.
- Idempotence : clé `(external_id, status)` ; un `gather` rejoué ne ré-acquitte pas.
- L'URL publique vient de `PUBLIC_WEBHOOK_BASE_URL` (à ajouter dans `.env.example` ; documenter ngrok /
  reverse proxy, et le fait qu'un webhook public expose l'endpoint : signature + PIN obligatoires).

### Tâche 2.3 — L'échelle d'escalade, appliquée aux approbations

**Fichier** : nouveau `packages/core/omi_core/notifications/dispatcher.py`

```python
class ChannelDispatcher:
    """Envoie une demande sur une echelle de canaux, avec delais et arret a la premiere reponse."""

    async def request_approval(self, req: ApprovalRequest, policy: NotificationPolicy) -> str:
        for channel, delay in self._ladder(policy, req.risk):
            await asyncio.sleep(delay) if delay else None
            if req.status != "pending":
                return req.status
            attempt = await self._send(channel, req)
            await self._record(attempt)
        return "expired"
```

- L'échelle par défaut : `ntfy` (boutons Accepter/Refuser) → `telegram` (clavier inline déjà écrit,
  `build_approval_keyboard`) → `sms` (lien signé) → `call` (Palier A + PIN si risque 3).
- **Chaque étape est sautée si son secret est absent** — pas d'erreur, une ligne de log et on passe.
- L'approbation au téléphone ne doit pas bloquer l'agent indéfiniment : `ApprovalManager.ttl_seconds`
  passe à `policy.approval_ttl_seconds` (900 s) ; l'agent reçoit `expired` et répond « je n'ai pas eu de
  réponse, je n'ai rien fait » — jamais un succès supposé.
- Quand l'utilisateur approuve depuis le téléphone, `ApprovalManager.resolve(..., channel="phone")`
  exige déjà le PIN pour `RiskLevel.DANGEROUS` (`approvals.py:72-84`) : ne pas affaiblir.

### Tâche 2.4 — Jetons d'action signés (liens ntfy / SMS)

**Fichier** : `packages/core/omi_core/security.py`

Aujourd'hui un bouton ntfy pointerait sur `/api/approvals/{id}` sans authentification. Ajouter :

```python
def sign_action_token(secret: str, approval_id: str, action: str, ttl: int = 900) -> str:
    payload = f"{approval_id}:{action}:{int(time.time()) + ttl}"
    mac = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}:{mac}"

def verify_action_token(secret: str, token: str) -> tuple[str, str] | None:
    """Retourne (approval_id, action) ou None. Usage unique via la table notification_attempts."""
```

- `GET /actions/{token}` : vérifie, marque l'approbation, répond une page HTML mobile (« C'est fait, Jarvis
  exécute ») — pas de JSON brut sur un téléphone.
- Un jeton = une action = une utilisation. Rejouer le lien affiche « déjà traité ».
- Le secret vient de `SecretStore` (clé `ACTION_TOKEN_SECRET`, générée au premier démarrage si absente).

### Tâche 2.5 — Le LLM ne touche pas aux canaux directement

`AgentLoop` continue d'appeler `ToolRegistry` → `ApprovalManager.create_request`. C'est le **runtime** qui
branche `on_approval_requested` sur le dispatcher. Aucun outil LLM ne peut « appeler l'utilisateur » :
il peut seulement demander une action, qui déclenche le circuit d'approbation. C'est la garantie anti-
hallucination (« j'ai prévenu Tom » alors que rien n'est parti).

**Tests phase 2** (`tests/test_twilio_flow.py`, hors ligne via `httpx.MockTransport`) :

- `test_call_creates_sid_and_attempt`, `test_no_sid_means_no_success` (le stub actuel échouerait).
- `test_webhook_rejects_bad_signature` (403), `test_webhook_rejects_unknown_number`.
- `test_gather_digit_1_approves`, `test_gather_digit_2_snoozes_10min`, `test_wrong_pin_denies`.
- `test_escalation_stops_after_approval` (pas de SMS si l'utilisateur a répondu sur ntfy).
- `test_action_token_is_single_use`.

**Validation manuelle** (à documenter dans le README) :

```bash
# 1. ntfy (2 min)
export NTFY_TOPIC=jarvis-test-xxxx && curl -d "test" https://ntfy.sh/$NTFY_TOPIC
# 2. Twilio (appel reel, ~0,02 EUR)
python -m omi_core.cli notify test --channel call
# 3. approbation bout en bout
python -m omi_core.cli notify ask "autoriser rm -rf build/ ?" --risk 3
```

---

## 4. Phase 3 — Les ordres : persister une intention, pas un texte

C'est la partie demandée explicitement : « quand je lui demande de me notifier certaines actions, que ça
persiste ». Le point dur n'est pas le stockage, c'est **la relecture par le code** : un ordre doit être
une requête, pas une phrase.

### Tâche 3.1 — Outils exposés au LLM

**Fichier** : nouveau `packages/core/omi_core/tools/notifications.py`, enregistré dans `StandardToolkit.register_all`

| Outil | Risque | Rôle |
|---|---|---|
| `notify_me(source_glob, event_type_glob, text_match=None, min_urgency=2, channels=None, expires_at=None, max_fires=None, note="")` | 1 | Crée un `NotificationOrder`, renvoie son `id` et un résumé lisible |
| `list_my_orders()` | 0 | Liste les ordres actifs (id, déclencheur, expiration, compteur) |
| `revoke_order(order_id)` | 1 | Désactive un ordre |
| `set_notification_policy(allow_calls=None, allow_emergency_calls=None, quiet_start=None, quiet_end=None, call_min_urgency=None, snooze_until=None, max_calls_per_day=None)` | 1 | Met à jour la politique, champs partiels |
| `show_notification_policy()` | 0 | Rend la politique effective en texte court |

Règles de conception :

- **Champs partiels** partout (`None` = ne change pas), pour que « ne m'appelle pas cette nuit » ne
  réinitialise pas les heures calmes.
- Chaque outil écrit un `MemoryEngine.record_episode` (trace conversationnelle) **et** la ligne en base.
- La réponse de l'outil contient le résumé de l'effet : « Ordre #ord_ab12 : si `build` échoue → SMS,
  valable jusqu'au 21/11 ». Le modèle n'a plus besoin d'inventer une confirmation.
- Le prompt système (`MemoryEngine.persona_prompt`, `runtime.py:275-281`) gagne une phrase : « Les ordres
  de notification passent par `notify_me` / `set_notification_policy` ; ne promets jamais une notification
  sans les avoir appelés. »

### Tâche 3.2 — Du langage naturel à l'ordre (deux étages)

**Fichier** : `packages/core/omi_core/notifications/parsing.py`

1. **Étage déterministe** (gratuit, couvre 80 % des cas) — expressions régulières françaises :

   | Phrase | Effet |
   |---|---|
   | « ne m'appelle pas sauf en cas d'urgence extrême » | `allow_emergency_calls=True`, `call_min_urgency=4` |
   | « ne m'appelle plus » / « coupe les appels » | `allow_calls=False` |
   | « pas la nuit » / « rien après 22 h » | `quiet_start=22:00` |
   | « préviens-moi si/quand <X> » | `notify_me(text_match=X, min_urgency=2)` |
   | « par SMS seulement » | `channel_ladder=["sms"]` |
   | « jusqu'à vendredi » | `expires_at` calculé depuis la date courante (locale `fr`) |

2. **Étage LLM** (repli) : le routeur `triage` (déjà routé, température 0) reçoit la phrase + le schéma
   JSON de l'outil et renvoie un appel d'outil. Jamais de texte libre interprété.

**Garde-fou** : si les deux étages échouent, on **ne devine pas** — réponse « je n'ai pas compris quel
événement surveiller, précise-le » (pas de faux ordre silencieux).

### Tâche 3.3 — Application dans le moteur

**Fichier** : `packages/core/omi_core/notifications/orders.py`

```python
def match_orders(orders, event) -> list[NotificationOrder]:
    return [o for o in orders if o.active and fnmatch(event.source, o.source_glob)
            and fnmatch(event.event_type, o.event_type_glob)
            and _filter_ok(o, event) and _text_ok(o, event)]
```

- Appelé dans `AttentionEngine.ingest_event` **avant** la règle déterministe : un ordre explicite gagne
  sur le triage automatique.
- L'ordre peut **monter** l'urgence (jamais descendre en dessous de la règle de sécurité : un ordre ne
  peut pas transformer une alarme incendie en notification muette — plancher = urgence évaluée par les
  règles, sauf ordre de type « silence » qui reste soumis à `emergency_override`).
- `fired_count += 1`, `last_fired_at = now` ; `max_fires` atteint ou `expires_at` dépassé → `active = 0`.
- Déduplication : un ordre ne se déclenche pas deux fois pour le même `dedup_key` dans la fenêtre
  `dedupe_window_seconds`.

### Tâche 3.4 — Cycle de vie visible

- **CLI** : `omi orders list|add|rm`, `omi policy show|set --no-calls --calls-only-emergency`,
  `omi notify test`.
- **Chat** : toute création d'ordre renvoie une carte « Ordre actif — [révoquer] » dans le fil.
- **UI** : section « Ordres et notifications » dans `ConfigPage` (voir phase 4).

### Tâche 3.5 — Les trois scénarios, testés

```python
async def test_do_not_call_me_except_extreme_emergency_persists(tmp_path):
    # 1. l'utilisateur parle, l'ordre est écrit
    # 2. redémarrage complet du runtime (même data_dir)
    # 3. urgence 3 -> aucun appel, aucune tentative SMS
    # 4. urgence 4 -> un appel, une ligne notification_attempts
```

- `test_notify_me_build_failure_fires_once` (max_fires=1, deux événements, un seul envoi).
- `test_order_expires` (horloge injectée).
- `test_order_does_not_silence_alarm` (ordonne le silence, l'alarme niveau 4 passe quand même).
- `test_natural_language_orders` (table de phrases → effet attendu, sans LLM).

**Validation phase 3** :

```bash
PYTHONPATH=packages/shared:packages/core:packages/desktop:. .venv/bin/python -m pytest tests/test_orders.py tests/test_notifications.py -q
```

---

## 5. Phase 4 — Interface, audit et coûts (P1)

### Tâche 4.1 — Page de configuration

**Fichiers** : `web/src/pages/ConfigPage.tsx` (section notifications vers la ligne 420, à côté des heures
calmes existantes), `web/src/lib/api.ts`, `web/src/store/useOmiStore.ts`

- Interrupteur **« Jarvis peut m'appeler »** + sous-option **« uniquement urgence extrême »** (radio).
- Heures calmes (déjà là), plafond d'appels/jour, délai d'escalade.
- Liste des canaux configurés avec leur état réel (secret présent ou non), et un bouton **« Tester »** par canal.
- Liste des ordres actifs, avec révocation et date d'expiration.
- Bandeau d'état : « Dernier appel : hier 21:14 — acquitté en 12 s ».

### Tâche 4.2 — Audit et anti-bruit

- Chaque alerte affiche son parcours : `ntfy ✓ → SMS ✓ → appel ✗ (snooze)`. Données :
  `notification_attempts`.
- Bouton « trop dérangeant » existant (`mark_too_noisy`) : il doit aussi **proposer** de créer un ordre de
  silence (`source_glob` prérempli) — c'est le pont entre la plainte et l'ordre.
- Journalisation : numéros masqués (`+33 6 •• •• 12 34`), jamais le PIN, jamais le jeton d'action.

### Tâche 4.3 — Coûts

- Compteur mensuel d'appels/SMS dans `settings` (`twilio_usage`), affiché dans la page config.
- Refus au-delà d'un plafond `monthly_telephony_budget_eur` (nouveau champ politique) : on retombe sur le
  push. Un appel d'urgence coûte quelques centimes, mille appels coûtent un abonnement.

---

## 6. Phase 5 — Sécurité : ce qui peut mal tourner

| Risque | Parade |
|---|---|
| Un tiers déclenche une approbation par SMS/appel | Numéros en liste blanche (`allowed_user_numbers`), signature Twilio, PIN pour risque 3 |
| Rejeu du lien d'action | Jeton HMAC à usage unique + expiration 15 min + page « déjà traité » |
| Le LLM prétend avoir prévenu | Aucun succès sans `notification_attempts.status in (sent, delivered)` ; le résultat de l'outil le dit |
| Un ordre éteint une alerte critique | Plancher d'urgence pour les sources `home.alarm` / `probe.failed≥3` ; `emergency_override` non contournable |
| Appel en boucle à 3 h du matin | Plafond/jour + intervalle minimum + heures calmes + `snooze_until`, tous **persistés** |
| Fuite de contenu sensible dans une notif | Troncature 300 caractères + `MemoryEngine.query_touches_highly_private_topic` consulté avant envoi |
| Webhook public exposé | Signature obligatoire, 403 par défaut, `PUBLIC_WEBHOOK_BASE_URL` documenté comme surface publique |
| Panne Twilio | Repli SMS → ntfy → WebSocket ; l'échec est visible dans l'UI, jamais silencieux |

---

## 7. Fichiers touchés (récapitulatif)

**Nouveaux**
- `packages/shared/omi_shared/notifications.py` — `NotificationPolicy`, `NotificationOrder`, schémas d'outils.
- `packages/core/omi_core/notifications/__init__.py`
- `packages/core/omi_core/notifications/policy.py` — `PolicyStore`
- `packages/core/omi_core/notifications/orders.py` — `match_orders`, cycle de vie
- `packages/core/omi_core/notifications/dispatcher.py` — `ChannelDispatcher`, échelle d'escalade
- `packages/core/omi_core/notifications/parsing.py` — phrases → ordres (regex + repli LLM)
- `packages/core/omi_core/channels/twilio_api.py` — client REST voix/SMS
- `packages/core/omi_core/tools/notifications.py` — outils LLM
- `tests/test_notifications.py`, `tests/test_orders.py`, `tests/test_twilio_flow.py`, `tests/test_attention_policy.py`

**Modifiés**
- `memory/schema.py` (migration 3), `memory/storage.py` (accès attempts/orders)
- `attention/engine.py` (politique + ordres), `attention/presence.py` (heures calmes lues depuis la politique)
- `channels/twilio_voice.py` (client réel), `runtime.py` (`_TwilioSender`, `_build_channels`, tick, dispatcher)
- `tools/approvals.py` (TTL politique, canaux de livraison), `tools/standard.py` (enregistrement)
- `api/server.py` (4 webhooks, `/actions/{token}`, endpoints policy/orders, tick lifespan)
- `cli.py` (`orders`, `policy`, `notify test`)
- `web/src/pages/ConfigPage.tsx`, `web/src/lib/api.ts`, `web/src/store/useOmiStore.ts`
- `.env.example` (corriger `TWILIO_ALLOWED_USER_NUMBERS`, ajouter `PUBLIC_WEBHOOK_BASE_URL`,
  `ACTION_TOKEN_SECRET`, `USER_PHONE_NUMBER` si conservé), `README.md`

**Ordre d'exécution conseillé** : 1.1 → 1.2 → 1.3 → 1.4 (tout testable hors ligne) → 2.4 → 2.1 → 2.2 →
2.5 → 3.1 → 3.3 → 3.2 → 4.1 → 5. Les phases 1 et 3 ne dépendent d'aucun compte externe : elles peuvent
être livrées et validées avant même de créer un compte Twilio.

---

## 8. Critères d'acceptation (définition de « fini »)

1. **« Jarvis, demande-moi avant de supprimer un dossier »** → requête d'approbation visible en push ;
   sans réponse en 2 min, SMS ; sans réponse en 10 min, appel avec PIN ; l'agent ne dépasse pas la
   demande et affiche « refusé/expiré » si l'utilisateur ne répond pas.
2. **« Préviens-moi si le build échoue »** → l'ordre survit à un `systemctl restart`, tire une seule fois
   (si `max_fires=1`), et `omi orders list` le montre ainsi que sa date d'expiration.
3. **« Ne m'appelle pas sauf en cas d'extrême urgence »** → à 3 h du matin, trois alertes de niveau 3
   n'appellent pas ; une alerte de niveau 4 appelle ; le journal (`notification_attempts`) prouve la
   différence et le plafond tient après redémarrage.
4. Aucun appel, SMS ou push ne part si le secret du canal est absent — et l'UI le dit.
5. `pytest` vert, `ruff check` et `ruff format --check` verts, `npm run build` + `vitest` verts.
6. Aucun numéro, PIN ou jeton en clair dans les logs.

---

## 9. Questions ouvertes (à trancher avant la phase 2)

1. **Numéro unique ou deux numéros ?** `USER_PHONE_NUMBER` (appels) et `TELEGRAM_ALLOWED_CHAT_ID`
   peuvent diverger. Proposition : une seule liste `ALLOWED_USER_NUMBERS`, format E.164, virgules.
2. **SMS entrants** : répondre « OUI » par SMS est pratique mais faible (usurpation d'expéditeur
   possible). Proposition : SMS = notification seulement, l'action se fait par lien signé ou appel + PIN.
3. **Le wiki comme interface d'édition** : faut-il accepter qu'un utilisateur édite
   `politique-notifications.md` à la main ? Proposition : non en v1 (le fichier est écrasé), puis import
   explicite `omi policy import` en v2.
4. **Fuseau et DST** : `Europe/Paris` codé en dur à plusieurs endroits (`AttentionRules.timezone`).
   Centraliser sur `zoneinfo` et tester le passage à l'heure d'hiver.
5. **Appels ConversationRelay (Palier B, conversation complète)** : hors périmètre de cette itération —
   Palier A (navigation DTMF + PIN) suffit pour autoriser/refuser. À réévaluer après usage réel.
