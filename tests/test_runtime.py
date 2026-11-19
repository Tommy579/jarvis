"""Tests unitaires et d'integration pour jarvis_core.runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path

from jarvis_core.runtime import build_runtime
from jarvis_shared.models import SystemEvent

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
    assert (data_dir / "jarvis.db").exists()
    assert not (data_dir / "jarvis_memory.db").exists()


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


async def test_alert_state_sends_telegram_with_reason(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    sent_tg: list[tuple[str, int]] = []

    class _FakeChannel:
        def __init__(self, sink: list[tuple[str, int]]) -> None:
            self.sink = sink

        async def send(self, message: str, urgency: int = 1) -> dict[str, bool]:
            self.sink.append((message, urgency))
            return {"ok": True}

    rt = build_runtime(
        data_dir=tmp_path / "data",
        config_dir=ROOT_DIR / "config",
        channels={"telegram": _FakeChannel(sent_tg)},
        allow_offline=True,
    )

    # 1. Evenement normal (is_at_pc=True) : pas de notif Telegram
    await rt.attention.ingest_event(
        SystemEvent(
            source="trading_bot",
            event_type="trading.cycle",
            payload={"skipped": False, "message": "Cycle termine"},
            dedup_key="trading:cycle:1",
        ),
        current_hour=14,
    )
    await asyncio.sleep(0)
    assert len(sent_tg) == 0

    # 2. Alerte TraderBot (urgence 3, is_at_pc=True -> canaux voice/sphere_alert) :
    # Telegram doit quand meme recevoir la notif avec la raison
    await rt.attention.ingest_event(
        SystemEvent(
            source="trading_bot",
            event_type="trading.halt",
            payload={"message": "TraderBot : errors=['No space left on device']"},
            dedup_key="trading:halt:1",
        ),
        current_hour=14,
    )
    await asyncio.sleep(0)
    assert len(sent_tg) == 1
    msg_at_pc, urg_at_pc = sent_tg[0]
    assert urg_at_pc == 3
    assert "TraderBot" in msg_at_pc
    assert "No space left on device" in msg_at_pc

    # 3. Alerte quand absent du PC (telegram deja dans decision.channels) :
    # pas de doublon
    rt.attention.presence.update_desktop_signals(idle_seconds=600.0, session_locked=True)
    await rt.attention.ingest_event(
        SystemEvent(
            source="uptime",
            event_type="probe.failed",
            payload={"consecutive_failures": 3, "message": "API down"},
            dedup_key="probe:api:1",
        ),
        current_hour=14,
    )
    await asyncio.sleep(0)
    assert len(sent_tg) == 2
    msg_away, urg_away = sent_tg[1]
    assert urg_away == 3
    assert "Sonde serveur en echec" in msg_away
    assert "API down" in msg_away


def test_wake_check_endpoint(tmp_path: Path, monkeypatch) -> None:
    import base64

    from fastapi.testclient import TestClient
    from jarvis_core.api.server import JarvisCoreState, create_app

    monkeypatch.setenv("JARVIS_ALLOW_INSECURE", "1")
    app = create_app(state=JarvisCoreState(data_dir=tmp_path / "jarvis_data"))
    client = TestClient(app)

    resp = client.post(
        "/api/voice/wake-check",
        json={
            "audio_base64": base64.b64encode(b"RIFF0000WAVE").decode("ascii"),
            "mime_type": "audio/wav",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"text": ""}


def test_silero_vad_rejects_static_noise() -> None:
    import io
    import wave

    import numpy as np
    from jarvis_core.api.server import JarvisCoreState

    rng = np.random.default_rng(42)
    noise = (rng.normal(0.0, 0.03, 24000)).clip(-1.0, 1.0)
    pcm = (noise * 32767.0).astype(np.int16).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm)
    has_speech, _ = JarvisCoreState._decode_and_vad_wav(buf.getvalue(), "audio/wav")
    assert has_speech is False
