"""Dispatcher multi-canaux avec échelle d'escalade pour les alertes et approbations."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from jarvis_shared.models import ApprovalRequest, RiskLevel
from jarvis_shared.notifications import NotificationAttempt, NotificationPolicy

from ..security import SecretStore, sign_action_token

logger = logging.getLogger(__name__)


class ChannelDispatcher:
    """Envoie une demande d'approbation sur les canaux actifs (Telegram)."""

    def __init__(
        self,
        db: Any = None,
        secret_store: SecretStore | None = None,
        public_base_url: str = "",
        telegram_sender: Any = None,
        action_token_secret: str = "",
    ) -> None:
        self.db = db
        self.secret_store = secret_store or SecretStore()
        self.public_base_url = public_base_url.rstrip("/")
        self.telegram_sender = telegram_sender
        self.action_token_secret = action_token_secret or self.secret_store.get_secret(
            "ACTION_TOKEN_SECRET", "default-action-token-secret-for-signing"
        )

    def _ladder(self, policy: NotificationPolicy, risk: RiskLevel) -> list[tuple[str, int]]:
        ladder: list[tuple[str, int]] = []
        delay = 0
        for channel in policy.channel_ladder:
            if channel != "telegram":
                continue
            ladder.append((channel, delay))
            delay = policy.escalation_seconds.get(channel, 120)
        return ladder

    async def request_approval(self, req: ApprovalRequest, policy: NotificationPolicy) -> str:
        """Déclenche la cascade d'escalade pour une approbation."""
        for channel, delay in self._ladder(policy, req.risk):
            if delay > 0:
                for _ in range(delay):
                    if req.status != "pending":
                        return req.status
                    await asyncio.sleep(1)

            if req.status != "pending":
                return req.status

            try:
                attempt = await self._send_approval(channel, req)
                if attempt and self.db and hasattr(self.db, "insert_attempt"):
                    self.db.insert_attempt(attempt.model_dump())
                    if hasattr(self.db, "update_approval_delivery"):
                        current_delivered = getattr(req, "delivered_channels", "")
                        new_delivered = (
                            f"{current_delivered},{channel}".strip(",")
                            if current_delivered
                            else channel
                        )
                        self.db.update_approval_delivery(req.id, new_delivered)
            except Exception as exc:
                logger.warning("Échec envoi approbation sur canal %s: %s", channel, exc)

        return req.status

    async def _send_approval(
        self, channel: str, req: ApprovalRequest
    ) -> NotificationAttempt | None:
        token_approve = sign_action_token(self.action_token_secret, req.id, "approve", ttl=900)
        token_deny = sign_action_token(self.action_token_secret, req.id, "deny", ttl=900)
        base = self.public_base_url or "http://127.0.0.1:8765"
        url_approve = f"{base}/actions/{token_approve}"
        url_deny = f"{base}/actions/{token_deny}"

        if channel == "telegram":
            if not self.telegram_sender:
                logger.debug("Telegram ignoré : expéditeur non configuré.")
                return None
            text = (
                f"🚨 *Demande d'approbation*\n\n"
                f"{req.description}\n"
                f"Outil: `{req.tool_name}` (risque {req.risk})\n\n"
                f"[Accepter]({url_approve}) | [Refuser]({url_deny})"
            )
            keyboard = [
                [
                    {"text": "✅ Accepter", "callback_data": f"approve:{req.id}:yes"},
                    {"text": "❌ Refuser", "callback_data": f"approve:{req.id}:no"},
                ]
            ]
            await self.telegram_sender.send(text, urgency=2, inline_keyboard=keyboard)
            return NotificationAttempt(
                notification_id=req.id,
                approval_id=req.id,
                channel="telegram",
                target="telegram",
                status="sent",
            )

        return None
