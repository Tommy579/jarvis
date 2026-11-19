"""Moteur d'attention : règles déterministes, triage LLM par lots (5 min), heartbeat (30 min) et escalade."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from jarvis_shared.models import (
    Message,
    NotificationRecord,
    Role,
    SystemEvent,
    UrgencyLevel,
    utc_now_iso,
)
from jarvis_shared.notifications import NotificationOrder, NotificationPolicy

from ..notifications.orders import match_orders, record_order_fired
from ..notifications.policy import PolicyStore
from .presence import PresenceDetector, PresenceSnapshot


@dataclass
class AttentionDecision:
    urgency: UrgencyLevel
    channels: list[str]
    message: str
    reason: str
    dedup_key: str
    escalate_to_call_after_seconds: int | None = None
    retry_interval_seconds: int | None = None
    deferred_to_digest: bool = False


class AttentionEngine:
    """Décide quand et comment interrompre l'utilisateur, avec déduplication et plafond d'appels."""

    def __init__(
        self,
        presence: PresenceDetector,
        storage: Any = None,
        router: Any = None,
        max_calls_per_day: int = 3,
        vip_contacts: list[str] | None = None,
        on_dispatch: Callable[[NotificationRecord, AttentionDecision], Any] | None = None,
        policy_store: PolicyStore | None = None,
        policy: NotificationPolicy | None = None,
    ):
        self.presence = presence
        self.storage = storage
        self.router = router
        self.max_calls_per_day = max_calls_per_day
        self.vip_contacts = {c.lower() for c in (vip_contacts or ["maman", "mere"])}
        self.on_dispatch = on_dispatch
        self.policy_store = policy_store
        self._policy = policy

        self.digest_queue: list[SystemEvent] = []
        self.pending_triage_batch: list[SystemEvent] = []
        self.active_alerts: dict[str, NotificationRecord] = {}
        self.noisy_sources: set[str] = set()
        self._calls_today_date: str = ""
        self._calls_today_count: int = 0
        self._last_call_ts: float = 0.0

    @property
    def current_policy(self) -> NotificationPolicy:
        if self._policy is not None:
            return self._policy
        if self.policy_store is not None:
            return self.policy_store.get()
        return NotificationPolicy()

    def _get_calls_today(self) -> dict[str, Any]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.storage is not None and hasattr(self.storage, "get_setting"):
            raw = self.storage.get_setting("calls_today")
            if raw:
                try:
                    data = json.loads(raw)
                    if data.get("date") == today:
                        return data
                except Exception:
                    pass
            return {"date": today, "count": 0, "last_ts": 0.0}

        if self._calls_today_date != today:
            self._calls_today_date = today
            self._calls_today_count = 0
            self._last_call_ts = 0.0
        return {
            "date": self._calls_today_date,
            "count": self._calls_today_count,
            "last_ts": self._last_call_ts,
        }

    def _can_place_call(
        self, urgency: UrgencyLevel, policy: NotificationPolicy | None = None
    ) -> bool:
        pol = policy or self.current_policy
        if not pol.enabled:
            return False

        if urgency >= UrgencyLevel.CRITICAL:
            if pol.emergency_override:
                return True
            return pol.allow_emergency_calls or pol.allow_calls

        if not pol.allow_calls:
            return False

        calls_info = self._get_calls_today()
        daily_limit = pol.max_calls_per_day if pol else self.max_calls_per_day
        if calls_info.get("count", 0) >= daily_limit:
            return False

        now_ts = time.time()
        last_ts = float(calls_info.get("last_ts", 0.0) or 0.0)
        min_sec = pol.min_seconds_between_calls if pol else 1800
        if last_ts > 0 and (now_ts - last_ts) < min_sec:
            return False

        return True

    def _record_call_placed(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now_ts = time.time()
        calls_info = self._get_calls_today()
        new_count = int(calls_info.get("count", 0)) + 1
        data = {"date": today, "count": new_count, "last_ts": now_ts}

        if self.storage is not None and hasattr(self.storage, "set_setting"):
            self.storage.set_setting("calls_today", json.dumps(data))

        self._calls_today_date = today
        self._calls_today_count = new_count
        self._last_call_ts = now_ts

    def evaluate_deterministic_rule(self, event: SystemEvent) -> tuple[UrgencyLevel | None, str]:
        """Premier filtre gratuit sans LLM pour les cas connus."""
        if event.source in self.noisy_sources:
            return UrgencyLevel.INFO, "Source marquee trop derangeante par l'utilisateur"

        if event.event_type == "home.alarm":
            return UrgencyLevel.CRITICAL, "Alarme critique domicile"

        if event.event_type == "trading.halt":
            return UrgencyLevel.URGENT, "Alerte ou erreur sur le TraderBot"

        if event.event_type == "trading.orders":
            return UrgencyLevel.IMPORTANT, "Ordres executes par le TraderBot"

        if event.event_type == "trading.cycle":
            if event.payload.get("skipped"):
                return UrgencyLevel.INFO, "Cycle TraderBot ignore (marche ferme)"
            return UrgencyLevel.NORMAL, "Cycle TraderBot termine"

        if event.event_type == "probe.failed":
            failures = int(event.payload.get("consecutive_failures", 1))
            if failures >= 3:
                return UrgencyLevel.URGENT, f"Sonde serveur en echec ({failures} essais)"
            return UrgencyLevel.NORMAL, "Echec ponctuel de sonde"

        if event.event_type == "calendar.upcoming":
            mins = int(event.payload.get("minutes_before", 30))
            if mins <= 15:
                return UrgencyLevel.IMPORTANT, f"Reunion dans {mins} min"
            return UrgencyLevel.NORMAL, f"Evenement dans {mins} min"

        if event.event_type == "routine.morning_briefing":
            return UrgencyLevel.NORMAL, "Briefing du matin"

        if event.event_type == "routine.evening_review":
            return UrgencyLevel.NORMAL, "Bilan du soir"

        if event.event_type in ("mail.received", "message.received"):
            sender = str(event.payload.get("sender", "")).lower()
            text = str(event.payload.get("text", "") or event.payload.get("subject", "")).lower()
            is_vip = any(v in sender for v in self.vip_contacts)
            has_urgent_kw = any(
                kw in text for kw in ("urgent", "rappelle", "secours", "hopital", "panne")
            )
            if is_vip and has_urgent_kw:
                return UrgencyLevel.URGENT, f"Contact VIP ({sender}) avec mention urgente"
            if is_vip:
                return UrgencyLevel.IMPORTANT, f"Message d'un contact VIP ({sender})"

        return None, ""

    def decide_channels(
        self,
        urgency: UrgencyLevel,
        snap: PresenceSnapshot,
        policy: NotificationPolicy | None = None,
        now: datetime | None = None,
    ) -> tuple[UrgencyLevel, list[str], bool, int | None]:
        """Calcule l'urgence effective, les canaux, si différé au digest, et le délai d'escalade."""
        pol = policy or self.current_policy
        now_dt = now or datetime.now(timezone.utc)

        if not pol.enabled:
            return urgency, [], False, None

        effective_urgency = urgency
        if pol.snooze_until:
            try:
                snooze_dt = datetime.fromisoformat(pol.snooze_until.replace("Z", "+00:00"))
                if now_dt < snooze_dt:
                    effective_urgency = min(effective_urgency, UrgencyLevel.INFO)
            except Exception:
                pass

        quiet = snap.in_quiet_hours
        if quiet and effective_urgency < pol.quiet_allow_urgency:
            if not (effective_urgency >= UrgencyLevel.CRITICAL and pol.emergency_override):
                effective_urgency = UrgencyLevel.INFO

        if effective_urgency == UrgencyLevel.INFO:
            return effective_urgency, [], True, None

        if effective_urgency == UrgencyLevel.NORMAL:
            channels = ["sphere", "toast"] if snap.is_at_pc else ["telegram_silent"]
            return effective_urgency, channels, False, None

        if effective_urgency == UrgencyLevel.IMPORTANT:
            channels = ["sphere", "voice"] if snap.is_at_pc else ["telegram", "ntfy_high"]
            return effective_urgency, channels, False, None

        if effective_urgency == UrgencyLevel.URGENT:
            ack_timeout = 1200 if snap.in_quiet_hours else 600
            if snap.is_at_pc and not snap.in_quiet_hours:
                channels = ["voice", "sphere_alert"]
            else:
                channels = ["ntfy_high"] if snap.in_quiet_hours else ["ntfy_high", "telegram"]
            return effective_urgency, channels, False, ack_timeout

        # UrgencyLevel.CRITICAL (niveau 4)
        base_channels: list[str] = ["voice"] if snap.is_at_pc else ["ntfy_max"]
        can_call = (pol.allow_emergency_calls or pol.allow_calls) and self._can_place_call(
            effective_urgency, pol
        )
        if can_call:
            if "sms" in pol.channel_ladder:
                base_channels.append("sms")
            if "call" in pol.channel_ladder:
                base_channels.append("phone_call")
                self._record_call_placed()

        return effective_urgency, base_channels, False, 0

    def route_channels(
        self,
        urgency: UrgencyLevel,
        snap: PresenceSnapshot,
        reason: str,
        message: str,
        dedup_key: str,
    ) -> AttentionDecision:
        """Choisit les canaux et les delais d'escalade selon l'urgence, la presence et les heures calmes."""
        eff_urgency, channels, deferred, esc_sec = self.decide_channels(urgency, snap)
        if deferred:
            return AttentionDecision(
                urgency=eff_urgency,
                channels=[],
                message=message,
                reason=reason,
                dedup_key=dedup_key,
                deferred_to_digest=True,
            )
        return AttentionDecision(
            urgency=eff_urgency,
            channels=channels,
            message=message,
            reason=reason,
            dedup_key=dedup_key,
            escalate_to_call_after_seconds=esc_sec,
            retry_interval_seconds=300 if eff_urgency >= UrgencyLevel.CRITICAL else None,
        )

    async def ingest_event(
        self, event: SystemEvent, current_hour: int = 14
    ) -> AttentionDecision | None:
        """Traite un evenement : verification des ordres, regle deterministe ou mise en lot."""
        dedup_key = (
            event.dedup_key or f"{event.source}:{event.event_type}:{event.payload.get('id', '')}"
        )
        if dedup_key in self.active_alerts and self.active_alerts[dedup_key].status == "sent":
            return None

        # Vérification des ordres personnalisés persistés
        if self.storage is not None and hasattr(self.storage, "list_orders"):
            raw_orders = self.storage.list_orders(active_only=True)
            orders = [NotificationOrder(**o) for o in raw_orders]
            matched = match_orders(orders, event)
            if matched:
                best_order = max(matched, key=lambda o: o.min_urgency)
                record_order_fired(self.storage, best_order)
                urgency = UrgencyLevel(max(int(event.urgency or 0), best_order.min_urgency))
                reason = f"Ordre #{best_order.id}: {best_order.note or 'Ordre actif'}"
                summary = str(
                    event.payload.get("message") or event.payload.get("subject") or reason
                )
                msg = best_order.message_template.replace("{summary}", summary)
                event.urgency = urgency
                return self._dispatch_decision(
                    urgency, msg, reason, dedup_key, current_hour=current_hour, event=event
                )

        urgency, reason = self.evaluate_deterministic_rule(event)
        if urgency is None:
            self.pending_triage_batch.append(event)
            return None

        event.urgency = urgency
        msg = str(event.payload.get("message") or event.payload.get("subject") or reason)
        return self._dispatch_decision(
            urgency, msg, reason, dedup_key, current_hour=current_hour, event=event
        )

    def _dispatch_decision(
        self,
        urgency: UrgencyLevel,
        message: str,
        reason: str,
        dedup_key: str,
        current_hour: int = 14,
        event: SystemEvent | None = None,
    ) -> AttentionDecision:
        snap = self.presence.snapshot(current_hour=current_hour)
        decision = self.route_channels(
            urgency=urgency,
            snap=snap,
            reason=reason,
            message=message,
            dedup_key=dedup_key,
        )
        if decision.deferred_to_digest and event is not None:
            self.digest_queue.append(event)
            return decision

        if not decision.channels and not decision.deferred_to_digest:
            # Politique désactivée ou canaux vides
            return decision

        notif = NotificationRecord(
            channel=",".join(decision.channels),
            urgency=urgency,
            message=message,
            reason=reason,
            dedup_key=dedup_key,
            status="sent",
        )
        self.active_alerts[dedup_key] = notif
        if self.storage is not None:
            if hasattr(self.storage, "insert_notification"):
                self.storage.insert_notification(notif)
            elif hasattr(self.storage, "add_notification"):
                self.storage.add_notification(
                    channel=notif.channel,
                    urgency=int(notif.urgency),
                    message=notif.message,
                    reason=notif.reason,
                )
        if self.on_dispatch:
            self.on_dispatch(notif, decision)
        return decision

    async def flush_triage_batch(self, current_hour: int = 14) -> list[AttentionDecision]:
        """Envoie le lot d'evenements ambigus au modele 'triage' (toutes les 5 min)."""
        if not self.pending_triage_batch:
            return []
        batch = list(self.pending_triage_batch)
        self.pending_triage_batch.clear()

        if self.router is None:
            return []

        items_payload = [
            {
                "index": idx,
                "source": ev.source,
                "type": ev.event_type,
                "payload": ev.payload,
            }
            for idx, ev in enumerate(batch)
        ]
        prompt = (
            "Tu es le filtre d'attention de Jarvis. Classe chaque evenement ci-dessous avec une urgence de 0 a 4 "
            "(0=digest, 1=normal, 2=important, 3=urgent, 4=critique) et une explication tres courte.\n"
            f"Evenements : {json.dumps(items_payload, ensure_ascii=False)}"
        )

        decisions: list[AttentionDecision] = []
        try:
            res = await self.router.run_role("triage", [Message(role=Role.USER, content=prompt)])
            for line in res.text.splitlines():
                m = re.search(r"(\d+)\s*:\s*(\d)\s*-\s*(.+)", line)
                if m:
                    idx, u_val, reason = int(m.group(1)), int(m.group(2)), m.group(3).strip()
                    if 0 <= idx < len(batch):
                        ev = batch[idx]
                        urgency = UrgencyLevel(min(4, max(0, u_val)))
                        ev.urgency = urgency
                        msg = str(ev.payload.get("message") or ev.payload.get("subject") or reason)
                        dedup = ev.dedup_key or f"{ev.source}:{ev.event_type}:{idx}"
                        dec = self._dispatch_decision(
                            urgency, msg, reason, dedup, current_hour=current_hour, event=ev
                        )
                        decisions.append(dec)
        except Exception:
            for ev in batch:
                ev.urgency = UrgencyLevel.INFO
                dec = self._dispatch_decision(
                    UrgencyLevel.INFO,
                    str(ev.payload.get("message") or "Evenement"),
                    "Repli suite a erreur de triage",
                    ev.dedup_key or f"{ev.source}:{ev.event_type}",
                    current_hour=current_hour,
                    event=ev,
                )
                decisions.append(dec)
        return decisions

    def acknowledge(self, dedup_key_or_id: str) -> bool:
        """Acquitte une alerte active pour couper la boucle d'escalade."""
        for key, notif in list(self.active_alerts.items()):
            if key == dedup_key_or_id or notif.id == dedup_key_or_id:
                notif.status = "acked"
                notif.acked_at = utc_now_iso()
                if self.storage is not None:
                    self.storage.insert_notification(notif)
                return True
        return False

    def mark_too_noisy(self, dedup_key_or_id: str, source: str | None = None) -> None:
        """Retient qu'une source ou alerte etait trop derangeante pour abaisser son urgence future."""
        if source:
            self.noisy_sources.add(source)
        for key, notif in list(self.active_alerts.items()):
            if key == dedup_key_or_id or notif.id == dedup_key_or_id:
                notif.status = "too_noisy"
                prefix = key.split(":")[0]
                if prefix:
                    self.noisy_sources.add(prefix)
                if self.storage is not None:
                    self.storage.insert_notification(notif)

    def check_escalations(self, elapsed_seconds: float) -> list[NotificationRecord]:
        """Escalade vers l'appel telephonique les alertes de niveau 3 non acquittees apres le delai."""
        pol = self.current_policy
        if not pol.enabled or not pol.allow_calls:
            return []

        escalated: list[NotificationRecord] = []
        for key, notif in self.active_alerts.items():
            if notif.status != "sent":
                continue
            if notif.urgency == UrgencyLevel.URGENT and elapsed_seconds >= 600:
                if self._can_place_call(notif.urgency, pol):
                    self._record_call_placed()
                    notif.status = "escalated"
                    notif.channel = f"{notif.channel},phone_call"
                    if self.storage is not None:
                        self.storage.insert_notification(notif)
                    escalated.append(notif)
        return escalated

    def evaluate_heartbeat(
        self, state: dict[str, Any], current_hour: int = 14
    ) -> AttentionDecision | None:
        """Heartbeat toutes les 30 min : detecte par ex. un depart imminent alors que l'utilisateur code."""
        next_departure_mins = state.get("next_departure_in_minutes")
        user_still_working = bool(state.get("is_coding_or_active", False))
        if (
            next_departure_mins is not None
            and int(next_departure_mins) <= 35
            and user_still_working
        ):
            ev = SystemEvent(
                source="calendar",
                event_type="departure.imminent",
                payload={"minutes_left": next_departure_mins},
                dedup_key="calendar:departure_imminent",
            )
            return self._dispatch_decision(
                UrgencyLevel.IMPORTANT,
                f"Depart dans {next_departure_mins} min pour ton rendez-vous.",
                "Rappel proactif avant depart",
                "calendar:departure_imminent",
                current_hour=current_hour,
                event=ev,
            )
        return None
