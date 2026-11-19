"""Moteur d'attention, bus d'evenements, detection de presence et machine d'escalade."""

from .bus import EventBus
from .engine import AttentionDecision, AttentionEngine
from .presence import PresenceDetector, PresenceSnapshot

__all__ = [
    "AttentionDecision",
    "AttentionEngine",
    "EventBus",
    "PresenceDetector",
    "PresenceSnapshot",
]
