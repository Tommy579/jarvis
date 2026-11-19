"""Superviseur et Watchdog temps reel pour les sous-agents autonomes."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis_shared.models import utc_now_iso
from jarvis_shared.rest_models import SubagentRunRecord

from jarvis_core.agent.loop import AgentLoop, SubagentStuckError
from jarvis_core.llm.catalog import ModelSelectionCatalog
from jarvis_core.llm.router import LLMRouter
from jarvis_core.tools.registry import ToolRegistry

ORCHESTRATOR_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "start_goal",
        "list_goals",
        "get_goal_status",
        "steer_goal",
        "pause_goal",
        "resume_goal",
        "cancel_goal",
        "run_autonomous_task",
        "list_autonomous_tasks",
        "cancel_autonomous_task",
        "delegate_subagent",
    }
)

TOOL_PROFILES: dict[str, set[str]] = {
    "research": {
        "web_search",
        "web_fetch",
        "memory_search",
        "read_file",
        "list_directory",
        "search_files",
        "browser_list_tabs",
        "browser_snapshot",
    },
    "coder": {
        "read_file",
        "write_file",
        "list_directory",
        "search_files",
        "run_command",
        "run_cli_subagent",
        "memory_search",
    },
    "browser": {
        "browser_list_tabs",
        "browser_snapshot",
        "browser_navigate",
        "browser_click",
        "browser_fill",
        "browser_select",
        "browser_drag",
        "browser_scroll",
        "browser_handle_dialog",
        "browser_analyze_media",
        "browser_launch",
        "web_search",
        "memory_search",
    },
    "writer": {
        "read_file",
        "write_file",
        "list_directory",
        "memory_search",
        "memory_save",
    },
    "verifier": {
        "read_file",
        "list_directory",
        "search_files",
        "run_command",
        "browser_list_tabs",
        "browser_snapshot",
        "memory_search",
        "get_system_stats",
        "list_processes",
    },
    "system": {
        "read_file",
        "write_file",
        "list_directory",
        "search_files",
        "run_command",
        "run_cli_subagent",
        "get_system_stats",
        "list_processes",
        "send_notification",
        "desktop_list_windows",
        "desktop_snapshot",
        "desktop_action",
    },
    "cli_agent": {
        "run_cli_subagent",
        "read_file",
        "list_directory",
        "search_files",
    },
}


@dataclass
class SupervisorEvent:
    """Evenement emis vers l'orchestrateur lorsqu'un sous-agent termine, ralentit ou deraille."""

    kind: str  # "completed" | "stuck" | "stalled" | "failed" | "cancelled"
    goal_id: str
    subtask_id: str
    run_id: str
    model: str
    reason: str = ""
    detail: str = ""
    result_summary: str = ""
    recent_tools: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    tokens_used: int = 0


@dataclass
class _WorkerHandle:
    run: SubagentRunRecord
    task: asyncio.Task[Any] | None = None
    guidance_queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    last_heartbeat_mono: float = field(default_factory=time.monotonic)
    stall_timeout_s: float = 45.0
    step_timeout_s: float = 40.0
    instructions: str = ""
    stall_reported: bool = False


class SubagentSupervisor:
    """Gere un pool asynchrone de sous-agents isoles avec Watchdog anti-blocage et anti-boucle."""

    def __init__(
        self,
        goal_id: str,
        router: LLMRouter,
        tool_registry: ToolRegistry,
        catalog: ModelSelectionCatalog,
        memory_engine: Any = None,
        *,
        watchdog_interval_s: float = 1.0,
        default_stall_timeout_s: float = 55.0,
        default_step_timeout_s: float = 40.0,
        on_run_update: Callable[[SubagentRunRecord], Any] | None = None,
        on_cost_delta: Callable[[float, int], Any] | None = None,
    ) -> None:
        self.goal_id = goal_id
        self.router = router
        self.tool_registry = tool_registry
        self.catalog = catalog
        self.memory_engine = memory_engine
        self.watchdog_interval_s = watchdog_interval_s
        self.default_stall_timeout_s = default_stall_timeout_s
        self.default_step_timeout_s = default_step_timeout_s
        self.on_run_update = on_run_update
        self.on_cost_delta = on_cost_delta

        self.events: asyncio.Queue[SupervisorEvent] = asyncio.Queue()
        self._workers: dict[str, _WorkerHandle] = {}
        self._watchdog_task: asyncio.Task[Any] | None = None
        self._closed = False

    def start_watchdog(self) -> None:
        if self._watchdog_task is None or self._watchdog_task.done():
            self._closed = False
            self._watchdog_task = asyncio.create_task(
                self._watchdog_loop(), name=f"jarvis-watchdog-{self.goal_id}"
            )

    async def stop_all(self) -> None:
        self._closed = True
        if self._watchdog_task is not None and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
        for handle in list(self._workers.values()):
            handle.cancel_event.set()
            if handle.task is not None and not handle.task.done():
                handle.task.cancel()
        for handle in list(self._workers.values()):
            if handle.task is not None:
                try:
                    await handle.task
                except (asyncio.CancelledError, Exception):
                    pass

    def resolve_allowed_tools(self, role_profile: str) -> set[str]:
        all_tools = set(getattr(self.tool_registry, "_tools", {}).keys()) - ORCHESTRATOR_ONLY_TOOLS
        profile = (role_profile or "general").strip().lower()
        if profile == "general" or profile not in TOOL_PROFILES:
            return all_tools
        allowed = set(TOOL_PROFILES[profile])
        if profile == "browser":
            allowed |= {t for t in all_tools if t.startswith("mcp_playwright_")}
        # Conserver uniquement les outils effectivement enregistres
        return (allowed & all_tools) or all_tools

    def active_runs(self) -> list[SubagentRunRecord]:
        return [
            h.run
            for h in self._workers.values()
            if h.task is not None and not h.task.done() and h.run.status == "running"
        ]

    def get_run(self, run_id: str) -> SubagentRunRecord | None:
        h = self._workers.get(run_id)
        return h.run if h else None

    def latest_run_for_subtask(self, subtask_id: str) -> SubagentRunRecord | None:
        matches = [h.run for h in self._workers.values() if h.run.subtask_id == subtask_id]
        return matches[-1] if matches else None

    def _emit_run_update(self, run: SubagentRunRecord) -> None:
        if self.on_run_update:
            try:
                self.on_run_update(run)
            except Exception:
                pass

    async def _watchdog_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(self.watchdog_interval_s)
            now = time.monotonic()
            for handle in list(self._workers.values()):
                if handle.task is None or handle.task.done() or handle.stall_reported:
                    continue
                idle_s = now - handle.last_heartbeat_mono
                if idle_s >= handle.stall_timeout_s:
                    handle.stall_reported = True
                    handle.cancel_event.set()
                    handle.run.status = "stalled"
                    handle.run.error = (
                        f"Watchdog : sous-agent silencieux depuis {idle_s:.1f}s "
                        f"(seuil {handle.stall_timeout_s:.0f}s)."
                    )
                    handle.run.finished_at = utc_now_iso()
                    self._emit_run_update(handle.run)
                    handle.task.cancel()
                    await self.events.put(
                        SupervisorEvent(
                            kind="stalled",
                            goal_id=self.goal_id,
                            subtask_id=handle.run.subtask_id,
                            run_id=handle.run.id,
                            model=handle.run.model,
                            reason="stall_timeout",
                            detail=handle.run.error,
                            cost_usd=handle.run.cost_usd,
                            tokens_used=handle.run.tokens_in + handle.run.tokens_out,
                        )
                    )

    def spawn_subagent(
        self,
        *,
        subtask_id: str,
        title: str,
        instructions: str,
        role_profile: str = "general",
        requested_model: str | None = None,
        max_iterations: int = 20,
        max_cost_usd: float = 0.5,
        step_timeout_s: float | None = None,
        stall_timeout_s: float | None = None,
    ) -> SubagentRunRecord:
        self.start_watchdog()
        require_vision = role_profile in ("browser", "vision")
        model_id = self.catalog.resolve_model(
            requested_model,
            role_profile=role_profile,
            require_vision=require_vision,
            router=self.router,
        )
        prof = self.catalog.get(model_id)
        provider_name = prof.provider if prof else ("openrouter" if "/" in model_id else "gemini")

        run_id = f"run_{uuid.uuid4().hex[:10]}"
        now = utc_now_iso()
        run = SubagentRunRecord(
            id=run_id,
            goal_id=self.goal_id,
            subtask_id=subtask_id,
            model=model_id,
            provider=provider_name,
            role_profile=role_profile,
            status="running",
            iterations=0,
            max_iterations=max(1, min(int(max_iterations), 80)),
            last_heartbeat_at=now,
            started_at=now,
        )
        handle = _WorkerHandle(
            run=run,
            stall_timeout_s=float(stall_timeout_s or self.default_stall_timeout_s),
            step_timeout_s=float(step_timeout_s or self.default_step_timeout_s),
            instructions=instructions,
        )
        self._workers[run_id] = handle
        self._emit_run_update(run)

        allowed_tools = self.resolve_allowed_tools(role_profile)
        system_prompt = (
            f"[SOUS-AGENT AUTONOME JARVIS — PROFIL: {role_profile.upper()}]\n"
            f"Tu es un sous-agent d'exécution spécialisé (modèle actif : {model_id}).\n"
            "Règles strictes :\n"
            "1. Exécute précisément la sous-tâche confiée avec les outils disponibles.\n"
            "2. Vérifie toujours le résultat réel de tes actions (tests, fichiers, snapshot) avant de conclure.\n"
            "3. Si tu reçois une `[CONSIGNE DU SUPERVISEUR]`, applique-la immédiatement.\n"
            "4. Ne devine jamais des résultats : si tu es bloqué par un manque externe impossible à contourner, "
            "explique exactement le blocage et ce que tu as déjà tenté.\n"
            "5. Rends un compte-rendu factuel, complet et structuré des preuves vérifiées."
        )

        async def _runner() -> None:
            prev_tokens = 0
            prev_cost = 0.0

            def _sync_cost(total_tok: int, total_c: float) -> None:
                nonlocal prev_tokens, prev_cost
                dtok = max(0, total_tok - prev_tokens)
                dcost = max(0.0, total_c - prev_cost)
                prev_tokens = total_tok
                prev_cost = total_c
                if (dtok > 0 or dcost > 0.0) and self.on_cost_delta:
                    self.on_cost_delta(dcost, dtok)

            def _on_hb(info: dict[str, Any]) -> None:
                handle.last_heartbeat_mono = time.monotonic()
                run.last_heartbeat_at = utc_now_iso()
                if "iteration" in info:
                    run.iterations = int(info["iteration"])
                if info.get("last_tool"):
                    run.last_tool = str(info["last_tool"])
                if "total_cost" in info:
                    run.cost_usd = round(float(info["total_cost"]), 6)
                if "total_tokens" in info:
                    tok = int(info["total_tokens"])
                    run.tokens_in = tok
                    _sync_cost(tok, float(info.get("total_cost", run.cost_usd)))
                self._emit_run_update(run)

            def _on_tc(tc: Any) -> None:
                handle.last_heartbeat_mono = time.monotonic()
                run.last_tool = tc.name
                run.last_heartbeat_at = utc_now_iso()
                self._emit_run_update(run)

            loop = AgentLoop(
                router=self.router,
                tool_registry=self.tool_registry,
                memory_engine=self.memory_engine,
                max_iterations=run.max_iterations,
                allow_extended_iterations=True,
                register_delegate_tool=False,
                allowed_tools=allowed_tools,
                system_prompt_override=system_prompt,
            )

            task_prompt = f"Sous-tâche : {title}\n\nInstructions détaillées :\n{instructions}"
            try:
                res = await loop.run_turn(
                    user_input=task_prompt,
                    channel="subagent",
                    role_name="agent",
                    autonomous_run=True,
                    task_id=f"{self.goal_id}:{subtask_id}",
                    max_iterations_override=run.max_iterations,
                    max_cost_usd=max_cost_usd,
                    cancel_event=handle.cancel_event,
                    on_tool_call=_on_tc,
                    on_heartbeat=_on_hb,
                    preserve_full_output=True,
                    model_override=model_id,
                    provider_override=provider_name,
                    fallback_models_override=[],
                    step_timeout_s=handle.step_timeout_s,
                    guidance_queue=handle.guidance_queue,
                    supervised_subagent=True,
                    skip_memory_write=True,
                )
                run.iterations = res.iterations
                run.tokens_in = sum(int(u.tokens_in) for u in res.usages)
                run.tokens_out = sum(int(u.tokens_out) for u in res.usages)
                run.cost_usd = round(sum(float(u.cost_usd) for u in res.usages), 6)
                _sync_cost(run.tokens_in + run.tokens_out, run.cost_usd)
                run.result_summary = res.text.strip()
                run.finished_at = utc_now_iso()

                if handle.cancel_event.is_set() and not handle.stall_reported:
                    run.status = "skipped"
                    self._emit_run_update(run)
                    await self.events.put(
                        SupervisorEvent(
                            kind="cancelled",
                            goal_id=self.goal_id,
                            subtask_id=subtask_id,
                            run_id=run_id,
                            model=model_id,
                            reason="cancelled",
                            detail="Sous-agent arrêté par le superviseur.",
                            cost_usd=run.cost_usd,
                            tokens_used=run.tokens_in + run.tokens_out,
                        )
                    )
                    return

                run.status = "completed"
                self._emit_run_update(run)
                await self.events.put(
                    SupervisorEvent(
                        kind="completed",
                        goal_id=self.goal_id,
                        subtask_id=subtask_id,
                        run_id=run_id,
                        model=model_id,
                        result_summary=run.result_summary,
                        cost_usd=run.cost_usd,
                        tokens_used=run.tokens_in + run.tokens_out,
                    )
                )
            except SubagentStuckError as exc:
                run.status = "stalled"
                run.error = exc.detail
                run.finished_at = utc_now_iso()
                self._emit_run_update(run)
                await self.events.put(
                    SupervisorEvent(
                        kind="stuck",
                        goal_id=self.goal_id,
                        subtask_id=subtask_id,
                        run_id=run_id,
                        model=model_id,
                        reason=exc.reason,
                        detail=exc.detail,
                        recent_tools=exc.recent_tools,
                        cost_usd=run.cost_usd,
                        tokens_used=run.tokens_in + run.tokens_out,
                    )
                )
            except asyncio.CancelledError:
                if not handle.stall_reported:
                    run.status = "skipped"
                    run.finished_at = utc_now_iso()
                    self._emit_run_update(run)
            except Exception as exc:
                run.status = "failed"
                run.error = str(exc)
                run.finished_at = utc_now_iso()
                self._emit_run_update(run)
                await self.events.put(
                    SupervisorEvent(
                        kind="failed",
                        goal_id=self.goal_id,
                        subtask_id=subtask_id,
                        run_id=run_id,
                        model=model_id,
                        reason="exception",
                        detail=str(exc),
                        cost_usd=run.cost_usd,
                        tokens_used=run.tokens_in + run.tokens_out,
                    )
                )

        handle.task = asyncio.create_task(_runner(), name=f"jarvis-sub-{run_id}")
        return run

    def steer_subagent(self, run_id: str, guidance: str) -> tuple[bool, str]:
        handle = self._workers.get(run_id)
        if not handle:
            return False, f"Run '{run_id}' introuvable."
        if handle.task is None or handle.task.done():
            return (
                False,
                f"Le run '{run_id}' est déjà terminé (statut: {handle.run.status}). "
                "Utilise restart_subagent pour le relancer avec un nouveau prompt ou modèle.",
            )
        handle.guidance_queue.put_nowait(guidance)
        handle.last_heartbeat_mono = time.monotonic()
        return True, f"Consigne injectée dans le sous-agent {run_id}."

    async def cancel_subagent(self, run_id: str) -> bool:
        handle = self._workers.get(run_id)
        if not handle:
            return False
        handle.cancel_event.set()
        if handle.task is not None and not handle.task.done():
            handle.task.cancel()
            try:
                await handle.task
            except (asyncio.CancelledError, Exception):
                pass
        return True
