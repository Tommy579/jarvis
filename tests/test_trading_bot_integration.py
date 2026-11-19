"""Tests de l'integration TraderBot (outil trading_status + webhook /api/webhooks/trading)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jarvis_core.api.server import JarvisCoreState, create_app
from jarvis_core.attention.engine import AttentionEngine
from jarvis_core.attention.presence import PresenceDetector
from jarvis_core.integrations.trading_bot import read_trading_status
from jarvis_core.tools.integrations import IntegrationToolkit, TradingStatusArgs
from jarvis_core.tools.registry import ToolRegistry
from jarvis_shared.models import SystemEvent, UrgencyLevel


def test_read_trading_status_and_toolkit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(
        json.dumps(
            {
                "trading_day": "2026-11-12",
                "day_start_equity": 98121.27,
                "peak_equity": 100288.1,
                "halted": False,
                "stops": {"AMD": {"stop_price": 502.92}},
            }
        ),
        encoding="utf-8",
    )
    active_cycle = {
        "timestamp": "2026-11-12T20:58:43+00:00",
        "session_date": "2026-11-12",
        "skipped": False,
        "equity": 98139.54,
        "cash": 9808.6,
        "positions_count": 1,
        "unrealized_pnl": 12.5,
        "positions": [{"symbol": "AMD", "qty": 10}],
        "orders": [],
    }
    skipped_cycle = {
        "timestamp": "2026-11-14T20:58:00+00:00",
        "session_date": "2026-11-14",
        "skipped": True,
        "skip_reason": "marché fermé (week-end)",
        "equity": 0.0,
    }
    (state_dir / "history.jsonl").write_text(
        json.dumps(active_cycle) + "\n" + json.dumps(skipped_cycle) + "\n",
        encoding="utf-8",
    )
    (state_dir / "audit.jsonl").write_text(
        json.dumps({"ts": "2026-11-12T20:58:56+00:00", "event": "risk_decision"}) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("TRADER_STATE_DIR", str(state_dir))
    res = read_trading_status()
    assert res["ok"] is True
    assert res["state"]["peak_equity"] == 100288.1
    assert res["latest_cycle"]["skipped"] is True
    assert res["latest_active_cycle"]["equity"] == 98139.54
    assert len(res["recent_audit"]) == 1

    registry = ToolRegistry()
    toolkit = IntegrationToolkit()
    toolkit.register_all(registry)
    assert registry.get("trading_status") is not None


@pytest.mark.asyncio
async def test_trading_status_tool_missing_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADER_STATE_DIR", str(tmp_path / "does_not_exist"))
    toolkit = IntegrationToolkit()
    res = await toolkit.trading_status(TradingStatusArgs())
    assert res["ok"] is False


def test_attention_rules_for_trading_events() -> None:
    engine = AttentionEngine(presence=PresenceDetector())
    u_halt, _ = engine.evaluate_deterministic_rule(
        SystemEvent(source="trading_bot", event_type="trading.halt", payload={})
    )
    u_orders, _ = engine.evaluate_deterministic_rule(
        SystemEvent(source="trading_bot", event_type="trading.orders", payload={})
    )
    u_skip, _ = engine.evaluate_deterministic_rule(
        SystemEvent(source="trading_bot", event_type="trading.cycle", payload={"skipped": True})
    )
    u_norm, _ = engine.evaluate_deterministic_rule(
        SystemEvent(source="trading_bot", event_type="trading.cycle", payload={"skipped": False})
    )
    assert u_halt == UrgencyLevel.URGENT
    assert u_orders == UrgencyLevel.IMPORTANT
    assert u_skip == UrgencyLevel.INFO
    assert u_norm == UrgencyLevel.NORMAL


def test_trading_webhook_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRADER_WEBHOOK_TOKEN", raising=False)
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    monkeypatch.setenv("JARVIS_ALLOW_INSECURE", "1")
    app = create_app(state=JarvisCoreState(data_dir=tmp_path / "jarvis_data"))
    client = TestClient(app)

    # 1. Cycle avec ordre execute
    resp = client.post(
        "/api/webhooks/trading",
        json={
            "event": "cycle_trading",
            "payload": {
                "timestamp": "2026-11-16T15:00:00+00:00",
                "session_date": "2026-11-16",
                "skipped": False,
                "equity": 100500.0,
                "positions_count": 2,
                "unrealized_pnl": 250.0,
                "orders": [{"symbol": "NVDA", "side": "buy", "qty": 5, "status": "filled"}],
                "halts": [],
                "errors": [],
            },
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["event_type"] == "trading.orders"
    assert "BUY 5 NVDA" in data["message"]

    # 2. Cycle avec alerte halt
    resp_halt = client.post(
        "/api/webhooks/trading",
        json={
            "event": "cycle_trading",
            "payload": {
                "timestamp": "2026-11-16T16:00:00+00:00",
                "session_date": "2026-11-16",
                "halts": ["MAX_DAILY_LOSS"],
                "errors": [],
            },
        },
    )
    assert resp_halt.status_code == 200
    assert resp_halt.json()["event_type"] == "trading.halt"

    # 3. Verification du jeton optionnel
    monkeypatch.setenv("TRADER_WEBHOOK_TOKEN", "secret-123")
    resp_unauth = client.post(
        "/api/webhooks/trading",
        json={"event": "cycle_trading", "payload": {"timestamp": "2026-11-16T17:00:00+00:00"}},
    )
    assert resp_unauth.status_code == 401

    resp_auth = client.post(
        "/api/webhooks/trading",
        headers={"Authorization": "Bearer secret-123"},
        json={
            "event": "cycle_trading",
            "payload": {
                "timestamp": "2026-11-16T17:00:00+00:00",
                "session_date": "2026-11-16",
                "skipped": True,
                "skip_reason": "marche ferme",
            },
        },
    )
    assert resp_auth.status_code == 200
    assert resp_auth.json()["event_type"] == "trading.cycle"
