"""Tests complets pour JARVIS 2.0 (contrats partages, memoire, connecteurs, canaux, onboarding, API)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from jarvis_core.api.server import JarvisCoreState, create_app
from jarvis_core.channels.telegram import (
    TelegramGateway,
    build_alert_keyboard,
    build_approval_keyboard,
)
from jarvis_core.integrations import (
    FinanceIntegration,
    GitHubProbesIntegration,
    GmailIntegration,
    GoogleCalendarIntegration,
    IntegrationRegistry,
    WeatherTransportIntegration,
)
from jarvis_core.integrations.gmail import filter_important_messages
from jarvis_core.memory.import_legacy import import_legacy_profile
from jarvis_core.memory.schema import MemoryDatabase
from jarvis_core.onboarding.service import OnboardingService
from jarvis_core.scheduler.routines import RoutineScheduler
from jarvis_core.tools.standard import StandardToolkit, extract_clean_html_text
from jarvis_desktop.platform.linux import search_files_linux
from jarvis_desktop.platform.windows import search_files_windows
from jarvis_shared.codegen import generate_typescript
from jarvis_shared.rest_models import FactCreate, FactUpdate
from jarvis_shared.ws_protocol import ClientWsEvent, ServerWsEvent
from pydantic import TypeAdapter


def test_shared_ws_contracts_and_ts_codegen() -> None:
    client_adapter = TypeAdapter(ClientWsEvent)
    server_adapter = TypeAdapter(ServerWsEvent)

    c_ev = client_adapter.validate_python(
        {"type": "user.text", "payload": {"text": "Salut Jarvis"}}
    )
    assert c_ev.type == "user.text"

    c_sensor = client_adapter.validate_python(
        {"type": "sensors.toggle", "payload": {"sensor": "mic", "paused": True}}
    )
    assert c_sensor.payload.paused is True

    s_ev = server_adapter.validate_python({"type": "state", "payload": {"value": "thinking"}})
    assert s_ev.payload.value == "thinking"

    s_appr = server_adapter.validate_python(
        {
            "type": "approval.request",
            "payload": {
                "id": "appr_1",
                "description": "Redemarrer service",
                "risk": 3,
                "expires_at": "2026-11-15T12:00:00Z",
            },
        }
    )
    assert s_appr.payload.risk == 3

    ts_path = Path(__file__).resolve().parents[1] / "web" / "src" / "types" / "generated.ts"
    assert ts_path.exists()
    assert generate_typescript() == ts_path.read_text(encoding="utf-8")


def test_memory_schema_crud_search_and_legacy_import(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "jarvis.db")
    expected_tables = {
        "facts",
        "episodes",
        "people",
        "events",
        "notifications",
        "llm_calls",
        "chat_messages",
        "approvals",
        "onboarding_answers",
    }
    assert expected_tables.issubset(db.table_names())

    created = db.create_fact(
        FactCreate(
            subject="Tom",
            predicate="editeur_prefere",
            object="Neovim",
            text="Utilise Neovim sous Arch Linux KDE Wayland.",
            category="travail",
            confidence=0.92,
        )
    )
    assert db.get_fact(created.id) is not None

    hits = db.search_facts_hybrid("Neovim Arch", limit=5)
    assert len(hits) >= 1
    assert hits[0].id == created.id

    updated = db.update_fact(
        created.id, FactUpdate(confidence=0.99, text="Utilise Neovim au quotidien.")
    )
    assert updated is not None
    assert updated.confidence == 0.99

    db.upsert_person("Maman", "Famille (VIP)", contact_ref="maman@exemple.fr")
    assert any(p.name == "Maman" for p in db.list_people())

    alert = db.add_notification("web", 3, "Batterie faible", reason="system.battery")
    acked = db.ack_alert(alert.id, too_noisy=False)
    assert acked is not None
    assert acked.status == "acked"

    exp = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    appr = db.create_approval("restart_service", "Redemarrer nginx", {"service": "nginx"}, 3, exp)
    assert db.decide_approval(appr.id, True).status == "approved"  # type: ignore[union-attr]

    db.log_llm_call("agent", "deepseek/deepseek-v4.1-flash-1017", 100, 40, 0.0005, 320.0)
    assert len(db.daily_usage()) == 1

    profile_path = tmp_path / "jarvis_profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "identity": {"name": "Tom", "language": "fr", "timezone": "Europe/Paris"},
                "work": {"current_projects": ["JARVIS 2.0"], "tech_stack": ["Python", "React"]},
                "preferences": {"communication_style": "concis"},
                "notes": [{"date": "2026-10-01", "text": "Prefere Wayland a X11."}],
            }
        ),
        encoding="utf-8",
    )
    wiki_dir = tmp_path / "wiki"
    inserted = import_legacy_profile(profile_path, db, wiki_dir)
    assert inserted >= 5
    # Pas de doublons au second import
    assert import_legacy_profile(profile_path, db, wiki_dir) == 0
    assert (wiki_dir / "moi.md").exists()
    assert (wiki_dir / "rules.md").exists()
    assert db.delete_fact(created.id) is True


def test_standard_toolkit_and_platform_adapters(tmp_path: Path) -> None:
    sample_file = tmp_path / "notes_arch.txt"
    sample_file.write_text("Configuration KDE Wayland", encoding="utf-8")

    lin_res = search_files_linux("notes_arch", root_dir=str(tmp_path), limit=10)
    assert any("notes_arch.txt" in m for m in lin_res["matches"])

    win_res = search_files_windows("notes_arch", root_dir=str(tmp_path), limit=10)
    assert any("notes_arch.txt" in m for m in win_res["matches"])

    cleaned = extract_clean_html_text(
        "<html><head><style>.x{}</style><script>alert(1)</script></head>"
        "<body><h1>Titre &amp; Suite</h1><p>Contenu utile.</p></body></html>"
    )
    assert "alert(1)" not in cleaned
    assert "Titre & Suite" in cleaned
    assert "Contenu utile." in cleaned

    db = MemoryDatabase(tmp_path / "tools.db")
    toolkit = StandardToolkit(db=db)

    read_out = asyncio.run(toolkit.execute("read_file", {"file_path": str(sample_file)}))
    assert read_out["ok"] is True
    assert "KDE Wayland" in read_out["content"]

    dir_out = asyncio.run(toolkit.execute("list_directory", {"path": str(tmp_path)}))
    assert dir_out["ok"] is True
    assert "[FILE] notes_arch.txt" in dir_out["items"]

    save_out = asyncio.run(
        toolkit.execute("memory_save", {"text": "Mot-cle unique Xylo99", "category": "test"})
    )
    assert save_out["ok"] is True

    search_out = asyncio.run(toolkit.execute("memory_search", {"query": "Xylo99"}))
    assert search_out["ok"] is True
    assert len(search_out["facts"]) == 1

    forget_out = asyncio.run(toolkit.execute("memory_forget", {"fact_id": save_out["fact"]["id"]}))
    assert forget_out["ok"] is True
    assert forget_out["deleted_count"] == 1


def test_integrations_filtering_and_finance_csv(tmp_path: Path) -> None:
    filtered = filter_important_messages(
        [
            {"id": "1", "from": "noreply@service.fr", "labelIds": ["INBOX"]},
            {"id": "2", "from": "newsletter@vip-perso.fr", "labelIds": ["INBOX"]},
            {"id": "3", "from": "promo@shop.fr", "labelIds": ["CATEGORY_PROMOTIONS"]},
            {"id": "4", "from": "collegue@entreprise.fr", "labelIds": ["INBOX"]},
        ],
        vip_senders={"vip-perso.fr"},
    )
    assert [m["id"] for m in filtered] == ["2", "4"]

    db = MemoryDatabase(tmp_path / "finance.db")
    fin = FinanceIntegration(enabled=True)
    count = fin.import_csv_text(
        "date,label,amount\n2026-11-01,Loyer,-850.00\n2026-11-02,Prime,400.00\n",
        db,
    )
    assert count == 2
    fin_facts = db.list_facts(category="finances")
    assert len(fin_facts) == 2
    assert all(f.sensitivity == "très privé" for f in fin_facts)

    registry = IntegrationRegistry(
        [
            GoogleCalendarIntegration(),
            GmailIntegration(),
            GitHubProbesIntegration(),
            WeatherTransportIntegration(enabled=True),
            fin,
        ]
    )
    assert len(registry.list_info()) == 5
    toggled = registry.toggle("gmail")
    assert toggled is not None and toggled.enabled is True


def test_telegram_whitelist_ntfy_and_scheduler() -> None:
    async def run_checks() -> None:
        gw = TelegramGateway(bot_token="tok", allowed_chat_id=12345)

        unauth = await gw.handle_text(99999, "/status")
        assert unauth.authorized is False
        assert 99999 in gw.blocked_attempts

        ok_cmd = await gw.handle_text(12345, "/status")
        assert ok_cmd.authorized is True
        assert "/status" in ok_cmd.reply_text

        bad_cmd = await gw.handle_text(12345, "/inconnue")
        assert bad_cmd.authorized is True
        assert "Commandes disponibles" in bad_cmd.reply_text

        cb_approve = await gw.handle_callback(12345, "approve:act_7:yes")
        assert cb_approve.authorized is True
        assert "approuvee" in cb_approve.reply_text

        cb_noisy = await gw.handle_callback(12345, "noisy:alert_9")
        assert cb_noisy.authorized is True

        assert build_approval_keyboard("act_7")[0][0]["callback_data"] == "approve:act_7:yes"
        assert build_alert_keyboard("alert_9")[0][1]["callback_data"] == "noisy:alert_9"

        async def noop() -> None:
            return None

        sched = RoutineScheduler(timezone="Europe/Paris")
        sched.configure_defaults(noop, noop, noop, noop, noop, enable_midday=False)
        assert sched.registered_jobs == [
            "morning_briefing",
            "pre_meeting_check",
            "evening_review",
            "nightly_consolidation",
        ]

    asyncio.run(run_checks())


def test_onboarding_10_modules_and_resume(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "onboard.db")
    db.create_fact(
        FactCreate(
            subject="Tom",
            predicate="se_nomme",
            object="Tom",
            text="Nom : Tom",
            category="identite",
        )
    )
    wiki_dir = tmp_path / "wiki"
    svc = OnboardingService(db, wiki_dir=wiki_dir)
    status = svc.get_status()
    assert status.total_modules == 10

    ident_mod = next(m for m in status.modules if m.id == "identite")
    preferred_q = next(q for q in ident_mod.questions if q.field_key == "preferred_name")
    assert preferred_q.already_known is True
    assert preferred_q.known_value == "Tom"

    first_daily = status.daily_question
    assert first_daily is not None

    updated = svc.submit_module_answers("rythme", {"wake_sleep": "07:15 / 23:15"})
    rythme_mod = next(m for m in updated.modules if m.id == "rythme")
    assert rythme_mod.answered_count >= 1
    assert (wiki_dir / "rythme.md").exists()

    # Persistance apres redemarrage du service
    restarted = OnboardingService(db, wiki_dir=wiki_dir)
    rythme_after = next(m for m in restarted.get_status().modules if m.id == "rythme")
    wake_q = next(q for q in rythme_after.questions if q.field_key == "wake_sleep")
    assert wake_q.already_known is True
    assert wake_q.known_value == "07:15 / 23:15"


def test_fastapi_endpoints_auth_and_ws(tmp_path: Path, monkeypatch) -> None:
    from jarvis_core.llm.base import LLMProvider
    from jarvis_core.runtime import build_runtime
    from jarvis_shared.models import ModelCapabilities, StreamDelta, Usage

    class _DeterministicProvider(LLMProvider):
        name = "openrouter"

        async def fetch_available_models(self) -> dict[str, ModelCapabilities]:
            return {}

        async def complete(
            self, model, messages, tools=None, fallback_models=None, temperature=0.2
        ):
            _ = (messages, tools, fallback_models, temperature)
            return StreamDelta(
                text='{"facts": []}',
                usage=Usage(model=model, provider=self.name, tokens_in=5, tokens_out=3),
            )

        async def stream(self, model, messages, tools=None, fallback_models=None, temperature=0.2):
            _ = (messages, tools, fallback_models, temperature)
            yield StreamDelta(
                text="Statut systeme nominal.",
                usage=Usage(model=model, provider=self.name, tokens_in=10, tokens_out=4),
            )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JARVIS_ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("JARVIS_ALLOW_INSECURE", "1")
    state = JarvisCoreState(
        data_dir=tmp_path / "data",
        config_dir=Path(__file__).resolve().parents[1] / "config",
        legacy_profile_path=tmp_path / "missing.json",
    )
    stub = _DeterministicProvider()
    state.runtime = build_runtime(
        data_dir=state.data_dir,
        config_dir=state.config_dir,
        database=state.db,
        providers={"openrouter": stub, "gemini": stub},
    )
    exp = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    appr = state.db.create_approval("deploy", "Deployer mise a jour", {}, 2, exp)
    alert = state.db.add_notification("web", 2, "Test alerte", reason="regle_test")

    app = create_app(state)
    client = TestClient(app)

    assert client.get("/api/health").json()["status"] == "ok"

    fact_resp = client.post(
        "/api/facts",
        json={
            "subject": "Tom",
            "predicate": "os",
            "object": "Arch Linux",
            "text": "Travaille sur Arch Linux",
            "category": "travail",
            "confidence": 0.9,
            "source": "api",
            "sensitivity": "normal",
        },
    )
    assert fact_resp.status_code == 200
    fid = fact_resp.json()["id"]
    assert client.get("/api/facts?q=Arch").status_code == 200
    assert client.delete(f"/api/facts/{fid}").json()["deleted"] is True

    put_wiki = client.put("/api/wiki/moi", json={"content": "# Profil\n\n- Arch Linux\n"})
    assert put_wiki.status_code == 200
    assert client.get("/api/wiki/moi").json()["slug"] == "moi"

    assert client.get("/api/onboarding/status").json()["total_modules"] == 10
    assert len(client.get("/api/models").json()) >= 1
    assert (
        client.put(
            "/api/models/role",
            json={"role": "agent", "model": "z-ai/glm-5.3"},
        ).json()["model"]
        == "z-ai/glm-5.3"
    )

    assert (
        client.post(f"/api/approvals/{appr.id}", json={"approved": True}).json()["status"]
        == "approved"
    )
    assert client.post(f"/api/alerts/{alert.id}/ack").json()["status"] == "acked"
    assert client.get("/api/sensors/blacklist").status_code == 200
    assert len(client.get("/api/integrations").json()) == 13

    with client.websocket_connect("/ws") as ws:
        init = ws.receive_json()
        assert init == {"type": "state", "payload": {"value": "idle"}}

        ws.send_json({"type": "user.text", "payload": {"text": "Quel est mon statut ?"}})
        ev1 = ws.receive_json()
        ev2 = ws.receive_json()
        ev3 = ws.receive_json()
        ev4 = ws.receive_json()
        assert ev1["type"] == "state" and ev1["payload"]["value"] == "thinking"
        assert ev2["type"] == "assistant.delta"
        assert "Statut systeme nominal." in ev2["payload"]["text"]
        assert "Bien recu" not in ev2["payload"]["text"]
        assert ev3["type"] == "assistant.done"
        assert ev4["type"] == "state" and ev4["payload"]["value"] == "idle"

    # Verification du rejet Bearer lorsque JARVIS_API_TOKEN est actif
    monkeypatch.setenv("JARVIS_API_TOKEN", "top-secret-token")
    assert client.get("/api/models").status_code == 401
    assert (
        client.get("/api/models", headers={"Authorization": "Bearer top-secret-token"}).status_code
        == 200
    )
    assert client.get("/api/health").status_code == 200


def test_integrations_oauth_urls_and_callback(tmp_path: Path, monkeypatch) -> None:
    import httpx

    monkeypatch.setenv("JARVIS_ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    monkeypatch.delenv("GOOGLE_OAUTH_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("SPOTIFY_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("JARVIS_ALLOW_INSECURE", "1")

    state = JarvisCoreState(
        data_dir=tmp_path / "data",
        config_dir=Path(__file__).resolve().parents[1] / "config",
        legacy_profile_path=tmp_path / "missing.json",
    )
    app = create_app(state)
    client = TestClient(app)

    integrations = {item["id"]: item for item in client.get("/api/integrations").json()}
    assert integrations["gmail"]["auth_type"] == "oauth"
    assert integrations["gmail"]["oauth_provider"] == "google"
    assert integrations["gmail"]["oauth_client_configured"] is False
    assert "developers.google.com/oauthplayground" in (integrations["gmail"]["authorize_url"] or "")
    assert integrations["google_calendar"]["auth_type"] == "oauth"
    assert integrations["google_contacts"]["auth_type"] == "oauth"
    assert integrations["spotify"]["auth_type"] == "oauth"
    assert integrations["spotify"]["authorize_url"] == "https://developer.spotify.com/dashboard"
    assert integrations["notion"]["authorize_url"] == "https://www.notion.so/my-integrations"
    assert integrations["github_probes"]["auth_type"] == "url"
    assert integrations["finance"]["auth_type"] == "csv"

    ext_oauth = client.get("/api/integrations/gmail/oauth/url")
    assert ext_oauth.status_code == 200
    assert ext_oauth.json()["mode"] == "external"
    assert "oauthplayground" in ext_oauth.json()["url"]

    # Enregistrement de Client ID / Secret seuls sans lever 400
    cfg_client = client.post(
        "/api/integrations/gmail/configure",
        json={"client_id": "g-client-id", "client_secret": "g-client-secret"},
    )
    assert cfg_client.status_code == 200
    assert cfg_client.json()["oauth_client_configured"] is True
    assert "accounts.google.com/o/oauth2/v2/auth" in (cfg_client.json()["authorize_url"] or "")

    redir_oauth = client.get("/api/integrations/gmail/oauth/url")
    assert redir_oauth.status_code == 200
    payload = redir_oauth.json()
    assert payload["mode"] == "redirect"
    parsed = urlparse(payload["url"])
    qs = parse_qs(parsed.query)
    assert parsed.netloc == "accounts.google.com"
    assert qs["client_id"] == ["g-client-id"]
    oauth_state = qs["state"][0]

    # Etat invalide rejete
    assert (
        client.get(
            "/api/oauth/google/callback?state=bad&code=abc", follow_redirects=False
        ).status_code
        == 400
    )

    class _FakeTokenResponse:
        status_code = 200
        text = "{}"

        def json(self) -> dict[str, str]:
            return {"access_token": "ya29.test-token", "refresh_token": "1//refresh"}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            _ = (args, kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            _ = (exc_type, exc, tb)

        async def post(self, url: str, data: dict[str, str]):
            assert url == "https://oauth2.googleapis.com/token"
            assert data["code"] == "auth-code-123"
            assert data["client_id"] == "g-client-id"
            assert data["client_secret"] == "g-client-secret"
            return _FakeTokenResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    # Callback accessible sans Bearer meme si JARVIS_API_TOKEN est defini
    monkeypatch.setenv("JARVIS_API_TOKEN", "top-secret-token")
    cb = client.get(
        f"/api/oauth/google/callback?state={oauth_state}&code=auth-code-123",
        follow_redirects=False,
    )
    assert cb.status_code == 302
    assert cb.headers["location"] == "/config?oauth=success&provider=google"

    after = {
        item["id"]: item
        for item in client.get(
            "/api/integrations", headers={"Authorization": "Bearer top-secret-token"}
        ).json()
    }
    for gid in ("gmail", "google_calendar", "google_contacts"):
        assert after[gid]["connected"] is True
        assert after[gid]["enabled"] is True


def test_outlook_oauth_and_apple_calendar_ics(tmp_path: Path, monkeypatch) -> None:
    from urllib.parse import parse_qs, urlparse

    import httpx
    from fastapi.testclient import TestClient
    from jarvis_core.api.server import JarvisCoreState, create_app
    from jarvis_core.integrations.apple_calendar import parse_ics_events

    monkeypatch.setenv("JARVIS_ISOLATED_TEST", "1")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JARVIS_ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv("JARVIS_ALLOW_INSECURE", "1")
    monkeypatch.delenv("MICROSOFT_CLIENT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("APPLE_CALENDAR_ICS_URL", raising=False)

    state = JarvisCoreState(
        data_dir=tmp_path / "data",
        config_dir=Path(__file__).resolve().parents[1] / "config",
        legacy_profile_path=tmp_path / "missing.json",
    )
    app = create_app(state)
    client = TestClient(app)

    integrations = {item["id"]: item for item in client.get("/api/integrations").json()}
    assert integrations["outlook_mail"]["auth_type"] == "oauth"
    assert integrations["outlook_mail"]["oauth_provider"] == "microsoft"
    assert integrations["outlook_calendar"]["auth_type"] == "oauth"
    assert integrations["apple_calendar"]["auth_type"] == "url"
    assert integrations["apple_calendar"]["authorize_url"] == "https://www.icloud.com/calendar/"

    unconf = client.get("/api/integrations/outlook_calendar/oauth/url")
    assert unconf.status_code == 200
    assert unconf.json()["mode"] == "unconfigured"

    cfg_ms = client.post(
        "/api/integrations/outlook_calendar/configure",
        json={"client_id": "ms-client-id", "client_secret": "ms-client-secret"},
    )
    assert cfg_ms.status_code == 200
    assert cfg_ms.json()["oauth_client_configured"] is True

    redir_ms = client.get("/api/integrations/outlook_calendar/oauth/url")
    assert redir_ms.status_code == 200
    assert redir_ms.json()["mode"] == "redirect"
    parsed = urlparse(redir_ms.json()["url"])
    qs = parse_qs(parsed.query)
    assert parsed.netloc == "login.microsoftonline.com"
    assert qs["client_id"] == ["ms-client-id"]
    ms_state = qs["state"][0]

    class _FakeMsTokenResponse:
        status_code = 200
        text = "{}"

        def json(self) -> dict[str, str]:
            return {"access_token": "ms-access-token", "refresh_token": "ms-refresh"}

    class _FakeMsAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            _ = (args, kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            _ = (exc_type, exc, tb)

        async def post(self, url: str, data: dict[str, str]):
            assert url == "https://login.microsoftonline.com/common/oauth2/v2.0/token"
            assert data["code"] == "ms-code-456"
            return _FakeMsTokenResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeMsAsyncClient)
    cb = client.get(
        f"/api/oauth/microsoft/callback?state={ms_state}&code=ms-code-456",
        follow_redirects=False,
    )
    assert cb.status_code == 302
    assert cb.headers["location"] == "/config?oauth=success&provider=microsoft"

    after = {item["id"]: item for item in client.get("/api/integrations").json()}
    for oid in ("outlook_mail", "outlook_calendar"):
        assert after[oid]["connected"] is True
        assert after[oid]["enabled"] is True

    cfg_apple = client.post(
        "/api/integrations/apple_calendar/configure",
        json={"url": "webcal://p12-caldav.icloud.com/published/2/demo"},
    )
    assert cfg_apple.status_code == 200
    assert cfg_apple.json()["connected"] is True

    ics_sample = (
        "BEGIN:VCALENDAR\r\n"
        "BEGIN:VEVENT\r\n"
        "SUMMARY:Point equipe\r\n"
        "DTSTART;TZID=Europe/Paris:20261116T100000\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    events = parse_ics_events(ics_sample)
    assert events == [{"summary": "Point equipe", "start": "20261116T100000"}]


def test_integration_tools_registered_in_runtime(tmp_path: Path, monkeypatch) -> None:
    import asyncio

    from jarvis_core.integrations.gmail import GmailIntegration
    from jarvis_core.runtime import build_runtime
    from jarvis_shared.models import ToolCall

    monkeypatch.setenv("GOOGLE_OAUTH_ACCESS_TOKEN", "ya29.test")
    rt = build_runtime(
        data_dir=tmp_path / "data",
        config_dir=Path(__file__).resolve().parents[1] / "config",
        allow_offline=True,
    )
    tool_names = {schema["function"]["name"] for schema in rt.tool_registry.list_schemas()}
    for expected in (
        "mail_search",
        "mail_draft",
        "mail_send",
        "calendar_list_events",
        "calendar_create_event",
    ):
        assert expected in tool_names

    async def _fake_send(self, to: str, subject: str, body: str):
        return {"ok": True, "message": {"id": "msg-1", "to": to, "subject": subject, "body": body}}

    monkeypatch.setattr(GmailIntegration, "send_message", _fake_send)
    res = asyncio.run(
        rt.tool_registry.execute(
            ToolCall(
                id="tc-mail-1",
                name="mail_send",
                arguments={"to": "tom@example.com", "subject": "Test", "body": "Coucou"},
            ),
            pre_approved=True,
        )
    )
    assert res.is_error is False
    assert res.output["ok"] is True
    assert res.output["provider"] == "google"
    assert res.output["message"]["to"] == "tom@example.com"


def test_standard_toolkit_defaults_to_root_and_run_command(tmp_path: Path) -> None:
    import asyncio

    from jarvis_core.runtime import build_runtime
    from jarvis_shared.models import ToolCall

    rt = build_runtime(
        data_dir=tmp_path / "data",
        config_dir=Path(__file__).resolve().parents[1] / "config",
        allow_offline=True,
    )
    list_res = asyncio.run(
        rt.tool_registry.execute(ToolCall(id="tc-ls", name="list_directory", arguments={}))
    )
    assert list_res.is_error is False
    assert list_res.output["path"] == "/"

    cmd_res = asyncio.run(
        rt.tool_registry.execute(
            ToolCall(id="tc-pwd", name="run_command", arguments={"command": "pwd"}),
            pre_approved=True,
        )
    )
    assert cmd_res.is_error is False
    assert cmd_res.output["cwd"] == "/"
    assert cmd_res.output["stdout"].strip() == "/"


def test_run_command_auto_and_write_file_permissions(tmp_path: Path) -> None:
    import asyncio

    from jarvis_core.runtime import build_runtime
    from jarvis_shared.models import ToolCall

    rt = build_runtime(
        data_dir=tmp_path / "data",
        config_dir=Path(__file__).resolve().parents[1] / "config",
        allow_offline=True,
    )

    # run_command est de niveau 2 (SENSITIVE) : execute avec pre_approved=True
    run_spec = rt.tool_registry.get("run_command")
    assert run_spec is not None
    assert int(run_spec.risk) == 2

    ok_cmd = asyncio.run(
        rt.tool_registry.execute(
            ToolCall(id="tc-echo", name="run_command", arguments={"command": "echo hello"}),
            pre_approved=True,
        )
    )
    assert ok_cmd.is_error is False
    assert ok_cmd.output["ok"] is True
    assert ok_cmd.output["stdout"].strip() == "hello"

    # Sans /ask, les commandes terminal sont autorisees sans approbation
    ok_no_ask = asyncio.run(
        rt.tool_registry.execute(
            ToolCall(id="tc-echo-no-ask", name="run_command", arguments={"command": "echo auto"}),
            ask_terminal_approval=False,
        )
    )
    assert ok_no_ask.is_error is False
    assert ok_no_ask.output["ok"] is True
    assert ok_no_ask.output["stdout"].strip() == "auto"

    # Avec /ask (ask_terminal_approval=True), les commandes de niveau < 3 passent sans notification,
    # tandis que les commandes de niveau 3 (DANGEROUS) declenchent une demande d'approbation.
    ok_ask_l2 = asyncio.run(
        rt.tool_registry.execute(
            ToolCall(id="tc-echo-ask", name="run_command", arguments={"command": "echo ask-l2"}),
            ask_terminal_approval=True,
        )
    )
    assert ok_ask_l2.is_error is False
    assert ok_ask_l2.output["stdout"].strip() == "ask-l2"
    assert rt.approval_queue.empty()

    for bad in ("rm -rf /", "mkfs.ext4 /dev/sda1", "shutdown -h now"):
        bad_res = asyncio.run(
            rt.tool_registry.execute(
                ToolCall(id="tc-bad", name="run_command", arguments={"command": bad}),
                ask_terminal_approval=True,
                approval_timeout=0.01,
            )
        )
        assert bad_res.is_error is True
        assert "approbation requise" in bad_res.output["error"].lower()
        req = rt.approval_queue.get_nowait()
        assert int(req.risk) == 3
        assert req.exact_command == bad

    # write_file works on normal paths without pre_approved
    target = tmp_path / "sub" / "note.txt"
    w_ok = asyncio.run(
        rt.tool_registry.execute(
            ToolCall(
                id="tc-w1",
                name="write_file",
                arguments={"file_path": str(target), "content": "jarvis"},
            )
        )
    )
    assert w_ok.output["ok"] is True
    assert target.read_text(encoding="utf-8") == "jarvis"

    # write_file refuses sensitive system paths
    w_etc = asyncio.run(
        rt.tool_registry.execute(
            ToolCall(
                id="tc-w2",
                name="write_file",
                arguments={"file_path": "/etc/passwd", "content": "nope"},
            )
        )
    )
    assert w_etc.output["ok"] is False
    assert "sensible" in w_etc.output["error"].lower()


def test_oauth_auto_refresh_on_401(tmp_path: Path, monkeypatch) -> None:
    import asyncio

    import httpx
    from jarvis_core.integrations.gmail import GmailIntegration

    env_file = tmp_path / ".env"
    env_file.write_text("GOOGLE_OAUTH_ACCESS_TOKEN=expired\n", encoding="utf-8")
    monkeypatch.setenv("JARVIS_ENV_PATH", str(env_file))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csec")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "1//refresh-tok")

    calls: list[str] = []

    class _FakeResp:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self) -> dict:
            return self._payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                req = httpx.Request("GET", "https://gmail.googleapis.com")
                raise httpx.HTTPStatusError(
                    "err", request=req, response=httpx.Response(self.status_code, request=req)
                )

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            _ = (args, kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            _ = (exc_type, exc, tb)

        async def request(self, method: str, url: str, headers: dict, **kwargs):
            _ = (method, url, kwargs)
            calls.append(headers["Authorization"])
            if headers["Authorization"] == "Bearer expired":
                return _FakeResp(401, {})
            return _FakeResp(200, {"messages": [{"id": "m-1"}]})

        async def post(self, url: str, data: dict):
            assert url == "https://oauth2.googleapis.com/token"
            assert data["refresh_token"] == "1//refresh-tok"
            return _FakeResp(200, {"access_token": "fresh-tok"})

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    gmail = GmailIntegration(access_token="expired", enabled=True)
    msgs = asyncio.run(gmail.list_messages())
    assert msgs == [{"id": "m-1"}]
    assert calls == ["Bearer expired", "Bearer fresh-tok"]
    assert gmail.access_token == "fresh-tok"
    assert "fresh-tok" in env_file.read_text(encoding="utf-8")


def test_untrusted_history_does_not_block_fresh_user_turn(tmp_path: Path) -> None:
    import asyncio

    from jarvis_core.agent.loop import AgentLoop
    from jarvis_core.tools.approvals import ApprovalManager
    from jarvis_core.tools.registry import ToolRegistry, ToolSpec
    from jarvis_shared.models import Message, RiskLevel, Role, StreamDelta, ToolCall, Usage
    from pydantic import BaseModel

    class _SendArgs(BaseModel):
        to: str

    class _MockRouter:
        def __init__(self) -> None:
            self.step = 0

        def is_role_provider_trusted_for_highly_private(self, role: str) -> bool:
            _ = role
            return True

        async def stream(self, role, messages, tools=None, temperature=0.2):
            _ = (role, messages, tools, temperature)
            self.step += 1
            if self.step == 1:
                yield StreamDelta(
                    tool_calls=[ToolCall(id="tc-1", name="mail_send", arguments={"to": "a@b.c"})],
                    usage=Usage(model="m", provider="p", tokens_in=1, tokens_out=1),
                )
            else:
                yield StreamDelta(
                    text="Envoye.",
                    usage=Usage(model="m", provider="p", tokens_in=1, tokens_out=1),
                )

    async def _fake_send(to: str) -> dict:
        return {"ok": True, "to": to}

    registry = ToolRegistry(approval_manager=ApprovalManager(ttl_seconds=0.05))
    registry.register(
        ToolSpec(
            name="mail_send",
            description="send",
            handler=_fake_send,
            args_model=_SendArgs,
            risk=RiskLevel.SENSITIVE,
        )
    )
    loop = AgentLoop(router=_MockRouter(), tool_registry=registry)
    loop.history.append(
        Message(role=Role.USER, content='<untrusted source="gmail">ancien mail</untrusted>')
    )
    res = asyncio.run(loop.run_turn("Envoie un mail test a moi-meme"))
    assert res.tool_results[0].output.get("blocked_by") != "untrusted_context_guard"
