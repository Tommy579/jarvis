"""Tests complets pour l'orchestrateur autonome par objectifs, le catalogue low-cost et le Watchdog."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jarvis_core.agent.orchestrator import GoalManager
from jarvis_core.agent.supervisor import TOOL_PROFILES, SubagentSupervisor
from jarvis_core.api.server import create_app
from jarvis_core.llm.base import LLMProvider
from jarvis_core.llm.catalog import ModelSelectionCatalog
from jarvis_core.llm.gemini import DEFAULT_KNOWN_GEMINI_MODELS, GeminiProvider
from jarvis_core.llm.router import LLMRouter, RoleRoute
from jarvis_core.memory.engine import DEFAULT_PERSONA_PROMPT, MemoryEngine
from jarvis_core.memory.schema import MemoryDatabase
from jarvis_core.memory.storage import MemoryStorage
from jarvis_core.memory.wiki import MarkdownWiki
from jarvis_core.tools.cli_agents import (
    CLIAgentToolkit,
    RunCliSubagentArgs,
    build_cli_subagent_command,
    execute_cli_subagent,
)
from jarvis_core.tools.registry import ToolRegistry, ToolSpec
from jarvis_shared.models import (
    Message,
    ModelCapabilities,
    RiskLevel,
    Role,
    StreamDelta,
    ToolCall,
    Usage,
)
from pydantic import BaseModel


class _EchoToolArgs(BaseModel):
    value: str = "ping"


def _tool_outputs(messages: list[Message]) -> list[Any]:
    out: list[Any] = []
    for m in messages:
        for tr in getattr(m, "tool_results", None) or []:
            out.append(tr.output)
    return out


def _has_verified_pass(messages: list[Message]) -> bool:
    for out in _tool_outputs(messages):
        if isinstance(out, dict) and out.get("passed") is True:
            return True
    return False


def _has_active_runs_in_last_tool(messages: list[Message]) -> bool:
    for m in reversed(messages):
        trs = getattr(m, "tool_results", None) or []
        if trs:
            out = trs[-1].output
            if isinstance(out, dict) and out.get("active_runs"):
                return True
            return False
    return False


class _ScriptedGoalProvider(LLMProvider):
    """Fournisseur scriptable qui distingue orchestrateur et sous-agents par modele ou system prompt."""

    def __init__(self, handler: Any, name: str = "openrouter") -> None:
        self.name = name
        self._handler = handler
        self.calls: list[dict[str, Any]] = []

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(
            provider=self.name,
            model=model,
            supports_tools=True,
            supports_streaming=True,
        )

    async def fetch_available_models(self) -> list[ModelCapabilities]:
        return [
            self.capabilities("deepseek/deepseek-v4.1-flash-1017"),
            self.capabilities("z-ai/glm-5.3"),
            self.capabilities("minimax/minimax-m3"),
            self.capabilities("moonshotai/kimi-k3"),
            self.capabilities("gemini-4-argon"),
            self.capabilities("gemini-4-carbon"),
            self.capabilities("gemini-4-hydrogen"),
        ]

    async def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        fallback_models: list[str] | None = None,
        temperature: float = 0.2,
    ) -> StreamDelta:
        self.calls.append({"model": model, "messages": list(messages)})
        res = await self._handler(model, messages, tools)
        if isinstance(res, StreamDelta):
            return res
        msg, usage = res
        return StreamDelta(
            text=msg.content,
            tool_calls=list(msg.tool_calls),
            finish_reason="stop",
            usage=usage,
        )

    async def stream(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        fallback_models: list[str] | None = None,
        temperature: float = 0.2,
    ) -> AsyncIterator[StreamDelta]:
        yield await self.complete(
            model=model,
            messages=messages,
            tools=tools,
            fallback_models=fallback_models,
            temperature=temperature,
        )


def _make_db_and_router(
    tmp_path: Path, provider: LLMProvider, *, provider_name: str = "openrouter"
) -> tuple[MemoryDatabase, LLMRouter, MemoryEngine, ToolRegistry]:
    db_path = tmp_path / "memory.db"
    db = MemoryDatabase(db_path)
    db.migrate()
    storage = MemoryStorage(db_path, database=db)
    wiki = MarkdownWiki(tmp_path / "wiki", auto_git=False)
    memory_engine = MemoryEngine(storage=storage, wiki=wiki)

    router = LLMRouter(
        providers={provider_name: provider},
        roles={
            "orchestrator": RoleRoute(
                role_name="orchestrator",
                model="deepseek/deepseek-v4.1-flash-1017",
                provider=provider_name,
                fallback=[],
            ),
            "agent": RoleRoute(
                role_name="agent",
                model="deepseek/deepseek-v4.1-flash-1017",
                provider=provider_name,
                fallback=[],
            ),
        },
        usage_logger=lambda role, usage: storage.log_llm_call(role, usage),
    )
    registry = ToolRegistry()

    async def _sample_read(value: str = "ok") -> dict[str, Any]:
        return {"ok": True, "echo": value}

    async def _sample_write(value: str = "written") -> dict[str, Any]:
        return {"ok": True, "saved": value}

    registry.register(
        ToolSpec(
            name="read_file",
            description="Lit un contenu de test",
            handler=_sample_read,
            args_model=_EchoToolArgs,
            risk=RiskLevel.READ_ONLY,
        )
    )
    registry.register(
        ToolSpec(
            name="write_file",
            description="Ecrit un contenu de test",
            handler=_sample_write,
            args_model=_EchoToolArgs,
            risk=RiskLevel.REVERSIBLE_WRITE,
        )
    )
    return db, router, memory_engine, registry


async def test_catalog_low_cost_models_and_gemini_pricing() -> None:
    config_dir = Path(__file__).resolve().parent.parent / "config"
    catalog = ModelSelectionCatalog.from_yaml(config_dir)

    expected = {
        "gemini-4-carbon",
        "deepseek/deepseek-v4.1-flash-1017",
        "minimax/minimax-m3",
        "z-ai/glm-5.3",
        "moonshotai/kimi-k3",
        "gemini-4-argon",
        "gemini-4-hydrogen",
    }
    assert expected.issubset(set(catalog.allowed_models))

    # Les modeles hors pool (couteux) sont refuses et remplaces par un modele low-cost adapte
    assert catalog.is_allowed("anthropic/claude-opus-4.6") is False
    resolved = catalog.resolve_model("anthropic/claude-opus-4.6", role_profile="coder")
    assert resolved in catalog.allowed_models

    # Alias courts
    assert catalog.normalize_id("kimi-k3") == "moonshotai/kimi-k3"
    assert catalog.normalize_id("deepseek-v4.1-1017") == "deepseek/deepseek-v4.1-flash-1017"

    # Le calcul de cout Gemini n'est plus bloque a 0.0
    assert DEFAULT_KNOWN_GEMINI_MODELS["gemini-4-argon"].input_cost_per_1m > 0.0
    fake_part = type("Part", (), {"text": "ok", "function_call": None})()
    fake_cand = type("Cand", (), {"content": type("Content", (), {"parts": [fake_part]})()})()
    fake_usage = type(
        "UsageMeta",
        (),
        {
            "prompt_token_count": 1_000_000,
            "candidates_token_count": 1_000_000,
            "cached_content_token_count": 0,
        },
    )()
    fake_resp = type("Resp", (), {"candidates": [fake_cand], "usage_metadata": fake_usage})()
    fake_models = type("Models", (), {"generate_content": lambda *a, **kw: fake_resp})()
    fake_client = type("Client", (), {"models": fake_models})()
    gp = GeminiProvider(api_key="test-key")
    gp._get_client = lambda: fake_client  # type: ignore[method-assign]
    delta = await gp.complete("gemini-4-argon", [Message(role=Role.USER, content="hi")])
    assert delta.usage is not None
    assert delta.usage.cost_usd > 0.0
    assert "start_goal" in DEFAULT_PERSONA_PROMPT

    # Les schemas Pydantic imbriques ($defs/$ref) sont applaties pour google-genai
    from google.genai import types as genai_types
    from jarvis_core.agent.orchestrator import PlanGoalArgs, SplitSubtaskArgs

    decls = GeminiProvider._to_gemini_function_declarations(
        [
            {
                "type": "function",
                "function": {
                    "name": "plan_goal",
                    "description": "d",
                    "parameters": PlanGoalArgs.model_json_schema(),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "split_subtask",
                    "description": "d",
                    "parameters": SplitSubtaskArgs.model_json_schema(),
                },
            },
        ]
    )
    cfg = genai_types.GenerateContentConfig(tools=decls)
    assert len(cfg.tools[0].function_declarations) == 2


async def test_goal_parallel_subagents_and_verification_gate(tmp_path: Path) -> None:
    orch_step = 0
    premature_blocked_seen = False

    async def _handler(
        model: str, messages: list[Message], tools: list[dict[str, Any]] | None
    ) -> tuple[Message, Usage]:
        nonlocal orch_step, premature_blocked_seen
        sys_txt = messages[0].content if messages else ""
        tool_names = {
            t["function"]["name"]
            for t in (tools or [])
            if isinstance(t, dict) and isinstance(t.get("function"), dict)
        }
        is_orch = "plan_goal" in tool_names

        if is_orch:
            orch_step += 1
            if orch_step == 1:
                return (
                    Message(
                        role=Role.ASSISTANT,
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="tc-plan",
                                name="plan_goal",
                                arguments={
                                    "title": "Audit + correctif",
                                    "success_criteria": ["Analyse prête", "Patch écrit"],
                                    "subtasks": [
                                        {
                                            "id": "s1",
                                            "title": "Collecte d'infos",
                                            "instructions": "Lis les éléments initiaux.",
                                            "role_profile": "research",
                                            "model": "deepseek/deepseek-v4.1-flash-1017",
                                        },
                                        {
                                            "id": "s2",
                                            "title": "Rédaction patch",
                                            "instructions": "Écris le livrable.",
                                            "role_profile": "coder",
                                            "model": "z-ai/glm-5.3",
                                        },
                                    ],
                                },
                            )
                        ],
                    ),
                    Usage(
                        model=model,
                        provider="openrouter",
                        tokens_in=40,
                        tokens_out=30,
                        cost_usd=0.0002,
                    ),
                )
            if orch_step == 2:
                return (
                    Message(
                        role=Role.ASSISTANT,
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="tc-sp1",
                                name="spawn_subagent",
                                arguments={
                                    "subtask_id": "s1",
                                    "model": "deepseek/deepseek-v4.1-flash-1017",
                                },
                            ),
                            ToolCall(
                                id="tc-sp2",
                                name="spawn_subagent",
                                arguments={"subtask_id": "s2", "model": "z-ai/glm-5.3"},
                            ),
                        ],
                    ),
                    Usage(
                        model=model,
                        provider="openrouter",
                        tokens_in=30,
                        tokens_out=25,
                        cost_usd=0.0001,
                    ),
                )
            if orch_step == 3:
                return (
                    Message(
                        role=Role.ASSISTANT,
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="tc-wait",
                                name="await_subagent_events",
                                arguments={"timeout_s": 5.0},
                            )
                        ],
                    ),
                    Usage(
                        model=model,
                        provider="openrouter",
                        tokens_in=20,
                        tokens_out=10,
                        cost_usd=0.0001,
                    ),
                )
            # Vérifie que les deux sous-tâches finissent si un seul événement a réveillé await
            if _has_active_runs_in_last_tool(messages):
                return (
                    Message(
                        role=Role.ASSISTANT,
                        content="",
                        tool_calls=[
                            ToolCall(
                                id=f"tc-wait-{orch_step}",
                                name="await_subagent_events",
                                arguments={"timeout_s": 5.0},
                            )
                        ],
                    ),
                    Usage(
                        model=model,
                        provider="openrouter",
                        tokens_in=20,
                        tokens_out=10,
                        cost_usd=0.0001,
                    ),
                )
            if not premature_blocked_seen:
                # Tentative prématurée de complete_goal sans vérification !
                premature_blocked_seen = True
                return (
                    Message(
                        role=Role.ASSISTANT,
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="tc-premature",
                                name="complete_goal",
                                arguments={"final_summary": "Fini sans vérifier"},
                            )
                        ],
                    ),
                    Usage(
                        model=model,
                        provider="openrouter",
                        tokens_in=20,
                        tokens_out=10,
                        cost_usd=0.0001,
                    ),
                )
            if not _has_verified_pass(messages):
                return (
                    Message(
                        role=Role.ASSISTANT,
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="tc-verify",
                                name="run_verification",
                                arguments={
                                    "instructions": "Vérifie les critères",
                                    "model": "minimax/minimax-m3",
                                },
                            )
                        ],
                    ),
                    Usage(
                        model=model,
                        provider="openrouter",
                        tokens_in=20,
                        tokens_out=15,
                        cost_usd=0.0001,
                    ),
                )
            return (
                Message(
                    role=Role.ASSISTANT,
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="tc-complete",
                            name="complete_goal",
                            arguments={
                                "final_summary": "Toutes les sous-tâches sont vérifiées et terminées.",
                                "verified_criteria": ["Analyse prête", "Patch écrit"],
                            },
                        )
                    ],
                ),
                Usage(
                    model=model, provider="openrouter", tokens_in=20, tokens_out=15, cost_usd=0.0001
                ),
            )

        # Sous-agent worker ou vérificateur
        user_txt = "\n".join(m.content for m in messages)
        if "VERDICT: PASS" in sys_txt or "VERDICT: PASS" in user_txt:
            return (
                Message(
                    role=Role.ASSISTANT,
                    content="Tous les critères sont prouvés.\nVERDICT: PASS",
                ),
                Usage(
                    model=model, provider="openrouter", tokens_in=15, tokens_out=10, cost_usd=0.0001
                ),
            )
        await asyncio.sleep(0.03)
        return (
            Message(role=Role.ASSISTANT, content=f"Travail terminé avec {model}."),
            Usage(model=model, provider="openrouter", tokens_in=15, tokens_out=10, cost_usd=0.0001),
        )

    provider = _ScriptedGoalProvider(_handler)
    db, router, memory_engine, registry = _make_db_and_router(tmp_path, provider)
    ws_events: list[tuple[str, dict[str, Any]]] = []
    manager = GoalManager(
        router=router,
        tool_registry=registry,
        memory_engine=memory_engine,
        database=db,
        on_ws_event=lambda t, p: ws_events.append((t, p)),
        watchdog_interval_s=0.1,
    )

    rec = manager.start_goal(
        "Réaliser un audit parallèle puis vérifier le résultat", max_cost_usd=1.0
    )
    for _ in range(80):
        cur = manager.get_goal(rec.id)
        if cur and cur.status == "completed":
            break
        await asyncio.sleep(0.05)

    final_rec = manager.get_goal(rec.id)
    assert final_rec is not None
    assert final_rec.status == "completed", (
        final_rec.status,
        final_rec.pause_reason,
        final_rec.final_summary,
        [(e.kind, e.message) for e in final_rec.events],
    )
    assert final_rec.status == "completed"
    assert final_rec.progress_pct == 100
    assert len(final_rec.subtasks) == 3
    assert any(s.role_profile == "verifier" and s.status == "completed" for s in final_rec.subtasks)
    assert all(s.status == "completed" for s in final_rec.subtasks)
    assert any(ev[0] == "goal.updated" for ev in ws_events)
    # Persistance SQLite vérifiée
    reloaded = db.get_goal(rec.id)
    assert reloaded is not None
    assert reloaded.status == "completed"
    # Le modèle orchestrateur ne s'est pas arrêté avant vérification
    assert "vérifiées" in (final_rec.final_summary or "")


async def test_watchdog_loop_detection_and_model_switch(tmp_path: Path) -> None:
    step_counter = 0
    restarted_with_argon = False

    async def _handler(
        model: str, messages: list[Message], tools: list[dict[str, Any]] | None
    ) -> tuple[Message, Usage]:
        nonlocal step_counter, restarted_with_argon
        tool_names = {
            t["function"]["name"]
            for t in (tools or [])
            if isinstance(t, dict) and isinstance(t.get("function"), dict)
        }
        is_orch = "plan_goal" in tool_names

        if is_orch:
            step_counter += 1
            if step_counter == 1:
                return (
                    Message(
                        role=Role.ASSISTANT,
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="o1",
                                name="plan_goal",
                                arguments={
                                    "title": "Tâche qui boucle puis bascule",
                                    "success_criteria": ["Résolu après changement de modèle"],
                                    "subtasks": [
                                        {
                                            "id": "s1",
                                            "title": "Sous-tâche délicate",
                                            "instructions": "Tente d'abord avec deepseek.",
                                            "role_profile": "coder",
                                            "model": "deepseek/deepseek-v4.1-flash-1017",
                                        }
                                    ],
                                },
                            )
                        ],
                    ),
                    Usage(
                        model=model,
                        provider="openrouter",
                        tokens_in=20,
                        tokens_out=20,
                        cost_usd=0.0001,
                    ),
                )
            if step_counter == 2:
                return (
                    Message(
                        role=Role.ASSISTANT,
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="o2",
                                name="spawn_subagent",
                                arguments={
                                    "subtask_id": "s1",
                                    "model": "deepseek/deepseek-v4.1-flash-1017",
                                },
                            )
                        ],
                    ),
                    Usage(
                        model=model,
                        provider="openrouter",
                        tokens_in=20,
                        tokens_out=10,
                        cost_usd=0.0001,
                    ),
                )
            if step_counter == 3:
                return (
                    Message(
                        role=Role.ASSISTANT,
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="o3",
                                name="await_subagent_events",
                                arguments={"timeout_s": 5.0},
                            )
                        ],
                    ),
                    Usage(
                        model=model,
                        provider="openrouter",
                        tokens_in=15,
                        tokens_out=10,
                        cost_usd=0.0001,
                    ),
                )
            # Vérifie que l'orchestrateur a reçu l'événement de blocage et redémarre avec gemini-4-argon
            if not restarted_with_argon:
                restarted_with_argon = True
                return (
                    Message(
                        role=Role.ASSISTANT,
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="o4",
                                name="restart_subagent",
                                arguments={
                                    "subtask_id": "s1",
                                    "new_model": "gemini-4-argon",
                                    "reason": "Boucle détectée sur deepseek",
                                },
                            ),
                            ToolCall(
                                id="o5",
                                name="await_subagent_events",
                                arguments={"timeout_s": 5.0},
                            ),
                        ],
                    ),
                    Usage(
                        model=model,
                        provider="openrouter",
                        tokens_in=20,
                        tokens_out=20,
                        cost_usd=0.0001,
                    ),
                )
            if not _has_verified_pass(messages):
                return (
                    Message(
                        role=Role.ASSISTANT,
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="o6",
                                name="run_verification",
                                arguments={"instructions": "Valide s1"},
                            )
                        ],
                    ),
                    Usage(
                        model=model,
                        provider="openrouter",
                        tokens_in=15,
                        tokens_out=10,
                        cost_usd=0.0001,
                    ),
                )
            return (
                Message(
                    role=Role.ASSISTANT,
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="o7",
                            name="complete_goal",
                            arguments={"final_summary": "Récupération réussie via gemini-4-argon."},
                        )
                    ],
                ),
                Usage(
                    model=model, provider="openrouter", tokens_in=15, tokens_out=10, cost_usd=0.0001
                ),
            )

        # Sous-agent s1 : avec deepseek il boucle exactement sur read_file({"value": "stuck"})
        if model == "deepseek/deepseek-v4.1-flash-1017":
            return (
                Message(
                    role=Role.ASSISTANT,
                    content="",
                    tool_calls=[
                        ToolCall(id="loop-tc", name="read_file", arguments={"value": "stuck"})
                    ],
                ),
                Usage(
                    model=model, provider="openrouter", tokens_in=10, tokens_out=5, cost_usd=0.0001
                ),
            )
        # Avec gemini-4-argon (relance) ou vérificateur, il réussit tout de suite
        return (
            Message(role=Role.ASSISTANT, content="Problème débloqué et résolu.\nVERDICT: PASS"),
            Usage(model=model, provider="openrouter", tokens_in=10, tokens_out=10, cost_usd=0.0001),
        )

    provider = _ScriptedGoalProvider(_handler)
    db, router, memory_engine, registry = _make_db_and_router(tmp_path, provider)
    router.providers["gemini"] = provider
    manager = GoalManager(
        router=router,
        tool_registry=registry,
        memory_engine=memory_engine,
        database=db,
        watchdog_interval_s=0.1,
    )

    rec = manager.start_goal("Corriger un bug tenace", max_cost_usd=1.0)
    for _ in range(100):
        cur = manager.get_goal(rec.id)
        if cur and cur.status == "completed":
            break
        await asyncio.sleep(0.05)

    final_rec = manager.get_goal(rec.id)
    assert final_rec is not None
    assert final_rec.status == "completed", (
        final_rec.status,
        final_rec.pause_reason,
        final_rec.final_summary,
        [(e.kind, e.message) for e in final_rec.events],
    )
    assert restarted_with_argon is True
    assert any(ev.kind in ("watchdog", "switch_model") for ev in final_rec.events)


async def test_supervisor_watchdog_detects_stall_timeout(tmp_path: Path) -> None:
    async def _slow_handler(
        model: str, messages: list[Message], tools: list[dict[str, Any]] | None
    ) -> tuple[Message, Usage]:
        _ = (model, messages, tools)
        await asyncio.sleep(2.0)
        return Message(role=Role.ASSISTANT, content="trop tard"), Usage()

    provider = _ScriptedGoalProvider(_slow_handler)
    _, router, _, registry = _make_db_and_router(tmp_path, provider)
    catalog = ModelSelectionCatalog()
    supervisor = SubagentSupervisor(
        goal_id="goal_stall_test",
        router=router,
        tool_registry=registry,
        catalog=catalog,
        watchdog_interval_s=0.05,
        default_stall_timeout_s=0.15,
        default_step_timeout_s=5.0,
    )
    supervisor.start_watchdog()
    try:
        run = supervisor.spawn_subagent(
            subtask_id="s_slow",
            title="Tâche lente",
            instructions="Bloque volontairement",
            role_profile="research",
            stall_timeout_s=0.15,
        )
        ev = await asyncio.wait_for(supervisor.events.get(), timeout=2.0)
        assert ev.kind == "stalled"
        assert ev.run_id == run.id
        assert (
            "ralenti" in ev.detail.lower() or "inactif" in ev.detail.lower() or ev.kind == "stalled"
        )
    finally:
        await supervisor.stop_all()


async def test_goal_budget_pause_and_resume(tmp_path: Path) -> None:
    phase = 0

    async def _handler(
        model: str, messages: list[Message], tools: list[dict[str, Any]] | None
    ) -> tuple[Message, Usage]:
        nonlocal phase
        phase += 1
        if phase == 1:
            return (
                Message(
                    role=Role.ASSISTANT,
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="p1",
                            name="plan_goal",
                            arguments={
                                "title": "Tâche coûteuse",
                                "success_criteria": ["Terminée après rallonge"],
                                "subtasks": [
                                    {
                                        "id": "s1",
                                        "title": "Étape 1",
                                        "instructions": "Travail simple",
                                    }
                                ],
                            },
                        )
                    ],
                ),
                # Coût > budget initial 0.01 -> pause_budget immédiate à l'itération suivante
                Usage(
                    model=model, provider="openrouter", tokens_in=100, tokens_out=50, cost_usd=0.02
                ),
            )
        is_orch = "plan_goal" in {
            t["function"]["name"]
            for t in (tools or [])
            if isinstance(t, dict) and isinstance(t.get("function"), dict)
        }
        if is_orch and not any(
            isinstance(out, dict) and out.get("subtask_id") == "s1"
            for out in _tool_outputs(messages)
        ):
            return (
                Message(
                    role=Role.ASSISTANT,
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="m1",
                            name="mark_subtask",
                            arguments={"subtask_id": "s1", "status": "completed", "summary": "OK"},
                        )
                    ],
                ),
                Usage(
                    model=model, provider="openrouter", tokens_in=15, tokens_out=10, cost_usd=0.001
                ),
            )
        if is_orch and not _has_verified_pass(messages):
            return (
                Message(
                    role=Role.ASSISTANT,
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="v1",
                            name="run_verification",
                            arguments={"instructions": "Vérifie après reprise"},
                        )
                    ],
                ),
                Usage(
                    model=model, provider="openrouter", tokens_in=20, tokens_out=10, cost_usd=0.001
                ),
            )
        if is_orch:
            return (
                Message(
                    role=Role.ASSISTANT,
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="complete_goal",
                            arguments={"final_summary": "Objectif terminé après rallonge budget."},
                        )
                    ],
                ),
                Usage(
                    model=model, provider="openrouter", tokens_in=10, tokens_out=10, cost_usd=0.001
                ),
            )
        return (
            Message(role=Role.ASSISTANT, content="Vérifié.\nVERDICT: PASS"),
            Usage(model=model, provider="openrouter", tokens_in=10, tokens_out=10, cost_usd=0.001),
        )

    provider = _ScriptedGoalProvider(_handler)
    db, router, memory_engine, registry = _make_db_and_router(tmp_path, provider)
    manager = GoalManager(
        router=router,
        tool_registry=registry,
        memory_engine=memory_engine,
        database=db,
        watchdog_interval_s=0.1,
    )
    rec = manager.start_goal("Tâche coûteuse", max_cost_usd=0.01)
    for _ in range(50):
        cur = manager.get_goal(rec.id)
        if cur and cur.status == "paused_budget":
            break
        await asyncio.sleep(0.03)

    paused = manager.get_goal(rec.id)
    assert paused is not None
    assert paused.status == "paused_budget", (
        paused.status,
        paused.pause_reason,
        [(e.kind, e.message) for e in paused.events],
    )

    manager.steer_goal(rec.id, "Budget augmenté, finalise.", resume=True, extra_budget_usd=0.5)
    for _ in range(60):
        cur = manager.get_goal(rec.id)
        if cur and cur.status == "completed":
            break
        await asyncio.sleep(0.03)

    completed = manager.get_goal(rec.id)
    assert completed is not None
    assert completed.status == "completed"


def test_goals_rest_endpoints_and_toolkit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JARVIS_API_TOKEN", "")
    monkeypatch.setenv("JARVIS_ALLOW_INSECURE", "1")
    app = create_app()
    with TestClient(app) as client:
        models_resp = client.get("/api/goals/models")
        assert models_resp.status_code == 200
        models_data = models_resp.json()
        assert isinstance(models_data, list)

        create_resp = client.post(
            "/api/goals",
            json={"goal": "Vérifier l'API REST des objectifs", "max_cost_usd": 1.25},
        )
        assert create_resp.status_code == 200
        goal = create_resp.json()
        gid = goal["id"]
        assert goal["max_cost_usd"] == 1.25

        list_resp = client.get("/api/goals")
        assert list_resp.status_code == 200
        assert any(item["id"] == gid for item in list_resp.json())

        get_resp = client.get(f"/api/goals/{gid}")
        assert get_resp.status_code == 200

        pause_resp = client.post(f"/api/goals/{gid}/pause")
        assert pause_resp.status_code == 200

        cancel_resp = client.post(f"/api/goals/{gid}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"

        stop_resp = client.post("/api/autonomy/emergency-stop")
        assert stop_resp.status_code == 200
        assert stop_resp.json()["emergency_stop"] is True

        budget_get = client.get("/api/usage/budget")
        assert budget_get.status_code == 200
        assert budget_get.json()["daily_budget_usd"] == 5.0

        budget_put = client.put("/api/usage/budget", json={"daily_budget_usd": 0.0})
        assert budget_put.status_code == 200
        assert budget_put.json()["daily_budget_usd"] == 0.0


async def test_cli_subagent_agy_and_hermes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cmd_agy, cwd_agy = build_cli_subagent_command(
        agent="agy",
        prompt="Analyse le repo",
        workdir=str(tmp_path),
        mode="plan",
        executable="/usr/local/bin/agy",
    )
    assert cmd_agy[:5] == ["/usr/local/bin/agy", "-p", "Analyse le repo", "--output-format", "text"]
    assert "--mode" in cmd_agy and "plan" in cmd_agy
    assert cwd_agy == str(tmp_path.resolve())

    cmd_hermes, _ = build_cli_subagent_command(
        agent="hermes",
        prompt="Corrige le test",
        executable="/usr/local/bin/hermes",
    )
    assert "-z" in cmd_hermes and "Corrige le test" in cmd_hermes
    assert "deepseek/deepseek-v4.1-flash-1017" in cmd_hermes
    assert "openrouter" in cmd_hermes

    cmd_qwen, _ = build_cli_subagent_command(
        agent="hermes",
        prompt="Tâche locale",
        model="qwen-3.5-2b",
        executable="/usr/local/bin/hermes",
    )
    assert "qwen3.5:2b" in cmd_qwen and "ollama" in cmd_qwen

    # Profils et enregistrement
    assert "run_cli_subagent" in TOOL_PROFILES["cli_agent"]
    assert "run_cli_subagent" in TOOL_PROFILES["coder"]
    reg = ToolRegistry()
    CLIAgentToolkit().register_all(reg)
    assert reg.get("run_cli_subagent") is not None

    # Execution mockee
    class _FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"OK_CLI_RESULT", b""

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")

    async def _fake_exec(*args: Any, **kwargs: Any) -> _FakeProc:
        _ = (args, kwargs)
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    res = await execute_cli_subagent(RunCliSubagentArgs(agent="hermes", prompt="ping"))
    assert res["ok"] is True
    assert res["output"] == "OK_CLI_RESULT"
    assert res["model"] == "deepseek/deepseek-v4.1-flash-1017"
