"""Boucle agent a limite adaptative, execution parallele d'outils, streaming TTS et sous-agents."""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis_shared.models import (
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

from jarvis_core.llm.base import BudgetExceededError, ProviderError
from jarvis_core.llm.router import LLMRouter
from jarvis_core.tools.registry import (
    TOOL_USAGE_KEY,
    ToolRegistry,
    ToolSpec,
    contains_untrusted_tags,
    extract_ask_flag,
    wrap_untrusted_content,
)

_CURRENT_SUBAGENT_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar(
    "jarvis_subagent_depth", default=0
)
_CURRENT_HAS_UNTRUSTED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "jarvis_has_untrusted", default=False
)


def ensure_concise_response(text: str, max_chars: int = 320, max_sentences: int = 2) -> str:
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
    completed_normally: bool = True
    error: str | None = None


class SubagentStuckError(RuntimeError):
    """Levee lorsqu'un sous-agent supervise tourne en boucle, enchaine les erreurs ou depasse son delai."""

    def __init__(self, reason: str, detail: str, recent_tools: list[str] | None = None) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.recent_tools = recent_tools or []


class AgentLoop:
    """Cycle d'execution d'un tour d'agent avec defense anti-injection et sous-agents."""

    MAX_ITERATIONS = 8
    BASE_ITERATIONS = 12
    ADAPTIVE_MAX_ITERATIONS = 64
    ADAPTIVE_STEP_INCREMENT = 8
    AUTONOMOUS_MAX_ITERATIONS = 200
    LLM_STEP_TIMEOUT_S = 120.0

    @staticmethod
    def _prune_old_snapshots(messages: list[Message], keep_recent: int = 2) -> list[Message]:
        """Ne garde en entier que les N derniers browser_snapshot pour eviter l'explosion du contexte."""
        seen = 0
        pruned: list[Message] = []
        for msg in reversed(messages):
            if msg.role == Role.TOOL and msg.tool_results:
                new_results: list[ToolResult] = []
                changed = False
                for tr in reversed(msg.tool_results):
                    if tr.name == "browser_snapshot":
                        seen += 1
                        if seen > keep_recent:
                            changed = True
                            new_results.append(
                                tr.model_copy(
                                    update={
                                        "output": {
                                            "ok": True,
                                            "snapshot": "[snapshot précédent omis pour alléger le contexte]",
                                        }
                                    }
                                )
                            )
                            continue
                    new_results.append(tr)
                if changed:
                    new_results.reverse()
                    pruned.append(msg.model_copy(update={"tool_results": new_results}))
                    continue
            pruned.append(msg)
        pruned.reverse()
        return pruned

    def __init__(
        self,
        router: LLMRouter,
        tool_registry: ToolRegistry,
        memory_engine: Any = None,
        max_iterations: int | None = None,
        on_state_change: Callable[[AgentState], Any] | None = None,
        on_sentence_ready: Callable[[str], Any] | None = None,
        on_tool_call: Callable[[ToolCall], Any] | None = None,
        on_tool_result: Callable[[ToolResult], Any] | None = None,
        subagent_depth: int = 0,
        allow_extended_iterations: bool = False,
        adaptive_iterations: bool = True,
        register_delegate_tool: bool = True,
        allowed_tools: set[str] | None = None,
        excluded_tools: set[str] | None = None,
        system_prompt_override: str | None = None,
    ):
        self.router = router
        self.tool_registry = tool_registry
        self.memory_engine = memory_engine
        self.allow_extended_iterations = allow_extended_iterations
        if max_iterations is None:
            self.max_iterations: int | None = None
            self.adaptive_iterations = adaptive_iterations
        else:
            self.max_iterations = max(1, int(max_iterations))
            self.adaptive_iterations = adaptive_iterations and allow_extended_iterations
        self.on_state_change = on_state_change
        self.on_sentence_ready = on_sentence_ready
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result
        self.subagent_depth = subagent_depth
        self.allowed_tools = allowed_tools
        self.excluded_tools = excluded_tools
        self.system_prompt_override = system_prompt_override
        self.history: list[Message] = []
        self._turn_lock = asyncio.Lock()
        if register_delegate_tool:
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
            _ = kwargs
            if self.tool_registry.autonomy_policy.emergency_stop:
                return {
                    "ok": False,
                    "error": "Arret d'urgence actif : delegation suspendue par l'utilisateur.",
                }
            current_depth = max(self.subagent_depth, _CURRENT_SUBAGENT_DEPTH.get())
            if current_depth >= 2:
                return {
                    "ok": False,
                    "error": "Profondeur maximale de sous-agents atteinte (2 niveaux maximum).",
                }
            inherited_untrusted = _CURRENT_HAS_UNTRUSTED.get()
            sub_task = task
            if context:
                sub_task = f"Contexte:\n{context}\n\nSous-tâche:\n{task}"

            turn_res = await self.delegate(
                task=sub_task,
                role_name=role,
                max_iterations=None,
                depth=current_depth + 1,
                is_untrusted_input=inherited_untrusted,
            )
            result_text = (
                wrap_untrusted_content("subagent", turn_res.text)
                if (turn_res.had_untrusted_context or inherited_untrusted)
                else turn_res.text
            )
            out: dict[str, Any] = {
                "ok": True,
                "result": result_text,
                "tool_calls_made": [tc.name for tc in turn_res.tool_calls_made],
                "had_untrusted_context": turn_res.had_untrusted_context or inherited_untrusted,
            }
            if turn_res.usages:
                out[TOOL_USAGE_KEY] = Usage(
                    model=turn_res.usages[-1].model,
                    provider=turn_res.usages[-1].provider,
                    tokens_in=sum(int(u.tokens_in) for u in turn_res.usages),
                    tokens_out=sum(int(u.tokens_out) for u in turn_res.usages),
                    cached_tokens=sum(int(u.cached_tokens) for u in turn_res.usages),
                    cost_usd=sum(float(u.cost_usd) for u in turn_res.usages),
                ).model_dump()
            return out

        self.tool_registry.register(
            ToolSpec(
                name="delegate_subagent",
                description=(
                    "Délègue une action ponctuelle (commande shell, lancement d'app/GUI/serveur, vérification de port ou modification ciblée) "
                    "ou une sous-tâche spécialisée à un sous-agent autonome rapide, sans lancer de plan lourd."
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
        *,
        autonomous_run: bool = False,
        task_id: str | None = None,
        max_iterations_override: int | None = None,
        max_cost_usd: float | None = None,
        max_tokens_budget: int | None = None,
        cancel_event: asyncio.Event | None = None,
        on_iteration: Callable[[int, str], Any] | None = None,
        preserve_full_output: bool = False,
        repeat_confirm_threshold: int = 30,
        model_override: str | None = None,
        provider_override: str | None = None,
        fallback_models_override: list[str] | None = None,
        step_timeout_s: float | None = None,
        guidance_queue: asyncio.Queue[str] | None = None,
        supervised_subagent: bool = False,
        max_consecutive_errors: int = 3,
        on_heartbeat: Callable[[dict[str, Any]], Any] | None = None,
        skip_memory_write: bool = False,
        ask_terminal_approval: bool | None = None,
    ) -> AgentTurnResult:
        if ask_terminal_approval is None:
            effective_ask, user_input = extract_ask_flag(user_input)
        else:
            effective_ask = ask_terminal_approval
        async with self._turn_lock:
            return await self._run_turn_locked(
                user_input=user_input,
                channel=channel,
                role_name=role_name,
                images=images,
                is_untrusted_input=is_untrusted_input,
                live_state=live_state,
                on_delta=on_delta,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
                autonomous_run=autonomous_run,
                task_id=task_id,
                max_iterations_override=max_iterations_override,
                max_cost_usd=max_cost_usd,
                max_tokens_budget=max_tokens_budget,
                cancel_event=cancel_event,
                on_iteration=on_iteration,
                preserve_full_output=preserve_full_output,
                repeat_confirm_threshold=repeat_confirm_threshold,
                model_override=model_override,
                provider_override=provider_override,
                fallback_models_override=fallback_models_override,
                step_timeout_s=step_timeout_s,
                guidance_queue=guidance_queue,
                supervised_subagent=supervised_subagent,
                max_consecutive_errors=max_consecutive_errors,
                on_heartbeat=on_heartbeat,
                skip_memory_write=skip_memory_write,
                ask_terminal_approval=effective_ask,
            )

    @staticmethod
    async def _cancel_in_flight(tasks: dict[str, asyncio.Task[ToolResult]]) -> None:
        pending = [t for t in tasks.values() if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        tasks.clear()

    async def _run_turn_locked(
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
        *,
        autonomous_run: bool = False,
        task_id: str | None = None,
        max_iterations_override: int | None = None,
        max_cost_usd: float | None = None,
        max_tokens_budget: int | None = None,
        cancel_event: asyncio.Event | None = None,
        on_iteration: Callable[[int, str], Any] | None = None,
        preserve_full_output: bool = False,
        repeat_confirm_threshold: int = 30,
        model_override: str | None = None,
        provider_override: str | None = None,
        fallback_models_override: list[str] | None = None,
        step_timeout_s: float | None = None,
        guidance_queue: asyncio.Queue[str] | None = None,
        supervised_subagent: bool = False,
        max_consecutive_errors: int = 3,
        on_heartbeat: Callable[[dict[str, Any]], Any] | None = None,
        skip_memory_write: bool = False,
        ask_terminal_approval: bool = False,
    ) -> AgentTurnResult:
        self._active_ask_terminal_approval = ask_terminal_approval
        self._set_state(AgentState.THINKING)

        user_msg = Message(
            role=Role.USER,
            content=user_input,
            images=images or [],
            contains_untrusted=is_untrusted_input or contains_untrusted_tags(user_input),
        )
        self.history.append(user_msg)

        require_trusted_provider = False
        # Assemblage du contexte sous budget de tokens (~6 000 tokens hors historique)
        if self.system_prompt_override is not None:
            context_messages = [
                Message(role=Role.SYSTEM, content=self.system_prompt_override),
                *self.history,
            ]
        elif self.memory_engine is not None:
            provider_trusted = self.router.is_role_provider_trusted_for_highly_private(role_name)
            if provider_trusted and self.memory_engine.query_touches_highly_private_topic(
                user_input
            ):
                require_trusted_provider = True
            context_messages = self.memory_engine.assemble_context(
                query=user_input,
                history=self.history,
                live_state=live_state or {"channel": channel},
                provider_trusted=provider_trusted,
            )
        else:
            from jarvis_core.memory.engine import DEFAULT_PERSONA_PROMPT

            context_messages = [
                Message(role=Role.SYSTEM, content=DEFAULT_PERSONA_PROMPT),
                *self.history,
            ]

        has_untrusted = user_msg.contains_untrusted or self._scan_untrusted(context_messages)
        depth_token = _CURRENT_SUBAGENT_DEPTH.set(
            max(self.subagent_depth, _CURRENT_SUBAGENT_DEPTH.get())
        )
        untrusted_token = _CURRENT_HAS_UNTRUSTED.set(has_untrusted)
        all_tool_calls: list[ToolCall] = []
        all_tool_results: list[ToolResult] = []
        usages: list[Usage] = []
        final_text = ""
        turn_error: str | None = None
        completed_normally = False
        iteration = 0
        last_step_sig: tuple[tuple[str, str], ...] | None = None
        consecutive_repeats = 0
        consecutive_errors = 0
        recent_sigs: list[tuple[tuple[str, str], ...]] = []
        effective_max_iters: int | None = (
            max(1, int(max_iterations_override))
            if max_iterations_override is not None
            else self.max_iterations
        )
        effective_step_timeout = (
            step_timeout_s if step_timeout_s is not None else self.LLM_STEP_TIMEOUT_S
        )
        effective_repeat_threshold = (
            min(repeat_confirm_threshold, 4) if supervised_subagent else repeat_confirm_threshold
        )
        total_tokens = 0
        total_cost = 0.0

        try:
            while effective_max_iters is None or iteration < effective_max_iters:
                if (cancel_event and cancel_event.is_set()) or (
                    autonomous_run and self.tool_registry.autonomy_policy.emergency_stop
                ):
                    final_text = final_text or "Tâche interrompue par arrêt d'urgence."
                    break

                if guidance_queue is not None:
                    while not guidance_queue.empty():
                        try:
                            hint = guidance_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        if hint.strip():
                            hint_msg = Message(
                                role=Role.USER,
                                content=f"[CONSIGNE DU SUPERVISEUR — APPLIQUE IMMEDIATEMENT]\n{hint.strip()}",
                            )
                            self.history.append(hint_msg)
                            context_messages.append(hint_msg)

                iteration += 1
                if on_iteration is not None:
                    iter_label = (
                        f"Itération {iteration}/{effective_max_iters}"
                        if effective_max_iters is not None
                        else f"Itération {iteration}"
                    )
                    res_cb = on_iteration(iteration, iter_label)
                    if inspect.isawaitable(res_cb):
                        await res_cb

                if on_heartbeat is not None:
                    await self._maybe_call(
                        on_heartbeat,
                        {
                            "phase": "llm_start",
                            "iteration": iteration,
                            "total_tokens": total_tokens,
                            "total_cost": total_cost,
                            "consecutive_errors": consecutive_errors,
                        },
                    )

                tools_schema = self.tool_registry.list_schemas(
                    has_untrusted_context=has_untrusted,
                    allowed_tools=self.allowed_tools,
                    excluded_tools=self.excluded_tools,
                )

                text_chunks: list[str] = []
                turn_tool_calls: list[ToolCall] = []
                in_flight_tool_tasks: dict[str, asyncio.Task[ToolResult]] = {}
                tc_cb = on_tool_call or self.on_tool_call
                allow_early_dispatch = not (autonomous_run or supervised_subagent)

                pruned_messages = self._prune_old_snapshots(context_messages, keep_recent=2)
                stream_kwargs: dict[str, Any] = {
                    "role": role_name,
                    "messages": pruned_messages,
                    "tools": tools_schema if tools_schema else None,
                }
                try:
                    sig = inspect.signature(self.router.stream)
                    accepts_varkw = any(
                        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                    )
                    if accepts_varkw or "model_override" in sig.parameters:
                        stream_kwargs["model_override"] = model_override
                    if accepts_varkw or "provider_override" in sig.parameters:
                        stream_kwargs["provider_override"] = provider_override
                    if accepts_varkw or "fallback_models_override" in sig.parameters:
                        stream_kwargs["fallback_models_override"] = fallback_models_override
                    if accepts_varkw or "timeout_s" in sig.parameters:
                        stream_kwargs["timeout_s"] = effective_step_timeout
                    if accepts_varkw or "require_trusted" in sig.parameters:
                        stream_kwargs["require_trusted"] = require_trusted_provider
                except (TypeError, ValueError):
                    pass
                budget_stopped = False
                step_failed_msg: str | None = None
                max_attempts = 1 if supervised_subagent else 3
                for attempt_idx in range(max_attempts):
                    text_chunks.clear()
                    turn_tool_calls.clear()
                    await self._cancel_in_flight(in_flight_tool_tasks)
                    try:
                        async with asyncio.timeout(effective_step_timeout):
                            async for delta in self.router.stream(**stream_kwargs):
                                if delta.text:
                                    self._set_state(AgentState.SPEAKING)
                                    text_chunks.append(delta.text)
                                    await self._maybe_call(on_delta, delta.text)
                                    if self.on_sentence_ready:
                                        self.on_sentence_ready(delta.text)
                                if delta.tool_calls:
                                    turn_tool_calls.extend(delta.tool_calls)
                                    if allow_early_dispatch:
                                        _CURRENT_HAS_UNTRUSTED.set(has_untrusted)
                                        for tc in delta.tool_calls:
                                            if tc.id not in in_flight_tool_tasks:
                                                await self._maybe_call(tc_cb, tc)
                                                in_flight_tool_tasks[tc.id] = asyncio.create_task(
                                                    self.tool_registry.execute(
                                                        tc,
                                                        has_untrusted_context=has_untrusted,
                                                        autonomous_run=autonomous_run,
                                                        task_id=task_id,
                                                        allowed_tools=self.allowed_tools,
                                                        excluded_tools=self.excluded_tools,
                                                        ask_terminal_approval=ask_terminal_approval,
                                                    )
                                                )
                                if delta.usage:
                                    usages.append(delta.usage)
                                    total_tokens += int(delta.usage.tokens_in) + int(
                                        delta.usage.tokens_out
                                    )
                                    total_cost += float(delta.usage.cost_usd)
                        step_failed_msg = None
                        break
                    except BudgetExceededError as exc:
                        await self._cancel_in_flight(in_flight_tool_tasks)
                        if supervised_subagent:
                            raise
                        budget_stopped = True
                        turn_error = str(exc)
                        if not final_text:
                            final_text = (
                                f"Plafond journalier atteint ({exc.limit_usd:.2f} $ par fournisseur IA). "
                                f"La tâche s'est arrêtée proprement après {iteration} étape(s)."
                            )
                        break
                    except (TimeoutError, asyncio.TimeoutError) as exc:
                        await self._cancel_in_flight(in_flight_tool_tasks)
                        if supervised_subagent:
                            raise SubagentStuckError(
                                "step_timeout",
                                f"Dépassement de délai LLM ({effective_step_timeout:.0f}s) à l'itération {iteration}.",
                                recent_tools=[tc.name for tc in all_tool_calls[-5:]],
                            ) from exc
                        if attempt_idx + 1 < max_attempts:
                            await asyncio.sleep(1.5 * (attempt_idx + 1))
                            continue
                        turn_error = "timeout"
                        step_failed_msg = (
                            final_text
                            or "Le modèle n'a pas répondu dans les délais ; demande-moi de reprendre pour continuer."
                        )
                    except ProviderError as exc:
                        await self._cancel_in_flight(in_flight_tool_tasks)
                        if supervised_subagent:
                            raise
                        if attempt_idx + 1 < max_attempts:
                            await asyncio.sleep(1.5 * (attempt_idx + 1))
                            continue
                        turn_error = str(exc)
                        step_failed_msg = (
                            final_text
                            or f"Fournisseur IA momentanément indisponible ({exc}). Demande-moi de reprendre."
                        )

                if budget_stopped:
                    await self._cancel_in_flight(in_flight_tool_tasks)
                    break
                if step_failed_msg is not None:
                    await self._cancel_in_flight(in_flight_tool_tasks)
                    final_text = step_failed_msg
                    break

                step_text = "".join(text_chunks).strip()
                if step_text:
                    final_text = step_text

                if not turn_tool_calls:
                    await self._cancel_in_flight(in_flight_tool_tasks)
                    if not preserve_full_output:
                        final_text = ensure_concise_response(final_text)
                    assistant_msg = Message(role=Role.ASSISTANT, content=final_text)
                    self.history.append(assistant_msg)
                    completed_normally = True
                    break

                if max_tokens_budget is not None and total_tokens >= max_tokens_budget:
                    await self._cancel_in_flight(in_flight_tool_tasks)
                    final_text = (
                        f"{final_text}\n\n[Budget de tokens atteint : {total_tokens}/{max_tokens_budget}]"
                    ).strip()
                    break
                if max_cost_usd is not None and total_cost >= max_cost_usd:
                    await self._cancel_in_flight(in_flight_tool_tasks)
                    final_text = (
                        f"{final_text}\n\n[Budget coût atteint : ${total_cost:.4f}/${max_cost_usd:.2f}]"
                    ).strip()
                    break

                # Detecter une repetition exacte d'etape ou un cycle court (longueur 1..4)
                step_sig = tuple(
                    (
                        tc.name,
                        json.dumps(tc.arguments, sort_keys=True, ensure_ascii=False, default=str),
                    )
                    for tc in turn_tool_calls
                )
                recent_sigs.append(step_sig)
                if len(recent_sigs) > 60:
                    recent_sigs.pop(0)
                if step_sig == last_step_sig:
                    consecutive_repeats += 1
                else:
                    last_step_sig = step_sig
                    consecutive_repeats = 1

                cycle_detected = False
                if effective_repeat_threshold > 0:
                    if consecutive_repeats >= effective_repeat_threshold:
                        cycle_detected = True
                    elif autonomous_run or supervised_subagent:
                        needed_cycles = 3 if supervised_subagent else 8
                        for k in (2, 3, 4):
                            if len(recent_sigs) >= k * needed_cycles:
                                pat = recent_sigs[-k:]
                                if all(
                                    recent_sigs[-k * (c + 1) : len(recent_sigs) - k * c] == pat
                                    for c in range(needed_cycles)
                                ):
                                    cycle_detected = True
                                    break

                if cycle_detected:
                    await self._cancel_in_flight(in_flight_tool_tasks)
                    if supervised_subagent:
                        raise SubagentStuckError(
                            "loop_detected",
                            (
                                f"Boucle d'actions répétées détectée sur {[tc.name for tc in turn_tool_calls]} "
                                f"({consecutive_repeats} répétitions)."
                            ),
                            recent_tools=[tc.name for tc in all_tool_calls[-6:]],
                        )
                    final_text = (
                        final_text
                        or "J'ai atteint une répétition d'actions identiques et je me suis arrêté proprement pour éviter de boucler."
                    )
                    completed_normally = True
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

                _CURRENT_HAS_UNTRUSTED.set(has_untrusted)
                for tc in turn_tool_calls:
                    if tc.id not in in_flight_tool_tasks:
                        await self._maybe_call(tc_cb, tc)

                async def _await_or_exec(tc: ToolCall) -> ToolResult:
                    existing = in_flight_tool_tasks.get(tc.id)
                    if existing is not None:
                        return await existing
                    return await self.tool_registry.execute(
                        tc,
                        has_untrusted_context=has_untrusted,
                        autonomous_run=autonomous_run,
                        task_id=task_id,
                        allowed_tools=self.allowed_tools,
                        excluded_tools=self.excluded_tools,
                        ask_terminal_approval=ask_terminal_approval,
                    )

                try:
                    results = await asyncio.gather(*[_await_or_exec(tc) for tc in turn_tool_calls])
                except BaseException:
                    await self._cancel_in_flight(in_flight_tool_tasks)
                    raise
                tr_cb = on_tool_result or self.on_tool_result
                iter_all_failed = bool(results)
                last_err_text = ""
                for res in results:
                    await self._maybe_call(tr_cb, res)
                    # Cout declare par un outil (analyse media, sous-traitance...)
                    if res.usage is not None:
                        usages.append(res.usage)
                        total_tokens += int(res.usage.tokens_in) + int(res.usage.tokens_out)
                        total_cost += float(res.usage.cost_usd)
                    if res.is_untrusted or contains_untrusted_tags(str(res.output)):
                        has_untrusted = True
                        _CURRENT_HAS_UNTRUSTED.set(True)
                    all_tool_results.append(res)
                    out_ok = res.output.get("ok", True) if isinstance(res.output, dict) else True
                    if not res.is_error and out_ok is not False:
                        iter_all_failed = False
                    elif isinstance(res.output, dict) and res.output.get("error"):
                        last_err_text = str(res.output.get("error"))[:240]

                if iter_all_failed:
                    consecutive_errors += 1
                else:
                    consecutive_errors = 0

                if on_heartbeat is not None:
                    await self._maybe_call(
                        on_heartbeat,
                        {
                            "phase": "tools_done",
                            "iteration": iteration,
                            "last_tool": turn_tool_calls[-1].name if turn_tool_calls else "",
                            "total_tokens": total_tokens,
                            "total_cost": total_cost,
                            "consecutive_errors": consecutive_errors,
                        },
                    )

                if supervised_subagent and consecutive_errors >= max_consecutive_errors:
                    raise SubagentStuckError(
                        "error_thrashing",
                        (
                            f"{consecutive_errors} erreurs d'outils consécutives "
                            f"(dernière : {last_err_text or 'échec outil'})."
                        ),
                        recent_tools=[tc.name for tc in all_tool_calls[-6:]],
                    )

                tool_msg = Message(
                    role=Role.TOOL,
                    tool_results=list(results),
                    contains_untrusted=has_untrusted,
                )
                self.history.append(tool_msg)
                context_messages.append(tool_msg)

                if not supervised_subagent and consecutive_repeats in (6, 12, 18):
                    nudge_msg = Message(
                        role=Role.USER,
                        content=(
                            "[SYSTÈME] Tu viens de répéter la même action plusieurs fois sans avancer. "
                            "Change d'approche, vérifie l'état actuel ou conclus proprement si la tâche est terminée."
                        ),
                    )
                    self.history.append(nudge_msg)
                    context_messages.append(nudge_msg)

                # Extension adaptative du budget si une limite explicite a ete fournie
                if (
                    effective_max_iters is not None
                    and iteration >= effective_max_iters
                    and self.adaptive_iterations
                    and max_iterations_override is None
                    and consecutive_errors == 0
                    and consecutive_repeats <= 1
                ):
                    adaptive_cap = (
                        self.AUTONOMOUS_MAX_ITERATIONS
                        if (autonomous_run or self.allow_extended_iterations)
                        else self.ADAPTIVE_MAX_ITERATIONS
                    )
                    if effective_max_iters < adaptive_cap:
                        effective_max_iters = min(
                            effective_max_iters + self.ADAPTIVE_STEP_INCREMENT,
                            adaptive_cap,
                        )
        finally:
            _CURRENT_SUBAGENT_DEPTH.reset(depth_token)
            _CURRENT_HAS_UNTRUSTED.reset(untrusted_token)
            # Borner self.history en RAM pour eviter une croissance infinie
            if len(self.history) > 200:
                del self.history[:-200]

        if (
            not completed_normally
            and not final_text
            and effective_max_iters is not None
            and iteration >= effective_max_iters
        ):
            final_text = (
                f"Limite d'étapes atteinte ({iteration}/{effective_max_iters}) avant la fin complète de la tâche. "
                "Demande-moi de continuer sur les onglets ouverts pour reprendre sans tout refaire."
            )

        # Persistance selective : jamais d'extraction automatique sur contexte non fiable (<untrusted>)
        if (
            not skip_memory_write
            and not has_untrusted
            and self.memory_engine is not None
            and final_text
            and self.memory_engine.should_remember_turn(user_input, final_text)
        ):
            if all_tool_calls or self.memory_engine.has_explicit_memory_intent(user_input):
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

        if not preserve_full_output:
            final_text = ensure_concise_response(final_text)
        self._set_state(AgentState.IDLE)
        return AgentTurnResult(
            text=final_text,
            tool_calls_made=all_tool_calls,
            tool_results=all_tool_results,
            iterations=iteration,
            had_untrusted_context=has_untrusted,
            usages=usages,
            completed_normally=completed_normally,
            error=turn_error,
        )

    async def delegate(
        self,
        task: str,
        role_name: str = "deep",
        max_iterations: int | None = None,
        *,
        depth: int | None = None,
        is_untrusted_input: bool = False,
    ) -> AgentTurnResult:
        """Lance un sous-agent isole sur une tache longue avec son propre contexte."""
        next_depth = depth if depth is not None else (self.subagent_depth + 1)
        sub_agent = AgentLoop(
            router=self.router,
            tool_registry=self.tool_registry,
            memory_engine=self.memory_engine,
            max_iterations=max_iterations,
            allow_extended_iterations=True,
            adaptive_iterations=True,
            subagent_depth=next_depth,
            register_delegate_tool=False,
        )
        return await sub_agent.run_turn(
            user_input=task,
            channel="subagent",
            role_name=role_name,
            is_untrusted_input=is_untrusted_input,
            ask_terminal_approval=getattr(self, "_active_ask_terminal_approval", False),
        )
