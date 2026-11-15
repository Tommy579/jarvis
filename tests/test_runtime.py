"""Tests unitaires et d'integration pour omi_core.runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path

from omi_core.runtime import build_runtime
from omi_shared.models import SystemEvent

ROOT_DIR = Path(__file__).resolve().parents[1]


def test_runtime_builds_router_and_tools(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    data_dir = tmp_path / "data"
    rt = build_runtime(
        data_dir=data_dir,
        config_dir=ROOT_DIR / "config",
        allow_offline=True,
    )

    agent_route = rt.router.get_role_route("agent")
    assert agent_route.model
    assert rt.tool_registry.get("memory_search") is not None
    assert rt.tool_registry.get("list_directory") is not None
    assert rt.tool_registry.get("read_file") is not None
    assert (data_dir / "omi.db").exists()
    assert not (data_dir / "omi_memory.db").exists()


async def test_dispatch_routes_to_configured_channels(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    sent_ntfy: list[tuple[str, int]] = []
    sent_calls: list[tuple[str, int]] = []

    class _FakeChannel:
        def __init__(self, sink: list[tuple[str, int]]) -> None:
            self.sink = sink

        async def send(self, message: str, urgency: int = 1) -> dict[str, bool]:
            self.sink.append((message, urgency))
            return {"ok": True}

    rt = build_runtime(
        data_dir=tmp_path / "data",
        config_dir=ROOT_DIR / "config",
        channels={
            "ntfy_max": _FakeChannel(sent_ntfy),
            "phone_call": _FakeChannel(sent_calls),
        },
        allow_offline=True,
    )
    rt.attention.presence.update_desktop_signals(idle_seconds=600.0, session_locked=True)

    decision = await rt.attention.ingest_event(
        SystemEvent(
            source="home",
            event_type="home.alarm",
            payload={"message": "Capteur porte entree"},
        ),
        current_hour=14,
    )
    assert decision is not None
    await asyncio.sleep(0)

    assert ("Capteur porte entree", 4) in sent_ntfy
    assert ("Capteur porte entree", 4) in sent_calls
