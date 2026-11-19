"""Tests bout-en-bout WebSocket, outils, approbations et persistance de server.py."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jarvis_core.api.server import JarvisCoreState, create_app
from jarvis_core.llm.base import LLMProvider
from jarvis_core.runtime import build_runtime
from jarvis_core.tools.registry import ToolSpec as RegistryToolSpec
from jarvis_shared.models import (
    Message,
    ModelCapabilities,
    RiskLevel,
    StreamDelta,
    ToolCall,
    Usage,
)
from pydantic import BaseModel

from evals.harness import EvalHarness

ROOT_DIR = Path(__file__).resolve().parents[1]


class _ScriptedStreamingProvider(LLMProvider):
    """Fournisseur factice avec reponses stream scriptees par tour."""

    name = "openrouter"

    def __init__(self, scripted_streams: list[list[StreamDelta]]) -> None:
        self.scripted_streams = list(scripted_streams)
        self.stream_calls = 0

    async def fetch_available_models(self) -> dict[str, ModelCapabilities]:
        return {}

    async def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        fallback_models: list[str] | None = None,
        temperature: float = 0.2,
    ) -> StreamDelta:
        _ = (messages, tools, fallback_models, temperature)
        return StreamDelta(
            text='{"facts": []}',
            usage=Usage(model=model, provider=self.name, tokens_in=5, tokens_out=4, cost_usd=0.0),
        )

    async def stream(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        fallback_models: list[str] | None = None,
        temperature: float = 0.2,
    ) -> AsyncIterator[StreamDelta]:
        _ = (messages, tools, fallback_models, temperature)
        idx = min(self.stream_calls, len(self.scripted_streams) - 1)
        self.stream_calls += 1
        for delta in self.scripted_streams[idx]:
            if delta.usage is None and delta.tool_calls is None:
                yield StreamDelta(
                    text=delta.text,
                    usage=Usage(
                        model=model, provider=self.name, tokens_in=10, tokens_out=5, cost_usd=0.0001
                    ),
                )
            else:
                yield delta


def _make_state(tmp_path: Path, streams: list[list[StreamDelta]] | None = None) -> JarvisCoreState:
    state = JarvisCoreState(
        data_dir=tmp_path / "data",
        config_dir=ROOT_DIR / "config",
        legacy_profile_path=tmp_path / "none.json",
    )
    if streams is not None:
        provider = _ScriptedStreamingProvider(streams)
        state.runtime = build_runtime(
            data_dir=state.data_dir,
            config_dir=state.config_dir,
            database=state.db,
            providers={"openrouter": provider, "gemini": provider},
        )
    return state


def test_ws_uses_agent_not_echo(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_ALLOW_INSECURE", "1")
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)

    state = _make_state(
        tmp_path,
        streams=[[StreamDelta(text="Bonjour "), StreamDelta(text="Tom !")]],
    )
    client = TestClient(create_app(state))

    deltas: list[str] = []
    seen_done = False
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json() == {"type": "state", "payload": {"value": "idle"}}
        ws.send_json({"type": "user.text", "payload": {"text": "Salut JARVIS"}})
        while True:
            ev = ws.receive_json()
            if ev["type"] == "assistant.delta":
                deltas.append(ev["payload"]["text"])
            elif ev["type"] == "assistant.done":
                seen_done = True
            elif ev["type"] == "state" and ev["payload"]["value"] == "idle" and seen_done:
                break

    assert deltas == ["Bonjour ", "Tom !"]
    assert "Bien recu" not in "".join(deltas)
    msgs = state.db.list_messages()
    assert any(m.role == "assistant" and m.content == "Bonjour Tom !" for m in msgs)


def test_ws_emits_tool_events(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_ALLOW_INSECURE", "1")
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)

    state = _make_state(
        tmp_path,
        streams=[
            [
                StreamDelta(
                    tool_calls=[
                        ToolCall(
                            id="tc_mem_1",
                            name="memory_save",
                            arguments={
                                "subject": "Tom",
                                "predicate": "prefere",
                                "text": "Tom prefere Rust.",
                            },
                        )
                    ],
                    usage=Usage(model="mock", provider="openrouter", tokens_in=8, tokens_out=4),
                )
            ],
            [StreamDelta(text="Fait enregistre.")],
        ],
    )
    client = TestClient(create_app(state))

    events: list[dict[str, Any]] = []
    seen_done = False
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "user.text", "payload": {"text": "Retiens que je prefere Rust"}})
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["type"] == "assistant.done":
                seen_done = True
            elif ev["type"] == "state" and ev["payload"]["value"] == "idle" and seen_done:
                break

    types = [e["type"] for e in events]
    assert "tool.call" in types
    assert "tool.result" in types
    tc_ev = next(e for e in events if e["type"] == "tool.call")
    tr_ev = next(e for e in events if e["type"] == "tool.result")
    assert tc_ev["payload"]["name"] == "memory_save"
    assert tr_ev["payload"]["ok"] is True
    assert any("Rust" in f.text for f in state.db.list_facts())


class _DeployArgs(BaseModel):
    env: str


def test_ws_interactive_approval_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_ALLOW_INSECURE", "1")
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)

    executed: list[str] = []

    async def _deploy_handler(env: str) -> dict[str, Any]:
        executed.append(env)
        return {"ok": True, "deployed": env}

    state = _make_state(
        tmp_path,
        streams=[
            [
                StreamDelta(
                    tool_calls=[
                        ToolCall(
                            id="tc_dep_1",
                            name="deploy_release",
                            arguments={"env": "staging"},
                        )
                    ],
                    usage=Usage(model="mock", provider="openrouter", tokens_in=8, tokens_out=4),
                )
            ],
            [StreamDelta(text="Deploiement staging termine.")],
        ],
    )
    assert state.runtime is not None
    state.runtime.tool_registry.register(
        RegistryToolSpec(
            name="deploy_release",
            description="Deploie une version en staging",
            handler=_deploy_handler,
            args_model=_DeployArgs,
            risk=RiskLevel.SENSITIVE,
        )
    )
    client = TestClient(create_app(state))

    events: list[dict[str, Any]] = []
    seen_done = False
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "user.text", "payload": {"text": "Deploie en staging"}})
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["type"] == "approval.request":
                ws.send_json(
                    {
                        "type": "action.approve",
                        "payload": {"id": ev["payload"]["id"], "approved": True},
                    }
                )
            elif ev["type"] == "assistant.done":
                seen_done = True
            elif ev["type"] == "state" and ev["payload"]["value"] == "idle" and seen_done:
                break

    assert executed == ["staging"]
    tr_ev = next(e for e in events if e["type"] == "tool.result")
    assert tr_ev["payload"]["ok"] is True
    approvals = state.db.list_approvals()
    assert len(approvals) == 1
    assert approvals[0].status == "approved"


async def test_sensitive_tool_waits_for_human_approval(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    executed: list[str] = []

    async def _deploy_handler(env: str) -> dict[str, Any]:
        executed.append(env)
        return {"ok": True, "deployed": env}

    rt = build_runtime(
        data_dir=tmp_path / "data",
        config_dir=ROOT_DIR / "config",
        allow_offline=True,
    )
    rt.tool_registry.register(
        RegistryToolSpec(
            name="deploy_release",
            description="Deploie une version en staging",
            handler=_deploy_handler,
            args_model=_DeployArgs,
            risk=RiskLevel.SENSITIVE,
        )
    )

    res = await rt.tool_registry.execute(
        ToolCall(id="tc_wait", name="deploy_release", arguments={"env": "prod"}),
        approval_timeout=0.05,
    )
    assert res.is_error is True
    assert executed == []
    approvals = rt.database.list_approvals()
    assert len(approvals) == 1
    assert approvals[0].status == "pending"


def test_role_model_survives_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_ALLOW_INSECURE", "1")
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)

    data_dir = tmp_path / "data"
    app1 = create_app(
        JarvisCoreState(
            data_dir=data_dir,
            config_dir=ROOT_DIR / "config",
            legacy_profile_path=tmp_path / "none.json",
        )
    )
    client1 = TestClient(app1)
    put_resp = client1.put(
        "/api/models/role",
        json={"role": "agent", "model": "deepseek/deepseek-v4.1-flash", "provider": "openrouter"},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["model"] == "deepseek/deepseek-v4.1-flash"

    app2 = create_app(
        JarvisCoreState(
            data_dir=data_dir,
            config_dir=ROOT_DIR / "config",
            legacy_profile_path=tmp_path / "none.json",
        )
    )
    client2 = TestClient(app2)
    models = client2.get("/api/models").json()
    agent_cfg = next(item for item in models if item["role"] == "agent")
    assert agent_cfg["model"] == "deepseek/deepseek-v4.1-flash"
    assert (
        app2.state.core.runtime.router.get_role_route("agent").model
        == "deepseek/deepseek-v4.1-flash"
    )

    ollama_resp = client2.put(
        "/api/models/role",
        json={"role": "agent", "model": "qwen3.5:2b"},
    )
    assert ollama_resp.status_code == 200
    assert ollama_resp.json()["model"] == "qwen3.5:2b"
    assert ollama_resp.json()["provider"] == "ollama"
    assert app2.state.core.runtime.router.get_role_route("agent").provider == "ollama"


def test_refuses_to_start_without_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    monkeypatch.delenv("JARVIS_ALLOW_INSECURE", raising=False)

    state = JarvisCoreState(
        data_dir=tmp_path / "data",
        config_dir=ROOT_DIR / "config",
        legacy_profile_path=tmp_path / "none.json",
    )
    with pytest.raises(RuntimeError, match="JARVIS_API_TOKEN"):
        create_app(state)


async def test_evals_endpoint_returns_404_when_never_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_ALLOW_INSECURE", "1")
    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)

    state = _make_state(tmp_path)
    client = TestClient(create_app(state))
    assert client.get("/api/evals/latest").status_code == 404

    harness = EvalHarness()
    results = await harness.run_all()
    harness.save_report(results, state.data_dir / "evals" / "latest.json")
    ok_resp = client.get("/api/evals/latest")
    assert ok_resp.status_code == 200
    assert ok_resp.json()["total"] == len(results)
