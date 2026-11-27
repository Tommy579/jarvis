"""Routines planifiees APScheduler : briefings, rappels pre-reunion, bilan du soir et nuit."""

from __future__ import annotations

from typing import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


class RoutineScheduler:
    def __init__(self, timezone: str = "Europe/Paris") -> None:
        self.timezone = timezone
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        self.registered_jobs: list[str] = []

    def configure_defaults(
        self,
        morning_briefing: Callable[[], Awaitable[None]],
        pre_meeting_check: Callable[[], Awaitable[None]],
        midday_check: Callable[[], Awaitable[None]] | None,
        evening_review: Callable[[], Awaitable[None]],
        nightly_consolidation: Callable[[], Awaitable[None]],
        enable_midday: bool = False,
    ) -> None:
        self.scheduler.add_job(
            morning_briefing,
            CronTrigger(hour=8, minute=5, timezone=self.timezone),
            id="morning_briefing",
            replace_existing=True,
        )
        self.scheduler.add_job(
            pre_meeting_check,
            CronTrigger(minute="*/5", timezone=self.timezone),
            id="pre_meeting_check",
            replace_existing=True,
        )
        if enable_midday and midday_check is not None:
            self.scheduler.add_job(
                midday_check,
                CronTrigger(hour=12, minute=30, timezone=self.timezone),
                id="midday_check",
                replace_existing=True,
            )
        self.scheduler.add_job(
            evening_review,
            CronTrigger(hour=20, minute=30, timezone=self.timezone),
            id="evening_review",
            replace_existing=True,
        )
        self.scheduler.add_job(
            nightly_consolidation,
            CronTrigger(hour=3, minute=0, timezone=self.timezone),
            id="nightly_consolidation",
            replace_existing=True,
        )
        self.registered_jobs = [job.id for job in self.scheduler.get_jobs()]

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
