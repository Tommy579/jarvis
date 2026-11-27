"""Moteur d'attention, detection de presence et machine d'escalade."""

from .engine import AttentionDecision, AttentionEngine
from .presence import PresenceDetector, PresenceSnapshot

__all__ = [
    "AttentionDecision",
    "AttentionEngine",
    "PresenceDetector",
    "PresenceSnapshot",
]
