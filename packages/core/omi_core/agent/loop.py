"""Boucle agent bornee (max 8 iterations), execution parallele d'outils, streaming TTS et sous-agents."""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from omi_shared.models import (
    AgentState,
    ImageAttachment,
    Message,
    RiskLevel,
    Role,
    ToolCall,
    ToolResult,
    Usage,
)
from pydantic import BaseModel, Field

from omi_core.llm.router import LLMRouter
from omi_core.tools.registry import ToolRegistry, ToolSpec, contains_untrusted_tags


def ensure_concise_response(text: str, max_chars: int = 480, max_sentences: int = 4) -> str:
    """Garantit que quel que soit le modele, la reponse reste concise avec mention [suite] si trop longue."""
    cleaned = (text or "").strip()
    if not cleaned or len(cleaned) <= max_chars:
        return cleaned

    lower = cleaned.lower()
    if "[suite]" in lower or "(suite)" in lower or "... suite" in lower or "suite..." in lower:
        return cleaned

    # Si le texte comporte plus de max_sentences phrases
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    if len(sentences) > max_sentences:
        shortened = " ".join(sentences[:max_sentences]).strip()
        if len(shortened) <= max_chars:
            return f"{shortened}\n\n... [suite]"

    # Recherche d'un point de coupure propre sous le seuil max_chars
    cut = cleaned[:max_chars].rfind(".")
    if cut == -1:
        cut = cleaned[:max_chars].rfind("!")
    if cut == -1:
        cut = cleaned[:max_chars].rfind("?")
    if cut == -1:
        cut = cleaned[:max_chars].rfind("\n")
    if cut == -1 or cut < 120:
        cut = cleaned[:max_chars].rfind(" ")

    if cut > 80:
        return f"{cleaned[:cut].strip()}\n\n... [suite]"

    return f"{cleaned[:max_chars].strip()}... [suite]"


class DelegateSubagentArgs(BaseModel):
    task: str = Field(
        min_length=1,
        description="Description claire et autonome de la sous-tâche à exécuter par le sous-agent.",
    )
    role: str = Field(
        default="deep",
        description="Rôle LLM ciblé ('deep' pour réflexion et tâches complexes, 'agent' pour le modèle général, 'memory' pour faits/wiki).",
    )
    context: str | None = Field(
        default=None,
        description="Contexte ou données spécifiques nécessaires à la réalisation de la sous-tâche.",
    )


@dataclass
class AgentTurnResult:
    text: str
    tool_calls_made: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    iterations: int = 0
    had_untrusted_context: bool = False
    usages: list[Usage] = field(default_factory=list)


class AgentLoop:
    """Cycle d'execution d'un tour d'agent avec defense anti-injection et sous-agents."""

    MAX_ITERATIONS = 8

    def __init__(
        self,
        router: LLMRouter,
        tool_registry: ToolRegistry,
        memory_engine: Any = None,
        max_iterations: int = MAX_ITERATIONS,
        on_state_change: Callable[[AgentState], Any] | None = None,
        on_sentence_ready: Callable[[str], Any] | None = None,
        on_tool_call: Callable[[ToolCall], Any] | None = None,
        on_tool_result: Callable[[ToolResult], Any] | None = None,
        subagent_depth: int = 0,
    ):
        self.router = router
        self.tool_registry = tool_registry
        self.memory_engine = memory_engine
        self.max_iterations = min(max_iterations, self.MAX_ITERATIONS)
        self.on_state_change = on_state_change
        self.on_sentence_ready = on_sentence_ready
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result
        self.subagent_depth = subagent_depth
        self.history: list[Message] = []
        self._register_subagent_tool()

    def _register_subagent_tool(self) -> None:
        """Enregistre l'outil delegate_subagent permettant au modele d'orchestrer des sous-agents."""
        if self.tool_registry is None:
            return
        if "delegate_subagent" in self.tool_registry._tools:
            return

        async def _handle_delegate_subagent(
            task: str,
            role: str = "deep",
            context: str | None = None,
            **kwargs: Any,
        ) -> dict[str, Any]:
            if self.subagent_depth >= 2:
                return {
                    "ok": False,
                    "error": "Profondeur maximale de sous-agents atteinte (2 niveaux maximum).",
                }
            sub_task = task
            if context:
                sub_task = f"Contexte:\n{context}\n\nSous-tâche:\n{task}"

            turn_res = await self.delegate(
                task=sub_task,
                role_name=role,
                max_iterations=6,
            )
            return {
                "ok": True,
                "result": turn_res.text,
                "tool_calls_made": [tc.name for tc in turn_res.tool_calls_made],
                "had_untrusted_context": turn_res.had_untrusted_context,
            }

        self.tool_registry.register(
            ToolSpec(
                name="delegate_subagent",
                description=(
                    "Délègue une sous-tâche complexe ou spécialisée à un sous-agent autonome. "
                    "Le moteur principal (modèle choisi par l'utilisateur) peut orchestrer et subdiviser "
                    "les tâches complexes en plusieurs sous-agents et agréger leurs résultats."
                ),
                handler=_handle_delegate_subagent,
                args_model=DelegateSubagentArgs,
                risk=RiskLevel.READ_ONLY,
            )
        )

    @staticmethod
    async def _maybe_call(cb: Callable[..., Any] | None, arg: Any) -> None:
        if cb is None:
            return
        res = cb(arg)
        if inspect.isawaitable(res):
            await res

    def _set_state(self, state: AgentState) -> None:
        if self.on_state_change:
            self.on_state_change(state)

    @staticmethod
    def _scan_untrusted(messages: list[Message]) -> bool:
        for msg in messages:
            if msg.contains_untrusted or contains_untrusted_tags(msg.content):
                return True
            for tr in msg.tool_results:
                if tr.is_untrusted or contains_untrusted_tags(str(tr.output)):
                    return True
        return False

    async def run_turn(
        self,
        user_input: str,
        channel: str = "web",
        role_name: str = "agent",
        images: list[ImageAttachment] | None = None,
        is_untrusted_input: bool = False,
        live_state: dict[str, Any] | None = None,
        on_delta: Callable[[str], Any] | None = None,
        on_tool_call: Callable[[ToolCall], Any] | None = None,
        on_tool_result: Callable[[ToolResult], Any] | None = None,
    ) -> AgentTurnResult:
        self._set_state(AgentState.THINKING)

        user_msg = Message(
            role=Role.USER,
            content=user_input,
            images=images or [],
            contains_untrusted=is_untrusted_input or contains_untrusted_tags(user_input),
        )
        self.history.append(user_msg)

        # Assemblage du contexte sous budget de tokens (~6 000 tokens hors historique)
        if self.memory_engine is not None:
            provider_trusted = self.router.is_role_provider_trusted_for_highly_private(role_name)
            context_messages = self.memory_engine.assemble_context(
                query=user_input,
                history=self.history,
                live_state=live_state or {"channel": channel},
                provider_trusted=provider_trusted,
            )
        else:
            from omi_core.memory.engine import DEFAULT_PERSONA_PROMPT

            context_messages = [
                Message(role=Role.SYSTEM, content=DEFAULT_PERSONA_PROMPT),
                *self.history,
            ]

        has_untrusted = self._scan_untrusted(context_messages)
        all_tool_calls: list[ToolCall] = []
        all_tool_results: list[ToolResult] = []
        usages: list[Usage] = []
        final_text = ""
        iteration = 0

        while iteration < self.max_iterations:
            iteration += 1
            tools_schema = self.tool_registry.list_schemas(has_untrusted_context=has_untrusted)

            text_chunks: list[str] = []
            turn_tool_calls: list[ToolCall] = []

            async for delta in self.router.stream(
                role=role_name,
                messages=context_messages,
                tools=tools_schema if tools_schema else None,
            ):
                if delta.text:
                    self._set_state(AgentState.SPEAKING)
                    text_chunks.append(delta.text)
                    await self._maybe_call(on_delta, delta.text)
                    if self.on_sentence_ready:
                        self.on_sentence_ready(delta.text)
                if delta.tool_calls:
                    turn_tool_calls.extend(delta.tool_calls)
                if delta.usage:
                    usages.append(delta.usage)

            step_text = "".join(text_chunks).strip()
            if step_text:
                final_text = step_text

            if not turn_tool_calls:
                final_text = ensure_concise_response(final_text)
                assistant_msg = Message(role=Role.ASSISTANT, content=final_text)
                self.history.append(assistant_msg)
                break

            # Le modele a demande un ou plusieurs appels d'outils : execution en parallele
            assistant_tc_msg = Message(
                role=Role.ASSISTANT,
                content=step_text,
                tool_calls=turn_tool_calls,
            )
            self.history.append(assistant_tc_msg)
            context_messages.append(assistant_tc_msg)
            all_tool_calls.extend(turn_tool_calls)

            tc_cb = on_tool_call or self.on_tool_call
            for tc in turn_tool_calls:
                await self._maybe_call(tc_cb, tc)

            results = await asyncio.gather(
                *[
                    self.tool_registry.execute(
                        tc,
                        has_untrusted_context=has_untrusted,
                    )
                    for tc in turn_tool_calls
                ]
            )
            tr_cb = on_tool_result or self.on_tool_result
            for res in results:
                await self._maybe_call(tr_cb, res)
                if res.is_untrusted or contains_untrusted_tags(str(res.output)):
                    has_untrusted = True
                all_tool_results.append(res)

            tool_msg = Message(
                role=Role.TOOL,
                tool_results=list(results),
                contains_untrusted=has_untrusted,
            )
            self.history.append(tool_msg)
            context_messages.append(tool_msg)

        # Persistance en memoire episodique et extraction asynchrone de faits
        if (
            self.memory_engine is not None
            and final_text
            and self.memory_engine.should_remember_turn(user_input, final_text)
        ):
            self.memory_engine.record_episode(
                kind="conversation",
                channel=channel,
                summary=f"Utilisateur: {user_input[:200]} -> Jarvis: {final_text[:200]}",
            )
            asyncio.create_task(
                self.memory_engine.extract_facts_from_turn_async(
                    user_text=user_input,
                    assistant_text=final_text,
                    router=self.router,
                )
            )

        final_text = ensure_concise_response(final_text)
        self._set_state(AgentState.IDLE)
        return AgentTurnResult(
            text=final_text,
            tool_calls_made=all_tool_calls,
            tool_results=all_tool_results,
            iterations=iteration,
            had_untrusted_context=has_untrusted,
            usages=usages,
        )

    async def delegate(
        self,
        task: str,
        role_name: str = "deep",
        max_iterations: int = 4,
    ) -> AgentTurnResult:
        """Lance un sous-agent isole sur une tache longue avec son propre budget d'iterations."""
        sub_agent = AgentLoop(
            router=self.router,
            tool_registry=self.tool_registry,
            memory_engine=self.memory_engine,
            max_iterations=min(max_iterations, 6),
            subagent_depth=self.subagent_depth + 1,
        )
        return await sub_agent.run_turn(
            user_input=task,
            channel="subagent",
            role_name=role_name,
        )
