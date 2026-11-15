"""Tests pour la prise de parole proactive de Jarvis via AttentionEngine -> WebSocket."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from omi_core.api.server import OmiCoreState, create_app
from omi_core.attention.engine import AttentionDecision
from omi_shared.models import NotificationRecord, UrgencyLevel


def test_proactive_alert_delivered_over_ws(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMI_ALLOW_INSECURE", "1")
    state = OmiCoreState(data_dir=tmp_path)
    runtime = state.ensure_runtime()
    app = create_app(state)

    monkeypatch.setattr(runtime.attention.presence, "is_in_quiet_hours", lambda _h: False)

    notif = NotificationRecord(
        id="notif_test_1",
        channel="desktop",
        urgency=UrgencyLevel.URGENT,
        message="Serveur principal hors ligne.",
        reason="Sonde serveur en echec",
    )
    decision = AttentionDecision(
        urgency=UrgencyLevel.URGENT,
        channels=["desktop"],
        message=notif.message,
        reason=notif.reason,
        dedup_key="probe.failed",
    )
    runtime.attention.on_dispatch(notif, decision)
    assert not runtime.alert_queue.empty()

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            first = ws.receive_json()
            assert first["type"] == "state"
            alert_ev = ws.receive_json()
            assert alert_ev["type"] == "alert"
            assert alert_ev["payload"]["id"] == "notif_test_1"
            assert alert_ev["payload"]["urgency"] == 3
            assert alert_ev["payload"]["message"] == "Serveur principal hors ligne."


def test_proactive_alert_respects_quiet_hours_for_non_critical(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMI_ALLOW_INSECURE", "1")
    state = OmiCoreState(data_dir=tmp_path)
    runtime = state.ensure_runtime()
    app = create_app(state)

    monkeypatch.setattr(runtime.attention.presence, "is_in_quiet_hours", lambda _h: True)

    notif = NotificationRecord(
        id="notif_quiet_1",
        channel="desktop",
        urgency=UrgencyLevel.URGENT,
        message="Rappel non critique.",
        reason="Test quiet hours",
    )
    decision = AttentionDecision(
        urgency=UrgencyLevel.URGENT,
        channels=["desktop"],
        message=notif.message,
        reason=notif.reason,
        dedup_key="quiet.test",
    )
    runtime.alert_queue.put_nowait((notif, decision))

    notif_crit = NotificationRecord(
        id="notif_crit_1",
        channel="desktop",
        urgency=UrgencyLevel.CRITICAL,
        message="Alarme critique.",
        reason="Critique",
    )
    decision_crit = AttentionDecision(
        urgency=UrgencyLevel.CRITICAL,
        channels=["desktop"],
        message=notif_crit.message,
        reason=notif_crit.reason,
        dedup_key="crit.test",
    )
    runtime.alert_queue.put_nowait((notif_crit, decision_crit))

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            first = ws.receive_json()
            assert first["type"] == "state"
            alert_ev = ws.receive_json()
            assert alert_ev["type"] == "alert"
            assert alert_ev["payload"]["id"] == "notif_crit_1"
