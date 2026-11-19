"""Correspondance et cycle de vie des ordres de notification persistés."""

from __future__ import annotations

import fnmatch
import json
import logging
from datetime import datetime, timezone
from typing import Any

from jarvis_shared.models import SystemEvent, utc_now_iso
from jarvis_shared.notifications import NotificationOrder

logger = logging.getLogger(__name__)


def _filter_ok(order: NotificationOrder, event: SystemEvent) -> bool:
    if not order.filter_json or order.filter_json == "{}":
        return True
    try:
        filters = json.loads(order.filter_json)
        if not isinstance(filters, dict):
            return True
        for key, val in filters.items():
            if key == "sender_contains":
                sender = str(event.payload.get("sender", "")).lower()
                if str(val).lower() not in sender:
                    return False
            else:
                event_val = event.payload.get(key)
                if event_val is None or str(event_val).lower() != str(val).lower():
                    return False
        return True
    except Exception as exc:
        logger.debug("Erreur analyse filter_json pour ordre %s: %s", order.id, exc)
        return True


def _text_ok(order: NotificationOrder, event: SystemEvent) -> bool:
    if not order.text_match:
        return True
    needle = order.text_match.lower()
    search_space = " ".join(
        [
            str(event.payload.get("text", "")),
            str(event.payload.get("message", "")),
            str(event.payload.get("subject", "")),
            str(event.payload.get("summary", "")),
            str(event.payload),
        ]
    ).lower()
    return needle in search_space


def _is_expired(order: NotificationOrder, now: datetime | None = None) -> bool:
    if not order.expires_at:
        return False
    try:
        now_dt = now or datetime.now(timezone.utc)
        exp_dt = datetime.fromisoformat(order.expires_at.replace("Z", "+00:00"))
        return now_dt > exp_dt
    except Exception:
        return False


def match_orders(
    orders: list[NotificationOrder],
    event: SystemEvent,
    now: datetime | None = None,
) -> list[NotificationOrder]:
    """Retourne les ordres actifs correspondants à l'événement."""
    matched: list[NotificationOrder] = []
    for order in orders:
        if not order.active:
            continue
        if _is_expired(order, now):
            continue
        if order.max_fires is not None and order.fired_count >= order.max_fires:
            continue
        if not fnmatch.fnmatch(event.source.lower(), order.source_glob.lower()):
            continue
        if not fnmatch.fnmatch(event.event_type.lower(), order.event_type_glob.lower()):
            continue
        if not _filter_ok(order, event):
            continue
        if not _text_ok(order, event):
            continue
        matched.append(order)
    return matched


def record_order_fired(db: Any, order: NotificationOrder) -> NotificationOrder:
    """Met à jour le compteur d'exécutions et désactive si plafond atteint."""
    order.fired_count += 1
    order.last_fired_at = utc_now_iso()
    order.updated_at = utc_now_iso()
    if order.max_fires is not None and order.fired_count >= order.max_fires:
        order.active = False
    if db and hasattr(db, "upsert_order"):
        db.upsert_order(order.model_dump())
    return order
