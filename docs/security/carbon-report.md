# Rapport d'audit de sécurité — Périmètre Carbon (API, outils, MCP, OAuth)

## Synthèse des vulnérabilités et vérifications

| ID | Sévérité | Fichier:ligne | Scénario d'exploitation | Preuve | Correctif | Statut |
|----|----------|---------------|--------------------------|--------|-----------|--------|
| C-01 | Élevé | `api/server.py:2148`, `security.py:165` | Jeton `?token=` accepté sur les requêtes mutantes (`POST`/`PUT`/`DELETE`), mis en cache potentiel et absence de cookie de session `HttpOnly` court | `tests/test_security_api.py::test_c01_query_token_get_only_and_session_cookie` | Cookie signé `jarvis_session` (`HttpOnly`, `SameSite=Strict`, clé dérivée HMAC), routes `/api/auth/session` et `/api/auth/logout`, `Cache-Control: no-store`, refus de `?token=` hors `GET`/`HEAD` | corrigé |
| C-02 | Élevé | `api/server.py:2741` | Webhook `/api/webhooks/trading` accessible sans jeton depuis le réseau/pont Docker si `JARVIS_ALLOW_INSECURE=1`, sans limite de débit ni plafond de taille JSON | `tests/test_security_api.py::test_c02_trading_webhook_explicit_auth_and_limits` | Authentification `Bearer` explicite, restriction stricte au loopback en mode dev non sécurisé (`403` hors loopback, `503` sinon), plafond 64 Ko (`413`) et rate-limiting dédié | corrigé |
| C-03 | Élevé | `api/server.py:2922, 3096` | Injection d'en-tête `Host` dans `_build_oauth_redirect_uri` (détournement du code OAuth vers un hôte tiers), rejeu d'un `code` OAuth avec un second `state`, exemption trop large `/api/oauth/*` et fuite de `resp.text` | `tests/test_security_api.py::test_c03_oauth_host_header_injection_and_code_replay` | Validation stricte de `Host` sur loopback, exemption restreinte à `GET /api/oauth/{provider}/callback`, registre anti-rejeu des codes OAuth, plafond de `oauth_states`, rate-limiting et masquage des erreurs amont | corrigé |
| C-04 | Élevé | `api/server.py:2778`, `integrations/base.py:146`, `apple_calendar.py:93` | Création/écriture de `.env` en `0644` (lisible par tout utilisateur local) lors de la persistance des jetons OAuth/API, et fuite d'URLs `.ics` / paramètres secrets dans les erreurs HTTP | `tests/test_security_api.py::test_c04_env_permissions_0600_and_secret_url_redaction` | Écriture `write_secret_env_0600` (`os.open` `0600` + `chmod 0600` + anti-injection CRLF/NUL), prise en compte de `JARVIS_ENV_PATH`, et masquage systématique `redact_sensitive_text` | corrigé |
| C-05 | Faible | `security.py:101` | Repli silencieux en mémoire (`os.environ`) dans `SecretStore.set_secret` sans avertissement ni validation du nom de clé | `tests/test_security_api.py::test_c05_secret_store_validation_and_volatile_warning` | Validation stricte du nom de secret et absence d'octet nul, journalisation `WARNING` explicite (sans divulguer la valeur) lors d'un repli mémoire | corrigé |
| C-06 | Critique | `tools/approvals.py:57`, `tools/registry.py:388`, `api/server.py:2598, 2654`, `security.py:112` | Absence de liaison cryptographique entre l'approbation et les arguments exacts (TOCTOU/rejeu), acceptation d'approbations expirées en base SQLite, et rejeu croisé du lien `/actions/{approve_token}` après un refus `/actions/{deny_token}` | `tests/test_security_api.py::test_c06_approval_toctou_binding_and_action_token_sibling_replay` | Empreinte SHA-256 canonique `(tool_name, frozen_args)` + `consume_approved` à usage unique, HMAC complet 256 bits sur `/actions/{token}`, verrouillage par `approval_id` et rejet `409`/`410` des demandes résolues ou expirées | corrigé |
| C-07 | Critique | `tools/standard.py:119, 226, 604`, `tools/registry.py:368` | Fuite de tous les secrets `os.environ` (`JARVIS_API_TOKEN`, clés LLM, jetons OAuth) vers les sous-processus `run_command`, exécution systématique via `/bin/sh`, et contournements triviaux de `/ask` (`sudo`, `rm -r -f /`, `rm -rf .` avec `cwd="/"`, `curl \| sh`, `eval`, `python -c`) | `tests/test_security_api.py::test_c07_run_command_sanitized_env_and_ask_bypasses_blocked` | Purge des variables sensibles dans `env`, exécution directe `create_subprocess_exec` sans shell quand aucun métacaractère n'est requis, et détection contextuelle (`cwd`, `sudo`, pipes vers shell, `eval`, `-c`) dans `is_dangerous_command` | corrigé |
| C-08 | Critique | `tools/autonomy.py:182`, `mcp/client.py:120, 255, 418` | `AutonomyPolicy.is_ui_tool` autorisait tout outil préfixé `mcp_playwright_*` même lorsque `is_interactive_ui=False` (permettant à `mcp_playwright_browser_evaluate` et `browser_file_upload` de contourner `<untrusted>` et d'être auto-approuvés en mode autonome) ; absence de filtrage `LD_PRELOAD`/`NODE_OPTIONS` et descriptions MCP non nettoyées | `tests/test_security_api.py::test_c08_mcp_env_guard_and_playwright_eval_blocked_in_autonomous_untrusted` | Respect strict de `is_interactive_ui` et blocage des outils MCP dangereux dans `AutonomyPolicy`, interdiction de `LD_PRELOAD`/`PYTHONPATH`/`NODE_OPTIONS`, validation des identifiants d'outils, nettoyage des balises `</untrusted>` et plafond de notifications JSON-RPC | corrigé |
| C-09 | Élevé | `security.py:143`, `api/server.py:2148, 3441` | Aucune limitation de débit sur les échecs d'authentification `/api/*` et `/ws` (force brute locale sur `127.0.0.1:8765`), et croissance mémoire non bornée de `SlidingWindowRateLimiter._hits` | `tests/test_security_api.py::test_c09_rate_limiter_bounded_and_api_bruteforce_lockout` | Éviction automatique et plafond `max_keys` dans `SlidingWindowRateLimiter` ; compteur d'échecs d'authentification bloquant l'IP en `429` / WS `4429` après 30 échecs/min sans brider le trafic authentifié légitime | corrigé |
| C-10 | Moyen | `api/server.py:2137, 2233, 2790, 3155` | Absence de gestionnaire global d'exceptions 500 et divulgation de chemins/URLs secrètes ou de réponses OAuth brutes hors authentification | `tests/test_security_api.py::test_c10_unhandled_500_sanitizes_internal_details` | Gestionnaire global `@application.exception_handler(Exception)` générique (`500`) et nettoyage systématique des messages `502` | corrigé |

---

## Détail par entrée (Vérifié)

### C-01 — Jeton en query string et cookie de session (`api/server.py`, `security.py`)
- **Hypothèse** : Le middleware acceptait `?token=` sur toutes les routes `/api/*`, exposant le jeton dans l'historique, les journaux et potentiellement sur des routes mutantes.
- **Vérification** : Confirmé dans `api/server.py:2126`. De plus, le lanceur ouvre `/?token=...` et `tests/test_auth_flow.py` vérifie `GET /api/models?token=...`.
- **Impact réel** : Toute route `POST`/`PUT`/`DELETE` pouvait être invoquée par simple URL `?token=...` (CSRF par navigation/image si le jeton fuitait), sans alternative par cookie `HttpOnly`.
- **Correctif (avant / après)** :
  - *Avant* : `request.query_params.get("token")` accepté sur toutes les méthodes HTTP sans cookie ni `Cache-Control: no-store`.
  - *Après* : Ajout de `sign_session_cookie` / `verify_session_cookie` (HMAC-SHA256 sur sous-clé dérivée, sans stocker `JARVIS_API_TOKEN` dans le cookie), émission automatique d'un cookie `jarvis_session` (`HttpOnly`, `SameSite=Strict`, `Cache-Control: no-store, private`) lors du bootstrap `GET` du lanceur ou via `POST /api/auth/session`, et refus strict (`401`) de `?token=` sur toutes les méthodes mutantes (`POST`, `PUT`, `PATCH`, `DELETE`).
- **Test ajouté** : `tests/test_security_api.py::test_c01_query_token_get_only_and_session_cookie`.
- **Risque résiduel** : Le paramètre initial `/?token=...` passé à `--app=` par `scripts/launch_jarvis_v2.sh` reste visible brièvement dans `ps` pour le même UID Linux (voir hors périmètre `launch_jarvis_v2.sh` / `web/src/lib/authToken.ts`).

### C-02 — Authentification et bornes du webhook trading (`api/server.py`)
- **Hypothèse** : `POST /api/webhooks/trading` n'exigeait un Bearer que si `TRADER_WEBHOOK_TOKEN` était défini.
- **Vérification** : Confirmé. En mode `JARVIS_ALLOW_INSECURE=1` sans `TRADER_WEBHOOK_TOKEN`, n'importe quelle IP pouvait injecter des alertes critiques `trading.halt` sans limite de taille ni rate-limit.
- **Impact réel** : Usurpation d'alertes critiques et saturation mémoire par corps JSON arbitraire si le port est exposé (Docker / LAN).
- **Correctif (avant / après)** :
  - *Avant* : Vérification optionnelle de `TRADER_WEBHOOK_TOKEN` et lecture `await request.json()` sans limite.
  - *Après* : Vérification explicite d'un en-tête `Authorization: Bearer` (`TRADER_WEBHOOK_TOKEN`, ou repli `JARVIS_API_TOKEN`), rejet `403` de tout pair hors loopback en mode `JARVIS_ALLOW_INSECURE=1` (`503` sinon), plafond strict à 64 Ko (`413`) et limitation de débit dédiée.
- **Test ajouté** : `tests/test_security_api.py::test_c02_trading_webhook_explicit_auth_and_limits`.
- **Risque résiduel** : En mode développeur local explicite (`JARVIS_ALLOW_INSECURE=1` sur `127.0.0.1`), un processus local peut pousser un événement de trading.

### C-03 — Callback OAuth, injection d'en-tête `Host` et anti-rejeu (`api/server.py`)
- **Hypothèse** : `/api/oauth/{provider}/callback` contourne l'authentification API ; vérifier l'unicité de `state`, la liaison au fournisseur, l'expiration et le non-rejeu.
- **Vérification** : `state` était déjà supprimé par `.pop()` et lié à `provider` avec TTL 15 min, **mais** trois failles exploitables ont été identifiées : (1) `_build_oauth_redirect_uri` utilisait `request.base_url` issu de l'en-tête `Host` non fiable (permettant de détourner `redirect_uri` vers un domaine attaquant), (2) tout préfixe `/api/oauth/*` contournait l'auth API quelle que soit la méthode HTTP, (3) un même `code` pouvait être soumis une seconde fois via un autre `state` valide et l'erreur `502` divulguait `resp.text[:240]`.
- **Impact réel** : Vol potentiel de code d'autorisation OAuth si l'application OAuth autorise plusieurs URI de redirection, et fuite d'informations amont hors authentification.
- **Correctif (avant / après)** :
  - *Avant* : `f"{str(request.base_url).rstrip('/')}/api/oauth/{provider}/callback"` et exemption globale `startswith("/api/oauth/")`.
  - *Après* : Liste blanche stricte d'hôtes loopback (`_TRUSTED_REDIRECT_HOSTS`) avec repli sur `default_oauth_redirect_uri`, exemption restreinte à `GET /api/oauth/{provider}/callback`, empreinte SHA-256 anti-rejeu des codes OAuth (`_consumed_oauth_codes`), plafond de 128 états actifs, rate-limiting et assainissement des erreurs `400`/`502`.
- **Test ajouté** : `tests/test_security_api.py::test_c03_oauth_host_header_injection_and_code_replay`.
- **Risque résiduel** : Néant sur boucle locale ; si Jarvis est placé derrière un reverse proxy HTTPS public, `JARVIS_HOST` doit être configuré explicitement.

### C-04 — Stockage des jetons d'intégration et masquage d'URLs secrètes (`api/server.py`, `integrations/*`)
- **Hypothèse** : Vérifier les permissions de persistance des access/refresh tokens et leur absence des réponses API / journaux.
- **Vérification** : `_persist_env` dans `api/server.py:2781` créait `.env` via `touch()` (`0644` selon l'`umask` courant) sans jamais appeler `os.chmod(..., 0o600)`. De plus, les exceptions `httpx` dans `AppleCalendarIntegration` et `GitHubProbesIntegration` propageaient l'URL complète (incluant les jetons `.ics` iCloud privés ou clés API en query string).
- **Impact réel** : Lecture des jetons OAuth Google/Microsoft/Spotify/Notion/Home Assistant par tout processus local d'un autre utilisateur système.
- **Correctif (avant / après)** :
  - *Avant* : `core_state.env_file.touch(exist_ok=True); set_key(...)` sans `chmod`, erreurs `str(exc)` brutes.
  - *Après* : Helper `write_secret_env_0600` ouvrant le fichier avec `os.open(..., 0o600)`, appliquant `chmod 0600` avant et après `set_key`, rejetant les retours chariot/octets nuls, et masquage systématique via `redact_sensitive_text`. Correction également de `SpotifyIntegration._from_env_token = True`.
- **Test ajouté** : `tests/test_security_api.py::test_c04_env_permissions_0600_and_secret_url_redaction`.
- **Risque résiduel** : Les secrets restent stockés en clair sur disque dans `.env` (permissions `0600`) lorsque `systemd-creds` ou `keyring` ne sont pas utilisés.

### C-05 — `SecretStore.set_secret` (`security.py`)
- **Hypothèse** : En cas d'échec `keyring`, `set_secret` écrit dans `os.environ` et retourne `False` ; vérifier qu'aucun appelant ne croit à une persistance disque.
- **Vérification** : Aucun appelant dans le dépôt n'invoque actuellement `set_secret` en ignorant son booléen de retour, mais la méthode acceptait n'importe quel nom de clé et échouait silencieusement sans journalisation.
- **Impact réel** : Risque faible (perte silencieuse d'un secret au redémarrage si un futur appelant ignore la valeur de retour sur un serveur sans D-Bus).
- **Correctif (avant / après)** :
  - *Avant* : `except Exception: os.environ[name] = value; return False`.
  - *Après* : Validation du nom (`_SECRET_NAME_RE`) et rejet des octets nuls, accompagnés d'un `logger.warning` explicite (sans journaliser la valeur secrète) indiquant la volatilité mémoire.
- **Test ajouté** : `tests/test_security_api.py::test_c05_secret_store_validation_and_volatile_warning`.
- **Risque résiduel** : Faible ; le comportement de repli mémoire est documenté et signalé dans les journaux.

### C-06 — Approbations, TOCTOU et rejeu des jetons d'action (`tools/approvals.py`, `tools/registry.py`, `api/server.py`, `security.py`)
- **Hypothèse** : Vérifier qu'une approbation est liée aux arguments exacts et ne peut être rejouée ni validée après expiration.
- **Vérification** : Trois vulnérabilités confirmées :
  1. `ToolRegistry.execute` passait `args_dict` mutable sans empreinte cryptographique ni consommation atomique de l'approbation.
  2. `POST /api/approvals/{id}` appelait `db.decide_approval` (qui ne vérifie pas `expires_at` dans `memory/schema.py`) avant `ApprovalManager`, permettant d'approuver en base et de diffuser `approval.resolved` sur une demande expirée.
  3. `POST /actions/{token}` ne mémorisait que le `token` utilisé et non l'`approval_id` : après un clic sur le lien `Refuser`, le lien jumeau `Approuver` restait accepté et diffusait une approbation contradictoire.
- **Impact réel** : Contournement d'un refus utilisateur via lien secondaire, validation d'actions expirées en base et fenêtre TOCTOU sur arguments mutables.
- **Correctif (avant / après)** :
  - *Avant* : HMAC tronqué à 128 bits, pas de digest d'arguments, suivi uniquement par `token`.
  - *Après* : HMAC-SHA256 complet (64 hex), validation stricte `action in {"approve", "deny"}` et interdiction de `:`, copie profonde `frozen_args` + digest canonique SHA-256 vérifié et consommé une seule fois par `ApprovalManager.consume_approved`, suivi par `_consumed_action_approvals` et rejet `409`/`410` sur `/api/approvals/{id}`.
- **Test ajouté** : `tests/test_security_api.py::test_c06_approval_toctou_binding_and_action_token_sibling_replay`.
- **Risque résiduel** : Note pour l'auditeur Opus (`memory/schema.py`, hors périmètre Carbon) : `MemoryDatabase.decide_approval` pourrait également vérifier `expires_at > now` directement dans la requête SQL.

### C-07 — Exécution de commandes `run_command` et contournements de `/ask` (`tools/standard.py`, `tools/registry.py`)
- **Hypothèse** : Vérifier la liste noire, le `cwd`, le timeout, l'absence de `shell=True`, et l'impossibilité de contourner `/ask`.
- **Vérification** : Confirmé :
  1. `run_command` utilisait toujours `create_subprocess_shell` sans filtrer `os.environ`, exposant `JARVIS_API_TOKEN`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY` et les jetons OAuth à toute commande exécutée.
  2. En mode `/ask`, `is_dangerous_command` ignorait `cwd` (`{"command": "rm -rf .", "cwd": "/"}` passait en niveau 2 sans approbation !), ainsi que `sudo`, `rm -r -f /`, `curl ... | sh`, `eval` et `python -c "shutil.rmtree(...)"`.
- **Impact réel** : Exfiltration triviale de tous les secrets de Jarvis via `env` et exécution sans approbation de commandes destructrices ou privilégiées sous `/ask`.
- **Correctif (avant / après)** :
  - *Avant* : `create_subprocess_shell(cmd_to_run, cwd=...)` avec `os.environ` complet et regex limitées aux chemins absolus explicites.
  - *Après* : Environnement assaini `_build_sanitized_command_env()`, exécution directe via `create_subprocess_exec` (sans shell) dès qu'aucun opérateur shell n'est présent, et détection dans `is_dangerous_command(command, cwd=...)` de `sudo`/`pkexec`/`doas`/`su`, des drapeaux séparés `rm -r -f`, des suppressions relatives depuis un `cwd` critique (`/`, `$HOME`, `/etc`, etc.), des pipes vers shell (`| sh`), `eval` et interpréteurs `-c`.
- **Test ajouté** : `tests/test_security_api.py::test_c07_run_command_sanitized_env_and_ask_bypasses_blocked`.
- **Risque résiduel** : Par conception produit (vérifiée par `tests/test_ask_command.py`), lorsque l'utilisateur choisit le mode `Auto` (sans `/ask`), les commandes des sous-agents s'exécutent sans confirmation humaine (hors contexte `<untrusted>`, où `run_command` reste totalement bloqué).

### C-08 — Cycle de vie et confinement des serveurs MCP (`mcp/client.py`, `tools/autonomy.py`)
- **Hypothèse** : Vérifier `create_subprocess_exec`, `config/mcp.yaml`, l'environnement transmis, les timeouts et la résistance à un serveur MCP malveillant.
- **Vérification** : Faille critique découverte dans `tools/autonomy.py:187` : `is_ui_tool` retournait `True` pour **tout** outil commençant par `mcp_playwright_`, ignorant l'attribut `is_interactive_ui=False` calculé par `mcp/client.py` ! Ainsi, `mcp_playwright_browser_evaluate` et `mcp_playwright_browser_file_upload` contournaient `untrusted_context_guard` et étaient auto-approuvés en mode autonome. De plus, `config.env` autorisait `LD_PRELOAD`/`NODE_OPTIONS`/`PYTHONPATH`, les descriptions d'outils MCP pouvaient injecter `</untrusted>`, et un flux infini de notifications JSON-RPC sans `id` saturait la boucle jusqu'au timeout.
- **Impact réel** : Exécution arbitraire de JavaScript dans le navigateur ou téléversement de fichiers locaux déclenchés par une simple injection de prompt sur une page web lorsque le serveur MCP Playwright est actif en mode autonome.
- **Correctif (avant / après)** :
  - *Avant* : `tool_name.startswith(("browser_", "desktop_", "mcp_playwright_"))` dans `autonomy.py` et copie brute de `config.env` / `description`.
  - *Après* : Pour tout outil `mcp_*`, `is_ui_tool` exige `is_interactive_ui=True` et bloque explicitement les fragments dangereux dans `can_bypass_untrusted_guard` et `should_auto_approve` ; `mcp/client.py` bloque `LD_PRELOAD`/`LD_LIBRARY_PATH`/`PYTHONPATH`/`NODE_OPTIONS`/`BASH_ENV`, valide les noms d'outils (`_MCP_IDENT_RE`), nettoie/tronque les descriptions et plafonne les notifications intercalées à 256.
- **Test ajouté** : `tests/test_security_api.py::test_c08_mcp_env_guard_and_playwright_eval_blocked_in_autonomous_untrusted`.
- **Risque résiduel** : Un binaire MCP explicitement activé par l'utilisateur dans `config/mcp.yaml` s'exécute toujours avec les droits fichiers de l'utilisateur courant (confinement OS/bwrap hors périmètre Carbon).

### C-09 — Limitation de débit anti-force brute (`security.py`, `api/server.py`)
- **Hypothèse** : Vérifier si `SlidingWindowRateLimiter` protège l'authentification API ou uniquement `/actions/*`.
- **Vérification** : Confirmé : seul `/actions/*` était protégé, et `_hits` dans `SlidingWindowRateLimiter` ne supprimait jamais les clés expirées (fuite mémoire).
- **Impact réel** : Force brute illimitée à haute fréquence sur `JARVIS_API_TOKEN` via HTTP `/api/*` ou WebSocket `/ws`, et gonflement mémoire sur clés multiples.
- **Correctif (avant / après)** :
  - *Avant* : Aucun rate-limiter sur `/api/*`, `/ws`, `/api/oauth/*/callback`, `/api/webhooks/trading` ; `defaultdict(deque)` sans éviction.
  - *Après* : Éviction automatique et plafond `max_keys=4096` dans `SlidingWindowRateLimiter` ; ajout de `_auth_fail_limiter` (blocage `429` / WS `4429` après 30 échecs d'authentification en 60 s sans impacter les requêtes valides), `_oauth_rate_limiter` et `_webhook_rate_limiter`.
- **Test ajouté** : `tests/test_security_api.py::test_c09_rate_limiter_bounded_and_api_bruteforce_lockout`.
- **Risque résiduel** : Sur boucle locale `127.0.0.1`, tous les processus locaux partagent la même IP pair ; un processus local malveillant peut provoquer un verrouillage temporaire de 60 s des tentatives d'authentification (déni de service local temporaire, sans compromission du secret).

### C-10 — Fuites d'information hors authentification (`api/server.py`)
- **Hypothèse** : Vérifier les messages 500, `/api/doctor` et `/api/version` hors authentification.
- **Vérification** : `/api/doctor` et `/api/version` sont bien protégés par `JARVIS_API_TOKEN` dès qu'il est défini (seul `/api/health` est public et n'expose que `status`, `service`, `version`, requis par `tests/test_backup_doctor.py`). En revanche, aucun gestionnaire global d'erreurs 500 n'était enregistré et `/api/oauth/{provider}/callback` (non authentifié) exposait les noms de variables d'environnement ainsi que le corps brut `resp.text`.
- **Impact réel** : Divulgation potentielle de chemins internes ou de réponses d'erreur OAuth amont sur exception imprévue.
- **Correctif (avant / après)** :
  - *Avant* : Exceptions non gérées laissées au gestionnaire par défaut et `resp.text[:240]` renvoyé dans le détail `502`.
  - *Après* : Ajout de `@application.exception_handler(Exception)` renvoyant systématiquement `{"detail": "Erreur interne du serveur."}` (`500`) et assainissement des erreurs `502` OAuth/voix/sync.
- **Test ajouté** : `tests/test_security_api.py::test_c10_unhandled_500_sanitizes_internal_details`.
- **Risque résiduel** : En mode explicitement non authentifié (`JARVIS_ALLOW_INSECURE=1`), `/api/doctor` et `/api/version` sont accessibles sur `127.0.0.1` conformément au mode développeur.

---

## Ce que je n'ai pas pu vérifier / Hors périmètre Carbon

1. **Passage initial du jeton en ligne de commande navigateur (`scripts/launch_jarvis_v2.sh` et `packaging/launcher.py`, périmètre Opus)** : Le lanceur ouvre Chromium avec `--app="http://localhost:8765/?token=${TOKEN}"`. Bien que le serveur émette désormais immédiatement un cookie `HttpOnly` (`jarvis_session`) avec `Cache-Control: no-store` et que `web/src/lib/authToken.ts` nettoie l'URL côté client, l'argument de ligne de commande du processus navigateur reste lisible dans `/proc/<pid>/cmdline` par les processus du même utilisateur Linux tant que le lanceur n'utilise pas un fichier ou jeton d'échange éphémère à usage unique.
2. **Stockage du jeton côté frontend (`web/src/lib/authToken.ts`, périmètre Neon)** : Le frontend stocke encore le jeton dans `sessionStorage` (et optionnellement `localStorage`). Le backend accepte désormais le cookie `HttpOnly` `jarvis_session` sur `/api/*` et `/ws`, ce qui permettra au frontend d'abandonner complètement le stockage JS accessible en cas de XSS.
3. **Vérification SQL d'expiration dans `MemoryDatabase.decide_approval` (`packages/core/jarvis_core/memory/schema.py`, périmètre Opus)** : La vérification d'expiration est désormais appliquée en amont dans `api/server.py` et `ApprovalManager`, mais la requête SQL `UPDATE approvals` dans `memory/schema.py` ne filtre pas encore `expires_at` directement au niveau SQLite.
4. **Tests E2E OAuth réels contre Google, Microsoft et Spotify** : Vérifiés par tests d'intégration simulés (`TestClient` + mocks `httpx`), sans appels réseau vers les serveurs d'autorisation externes.
