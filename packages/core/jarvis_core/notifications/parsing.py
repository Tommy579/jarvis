"""Analyse déterministe en langage naturel des ordres de notification et des politiques."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from jarvis_shared.notifications import NotificationOrder


def parse_notification_intent(text: str, now: datetime | None = None) -> dict[str, Any] | None:
    """Analyse une phrase en français pour détecter une intention d'ordre ou de politique."""
    clean = text.strip().lower()
    now_dt = now or datetime.now(timezone.utc)

    # 1. Ne m'appelle pas sauf en cas d'extrême urgence
    if re.search(
        r"ne\s+m['’]appelle\s+(?:pas\s+)?sauf\s+(?:en\s+cas\s+d['’])?(?:urgence\s+extr[êe]me|extr[êe]me\s+urgence)",
        clean,
    ):
        return {
            "type": "policy",
            "updates": {
                "allow_emergency_calls": True,
                "allow_calls": False,
                "call_min_urgency": 4,
            },
            "summary": "Politique mise à jour : appels coupés sauf en cas d'extrême urgence (niveau 4).",
        }

    # 2. Coupe les appels / Ne m'appelle plus
    if re.search(
        r"\b(?:coupe|d[ée]sactive|bloque)\s+les\s+appels\b|ne\s+m['’]appelle\s+plus\b|pas\s+d['’]appel\b",
        clean,
    ):
        return {
            "type": "policy",
            "updates": {"allow_calls": False},
            "summary": "Politique mise à jour : appels téléphoniques désactivés.",
        }

    # 3. Heures calmes : pas la nuit / rien après X h
    m_night = re.search(
        r"rien\s+apr[èe]s\s+(\d{1,2})\s*h|pas\s+la\s+nuit\s*(?:apr[èe]s\s+(\d{1,2})\s*h)?", clean
    )
    if m_night:
        hour = int(m_night.group(1) or m_night.group(2) or 22)
        q_start = f"{hour:02d}:00"
        return {
            "type": "policy",
            "updates": {"quiet_start": q_start},
            "summary": f"Politique mise à jour : mode silencieux dès {q_start}.",
        }

    # 4. Échelle : par SMS seulement
    if re.search(r"\b(?:par\s+sms\s+seulement|uniquement\s+par\s+sms)\b", clean):
        return {
            "type": "policy",
            "updates": {"channel_ladder": ["sms"]},
            "summary": "Politique mise à jour : canal d'alerte restreint aux SMS.",
        }

    # 5. Ordres : préviens-moi si/quand <X>
    m_order = re.search(
        r"(?:pr[ée]viens-moi|notifie-moi|avertis-moi)\s+(?:si|quand|lorsque)\s+(.+)", clean
    )
    if m_order:
        target_subject = m_order.group(1).strip()
        expires_at = None
        if "jusqu'à vendredi" in target_subject or "jusqu'a vendredi" in target_subject:
            days_ahead = (4 - now_dt.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            exp_date = (now_dt + timedelta(days=days_ahead)).replace(
                hour=23, minute=59, second=59, microsecond=0
            )
            expires_at = exp_date.isoformat()
            target_subject = re.sub(
                r"jusqu['’]à\s+vendredi|jusqu['’]a\s+vendredi", "", target_subject
            ).strip()

        order = NotificationOrder(
            text_match=target_subject,
            min_urgency=2,
            expires_at=expires_at,
            note=f"Ordre: prévenir quand '{target_subject}'",
        )
        return {
            "type": "order",
            "order": order,
            "summary": f"Ordre #{order.id} enregistré : préviendra quand '{target_subject}' survient.",
        }

    return None
