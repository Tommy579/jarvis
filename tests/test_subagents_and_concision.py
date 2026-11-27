"""Tests unitaires pour la concision universelle et l'orchestration de sous-agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from jarvis_core.agent.loop import AgentLoop, ensure_concise_response
from jarvis_core.llm.base import LLMProvider
from jarvis_core.llm.router import LLMRouter, RoleRoute
from jarvis_core.memory.engine import MemoryEngine
from jarvis_core.memory.storage import MemoryStorage
from jarvis_core.memory.wiki import MarkdownWiki
from jarvis_core.tools.registry import ToolRegistry
from jarvis_shared.models import ModelCapabilities, Role, StreamDelta, ToolCall, Usage


class _MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, responses: list[list[StreamDelta]]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def fetch_available_models(self) -> dict[str, ModelCapabilities]:
        return {}

    async def complete(self, *args, **kwargs) -> StreamDelta:
        return StreamDelta(text="ok")

    async def stream(self, *args, **kwargs):
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        for delta in self.responses[idx]:
            yield delta


def test_ensure_concise_response() -> None:
    # 1. Reponse deja courte
    short_text = "Serveur en ligne. Tout fonctionne correctement."
    assert ensure_concise_response(short_text) == short_text

    # 2. Reponse longue avec deja [suite]
    with_suite = (
        "Voici les points cles de la situation : le CPU est a 12% et la memoire est stable. "
        "Les services tournent normalement et aucun incident critique n'a ete detecte sur le reseau. "
        "Des verifications secondaires sont en attente d'approbation sur le serveur distant... [suite]"
    )
    assert ensure_concise_response(with_suite) == with_suite

    # 3. Reponse verbeuse sans mention de suite -> tronquee proprement avec ... [suite]
    long_verbose = (
        "Bonjour Tom, j'ai effectue une analyse complete de ton systeme. "
        "Premièrement, le processeur fonctionne a un niveau optimal avec seulement 10% d'utilisation. "
        "Deuxièmement, la memoire vive disponible est de 16 gigaoctets ce qui est amplement suffisant. "
        "Troisièmement, le disque principal dispose de 240 gigaoctets libres. "
        "Quatrièmement, le reseau est stable et sans aucune perte de paquets detectee sur les dernieres 24 heures. "
        "Cinquièmement, les processus d'arriere-plan ne montrent aucune anomalie."
    )
    result = ensure_concise_response(long_verbose)
    assert "... [suite]" in result
    assert len(result) < len(long_verbose)


def test_persona_prompt_contains_concision_and_orchestration(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    wiki_path = tmp_path / "wiki"
    wiki = MarkdownWiki(wiki_path, auto_git=False)
    storage = MemoryStorage(db_path)
    engine = MemoryEngine(storage=storage, wiki=wiki)

    context = engine.assemble_context(query="Bonjour", history=[])
    system_text = "\n".join(m.content for m in context if m.role == Role.SYSTEM)

    assert "Concision maximale" in system_text
    assert "[suite]" in system_text
    assert "delegate_subagent" in system_text
    assert "orchestrer" in system_text


@pytest.mark.asyncio
async def test_subagent_tool_execution(tmp_path: Path) -> None:
    # Provider simulant le sous-agent qui repond
    subagent_provider = _MockProvider(
        [
            # Reponse du sous-agent
            [
                StreamDelta(
                    text="Sous-tache analysee : aucun probleme detecte.",
                    usage=Usage(
                        model="deep", provider="mock", tokens_in=5, tokens_out=8, cost_usd=0.0
                    ),
                )
            ]
        ]
    )
    router = LLMRouter(
        providers={"mock": subagent_provider},
        roles={
            "agent": RoleRoute(role_name="agent", model="mock-agent", provider="mock"),
            "deep": RoleRoute(role_name="deep", model="mock-deep", provider="mock"),
        },
    )
    registry = ToolRegistry()
    AgentLoop(router=router, tool_registry=registry)  # enregistre delegate_subagent

    # L'outil doit etre enregistre automatiquement
    spec = registry.get("delegate_subagent")
    assert spec is not None
    assert spec.name == "delegate_subagent"

    # Execution directe du handler de l'outil
    res = await spec.handler(task="Verifier la charge du serveur", role="deep")
    assert res["ok"] is True
    assert "Sous-tache analysee" in res["result"]


@pytest.mark.asyncio
async def test_adaptive_step_limit_exceeds_eight_when_progressing() -> None:
    from pydantic import BaseModel

    class _PingArgs(BaseModel):
        step: int

    # 15 appels d'outils successifs puis reponse finale (> 8 et > BASE_ITERATIONS=12)
    responses: list[list[StreamDelta]] = [
        [
            StreamDelta(
                tool_calls=[ToolCall(id=f"tc_{i}", name="ping", arguments={"step": i})],
                usage=Usage(
                    model="agent", provider="mock", tokens_in=2, tokens_out=2, cost_usd=0.0
                ),
            )
        ]
        for i in range(15)
    ]
    responses.append([StreamDelta(text="Termine apres 15 etapes.")])

    provider = _MockProvider(responses)
    router = LLMRouter(
        providers={"mock": provider},
        roles={"agent": RoleRoute(role_name="agent", model="mock-agent", provider="mock")},
    )
    registry = ToolRegistry()

    from jarvis_core.tools.registry import ToolSpec

    async def _ping_handler(step: int) -> dict[str, Any]:
        return {"ok": True, "step": step}

    registry.register(
        ToolSpec(
            name="ping",
            description="Outil de test",
            handler=_ping_handler,
            args_model=_PingArgs,
        )
    )

    loop = AgentLoop(router=router, tool_registry=registry)
    res = await loop.run_turn("Execute 15 etapes")
    assert res.iterations == 16
    assert "Termine apres 15 etapes." in res.text


@pytest.mark.asyncio
async def test_main_agent_excludes_direct_coding_tools(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    from jarvis_core.runtime import build_runtime

    rt = build_runtime(
        data_dir=tmp_path / "data",
        config_dir=Path(__file__).resolve().parent.parent / "config",
        allow_offline=True,
    )
    schemas = rt.tool_registry.list_schemas(excluded_tools=rt.agent.excluded_tools)
    names = {s["function"]["name"] for s in schemas}
    assert "delegate_subagent" in names
    assert "start_goal" in names
    assert "write_file" not in names
    assert "run_command" not in names

    blocked = await rt.tool_registry.execute(
        ToolCall(id="tc-w", name="write_file", arguments={"path": "x.py", "content": "pass"}),
        excluded_tools=rt.agent.excluded_tools,
    )
    assert blocked.is_error is True
    assert blocked.output.get("blocked_by") == "orchestrator_delegation_policy"


@pytest.mark.asyncio
async def test_agent_loop_uncapped_even_with_errors() -> None:
    from jarvis_core.tools.registry import ToolSpec
    from pydantic import BaseModel

    class _StepArgs(BaseModel):
        step: int

    # 75 etapes (> 64 et > 20) incluant des erreurs d'outils transitoires
    responses: list[list[StreamDelta]] = [
        [
            StreamDelta(
                tool_calls=[ToolCall(id=f"tc_{i}", name="step_tool", arguments={"step": i})],
                usage=Usage(
                    model="agent", provider="mock", tokens_in=2, tokens_out=2, cost_usd=0.0
                ),
            )
        ]
        for i in range(75)
    ]
    responses.append([StreamDelta(text="Objectif complet apres 75 etapes.")])

    provider = _MockProvider(responses)
    router = LLMRouter(
        providers={"mock": provider},
        roles={"agent": RoleRoute(role_name="agent", model="mock-agent", provider="mock")},
    )
    registry = ToolRegistry()

    async def _step_handler(step: int) -> dict[str, Any]:
        if step % 10 == 0:
            return {"ok": False, "error": "erreur transitoire"}
        return {"ok": True, "step": step}

    registry.register(
        ToolSpec(
            name="step_tool",
            description="Outil de test multi-etapes",
            handler=_step_handler,
            args_model=_StepArgs,
        )
    )

    loop = AgentLoop(router=router, tool_registry=registry)
    res = await loop.run_turn("Execute 75 etapes sans t'arreter")
    assert res.iterations == 76
    assert "Objectif complet apres 75 etapes." in res.text


@pytest.mark.asyncio
async def test_per_provider_daily_budget_failover_and_clean_stop(tmp_path: Path) -> None:
    class _NamedProvider(LLMProvider):
        def __init__(self, name: str, reply: str) -> None:
            self.name = name
            self.reply = reply
            self.calls = 0

        async def fetch_available_models(self) -> dict[str, ModelCapabilities]:
            return {}

        async def complete(self, *args, **kwargs) -> StreamDelta:
            self.calls += 1
            return StreamDelta(
                text=self.reply,
                usage=Usage(model="m", provider=self.name, tokens_in=5, tokens_out=5, cost_usd=0.1),
            )

        async def stream(self, *args, **kwargs):
            yield await self.complete(*args, **kwargs)

    storage = MemoryStorage(tmp_path / "budget.db")
    storage.log_llm_call(
        "agent",
        Usage(
            model="deepseek/deepseek-v4.1-flash-1017",
            provider="openrouter",
            tokens_in=10,
            tokens_out=10,
            cost_usd=10.5,
        ),
    )
    storage.log_llm_call(
        "agent",
        Usage(
            model="gemini-3.5-flash-lite",
            provider="gemini",
            tokens_in=10,
            tokens_out=10,
            cost_usd=1.0,
        ),
    )

    assert storage.get_today_spend_usd("openrouter") == pytest.approx(10.5)
    assert storage.get_today_spend_usd("gemini") == pytest.approx(1.0)

    or_prov = _NamedProvider("openrouter", "from-openrouter")
    gem_prov = _NamedProvider("gemini", "from-gemini")
    router = LLMRouter(
        providers={"openrouter": or_prov, "gemini": gem_prov},
        roles={
            "agent": RoleRoute(
                role_name="agent",
                model="deepseek/deepseek-v4.1-flash-1017",
                provider="openrouter",
                fallback=["gemini-3.5-flash-lite"],
            )
        },
        daily_budget_usd=10.0,
        usage_logger=lambda role, usage: storage.log_llm_call(role, usage),
        daily_provider_spend_getter=storage.get_today_spend_usd,
    )

    # openrouter > 10$ -> bascule automatique sur gemini (< 10$) sans erreur
    loop = AgentLoop(router=router, tool_registry=ToolRegistry())
    res = await loop.run_turn("Bonjour")
    assert or_prov.calls == 0
    assert gem_prov.calls == 1
    assert "from-gemini" in res.text

    # Si gemini depasse aussi 10$, AgentLoop s'arrete proprement sans lever d'exception
    storage.log_llm_call(
        "agent",
        Usage(
            model="gemini-3.5-flash-lite",
            provider="gemini",
            tokens_in=10,
            tokens_out=10,
            cost_usd=9.5,
        ),
    )
    res_stop = await loop.run_turn("Continue")
    assert "Plafond journalier" in res_stop.text
    assert "10.00 $" in res_stop.text
