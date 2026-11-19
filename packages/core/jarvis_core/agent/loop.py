"""Boucle agent a limite adaptative, execution parallele d'outils, streaming TTS et sous-agents."""

from __future__ import annotations

import asyncio
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

from jarvis_core.llm.router import LLMRouter
from jarvis_core.tools.registry import ToolRegistry, ToolSpec, contains_untrusted_tags


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
            self.max_iterations = self.BASE_ITERATIONS
            self.adaptive_iterations = adaptive_iterations
        else:
            cap = (
                self.AUTONOMOUS_MAX_ITERATIONS
                if allow_extended_iterations
                else max(self.MAX_ITERATIONS, max_iterations)
            )
            self.max_iterations = min(max_iterations, cap)
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
                max_iterations=12,
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
        if self.system_prompt_override is not None:
            context_messages = [
                Message(role=Role.SYSTEM, content=self.system_prompt_override),
                *self.history,
            ]
        elif self.memory_engine is not None:
            provider_trusted = self.router.is_role_provider_trusted_for_highly_private(role_name)
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

        has_untrusted = user_msg.contains_untrusted
        all_tool_calls: list[ToolCall] = []
        all_tool_results: list[ToolResult] = []
        usages: list[Usage] = []
        final_text = ""
        completed_normally = False
        iteration = 0
        last_step_sig: tuple[tuple[str, str], ...] | None = None
        consecutive_repeats = 0
        consecutive_errors = 0
        recent_sigs: list[tuple[tuple[str, str], ...]] = []
        effective_max_iters = (
            min(max_iterations_override, self.AUTONOMOUS_MAX_ITERATIONS)
            if max_iterations_override is not None
            else (
                self.AUTONOMOUS_MAX_ITERATIONS
                if autonomous_run and self.max_iterations == self.MAX_ITERATIONS
                else self.max_iterations
            )
        )
        effective_step_timeout = (
            step_timeout_s if step_timeout_s is not None else self.LLM_STEP_TIMEOUT_S
        )
        effective_repeat_threshold = (
            min(repeat_confirm_threshold, 4) if supervised_subagent else repeat_confirm_threshold
        )
        total_tokens = 0
        total_cost = 0.0

        while iteration < effective_max_iters:
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
                res_cb = on_iteration(iteration, f"Itération {iteration}/{effective_max_iters}")
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
            except (TypeError, ValueError):
                pass
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
                                            )
                                        )
                        if delta.usage:
                            usages.append(delta.usage)
                            total_tokens += int(delta.usage.tokens_in) + int(delta.usage.tokens_out)
                            total_cost += float(delta.usage.cost_usd)
            except (TimeoutError, asyncio.TimeoutError) as exc:
                if supervised_subagent:
                    raise SubagentStuckError(
                        "step_timeout",
                        f"Dépassement de délai LLM ({effective_step_timeout:.0f}s) à l'itération {iteration}.",
                        recent_tools=[tc.name for tc in all_tool_calls[-5:]],
                    ) from exc
                raise

            step_text = "".join(text_chunks).strip()
            if step_text:
                final_text = step_text

            if not turn_tool_calls:
                if not preserve_full_output:
                    final_text = ensure_concise_response(final_text)
                assistant_msg = Message(role=Role.ASSISTANT, content=final_text)
                self.history.append(assistant_msg)
                completed_normally = True
                break

            if max_tokens_budget is not None and total_tokens >= max_tokens_budget:
                final_text = (
                    f"{final_text}\n\n[Budget de tokens atteint : {total_tokens}/{max_tokens_budget}]"
                ).strip()
                break
            if max_cost_usd is not None and total_cost >= max_cost_usd:
                final_text = (
                    f"{final_text}\n\n[Budget coût atteint : ${total_cost:.4f}/${max_cost_usd:.2f}]"
                ).strip()
                break

            # Detecter une repetition exacte d'etape ou un cycle court (longueur 1..4)
            step_sig = tuple(
                (tc.name, json.dumps(tc.arguments, sort_keys=True, ensure_ascii=False, default=str))
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
            if (autonomous_run or supervised_subagent) and effective_repeat_threshold > 0:
                if consecutive_repeats >= effective_repeat_threshold:
                    cycle_detected = True
                else:
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
                if supervised_subagent:
                    raise SubagentStuckError(
                        "loop_detected",
                        (
                            f"Boucle d'actions répétées détectée sur {[tc.name for tc in turn_tool_calls]} "
                            f"({consecutive_repeats} répétitions)."
                        ),
                        recent_tools=[tc.name for tc in all_tool_calls[-6:]],
                    )
                approval_mgr = getattr(self.tool_registry, "approval_manager", None)
                decision = "timeout"
                if approval_mgr is not None:
                    req = approval_mgr.create_request(
                        tool_name="autonomous_loop_continue",
                        arguments={"tool": turn_tool_calls[0].name, "repeats": consecutive_repeats},
                        description=(
                            f"Jarvis répète une boucle d'actions ({turn_tool_calls[0].name}). "
                            "Autoriser à continuer ?"
                        ),
                        risk=RiskLevel.SENSITIVE,
                    )
                    decision = await approval_mgr.wait_for_decision(
                        req.id,
                        timeout=getattr(self.tool_registry, "approval_timeout", None),
                    )
                if decision == "approved":
                    consecutive_repeats = 0
                    recent_sigs.clear()
                else:
                    final_text = (
                        f"Boucle répétée détectée ({turn_tool_calls[0].name}) "
                        f"sans confirmation ({decision}). Dis-moi si je dois continuer."
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
                )

            results = await asyncio.gather(*[_await_or_exec(tc) for tc in turn_tool_calls])
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

            # Extension adaptative du budget si l'agent progresse sans erreur ni boucle
            if (
                iteration >= effective_max_iters
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

        if not completed_normally and not final_text and iteration >= effective_max_iters:
            final_text = (
                f"Limite d'étapes atteinte ({iteration}/{effective_max_iters}) avant la fin complète de la tâche. "
                "Demande-moi de continuer sur les onglets ouverts pour reprendre sans tout refaire."
            )

        # Persistance selective : episodes uniquement pour les actions reelles ou demandes explicites
        if (
            not skip_memory_write
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
        )

    async def delegate(
        self,
        task: str,
        role_name: str = "deep",
        max_iterations: int = 12,
    ) -> AgentTurnResult:
        """Lance un sous-agent isole sur une tache longue avec son propre budget d'iterations."""
        sub_agent = AgentLoop(
            router=self.router,
            tool_registry=self.tool_registry,
            memory_engine=self.memory_engine,
            max_iterations=max_iterations,
            allow_extended_iterations=True,
            adaptive_iterations=True,
            subagent_depth=self.subagent_depth + 1,
        )
        return await sub_agent.run_turn(
            user_input=task,
            channel="subagent",
            role_name=role_name,
        )
