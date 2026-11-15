"""Modeles de donnees pour la politique de notification et les ordres durables."""

from __future__ import annotations

import uuid
from typing import Any, Literal
from pydantic import BaseModel, Field

from .models import utc_now_iso

UrgencyLiteral = Literal[0, 1, 2, 3, 4]
HighRiskLiteral = Literal[2, 3]


class NotificationPolicy(BaseModel):
    schema_version: int = 1
    enabled: bool = True
    timezone: str = "Europe/Paris"

    # Heures calmes
    quiet_start: str = "23:00"
    quiet_end: str = "08:00"
    quiet_allow_urgency: UrgencyLiteral = 3  # ce qui passe quand meme la nuit (niveau 3+ par defaut)

    # Appels
    allow_calls: bool = True
    allow_emergency_calls: bool = True  # ordre 'ne m'appelle pas sauf urgence extreme'
    call_min_urgency: UrgencyLiteral = 3  # niveau minimum pour decrocher le telephone
    call_require_pin: bool = True
    max_calls_per_day: int = 3  # niveau 4 = urgence, ne consomme pas le quota
    min_seconds_between_calls: int = 1800

    # Canaux autorises, dans l'ordre de l'echelle
    channel_ladder: list[Literal["ntfy", "telegram", "sms", "call"]] = Field(
        default_factory=lambda: ["ntfy", "telegram", "sms", "call"]
    )
    escalation_seconds: dict[str, int] = Field(
        default_factory=lambda: {"ntfy": 120, "telegram": 300, "sms": 300}
    )

    # Approbations
    approval_ttl_seconds: int = 900  # 15 min pour repondre sur mobile/telephone
    approval_escalation: bool = True
    approval_min_risk_for_call: HighRiskLiteral = 2

    # Reduction de bruit
    dedupe_window_seconds: int = 3600
    snooze_until: str | None = None
    emergency_override: bool = True  # niveau 4 ignore tout sauf enabled=False


class NotificationOrder(BaseModel):
    id: str = Field(default_factory=lambda: f"ord_{uuid.uuid4().hex[:10]}")
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    # Declencheur
    source_glob: str = "*"  # ex: 'github:*', 'build', 'calendar'
    event_type_glob: str = "*"  # ex: 'probe.failed', 'message.received'
    filter_json: str = "{}"  # ex: {"sender_contains": "maman"}
    text_match: str | None = None  # sous-chaine insensible a la casse

    # Effet
    min_urgency: int = 1
    channels_json: str = "[]"  # [] = laisser la politique decider
    message_template: str = "{summary}"

    # Cycle de vie
    expires_at: str | None = None
    max_fires: int | None = None
    fired_count: int = 0
    last_fired_at: str | None = None
    active: bool = True
    origin: Literal["user", "agent", "onboarding"] = "user"
    note: str = ""


class NotificationAttempt(BaseModel):
    id: str = Field(default_factory=lambda: f"att_{uuid.uuid4().hex[:12]}")
    notification_id: str
    approval_id: str | None = None
    order_id: str | None = None
    channel: str  # ntfy | telegram | sms | call | ws
    target: str  # topic / chat_id / numero masque
    ts: str = Field(default_factory=utc_now_iso)
    status: Literal["queued", "sent", "delivered", "failed", "answered", "expired"] = "queued"
    error: str | None = None
    external_id: str | None = None
