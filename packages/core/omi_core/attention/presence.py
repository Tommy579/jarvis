"""Calcul de presence multi-signaux (KIdleTime, verrouillage session, visage local, telephone, Telegram)."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class PresenceSnapshot:
    is_at_pc: bool
    is_home: bool
    in_quiet_hours: bool
    idle_seconds: float
    session_locked: bool
    face_detected: bool


class PresenceDetector:
    """Agrege les signaux locaux et mobiles pour determiner si l'utilisateur est au PC ou absent."""

    def __init__(
        self,
        quiet_start_hour: int = 23,
        quiet_end_hour: int = 8,
        pc_idle_threshold_seconds: float = 300.0,
    ):
        self.quiet_start_hour = quiet_start_hour
        self.quiet_end_hour = quiet_end_hour
        self.pc_idle_threshold_seconds = pc_idle_threshold_seconds

        self.idle_seconds: float = 0.0
        self.session_locked: bool = False
        self.face_detected: bool = False
        self.phone_location: str = "home"
        self.last_telegram_activity_ts: float = 0.0

    def update_desktop_signals(
        self,
        idle_seconds: float | None = None,
        session_locked: bool | None = None,
        face_detected: bool | None = None,
    ) -> None:
        if idle_seconds is not None:
            self.idle_seconds = max(0.0, float(idle_seconds))
        if session_locked is not None:
            self.session_locked = bool(session_locked)
        if face_detected is not None:
            self.face_detected = bool(face_detected)

    def update_phone_location(self, location: str) -> None:
        self.phone_location = location.strip().lower()

    def record_telegram_activity(self, ts: float | None = None) -> None:
        self.last_telegram_activity_ts = ts if ts is not None else time.time()

    def is_in_quiet_hours(self, current_hour: int) -> bool:
        if self.quiet_start_hour > self.quiet_end_hour:
            return current_hour >= self.quiet_start_hour or current_hour < self.quiet_end_hour
        return self.quiet_start_hour <= current_hour < self.quiet_end_hour

    @property
    def is_at_pc(self) -> bool:
        return not self.session_locked and (
            self.idle_seconds < self.pc_idle_threshold_seconds or self.face_detected
        )

    def snapshot(self, current_hour: int = 14) -> PresenceSnapshot:
        is_at_pc = not self.session_locked and (
            self.idle_seconds < self.pc_idle_threshold_seconds or self.face_detected
        )
        is_home = is_at_pc or self.phone_location in ("home", "maison")
        in_quiet = self.is_in_quiet_hours(current_hour)
        return PresenceSnapshot(
            is_at_pc=is_at_pc,
            is_home=is_home,
            in_quiet_hours=in_quiet,
            idle_seconds=self.idle_seconds,
            session_locked=self.session_locked,
            face_detected=self.face_detected,
        )
