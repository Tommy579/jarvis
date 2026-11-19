"""Tests des routines planifiees : cablage dans l'application, textes et distribution.

Ces tests verrouillent trois defauts constates :
- le planificateur de routines n'etait instancie nulle part (aucune consolidation nocturne) ;
- les notifications de niveau 1 et 2 n'atteignaient aucune surface (canaux « sphere »
  inconnus de la file WebSocket) ;
- les textes de briefing n'existaient pas.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jarvis_core.api.server import (
    JarvisCoreState,
    _build_routine_scheduler,
    _ingest_calendar_events,
    _minutes_until,
    create_app,
)
from jarvis_core.attention.engine import AttentionDecision
from jarvis_core.memory.schema import MemoryDatabase
from jarvis_core.runtime import build_notification_dispatcher
from jarvis_core.scheduler.briefings import (
    agenda_from_events,
    build_evening_review,
    build_morning_briefing,
)
from jarvis_shared.models import NotificationRecord, UrgencyLevel
from jarvis_shared.rest_models import AlertRecord

EXPECTED_JOBS = {
    "morning_briefing",
    "pre_meeting_check",
    "evening_review",
    "nightly_consolidation",
}


def _isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> JarvisCoreState:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JARVIS_ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    monkeypatch.setenv("JARVIS_ALLOW_INSECURE", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return JarvisCoreState(
        data_dir=tmp_path / "data",
        config_dir=Path(__file__).resolve().parents[1] / "config",
        legacy_profile_path=tmp_path / "missing.json",
    )


def test_l_application_enregistre_les_routines(tmp_path, monkeypatch) -> None:
    state = _isolated_state(tmp_path, monkeypatch)
    app = create_app(state)
    with TestClient(app):
        scheduler = state.scheduler
        assert scheduler is not None, "Le planificateur n'est pas instancie au demarrage"
        assert EXPECTED_JOBS.issubset(set(scheduler.registered_jobs))
        # En contexte de test isole on enregistre sans demarrer (pas de jobs reels).
        assert scheduler.scheduler.running is False
    assert state.scheduler is None, "Le planificateur doit etre arrete a l'arret de l'app"


def test_les_routines_consomment_le_runtime(tmp_path, monkeypatch) -> None:
    """Chaque routine doit etre une coroutine reelle, pas un stub."""
    state = _isolated_state(tmp_path, monkeypatch)
    create_app(state)
    scheduler = _build_routine_scheduler(state)
    jobs = {job.id: job for job in scheduler.scheduler.get_jobs()}
    for job_id in EXPECTED_JOBS:
        assert asyncio.iscoroutinefunction(jobs[job_id].func)


def test_briefing_du_matin_resume_les_attentes(tmp_path, monkeypatch) -> None:
    _isolated_state(tmp_path, monkeypatch)
    db = MemoryDatabase(tmp_path / "briefing.db")
    db.create_approval(
        tool_name="run_command",
        description="Redemarrer nginx",
        args={"command": "systemctl restart nginx"},
        risk=2,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    )
    db.add_notification(channel="web", urgency=2, message="Reunion dans 10 min", reason="test")
    start = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    text = build_morning_briefing(db, agenda=[{"summary": "Dentiste", "start": start}])
    assert "1 approbation(s) en attente" in text
    assert "1 alerte(s) non acquittee(s)" in text
    assert "Dentiste" in text
    assert "Bonjour" in text


def test_bilan_du_soir_mentionne_le_cout(tmp_path, monkeypatch) -> None:
    _isolated_state(tmp_path, monkeypatch)
    db = MemoryDatabase(tmp_path / "review.db")
    text = build_evening_review(db)
    assert text.startswith("Bilan du")
    assert "Cout LLM" in text


def test_agenda_depuis_les_evenements_de_connecteur() -> None:
    events = [
        {
            "source": "google_calendar",
            "type": "calendar.upcoming",
            "payload": {"summary": "Point equipe", "start": "2026-11-19T09:00:00+01:00"},
        },
        {"source": "gmail", "type": "mail.received", "payload": {"subject": "Facture"}},
    ]
    agenda = agenda_from_events(events)
    assert agenda == [{"summary": "Point equipe", "start": "2026-11-19T09:00:00+01:00"}]


def _decision(urgency: UrgencyLevel, channels: list[str]) -> AttentionDecision:
    return AttentionDecision(
        urgency=urgency,
        channels=channels,
        message="Message de test",
        reason="Test",
        dedup_key="test:1",
    )


def test_notification_de_niveau_2_atteint_l_interface() -> None:
    """Une notification NORMAL doit arriver dans la file de l'UI (canaux sphere/toast)."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    sent: list[tuple[str, str]] = []

    class _Sender:
        def __init__(self, name: str) -> None:
            self.name = name

    def _schedule(sender, text, urgency, inline_keyboard=None) -> None:
        sent.append((sender.name, text))

    dispatch = build_notification_dispatcher({"telegram": _Sender("telegram")}, queue, _schedule)
    notif = NotificationRecord(
        channel="sphere,toast",
        urgency=UrgencyLevel.NORMAL,
        message="Briefing du matin",
        reason="Routine",
        dedup_key="routine:1",
    )
    dispatch(notif, _decision(UrgencyLevel.NORMAL, ["sphere", "toast"]))

    assert queue.qsize() == 1
    assert sent == [], "Aucun canal externe ne doit etre utilise pour le niveau 1"


def test_notification_de_niveau_3_envoie_telegram_et_ui() -> None:
    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    sent: list[tuple[str, str]] = []

    class _Sender:
        def __init__(self, name: str) -> None:
            self.name = name

    def _schedule(sender, text, urgency, inline_keyboard=None) -> None:
        sent.append((sender.name, text))

    dispatch = build_notification_dispatcher({"telegram": _Sender("telegram")}, queue, _schedule)
    notif = NotificationRecord(
        channel="telegram",
        urgency=UrgencyLevel.URGENT,
        message="Serveur tombe",
        reason="Sonde",
        dedup_key="probe:1",
    )
    dispatch(notif, _decision(UrgencyLevel.URGENT, ["telegram"]))

    assert queue.qsize() == 1
    assert [name for name, _ in sent] == ["telegram"]
    assert "Serveur tombe" in sent[0][1]


def test_alertes_non_acquittees_comptees(tmp_path) -> None:
    db = MemoryDatabase(tmp_path / "alerts.db")
    db.add_notification(channel="web", urgency=3, message="Alerte 1", reason="t")
    alerts = db.list_alerts(limit=10)
    assert isinstance(alerts[0], AlertRecord)
    assert alerts[0].status == "sent"


def test_minutes_until_parse_les_dates_iso() -> None:
    now = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    assert _minutes_until("2026-11-19T10:30:00+00:00", now=now) == 30
    assert _minutes_until("2026-11-19T09:00:00Z", now=now) == -60
    assert _minutes_until("2026-11-19", now=now) is None  # date seule : inexploitable
    assert _minutes_until("pas une date", now=now) is None
    assert _minutes_until("", now=now) is None


def test_ingestion_agenda_filtre_la_fenetre(tmp_path, monkeypatch) -> None:
    """Seuls les rendez-vous a moins d'une heure sont pousses vers l'attention."""
    state = _isolated_state(tmp_path, monkeypatch)
    state.ensure_runtime()
    soon = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    late = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    events = [
        {
            "source": "google_calendar",
            "type": "calendar.upcoming",
            "payload": {"summary": "Point equipe", "start": soon},
        },
        {
            "source": "google_calendar",
            "type": "calendar.upcoming",
            "payload": {"summary": "Dentiste", "start": late},
        },
        {
            "source": "gmail",
            "type": "mail.received",
            "payload": {"subject": "Facture", "start": soon},
        },
    ]
    assert asyncio.run(_ingest_calendar_events(state, events)) == 1
