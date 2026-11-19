"""Construction des textes de routines (briefing du matin, bilan du soir).

Fonctions pures : elles ne parlent ni au reseau ni au LLM, ce qui permet de les tester
sans fournisseur configure. Les donnees viennent de la base SQLite locale.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

ACTIVE_GOAL_STATUSES = frozenset({"planning", "running", "verifying", "paused"})


def _pending_approvals(db: Any) -> list[Any]:
    try:
        return [a for a in db.list_approvals() if a.status == "pending"]
    except Exception:
        return []


def _unacked_alerts(db: Any) -> list[Any]:
    try:
        return [a for a in db.list_alerts(limit=100) if a.status == "sent"]
    except Exception:
        return []


def _active_goals(db: Any) -> list[Any]:
    try:
        return [g for g in db.list_goals(limit=20) if g.status in ACTIVE_GOAL_STATUSES]
    except Exception:
        return []


def _spend_last_day(db: Any) -> float:
    try:
        return round(sum(item.cost for item in db.daily_usage(days=1)), 4)
    except Exception:
        return 0.0


def _messages_today(db: Any, now: datetime) -> int:
    today = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    try:
        return sum(1 for m in db.list_messages(limit=500) if str(m.ts).startswith(today))
    except Exception:
        return 0


def _format_agenda(agenda: list[dict[str, Any]] | None) -> list[str]:
    lines: list[str] = []
    for item in agenda or []:
        summary = str(item.get("summary") or "").strip()
        start = str(item.get("start") or "").strip()
        if not summary:
            continue
        lines.append(f"- {start[:16].replace('T', ' ')} {summary}" if start else f"- {summary}")
        if len(lines) >= 5:
            break
    return lines


def build_morning_briefing(
    db: Any,
    agenda: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> str:
    """Resume du matin : approbations, alertes en attente, agenda et budget consacre."""
    ref = now or datetime.now(timezone.utc)
    approvals = _pending_approvals(db)
    alerts = _unacked_alerts(db)
    goals = _active_goals(db)
    spend = _spend_last_day(db)

    parts: list[str] = [f"Bonjour, briefing du {ref.astimezone().strftime('%d/%m')}."]
    if approvals:
        parts.append(f"{len(approvals)} approbation(s) en attente.")
    if alerts:
        parts.append(f"{len(alerts)} alerte(s) non acquittee(s).")
    if not approvals and not alerts:
        parts.append("Rien en attente de ta part.")

    agenda_lines = _format_agenda(agenda)
    if agenda_lines:
        parts.append("Agenda :\n" + "\n".join(agenda_lines))
    else:
        parts.append("Aucun evenement a venir detecte.")

    if goals:
        titles = ", ".join(g.title[:40] for g in goals[:3])
        parts.append(f"{len(goals)} objectif(s) en cours : {titles}.")
    if spend > 0:
        parts.append(f"Cout LLM des dernieres 24 h : ${spend:.4f}.")
    return "\n".join(parts)


def build_evening_review(db: Any, now: datetime | None = None) -> str:
    """Bilan du soir : activite de la journee, objectifs encore ouverts et cout."""
    ref = now or datetime.now(timezone.utc)
    messages = _messages_today(db, ref)
    goals = _active_goals(db)
    approvals = _pending_approvals(db)
    alerts = _unacked_alerts(db)
    spend = _spend_last_day(db)

    parts: list[str] = [f"Bilan du {ref.astimezone().strftime('%d/%m')}."]
    parts.append(f"{messages} message(s) echanges aujourd'hui.")
    if goals:
        parts.append(f"{len(goals)} objectif(s) encore ouvert(s).")
    if approvals:
        parts.append(f"{len(approvals)} approbation(s) toujours en attente.")
    if alerts:
        parts.append(f"{len(alerts)} alerte(s) non acquittee(s).")
    parts.append(f"Cout LLM des dernieres 24 h : ${spend:.4f}.")
    return "\n".join(parts)


def agenda_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ne garde que les evenements d'agenda d'une liste d'evenements de connecteurs."""
    agenda: list[dict[str, Any]] = []
    for ev in events or []:
        if ev.get("type") != "calendar.upcoming":
            continue
        payload = ev.get("payload") or {}
        agenda.append({"summary": payload.get("summary"), "start": payload.get("start")})
    return agenda
