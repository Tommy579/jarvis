"""Connecteur lecture seule et parseur webhook pour le TraderBot local."""

from __future__ import annotations

import json
import os
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any

DEFAULT_TRADER_STATE_DIR = Path.home() / "Documents" / "trader" / "state"


def resolve_trader_state_dir(state_dir: str | Path | None = None) -> Path:
    """Retourne le dossier d'etat du TraderBot (env TRADER_STATE_DIR ou defaut)."""
    if state_dir is not None:
        return Path(state_dir).expanduser()
    env_val = os.environ.get("TRADER_STATE_DIR", "").strip()
    if env_val:
        return Path(env_val).expanduser()
    return DEFAULT_TRADER_STATE_DIR


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.is_file() or limit <= 0:
        return []
    tail: deque[dict[str, Any]] = deque(maxlen=limit)
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    tail.append(item)
    except OSError:
        return []
    return list(tail)


def _find_latest_active_cycle(path: Path) -> dict[str, Any] | None:
    """Retourne le dernier cycle non ignore (marche ouvert) depuis history.jsonl."""
    if not path.is_file():
        return None
    latest: dict[str, Any] | None = None
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict) and not item.get("skipped", False):
                    latest = item
    except OSError:
        return None
    return latest


def read_trading_status(
    state_dir: str | Path | None = None,
    *,
    history_limit: int = 5,
    audit_limit: int = 10,
) -> dict[str, Any]:
    """Lit l'etat courant, l'historique recent et l'audit du TraderBot."""
    root = resolve_trader_state_dir(state_dir)
    if not root.is_dir():
        return {
            "ok": False,
            "error": f"Dossier d'etat TraderBot introuvable: {root}",
        }

    state = _read_json(root / "state.json")
    history_path = root / "history.jsonl"
    recent_history = _read_jsonl_tail(history_path, history_limit)
    latest_cycle = recent_history[-1] if recent_history else None
    latest_active = (
        latest_cycle
        if (latest_cycle and not latest_cycle.get("skipped", False))
        else _find_latest_active_cycle(history_path)
    )
    recent_audit = _read_jsonl_tail(root / "audit.jsonl", audit_limit)

    return {
        "ok": True,
        "state_dir": str(root),
        "state": state,
        "latest_cycle": latest_cycle,
        "latest_active_cycle": latest_active,
        "recent_history": recent_history,
        "recent_audit": recent_audit,
    }


def format_trading_webhook_event(
    event_name: str, payload: Mapping[str, Any]
) -> tuple[str, str, str | None]:
    """Transforme un payload WebhookNotifier generic en (event_type, message, dedup_key)."""
    halts = payload.get("halts") or []
    errors = payload.get("errors") or []
    orders = payload.get("orders") or []
    skipped = bool(payload.get("skipped", False))
    session_date = str(payload.get("session_date") or "").strip()
    ts = str(payload.get("timestamp") or "").strip()
    prefix = f"TraderBot ({session_date})" if session_date else "TraderBot"

    if halts or errors:
        event_type = "trading.halt"
        parts = [f"{prefix} : alerte/erreur"]
        if halts:
            parts.append(f"halts={halts}")
        if errors:
            parts.append(f"errors={errors}")
        summary = " — ".join(parts)
    elif orders:
        event_type = "trading.orders"
        order_labels = [
            f"{o.get('side', '?').upper()} {o.get('qty', '?')} {o.get('symbol', '?')} ({o.get('status', '?')})"
            for o in orders
            if isinstance(o, Mapping)
        ]
        summary = f"{prefix} : {len(orders)} ordre(s) — " + ", ".join(order_labels)
    elif skipped:
        event_type = "trading.cycle"
        reason = str(payload.get("skip_reason") or "marche ferme")
        summary = f"{prefix} : cycle ignore ({reason})"
    else:
        event_type = "trading.cycle"
        equity = payload.get("equity")
        pnl = payload.get("unrealized_pnl")
        pos_count = payload.get("positions_count")
        eq_str = f"{float(equity):,.2f} $" if isinstance(equity, (int, float)) else "?"
        pnl_str = f"{float(pnl):+,.2f} $" if isinstance(pnl, (int, float)) else "?"
        pos_str = f"{int(pos_count)}" if isinstance(pos_count, int) else "?"
        summary = f"{prefix} : {pos_str} positions, equite {eq_str}, PnL latent {pnl_str}"

    dedup_key = f"trading:{event_name}:{ts}" if ts else None
    return event_type, summary, dedup_key
