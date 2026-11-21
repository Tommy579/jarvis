"""Tests pour la configuration du niveau de raisonnement par role/modele."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from jarvis_core.api.server import JarvisCoreState, create_app
from jarvis_core.llm.base import LLMProvider
from jarvis_core.llm.ollama import OllamaProvider
from jarvis_core.llm.openrouter import OpenRouterProvider
from jarvis_core.llm.router import LLMRouter, RoleRoute
from jarvis_core.runtime import _load_roles
from jarvis_shared.models import Message, ModelCapabilities, Role, StreamDelta, Usage

ROOT_DIR = Path(__file__).resolve().parents[1]


class _ThinkingAwareProvider(LLMProvider):
    def __init__(self, name: str = "openrouter") -> None:
        self.name = name
        self.calls: list[tuple[str, str | None]] = []

    async def fetch_available_models(self) -> dict[str, ModelCapabilities]:
        return {}

    async def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        fallback_models: list[str] | None = None,
        temperature: float = 0.2,
        thinking_mode: str | None = None,
    ) -> StreamDelta:
        _ = (messages, tools, fallback_models, temperature)
        self.calls.append((model, thinking_mode))
        return StreamDelta(
            text="ok",
            usage=Usage(model=model, provider=self.name, tokens_in=5, tokens_out=2),
        )

    async def stream(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        fallback_models: list[str] | None = None,
        temperature: float = 0.2,
        thinking_mode: str | None = None,
    ) -> AsyncIterator[StreamDelta]:
        delta = await self.complete(
            model,
            messages,
            tools=tools,
            fallback_models=fallback_models,
            temperature=temperature,
            thinking_mode=thinking_mode,
        )
        yield delta


def test_openrouter_and_ollama_payload_respect_explicit_thinking_mode() -> None:
    or_prov = OpenRouterProvider(api_key="test-key")
    msgs = [Message(role=Role.USER, content="Salut")]

    assert "reasoning" not in or_prov.build_payload("deepseek/deepseek-v4.1-flash-1017", msgs)
    assert or_prov.build_payload(
        "deepseek/deepseek-v4.1-flash-1017", msgs, thinking_mode="off"
    )["reasoning"] == {"enabled": False}
    assert or_prov.build_payload(
        "deepseek/deepseek-v4.1-flash-1017", msgs, thinking_mode="high"
    )["reasoning"] == {"effort": "high"}

    ol_prov = OllamaProvider()
    assert "reasoning_effort" not in ol_prov.build_payload("qwen3.5:2b", msgs, thinking_mode="auto")
    assert ol_prov.build_payload("qwen3.5:2b", msgs, thinking_mode="off")["reasoning_effort"] == "none"
    assert (
        ol_prov.build_payload("qwen3.5:2b", msgs, thinking_mode="medium")["reasoning_effort"]
        == "medium"
    )


async def test_router_passes_per_role_thinking_mode() -> None:
    or_prov = _ThinkingAwareProvider(name="openrouter")
    gem_prov = _ThinkingAwareProvider(name="gemini")
    router = LLMRouter(
        providers={"openrouter": or_prov, "gemini": gem_prov},
        roles={
            "agent": RoleRoute("agent", "deepseek/deepseek-v4.1-flash-1017", "openrouter", thinking_mode="auto"),
            "triage": RoleRoute("triage", "gemini-4-carbon", "gemini", thinking_mode="off"),
            "deep": RoleRoute("deep", "gemini-4-hydrogen", "gemini", thinking_mode="high"),
        },
    )
    msgs = [Message(role=Role.USER, content="Test")]

    await router.complete("triage", msgs)
    await router.complete("deep", msgs)
    await router.complete("agent", msgs)
    assert gem_prov.calls == [("gemini-4-carbon", "off"), ("gemini-4-hydrogen", "high")]
    assert or_prov.calls == [("deepseek/deepseek-v4.1-flash-1017", "auto")]

    router.set_thinking_mode("low")
    await router.complete("triage", msgs)
    await router.complete("agent", msgs)
    assert gem_prov.calls[-1] == ("gemini-4-carbon", "off")
    assert or_prov.calls[-1] == ("deepseek/deepseek-v4.1-flash-1017", "low")


def test_load_roles_reads_thinking_mode_from_yaml(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "models.yaml").write_text(
        """
defaults:
  provider: openrouter
roles:
  agent:
    model: deepseek/deepseek-v4.1-flash-1017
    provider: openrouter
    thinking_mode: medium
  triage:
    model: gemini-4-carbon
    provider: gemini
    reasoning: off
""".strip(),
        encoding="utf-8",
    )

    roles = _load_roles(cfg_dir)
    assert roles["agent"].thinking_mode == "medium"
    assert roles["triage"].thinking_mode == "off"


def test_api_role_thinking_mode_persists_across_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JARVIS_ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv("JARVIS_API_TOKEN", "test-token")
    headers = {"Authorization": "Bearer test-token"}

    data_dir = tmp_path / "data"
    app1 = create_app(
        JarvisCoreState(
            data_dir=data_dir,
            config_dir=ROOT_DIR / "config",
            legacy_profile_path=tmp_path / "none.json",
        )
    )
    client1 = TestClient(app1)

    resp = client1.put(
        "/api/models/role",
        headers=headers,
        json={
            "role": "triage",
            "model": "gemini-4-carbon",
            "provider": "gemini",
            "thinking_mode": "off",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["thinking_mode"] == "off"

    resp_agent = client1.put(
        "/api/models/role",
        headers=headers,
        json={
            "role": "agent",
            "model": "deepseek/deepseek-v4.1-flash-1017",
            "thinking_mode": "high",
        },
    )
    assert resp_agent.status_code == 200
    assert resp_agent.json()["thinking_mode"] == "high"
    assert client1.get("/api/models/thinking", headers=headers).json() == {"mode": "high"}

    app2 = create_app(
        JarvisCoreState(
            data_dir=data_dir,
            config_dir=ROOT_DIR / "config",
            legacy_profile_path=tmp_path / "none.json",
        )
    )
    client2 = TestClient(app2)
    models = client2.get("/api/models", headers=headers).json()
    triage_cfg = next(item for item in models if item["role"] == "triage")
    agent_cfg = next(item for item in models if item["role"] == "agent")
    assert triage_cfg["thinking_mode"] == "off"
    assert agent_cfg["thinking_mode"] == "high"
    assert app2.state.core.runtime.router.get_role_route("triage").thinking_mode == "off"
    assert app2.state.core.runtime.router.get_role_route("agent").thinking_mode == "high"


async def test_router_never_sends_openrouter_slug_to_gemini() -> None:
    gem_prov = _ThinkingAwareProvider(name="gemini")
    router = LLMRouter(providers={"gemini": gem_prov})
    route = router.set_role_model(
        "agent",
        "deepseek/deepseek-v4.1-flash-1017",
        provider="gemini",
    )
    assert route.provider == "openrouter"

    await router.complete("agent", [Message(role=Role.USER, content="Salut")])
    assert gem_prov.calls == [("gemini-3.5-flash-lite", "auto")]


def test_router_candidate_chain_uses_lightweight_fallbacks() -> None:
    or_prov = _ThinkingAwareProvider(name="openrouter")
    gem_prov = _ThinkingAwareProvider(name="gemini")
    router = LLMRouter(
        providers={"openrouter": or_prov, "gemini": gem_prov},
        roles={
            "agent": RoleRoute("agent", "gemini-4-hydrogen", "gemini", fallback=[]),
        },
    )
    chain = router._candidate_chain(router.get_role_route("agent"))
    assert [(p.name, m) for p, m in chain] == [
        ("gemini", "gemini-4-hydrogen"),
        ("openrouter", "deepseek/deepseek-v4.1-flash-1017"),
        ("gemini", "gemini-3.5-flash-lite"),
    ]
