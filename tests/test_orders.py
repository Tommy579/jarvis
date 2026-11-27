"""Tests des ordres de notification, de leur cycle de vie et du parsing en langage naturel."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jarvis_core.attention.engine import AttentionEngine
from jarvis_core.attention.presence import PresenceDetector
from jarvis_core.memory.schema import MemoryDatabase
from jarvis_core.notifications.orders import match_orders
from jarvis_core.notifications.policy import PolicyStore
from jarvis_core.tools.notifications import (
    ListMyOrdersArgs,
    NotificationToolkit,
    NotifyMeArgs,
    RevokeOrderArgs,
    SetNotificationPolicyArgs,
    ShowNotificationPolicyArgs,
)
from jarvis_shared.models import SystemEvent, UrgencyLevel
from jarvis_shared.notifications import NotificationOrder


@pytest.mark.asyncio
async def test_do_not_call_me_except_extreme_emergency_persists():
    """Vérifie la persistance après redémarrage de la consigne 'ne m'appelle pas sauf urgence extrême'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "orders_test.db"
        db = MemoryDatabase(db_path)
        store = PolicyStore(db)

        # 1. Mise à jour de la politique
        store.update(allow_calls=False, allow_emergency_calls=True)

        # 2. Redémarrage avec une nouvelle instance sur la même DB
        store2 = PolicyStore(db)
        presence = PresenceDetector()
        engine = AttentionEngine(presence=presence, storage=db, policy_store=store2)

        # 3. Urgence 3 -> pas d'appel ni de SMS
        ev3 = SystemEvent(
            source="uptime",
            event_type="probe.failed",
            payload={"consecutive_failures": 3, "message": "API lente"},
            dedup_key="probe:api_slow",
        )
        dec3 = await engine.ingest_event(ev3, current_hour=14)
        assert dec3 is not None
        assert "phone_call" not in dec3.channels
        assert "sms" not in dec3.channels

        # 4. Urgence 4 -> appel téléphonique autorisé
        ev4 = SystemEvent(
            source="home",
            event_type="home.alarm",
            payload={"message": "Alarme intrusion"},
            dedup_key="home:alarm_crit",
        )
        dec4 = await engine.ingest_event(ev4, current_hour=14)
        assert dec4 is not None
        assert "phone_call" in dec4.channels


@pytest.mark.asyncio
async def test_notify_me_build_failure_fires_once():
    """Un ordre avec max_fires=1 ne se déclenche qu'une seule fois sur deux événements successifs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "orders_fires.db"
        db = MemoryDatabase(db_path)
        presence = PresenceDetector()
        engine = AttentionEngine(presence=presence, storage=db)

        # Création de l'ordre
        order = NotificationOrder(
            source_glob="github:*",
            event_type_glob="build.failed",
            min_urgency=3,
            max_fires=1,
            note="Alerte build",
        )
        db.upsert_order(order.model_dump())

        # 1er événement : déclenche l'ordre
        ev1 = SystemEvent(
            source="github:repo",
            event_type="build.failed",
            payload={"message": "CI échouée"},
            dedup_key="build:1",
        )
        dec1 = await engine.ingest_event(ev1, current_hour=14)
        assert dec1 is not None
        assert dec1.urgency == UrgencyLevel.URGENT
        assert "Ordre #" in dec1.reason

        # L'ordre est maintenant inactif car fired_count == 1 >= max_fires
        active_orders = db.list_orders(active_only=True)
        assert active_orders == []

        # 2e événement : l'ordre ne s'applique plus
        ev2 = SystemEvent(
            source="github:repo",
            event_type="build.failed",
            payload={"message": "CI échouée à nouveau"},
            dedup_key="build:2",
        )
        dec2 = await engine.ingest_event(ev2, current_hour=14)
        # Ne match plus l'ordre, retombe sur la règle déterministe par défaut (pas d'alerte urgente d'ordre)
        if dec2:
            assert "Ordre #" not in dec2.reason


def test_order_expiration_and_matching():
    """Vérifie qu'un ordre expiré n'est pas retenu par match_orders."""
    now = datetime.now(timezone.utc)
    expired_order = NotificationOrder(
        source_glob="*",
        event_type_glob="test.event",
        expires_at=(now - timedelta(hours=1)).isoformat(),
        active=True,
    )
    future_order = NotificationOrder(
        source_glob="*",
        event_type_glob="test.event",
        expires_at=(now + timedelta(hours=1)).isoformat(),
        active=True,
    )

    ev = SystemEvent(source="test", event_type="test.event", payload={})
    matched = match_orders([expired_order, future_order], ev, now=now)
    assert len(matched) == 1
    assert matched[0].id == future_order.id


@pytest.mark.asyncio
async def test_notification_tools():
    """Vérifie le bon fonctionnement des 5 outils LLM de notification."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "tools_test.db"
        db = MemoryDatabase(db_path)
        tools = NotificationToolkit(database=db)

        # 1. notify_me
        res_create = await tools.notify_me(
            NotifyMeArgs(
                source_glob="calendar",
                event_type_glob="meeting.soon",
                text_match="Comité",
                min_urgency=2,
            )
        )
        assert res_create["ok"] is True
        order_id = res_create["order_id"]

        # 2. list_my_orders
        res_list = await tools.list_my_orders(ListMyOrdersArgs(active_only=True))
        assert res_list["ok"] is True
        assert res_list["count"] == 1
        assert res_list["orders"][0]["id"] == order_id

        # 3. revoke_order
        res_rev = await tools.revoke_order(RevokeOrderArgs(order_id=order_id))
        assert res_rev["ok"] is True
        res_list_after = await tools.list_my_orders(ListMyOrdersArgs(active_only=True))
        assert res_list_after["count"] == 0

        # 4. set_notification_policy
        res_pol = await tools.set_notification_policy(
            SetNotificationPolicyArgs(allow_calls=False, max_calls_per_day=5)
        )
        assert res_pol["ok"] is True
        assert res_pol["policy"]["allow_calls"] is False
        assert res_pol["policy"]["max_calls_per_day"] == 5

        # 5. show_notification_policy
        res_show = await tools.show_notification_policy(ShowNotificationPolicyArgs())
        assert res_show["ok"] is True
        assert res_show["policy"]["allow_calls"] is False
