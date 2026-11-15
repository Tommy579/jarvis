"""Tests de la politique de notification et de son intégration dans AttentionEngine."""

from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from omi_core.attention.engine import AttentionEngine
from omi_core.attention.presence import PresenceDetector
from omi_core.memory.schema import MemoryDatabase
from omi_core.notifications.policy import PolicyStore
from omi_shared.models import SystemEvent, UrgencyLevel
from omi_shared.notifications import NotificationPolicy


@pytest.mark.asyncio
async def test_no_calls_except_extreme_emergency():
    """Politique allow_calls=False, allow_emergency_calls=True :
    Urgence 3 -> aucun canal call/sms.
    Urgence 4 -> call présent.
    """
    presence = PresenceDetector()
    policy = NotificationPolicy(
        allow_calls=False,
        allow_emergency_calls=True,
    )
    engine = AttentionEngine(presence=presence, policy=policy)

    # Urgence 3 (URGENT)
    ev3 = SystemEvent(
        source="uptime",
        event_type="probe.failed",
        payload={"consecutive_failures": 3, "message": "Service dégradé"},
        dedup_key="probe:svc_deg",
    )
    dec3 = await engine.ingest_event(ev3, current_hour=14)
    assert dec3 is not None
    assert dec3.urgency == UrgencyLevel.URGENT
    assert "phone_call" not in dec3.channels
    assert "sms" not in dec3.channels
    # Même après timeout, aucune escalade vers l'appel
    esc = engine.check_escalations(elapsed_seconds=1200)
    assert len(esc) == 0

    # Urgence 4 (CRITICAL / Extrême urgence)
    ev4 = SystemEvent(
        source="home",
        event_type="home.alarm",
        payload={"message": "Alarme intrusion"},
        dedup_key="home:alarm_1",
    )
    dec4 = await engine.ingest_event(ev4, current_hour=14)
    assert dec4 is not None
    assert dec4.urgency == UrgencyLevel.CRITICAL
    assert "phone_call" in dec4.channels


@pytest.mark.asyncio
async def test_quiet_hours_swallow_level_3():
    """2 h du matin, avec quiet_allow_urgency=4, urgence 3 -> digest, aucun envoi immédiat."""
    presence = PresenceDetector()
    policy = NotificationPolicy(
        quiet_start="23:00",
        quiet_end="08:00",
        quiet_allow_urgency=4,
    )
    engine = AttentionEngine(presence=presence, policy=policy)

    ev = SystemEvent(
        source="uptime",
        event_type="probe.failed",
        payload={"consecutive_failures": 3, "message": "API indisponible"},
        dedup_key="probe:night_api",
    )
    dec = await engine.ingest_event(ev, current_hour=2)
    assert dec is not None
    assert dec.deferred_to_digest is True
    assert dec.channels == []
    assert len(engine.digest_queue) == 1


@pytest.mark.asyncio
async def test_daily_call_budget_survives_restart():
    """Deux instances successives partageant la même base SQLite : le 4e appel non urgent est refusé."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_memory.db"
        db = MemoryDatabase(db_path)
        policy_store = PolicyStore(db)
        policy_store.update(max_calls_per_day=3, min_seconds_between_calls=0)

        presence1 = PresenceDetector()
        engine1 = AttentionEngine(presence=presence1, storage=db, policy_store=policy_store)

        # 3 appels placés via engine1
        assert engine1._can_place_call(UrgencyLevel.URGENT)
        engine1._record_call_placed()
        assert engine1._can_place_call(UrgencyLevel.URGENT)
        engine1._record_call_placed()
        assert engine1._can_place_call(UrgencyLevel.URGENT)
        engine1._record_call_placed()

        # Le 4e appel est refusé sur engine1
        assert not engine1._can_place_call(UrgencyLevel.URGENT)

        # Redémarrage : nouvelle instance avec la même base de données
        presence2 = PresenceDetector()
        engine2 = AttentionEngine(presence=presence2, storage=db, policy_store=policy_store)

        # Le plafond journalier persiste !
        assert not engine2._can_place_call(UrgencyLevel.URGENT)

        # Mais le niveau 4 (urgence extrême) passe toujours le quota
        assert engine2._can_place_call(UrgencyLevel.CRITICAL)


@pytest.mark.asyncio
async def test_policy_disabled_sends_nothing():
    """enabled=False -> channels == [] même pour une alarme niveau 4."""
    presence = PresenceDetector()
    policy = NotificationPolicy(enabled=False)
    engine = AttentionEngine(presence=presence, policy=policy)

    ev = SystemEvent(
        source="home",
        event_type="home.alarm",
        payload={"message": "Feu déclaré"},
        dedup_key="home:fire_alarm",
    )
    dec = await engine.ingest_event(ev, current_hour=14)
    assert dec is not None
    assert dec.channels == []
