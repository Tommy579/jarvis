"""Gestionnaire d'approbations asynchrones (expiration 2 min, PIN vocal niveau 3)."""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from jarvis_shared.models import ApprovalRequest, RiskLevel


class ApprovalManager:
    """Gere les demandes d'approbation pour les outils de niveau 2 et 3."""

    def __init__(
        self,
        ttl_seconds: float = 120.0,
        voice_pin: str | None = None,
        on_approval_requested: Callable[[ApprovalRequest], Any] | None = None,
    ):
        self.ttl_seconds = ttl_seconds
        self.voice_pin = voice_pin
        self.on_approval_requested = on_approval_requested
        self._requests: dict[str, ApprovalRequest] = {}
        self._events: dict[str, asyncio.Event] = {}

    def create_request(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        description: str,
        risk: RiskLevel,
        exact_command: str | None = None,
    ) -> ApprovalRequest:
        expires = (datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)).isoformat()
        req = ApprovalRequest(
            tool_name=tool_name,
            arguments=arguments,
            description=description,
            exact_command=exact_command,
            risk=risk,
            expires_at=expires,
        )
        self._requests[req.id] = req
        self._events[req.id] = asyncio.Event()
        if self.on_approval_requested:
            self.on_approval_requested(req)
        return req

    def resolve(
        self,
        request_id: str,
        approved: bool,
        channel: str = "web",
        provided_pin: str | None = None,
    ) -> ApprovalRequest:
        req = self._requests.get(request_id)
        if not req:
            raise KeyError(f"Demande d'approbation '{request_id}' introuvable.")

        now = datetime.now(timezone.utc)
        expires_dt = datetime.fromisoformat(req.expires_at)
        if now >= expires_dt:
            req.status = "expired"
            ev = self._events.get(request_id)
            if ev:
                ev.set()
            return req

        # Au telephone, une action de niveau 3 exige un code PIN vocal valide
        if approved and channel == "phone" and req.risk >= RiskLevel.DANGEROUS:
            if (
                not self.voice_pin
                or not provided_pin
                or not hmac.compare_digest(str(self.voice_pin), str(provided_pin).strip())
            ):
                req.status = "denied"
                ev = self._events.get(request_id)
                if ev:
                    ev.set()
                return req

        req.status = "approved" if approved else "denied"
        ev = self._events.get(request_id)
        if ev:
            ev.set()
        return req

    async def wait_for_decision(self, request_id: str, timeout: float | None = None) -> str:
        req = self._requests.get(request_id)
        ev = self._events.get(request_id)
        if not req or not ev:
            return "expired"
        wait_secs = timeout if timeout is not None else self.ttl_seconds
        try:
            await asyncio.wait_for(ev.wait(), timeout=wait_secs)
        except asyncio.TimeoutError:
            req.status = "expired"
        return req.status

    def list_pending(self) -> list[ApprovalRequest]:
        now = datetime.now(timezone.utc)
        pending: list[ApprovalRequest] = []
        for req in self._requests.values():
            if req.status == "pending":
                if now >= datetime.fromisoformat(req.expires_at):
                    req.status = "expired"
                else:
                    pending.append(req)
        return pending
