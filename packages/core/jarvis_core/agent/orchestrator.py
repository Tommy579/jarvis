"""Orchestrateur hierarchique oriente objectifs avec supervision active de sous-agents low-cost."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from typing import Any

from jarvis_shared.models import (
    Message,
    NotificationRecord,
    RiskLevel,
    Role,
    ToolResult,
    UrgencyLevel,
    utc_now_iso,
)
from jarvis_shared.rest_models import (
    GoalEventKind,
    GoalEventRecord,
    GoalRecord,
    SubagentRunRecord,
    SubtaskRecord,
)
from pydantic import BaseModel, Field

from jarvis_core.agent.supervisor import SubagentSupervisor, SupervisorEvent
from jarvis_core.attention.engine import AttentionDecision
from jarvis_core.llm.catalog import ModelSelectionCatalog
from jarvis_core.llm.router import LLMRouter
from jarvis_core.tools.autonomy import AutonomyPolicy
from jarvis_core.tools.cli_agents import CLIAgentToolkit
from jarvis_core.tools.registry import ToolRegistry
from jarvis_core.tools.registry import ToolSpec as RegistryToolSpec

# === Schemas Pydantic des outils de l'Orchestrateur ===


class _SubtaskSpec(BaseModel):
    id: str | None = None
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    instructions: str = Field(min_length=1)
    role_profile: str = Field(
        default="general",
        description="Profil d'outils : research | coder | browser | writer | verifier | system | general",
    )
    model: str | None = Field(
        default=None,
        description="Modele low-cost choisi dans la pool (ex: deepseek/deepseek-v4.1-flash-1017, gemini-4-carbon, gemini-4-argon, z-ai/glm-5.3, minimax/minimax-m3, moonshotai/kimi-k3).",
    )
    depends_on: list[str] = Field(default_factory=list)
    max_attempts: int = Field(default=3, ge=1, le=5)


class PlanGoalArgs(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    success_criteria: list[str] = Field(min_length=1, max_length=20)
    subtasks: list[_SubtaskSpec] = Field(min_length=1, max_length=25)


class SpawnSubagentArgs(BaseModel):
    subtask_id: str = Field(min_length=1)
    instructions: str | None = None
    model: str | None = None
    role_profile: str | None = None
    max_iterations: int = Field(default=20, ge=2, le=60)
    stall_timeout_s: float = Field(default=55.0, ge=5.0, le=180.0)


class AwaitEventsArgs(BaseModel):
    timeout_s: float = Field(default=20.0, ge=0.2, le=60.0)


class SteerSubagentArgs(BaseModel):
    run_id: str = Field(min_length=1)
    guidance: str = Field(min_length=1, max_length=3000)


class RestartSubagentArgs(BaseModel):
    subtask_id: str = Field(min_length=1)
    new_model: str | None = None
    updated_instructions: str | None = None
    reason: str = ""


class SplitSubtaskArgs(BaseModel):
    subtask_id: str = Field(min_length=1)
    new_subtasks: list[_SubtaskSpec] = Field(min_length=2, max_length=10)


class MarkSubtaskArgs(BaseModel):
    subtask_id: str = Field(min_length=1)
    status: str = Field(pattern="^(completed|skipped)$")
    summary: str = ""


class RunVerificationArgs(BaseModel):
    instructions: str = Field(
        min_length=1,
        description="Instructions precises de verification factuelle de chaque critere de succes.",
    )
    model: str | None = Field(
        default=None,
        description="Modele low-cost pour le sous-agent verificateur (defaut: gemini-4-argon ou deepseek).",
    )
    verified_criteria: list[str] = Field(
        default_factory=list,
        description="Criteres de succes que tu as deja constates ou demandes a verifier.",
    )


class CompleteGoalArgs(BaseModel):
    final_summary: str = Field(min_length=1, max_length=6000)
    verified_criteria: list[str] = Field(default_factory=list)


class RequestUserInputArgs(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    reason: str = "clarification"


class GetGoalStateArgs(BaseModel):
    pass


# === Schemas des outils exposes a l'agent principal Jarvis ===


class StartGoalArgs(BaseModel):
    goal: str = Field(
        min_length=1,
        max_length=8000,
        description="Objectif complet a accomplir de facon 100% autonome.",
    )
    max_cost_usd: float = Field(default=1.5, ge=0.05, le=20.0)
    orchestrator_model: str | None = Field(
        default=None,
        description="Modele orchestrateur optionnel (defaut: gemini-4-argon).",
    )
    notify_on_finish: bool = True


class ListGoalsArgs(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)


class GoalIdArgs(BaseModel):
    goal_id: str = Field(min_length=1)


class SteerGoalArgs(BaseModel):
    goal_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=4000)
    resume: bool = True
    extra_budget_usd: float | None = Field(default=None, ge=0.05, le=20.0)


class ResumeGoalArgs(BaseModel):
    goal_id: str = Field(min_length=1)
    extra_budget_usd: float | None = Field(default=None, ge=0.05, le=20.0)


def _compute_progress(subtasks: list[SubtaskRecord]) -> int:
    if not subtasks:
        return 0
    done = sum(1 for s in subtasks if s.status in ("completed", "skipped"))
    return min(99, int((done / len(subtasks)) * 100))


class GoalOrchestrator:
    """Machine a etats persistante qui orchestre et supervise des sous-agents jusqu'a verification complete."""

    MAX_ORCHESTRATOR_STEPS = 100

    def __init__(
        self,
        goal: GoalRecord,
        router: LLMRouter,
        tool_registry: ToolRegistry,
        catalog: ModelSelectionCatalog,
        memory_engine: Any = None,
        *,
        on_goal_update: Callable[[GoalRecord], Any] | None = None,
        on_subagent_update: Callable[[SubagentRunRecord], Any] | None = None,
        on_event: Callable[[GoalEventRecord], Any] | None = None,
        watchdog_interval_s: float = 1.0,
        default_stall_timeout_s: float = 90.0,
        default_step_timeout_s: float = 75.0,
        max_orchestrator_steps: int = MAX_ORCHESTRATOR_STEPS,
    ) -> None:
        self.goal = goal
        self.router = router
        self.tool_registry = tool_registry
        self.catalog = catalog
        self.memory_engine = memory_engine
        self.on_goal_update = on_goal_update
        self.on_subagent_update = on_subagent_update
        self.on_event = on_event
        self.max_orchestrator_steps = max_orchestrator_steps

        self.cancel_event = asyncio.Event()
        self.pause_event = asyncio.Event()
        self.guidance_queue: asyncio.Queue[str] = asyncio.Queue()

        self._verification_passed = False
        self._last_worker_finish_seq = 0
        self._last_verification_seq = 0
        self._seq_counter = 0

        self.supervisor = SubagentSupervisor(
            goal_id=goal.id,
            router=router,
            tool_registry=tool_registry,
            catalog=catalog,
            memory_engine=memory_engine,
            watchdog_interval_s=watchdog_interval_s,
            default_stall_timeout_s=default_stall_timeout_s,
            default_step_timeout_s=default_step_timeout_s,
            on_run_update=self._handle_run_update,
            on_cost_delta=self._handle_cost_delta,
        )
        self.orch_registry = self._build_orchestrator_registry()

    def _emit_goal(self) -> None:
        self.goal.updated_at = utc_now_iso()
        if self.on_goal_update:
            try:
                self.on_goal_update(self.goal)
            except Exception:
                pass

    def _record_event(
        self,
        kind: GoalEventKind,
        message: str,
        *,
        subtask_id: str | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GoalEventRecord:
        ev = GoalEventRecord(
            id=f"gev_{uuid.uuid4().hex[:10]}",
            goal_id=self.goal.id,
            ts=utc_now_iso(),
            kind=kind,
            subtask_id=subtask_id,
            run_id=run_id,
            message=message,
            metadata=metadata or {},
        )
        self.goal.events.append(ev)
        if len(self.goal.events) > 120:
            self.goal.events = self.goal.events[-120:]
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception:
                pass
        return ev

    def _handle_run_update(self, run: SubagentRunRecord) -> None:
        for sub in self.goal.subtasks:
            if sub.id == run.subtask_id:
                existing_idx = next((i for i, r in enumerate(sub.runs) if r.id == run.id), None)
                if existing_idx is not None:
                    sub.runs[existing_idx] = run
                else:
                    sub.runs.append(run)
                break
        if self.on_subagent_update:
            try:
                self.on_subagent_update(run)
            except Exception:
                pass

    def _handle_cost_delta(self, dcost: float, dtok: int) -> None:
        self.goal.cost_usd = round(self.goal.cost_usd + float(dcost), 6)
        self.goal.tokens_used += int(dtok)
        self._emit_goal()

    def _find_subtask(self, subtask_id: str) -> SubtaskRecord | None:
        for sub in self.goal.subtasks:
            if sub.id == subtask_id:
                return sub
        return None

    def _summarize_state(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal.id,
            "status": self.goal.status,
            "cost_usd": self.goal.cost_usd,
            "max_cost_usd": self.goal.max_cost_usd,
            "success_criteria": self.goal.success_criteria,
            "verified_criteria": self.goal.verified_criteria,
            "active_runs": [r.model_dump() for r in self.supervisor.active_runs()],
            "subtasks": [
                {
                    "id": s.id,
                    "title": s.title,
                    "role_profile": s.role_profile,
                    "assigned_model": s.assigned_model,
                    "depends_on": s.depends_on,
                    "status": s.status,
                    "attempt": s.attempt,
                    "max_attempts": s.max_attempts,
                    "error": s.error,
                    "result": s.result[:600],
                }
                for s in self.goal.subtasks
            ],
        }

    def _format_event(self, ev: SupervisorEvent) -> dict[str, Any]:
        sub = self._find_subtask(ev.subtask_id)
        self._seq_counter += 1
        if sub is not None:
            sub.updated_at = utc_now_iso()
            if ev.kind == "completed":
                sub.status = "completed"
                sub.result = ev.result_summary
                sub.error = None
                self._last_worker_finish_seq = self._seq_counter
                if sub.role_profile == "verifier":
                    self._verification_passed = True
                    self._last_verification_seq = self._seq_counter
            elif ev.kind in ("stuck", "stalled"):
                sub.status = "stalled"
                sub.error = ev.detail
                self._record_event(
                    "watchdog",
                    f"Watchdog ({ev.reason}) sur '{sub.title}' [{ev.model}] : {ev.detail}",
                    subtask_id=sub.id,
                    run_id=ev.run_id,
                    metadata={"reason": ev.reason, "model": ev.model},
                )
            elif ev.kind == "failed":
                sub.status = "failed"
                sub.error = ev.detail
                self._record_event(
                    "error",
                    f"Échec sous-agent '{sub.title}' [{ev.model}] : {ev.detail}",
                    subtask_id=sub.id,
                    run_id=ev.run_id,
                )
        self.goal.progress_pct = _compute_progress(self.goal.subtasks)
        self._emit_goal()
        return {
            "kind": ev.kind,
            "subtask_id": ev.subtask_id,
            "run_id": ev.run_id,
            "model": ev.model,
            "reason": ev.reason,
            "detail": ev.detail,
            "recent_tools": ev.recent_tools,
            "result_summary": ev.result_summary[:2500],
        }

    def _build_orchestrator_registry(self) -> ToolRegistry:
        reg = ToolRegistry(
            approval_manager=self.tool_registry.approval_manager,
            current_platform=self.tool_registry.current_platform,
            autonomy_policy=self.tool_registry.autonomy_policy,
        )

        async def _plan_goal(
            title: str,
            success_criteria: list[str],
            subtasks: list[dict[str, Any]],
        ) -> dict[str, Any]:
            now = utc_now_iso()
            self.goal.title = title[:200]
            self.goal.success_criteria = [
                str(c).strip() for c in success_criteria if str(c).strip()
            ]
            self.goal.verified_criteria = []
            self._verification_passed = False

            built: list[SubtaskRecord] = []
            for idx, raw_sub in enumerate(subtasks, start=1):
                spec = _SubtaskSpec.model_validate(raw_sub)
                sid = (spec.id or f"sub_{idx}").strip()
                resolved_model = self.catalog.resolve_model(
                    spec.model,
                    role_profile=spec.role_profile,
                    require_vision=spec.role_profile in ("browser", "vision"),
                    router=self.router,
                )
                built.append(
                    SubtaskRecord(
                        id=sid,
                        goal_id=self.goal.id,
                        title=spec.title,
                        description=spec.description,
                        instructions=spec.instructions,
                        role_profile=spec.role_profile,
                        assigned_model=resolved_model,
                        depends_on=spec.depends_on,
                        status="pending",
                        attempt=0,
                        max_attempts=spec.max_attempts,
                        created_at=now,
                        updated_at=now,
                    )
                )
            self.goal.subtasks = built
            self.goal.status = "running"
            self.goal.current_phase = f"Exécution ({len(built)} sous-tâches)"
            self.goal.progress_pct = 5
            self._record_event(
                "plan",
                f"Plan établi : {len(built)} sous-tâche(s), {len(self.goal.success_criteria)} critère(s) de succès.",
                metadata={"subtasks": [s.id for s in built]},
            )
            self._emit_goal()
            return {
                "ok": True,
                "goal_id": self.goal.id,
                "subtasks": [
                    {
                        "id": s.id,
                        "title": s.title,
                        "assigned_model": s.assigned_model,
                        "role_profile": s.role_profile,
                    }
                    for s in built
                ],
            }

        async def _spawn_subagent(
            subtask_id: str,
            instructions: str | None = None,
            model: str | None = None,
            role_profile: str | None = None,
            max_iterations: int = 20,
            stall_timeout_s: float = 55.0,
        ) -> dict[str, Any]:
            sub = self._find_subtask(subtask_id)
            if sub is None:
                return {"ok": False, "error": f"Sous-tâche '{subtask_id}' introuvable."}

            unmet = [
                dep
                for dep in sub.depends_on
                if (d := self._find_subtask(dep)) is not None
                and d.status not in ("completed", "skipped")
            ]
            if unmet:
                return {
                    "ok": False,
                    "error": f"Dépendances non terminées pour '{subtask_id}' : {unmet}.",
                }

            eff_profile = role_profile or sub.role_profile
            eff_instructions = instructions or sub.instructions
            eff_model = model or sub.assigned_model
            sub.attempt += 1
            sub.status = "running"
            sub.error = None
            sub.updated_at = utc_now_iso()

            remaining_budget = max(0.05, self.goal.max_cost_usd - self.goal.cost_usd)
            run = self.supervisor.spawn_subagent(
                subtask_id=sub.id,
                title=sub.title,
                instructions=eff_instructions,
                role_profile=eff_profile,
                requested_model=eff_model,
                max_iterations=max_iterations,
                max_cost_usd=remaining_budget,
                stall_timeout_s=stall_timeout_s,
            )
            sub.assigned_model = run.model
            self.goal.status = "running"
            self.goal.current_phase = f"Sous-agent actif : {sub.title[:60]}"
            self._record_event(
                "spawn",
                f"Sous-agent lancé pour '{sub.title}' sur `{run.model}` (profil: {eff_profile}, tentative {sub.attempt}).",
                subtask_id=sub.id,
                run_id=run.id,
                metadata={"model": run.model, "profile": eff_profile},
            )
            self._emit_goal()
            return {
                "ok": True,
                "run_id": run.id,
                "subtask_id": sub.id,
                "model": run.model,
                "attempt": sub.attempt,
            }

        async def _await_subagent_events(timeout_s: float = 20.0) -> dict[str, Any]:
            active = self.supervisor.active_runs()
            if not active and self.supervisor.events.empty():
                return {
                    "ok": True,
                    "events": [],
                    "active_runs": 0,
                    "state": self._summarize_state(),
                }
            collected: list[dict[str, Any]] = []
            try:
                first = await asyncio.wait_for(self.supervisor.events.get(), timeout=timeout_s)
                collected.append(self._format_event(first))
                while not self.supervisor.events.empty():
                    collected.append(self._format_event(self.supervisor.events.get_nowait()))
            except asyncio.TimeoutError:
                pass
            return {
                "ok": True,
                "events": collected,
                "active_runs": len(self.supervisor.active_runs()),
                "state": self._summarize_state(),
            }

        async def _steer_subagent(run_id: str, guidance: str) -> dict[str, Any]:
            ok, msg = self.supervisor.steer_subagent(run_id, guidance)
            if ok:
                run = self.supervisor.get_run(run_id)
                self._record_event(
                    "steer",
                    f"Consigne injectée dans {run_id} : {guidance[:160]}",
                    subtask_id=run.subtask_id if run else None,
                    run_id=run_id,
                )
                self._emit_goal()
            return {"ok": ok, "message": msg}

        async def _restart_subagent(
            subtask_id: str,
            new_model: str | None = None,
            updated_instructions: str | None = None,
            reason: str = "",
        ) -> dict[str, Any]:
            sub = self._find_subtask(subtask_id)
            if sub is None:
                return {"ok": False, "error": f"Sous-tâche '{subtask_id}' introuvable."}

            if sub.attempt >= sub.max_attempts:
                return {
                    "ok": False,
                    "error": (
                        f"Nombre maximal de tentatives atteint ({sub.attempt}/{sub.max_attempts}) pour '{subtask_id}'. "
                        "Découpe cette sous-tâche avec `split_subtask` ou demande à l'utilisateur via `request_user_input`."
                    ),
                }

            prev_run = self.supervisor.latest_run_for_subtask(subtask_id)
            if prev_run and prev_run.status == "running":
                await self.supervisor.cancel_subagent(prev_run.id)

            prev_model = prev_run.model if prev_run else sub.assigned_model
            chosen_model = new_model
            if not chosen_model or self.catalog.normalize_id(chosen_model) == prev_model:
                chain = self.catalog.escalation_chain(
                    prev_model,
                    require_vision=sub.role_profile in ("browser", "vision"),
                    router=self.router,
                )
                chosen_model = chain[0] if chain else prev_model
            else:
                chosen_model = self.catalog.resolve_model(
                    chosen_model,
                    role_profile=sub.role_profile,
                    require_vision=sub.role_profile in ("browser", "vision"),
                    router=self.router,
                )

            base_inst = updated_instructions or sub.instructions
            failure_ctx = ""
            if prev_run and prev_run.error:
                failure_ctx = (
                    f"\n\n[HISTORIQUE TENTATIVE PRÉCÉDENTE SUR {prev_model}]\n"
                    f"Échec / blocage : {prev_run.error}\n"
                    "Évite absolument de répéter cette même erreur."
                )
            sub.attempt += 1
            sub.status = "retrying"
            sub.assigned_model = chosen_model
            sub.error = None
            sub.updated_at = utc_now_iso()

            remaining_budget = max(0.05, self.goal.max_cost_usd - self.goal.cost_usd)
            run = self.supervisor.spawn_subagent(
                subtask_id=sub.id,
                title=sub.title,
                instructions=f"{base_inst}{failure_ctx}",
                role_profile=sub.role_profile,
                requested_model=chosen_model,
                max_iterations=20,
                max_cost_usd=remaining_budget,
            )
            sub.status = "running"
            ev_kind: GoalEventKind = "switch_model" if chosen_model != prev_model else "steer"
            self._record_event(
                ev_kind,
                f"Relance '{sub.title}' : `{prev_model}` → `{run.model}` ({reason or 'réorientation superviseur'}).",
                subtask_id=sub.id,
                run_id=run.id,
                metadata={"from_model": prev_model, "to_model": run.model, "reason": reason},
            )
            self._emit_goal()
            return {
                "ok": True,
                "run_id": run.id,
                "subtask_id": sub.id,
                "previous_model": prev_model,
                "model": run.model,
                "attempt": sub.attempt,
            }

        async def _split_subtask(
            subtask_id: str, new_subtasks: list[dict[str, Any]]
        ) -> dict[str, Any]:
            sub = self._find_subtask(subtask_id)
            if sub is None:
                return {"ok": False, "error": f"Sous-tâche '{subtask_id}' introuvable."}
            prev_run = self.supervisor.latest_run_for_subtask(subtask_id)
            if prev_run and prev_run.status == "running":
                await self.supervisor.cancel_subagent(prev_run.id)

            sub.status = "skipped"
            sub.result = "Découpée en sous-tâches plus atomiques."
            sub.updated_at = utc_now_iso()
            now = utc_now_iso()
            added_ids: list[str] = []
            for idx, raw in enumerate(new_subtasks, start=1):
                spec = _SubtaskSpec.model_validate(raw)
                nid = (spec.id or f"{subtask_id}_p{idx}").strip()
                resolved_model = self.catalog.resolve_model(
                    spec.model,
                    role_profile=spec.role_profile,
                    require_vision=spec.role_profile in ("browser", "vision"),
                    router=self.router,
                )
                self.goal.subtasks.append(
                    SubtaskRecord(
                        id=nid,
                        goal_id=self.goal.id,
                        title=spec.title,
                        description=spec.description,
                        instructions=spec.instructions,
                        role_profile=spec.role_profile,
                        assigned_model=resolved_model,
                        depends_on=spec.depends_on,
                        status="pending",
                        attempt=0,
                        max_attempts=spec.max_attempts,
                        created_at=now,
                        updated_at=now,
                    )
                )
                added_ids.append(nid)
            self._record_event(
                "split",
                f"Sous-tâche '{sub.title}' découpée en {len(added_ids)} étapes : {added_ids}.",
                subtask_id=sub.id,
            )
            self._emit_goal()
            return {"ok": True, "replaced": subtask_id, "new_subtasks": added_ids}

        async def _mark_subtask(subtask_id: str, status: str, summary: str = "") -> dict[str, Any]:
            sub = self._find_subtask(subtask_id)
            if sub is None:
                return {"ok": False, "error": f"Sous-tâche '{subtask_id}' introuvable."}
            sub.status = status  # type: ignore[assignment]
            if summary:
                sub.result = summary
            sub.updated_at = utc_now_iso()
            self.goal.progress_pct = _compute_progress(self.goal.subtasks)
            self._emit_goal()
            return {"ok": True, "subtask_id": subtask_id, "status": status}

        async def _run_verification(
            instructions: str,
            model: str | None = None,
            verified_criteria: list[str] | None = None,
        ) -> dict[str, Any]:
            if self.supervisor.active_runs():
                return {
                    "ok": False,
                    "error": "Attends que tous les sous-agents actifs terminent avant la vérification finale.",
                }
            self.goal.status = "verifying"
            self.goal.current_phase = "Vérification des critères de succès"
            self._emit_goal()

            ver_sub_id = f"verify_{len(self.goal.subtasks) + 1}"
            now = utc_now_iso()
            ver_model = self.catalog.resolve_model(
                model or "gemini-4-argon",
                role_profile="verifier",
                router=self.router,
            )
            ver_sub = SubtaskRecord(
                id=ver_sub_id,
                goal_id=self.goal.id,
                title="Vérification indépendante des critères",
                description="Contrôle factuel de complétude avant clôture.",
                instructions=instructions,
                role_profile="verifier",
                assigned_model=ver_model,
                status="running",
                attempt=1,
                max_attempts=2,
                created_at=now,
                updated_at=now,
            )
            self.goal.subtasks.append(ver_sub)
            remaining_budget = max(0.05, self.goal.max_cost_usd - self.goal.cost_usd)

            criteria_block = (
                "\n".join(f"- {c}" for c in self.goal.success_criteria) or "(objectif principal)"
            )
            ver_prompt = (
                f"Critères de succès à vérifier FACTUELLEMENT avec tes outils :\n{criteria_block}\n\n"
                f"Instructions :\n{instructions}\n\n"
                "Termine ton rapport par une ligne claire commençant par `VERDICT: PASS` si TOUS les critères sont prouvés, "
                "ou `VERDICT: FAIL - <manques>` sinon."
            )
            run = self.supervisor.spawn_subagent(
                subtask_id=ver_sub_id,
                title=ver_sub.title,
                instructions=ver_prompt,
                role_profile="verifier",
                requested_model=ver_model,
                max_iterations=15,
                max_cost_usd=remaining_budget,
            )
            # Attendre le resultat du verificateur
            ver_ev: SupervisorEvent | None = None
            while True:
                ev = await self.supervisor.events.get()
                formatted = self._format_event(ev)
                if ev.run_id == run.id:
                    ver_ev = ev
                    break
                # Si un autre evenement residuel arrive, on le garde en trace
                self._record_event(
                    "watchdog", f"Événement parallèle pendant vérification : {formatted['kind']}"
                )

            ver_text = ver_ev.result_summary if ver_ev else ""
            passed = (
                ver_ev is not None
                and ver_ev.kind == "completed"
                and "VERDICT: FAIL" not in ver_text.upper()
                and ("VERDICT: PASS" in ver_text.upper() or bool(verified_criteria))
            )
            if passed:
                self._verification_passed = True
                self.goal.verified_criteria = list(verified_criteria or self.goal.success_criteria)
                self._record_event(
                    "verify",
                    f"Vérification réussie sur `{run.model}`.",
                    subtask_id=ver_sub_id,
                    run_id=run.id,
                )
            else:
                self._verification_passed = False
                ver_sub.status = "failed"
                ver_sub.error = (
                    ver_ev.detail if ver_ev and ver_ev.detail else "Critères non validés."
                )
                self._record_event(
                    "verify",
                    f"Vérification échouée sur `{run.model}` : correction requise.",
                    subtask_id=ver_sub_id,
                    run_id=run.id,
                )
            self.goal.status = "running"
            self._emit_goal()
            return {
                "ok": True,
                "passed": passed,
                "model": run.model,
                "report": ver_text[:3000],
                "error": ver_ev.detail if ver_ev else "",
            }

        async def _complete_goal(
            final_summary: str,
            verified_criteria: list[str] | None = None,
        ) -> dict[str, Any]:
            if self.supervisor.active_runs():
                return {
                    "ok": False,
                    "error": "Impossible de clôturer : des sous-agents sont encore actifs.",
                }
            unfinished = [
                s.id for s in self.goal.subtasks if s.status not in ("completed", "skipped")
            ]
            if unfinished:
                return {
                    "ok": False,
                    "error": (
                        f"Sous-tâches encore non terminées : {unfinished}. "
                        "Exécute-les, corrige-les ou clôture-les avant `complete_goal`."
                    ),
                }
            if not self._verification_passed:
                return {
                    "ok": False,
                    "error": (
                        "Clôture refusée : aucune vérification finale réussie (`run_verification`) "
                        "n'a encore prouvé que tous les critères sont atteints."
                    ),
                }
            if verified_criteria:
                self.goal.verified_criteria = list(verified_criteria)
            elif not self.goal.verified_criteria:
                self.goal.verified_criteria = list(self.goal.success_criteria)
            self.goal.status = "completed"
            self.goal.progress_pct = 100
            self.goal.current_phase = "Terminé"
            self.goal.final_summary = final_summary
            self.goal.finished_at = utc_now_iso()
            self._record_event("complete", "Objectif accompli et vérifié.")
            self._emit_goal()
            return {"ok": True, "status": "completed"}

        async def _request_user_input(
            question: str, reason: str = "clarification"
        ) -> dict[str, Any]:
            if self.supervisor.active_runs():
                await self.supervisor.stop_all()
            self.goal.status = "waiting_user"
            self.goal.current_phase = "En attente de votre réponse"
            self.goal.pause_reason = question
            self._record_event("pause", f"Intervention utilisateur requise ({reason}) : {question}")
            self._emit_goal()
            return {"ok": True, "status": "waiting_user", "question": question}

        async def _get_goal_state() -> dict[str, Any]:
            return {"ok": True, "state": self._summarize_state()}

        specs = [
            (
                "plan_goal",
                "Definit le plan DAG de sous-taches et les criteres de succes verifiables.",
                PlanGoalArgs,
                _plan_goal,
            ),
            (
                "spawn_subagent",
                "Lance un sous-agent isole avec modele low-cost et instructions sur mesure (plusieurs appels possibles en parallele).",
                SpawnSubagentArgs,
                _spawn_subagent,
            ),
            (
                "await_subagent_events",
                "Attend la fin, l'alerte Watchdog ou l'echec d'un sous-agent actif.",
                AwaitEventsArgs,
                _await_subagent_events,
            ),
            (
                "steer_subagent",
                "Injecte une consigne corrective dans un sous-agent encore en cours d'execution.",
                SteerSubagentArgs,
                _steer_subagent,
            ),
            (
                "restart_subagent",
                "Relance une sous-tache bloquee ou en boucle avec de nouvelles instructions et/ou un autre modele low-cost.",
                RestartSubagentArgs,
                _restart_subagent,
            ),
            (
                "split_subtask",
                "Decoupe une sous-tache trop complexe en plusieurs sous-taches atomiques.",
                SplitSubtaskArgs,
                _split_subtask,
            ),
            (
                "mark_subtask",
                "Marque une sous-tache comme completed ou skipped.",
                MarkSubtaskArgs,
                _mark_subtask,
            ),
            (
                "run_verification",
                "Execute un sous-agent verificateur independant pour prouver que tous les criteres de succes sont remplis.",
                RunVerificationArgs,
                _run_verification,
            ),
            (
                "complete_goal",
                "Cloture l'objectif (uniquement apres verification reussie et toutes sous-taches terminees).",
                CompleteGoalArgs,
                _complete_goal,
            ),
            (
                "request_user_input",
                "Met l'objectif en pause et pose une question a l'utilisateur uniquement en cas de blocage externe reel.",
                RequestUserInputArgs,
                _request_user_input,
            ),
            (
                "get_goal_state",
                "Retourne l'etat complet des sous-taches, runs actifs et couts.",
                GetGoalStateArgs,
                _get_goal_state,
            ),
        ]
        for name, desc, args_m, fn in specs:

            def _wrap(handler: Any, model_cls: type[BaseModel]) -> Any:
                async def _call(**kwargs: Any) -> dict[str, Any]:
                    parsed = model_cls.model_validate(kwargs)
                    return await handler(**parsed.model_dump())

                return _call

            reg.register(
                RegistryToolSpec(
                    name=name,
                    description=desc,
                    handler=_wrap(fn, args_m),
                    args_model=args_m,
                    risk=RiskLevel.READ_ONLY,
                )
            )
        CLIAgentToolkit().register_all(reg)
        return reg

    def _orchestrator_system_prompt(self) -> str:
        catalog_desc = self.catalog.describe_for_orchestrator(self.router)
        return (
            "Tu es l'Orchestrateur principal autonome de JARVIS 2.0.\n"
            "Ta mission : atteindre à 100% l'objectif de l'utilisateur en coordonnant des sous-agents spécialisés.\n"
            "Tu ne t'arrêtes JAMAIS tant que l'objectif n'est pas réellement terminé ET prouvé par `run_verification`.\n\n"
            f"{catalog_desc}\n\n"
            "RÈGLES IMPÉRATIVES D'ORCHESTRATION :\n"
            "1. Commence toujours par `plan_goal` avec des critères de succès concrets et vérifiables, et des sous-tâches atomiques.\n"
            "2. Choisis pour chaque sous-tâche le modèle le MOINS CHER adapté (`gemini-4-carbon`, `deepseek/deepseek-v4.1-flash-1017`, "
            "`minimax/minimax-m3`, `z-ai/glm-5.3`, `moonshotai/kimi-k3`, `gemini-4-argon`, en gardant `gemini-4-hydrogen` pour les déblocages difficiles "
            "et `qwen3.5:2b` uniquement en secours local ultra-léger si vraiment nécessaire).\n"
            "3. Tu peux aussi déléguer des tâches CLI autonomes via `run_cli_subagent` (`agy` ou `hermes` avec `deepseek/deepseek-v4.1-flash-1017` sous OpenRouter par défaut, ou `qwen3.5:2b` en secours local) soit directement, soit via un sous-agent `cli_agent` / `coder`.\n"
            "4. Lance les sous-tâches indépendantes en parallèle avec `spawn_subagent`, puis appelle `await_subagent_events`.\n"
            "5. Si le Watchdog signale un sous-agent lent (`stall_timeout`), en boucle (`loop_detected`) ou en erreur (`error_thrashing`), "
            "analyse la cause et agis immédiatement : `steer_subagent`, `restart_subagent` (avec bascule vers un autre modèle ou instructions clarifiées) ou `split_subtask`.\n"
            "6. Avant d'appeler `complete_goal`, appelle OBLIGATOIREMENT `run_verification` pour vérifier chaque critère sur des preuves réelles.\n"
            "7. Si la vérification échoue, crée ou relance des sous-tâches correctives jusqu'à ce que tout passe.\n"
            "8. Ne demande jamais à l'utilisateur (`request_user_input`) sauf blocage externe impossible à résoudre seul (mot de passe, CAPTCHA, ambiguïté majeure)."
        )

    async def run(self, initial_messages: list[Message] | None = None) -> GoalRecord:
        """Execute la boucle d'orchestration jusqu'a completion, pause ou annulation."""
        messages: list[Message] = list(initial_messages or [])
        if not messages:
            messages = [
                Message(role=Role.SYSTEM, content=self._orchestrator_system_prompt()),
                Message(
                    role=Role.USER,
                    content=(
                        f"Objectif à accomplir de manière 100% autonome :\n{self.goal.goal}\n\n"
                        f"Budget max : ${self.goal.max_cost_usd:.2f}. Commence par établir le plan (`plan_goal`)."
                    ),
                ),
            ]
        else:
            # S'assurer que le system prompt est a jour lors d'une reprise
            if messages[0].role == Role.SYSTEM:
                messages[0] = Message(role=Role.SYSTEM, content=self._orchestrator_system_prompt())

        tools_schema = self.orch_registry.list_schemas()
        step = 0
        no_tool_streak = 0
        orch_model = self.catalog.resolve_model(
            self.goal.orchestrator_model,
            role_profile="orchestrator",
            router=self.router,
        )
        self.goal.orchestrator_model = orch_model
        self._emit_goal()

        try:
            while step < self.max_orchestrator_steps:
                if self.cancel_event.is_set() or self.tool_registry.autonomy_policy.emergency_stop:
                    await self.supervisor.stop_all()
                    self.goal.status = "cancelled"
                    self.goal.current_phase = "Annulé"
                    self.goal.finished_at = utc_now_iso()
                    self._record_event("pause", "Objectif annulé.")
                    self._emit_goal()
                    return self.goal

                if self.pause_event.is_set():
                    await self.supervisor.stop_all()
                    self.goal.status = "paused"
                    self.goal.current_phase = "En pause"
                    self.goal.pause_reason = (
                        self.goal.pause_reason or "Mis en pause par l'utilisateur."
                    )
                    self._record_event("pause", self.goal.pause_reason)
                    self._emit_goal()
                    return self.goal

                if self.goal.cost_usd >= self.goal.max_cost_usd:
                    await self.supervisor.stop_all()
                    self.goal.status = "paused_budget"
                    self.goal.current_phase = "Budget atteint"
                    self.goal.pause_reason = f"Budget max atteint (${self.goal.cost_usd:.4f} / ${self.goal.max_cost_usd:.2f})."
                    self._record_event("pause", self.goal.pause_reason)
                    self._emit_goal()
                    return self.goal

                # Drainer d'eventuelles consignes utilisateur envoyees en direct
                while not self.guidance_queue.empty():
                    try:
                        user_hint = self.guidance_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if user_hint.strip():
                        messages.append(
                            Message(
                                role=Role.USER,
                                content=f"[NOUVELLE INSTRUCTION DE L'UTILISATEUR]\n{user_hint.strip()}",
                            )
                        )
                        self._record_event(
                            "steer", f"Instruction utilisateur reçue : {user_hint.strip()[:160]}"
                        )

                # Drainer les evenements Watchdog/sous-agents deja arrives avant d'interroger l'orchestrateur
                pre_events: list[dict[str, Any]] = []
                while not self.supervisor.events.empty():
                    pre_events.append(self._format_event(self.supervisor.events.get_nowait()))
                if pre_events:
                    messages.append(
                        Message(
                            role=Role.USER,
                            content=(
                                "[ÉVÉNEMENTS SOUS-AGENTS / WATCHDOG]\n"
                                f"{json.dumps(pre_events, ensure_ascii=False)}"
                            ),
                        )
                    )

                step += 1
                prof = self.catalog.get(orch_model)
                orch_prov = prof.provider if prof else None
                fb_chain = self.catalog.escalation_chain(orch_model, router=self.router)

                delta = await self.router.complete(
                    role="orchestrator",
                    messages=messages,
                    tools=tools_schema,
                    model_override=orch_model,
                    provider_override=orch_prov,
                    fallback_models_override=fb_chain,
                    timeout_s=60.0,
                )
                if delta.usage:
                    self.goal.tokens_used += int(delta.usage.tokens_in) + int(
                        delta.usage.tokens_out
                    )
                    self.goal.cost_usd = round(self.goal.cost_usd + float(delta.usage.cost_usd), 6)
                    self._emit_goal()

                if not delta.tool_calls:
                    no_tool_streak += 1
                    if delta.text:
                        messages.append(Message(role=Role.ASSISTANT, content=delta.text))
                    if self.supervisor.active_runs():
                        # Attendre un evenement sous-agent plutot que boucler a vide
                        try:
                            ev = await asyncio.wait_for(self.supervisor.events.get(), timeout=15.0)
                            ev_data = [self._format_event(ev)]
                            while not self.supervisor.events.empty():
                                ev_data.append(
                                    self._format_event(self.supervisor.events.get_nowait())
                                )
                            messages.append(
                                Message(
                                    role=Role.USER,
                                    content=f"[ÉVÉNEMENTS SOUS-AGENTS]\n{json.dumps(ev_data, ensure_ascii=False)}",
                                )
                            )
                            no_tool_streak = 0
                        except asyncio.TimeoutError:
                            pass
                        continue

                    if no_tool_streak >= 4:
                        self.goal.status = "paused"
                        self.goal.pause_reason = (
                            "L'orchestrateur n'a pas émis d'action outil après 4 relances."
                        )
                        self._record_event("pause", self.goal.pause_reason)
                        self._emit_goal()
                        return self.goal

                    messages.append(
                        Message(
                            role=Role.USER,
                            content=(
                                "[SYSTÈME] L'objectif n'est PAS terminé. Tu ne peux pas t'arrêter sans appeler "
                                "`complete_goal` après `run_verification`. Utilise tes outils (`plan_goal`, "
                                "`spawn_subagent`, `run_verification`, `complete_goal` ou `request_user_input`)."
                            ),
                        )
                    )
                    continue

                no_tool_streak = 0
                messages.append(
                    Message(
                        role=Role.ASSISTANT,
                        content=delta.text or "",
                        tool_calls=delta.tool_calls,
                    )
                )

                results: list[ToolResult] = []
                for tc in delta.tool_calls:
                    res = await self.orch_registry.execute(
                        tc,
                        autonomous_run=True,
                        task_id=self.goal.id,
                    )
                    results.append(res)

                messages.append(Message(role=Role.TOOL, tool_results=results))

                if self.goal.status in ("completed", "waiting_user", "cancelled", "paused"):
                    await self.supervisor.stop_all()
                    return self.goal

            # Plafond d'iterations orchestrateur atteint -> pause propre avec reprise possible
            await self.supervisor.stop_all()
            self.goal.status = "paused"
            self.goal.current_phase = "Pause checkpoint"
            self.goal.pause_reason = (
                f"Plafond de cycles d'orchestration ({self.max_orchestrator_steps}) atteint — "
                "checkpoint sauvegardé, prêt à reprendre."
            )
            self._record_event("pause", self.goal.pause_reason)
            self._emit_goal()
            return self.goal
        except asyncio.CancelledError:
            await self.supervisor.stop_all()
            self.goal.status = "cancelled"
            self.goal.current_phase = "Annulé"
            self.goal.finished_at = utc_now_iso()
            self._emit_goal()
            return self.goal
        except Exception as exc:
            await self.supervisor.stop_all()
            self.goal.status = "failed"
            self.goal.current_phase = "Erreur orchestrateur"
            self.goal.pause_reason = str(exc)
            self._record_event("error", f"Erreur fatale orchestrateur : {exc}")
            self._emit_goal()
            return self.goal


class GoalManager:
    """Gestionnaire persistant de tous les objectifs et de leurs orchestrateurs en arriere-plan."""

    def __init__(
        self,
        router: LLMRouter,
        tool_registry: ToolRegistry,
        catalog: ModelSelectionCatalog | None = None,
        memory_engine: Any = None,
        database: Any = None,
        policy: AutonomyPolicy | None = None,
        channels: dict[str, Any] | None = None,
        alert_queue: asyncio.Queue[tuple[NotificationRecord, AttentionDecision]] | None = None,
        on_ws_event: Callable[[str, dict[str, Any]], Any] | None = None,
        *,
        watchdog_interval_s: float = 1.0,
        default_stall_timeout_s: float = 90.0,
        default_step_timeout_s: float = 75.0,
    ) -> None:
        self.router = router
        self.tool_registry = tool_registry
        self.catalog = catalog or ModelSelectionCatalog()
        self.memory_engine = memory_engine
        self.database = database
        self.policy = policy or getattr(tool_registry, "autonomy_policy", AutonomyPolicy())
        self.channels = channels or {}
        self.alert_queue = alert_queue
        self.on_ws_event = on_ws_event
        self.watchdog_interval_s = watchdog_interval_s
        self.default_stall_timeout_s = default_stall_timeout_s
        self.default_step_timeout_s = default_step_timeout_s

        self._goals: dict[str, GoalRecord] = {}
        self._orchestrators: dict[str, GoalOrchestrator] = {}
        self._bg_tasks: dict[str, asyncio.Task[Any]] = {}

    def _emit_ws(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.on_ws_event:
            try:
                self.on_ws_event(event_type, payload)
            except Exception:
                pass

    def _persist_goal(self, goal: GoalRecord) -> None:
        self._goals[goal.id] = goal
        if self.database is not None and hasattr(self.database, "save_goal"):
            self.database.save_goal(goal)
            for sub in goal.subtasks:
                self.database.upsert_goal_subtask(sub)
        self._emit_ws(
            "goal.updated",
            {
                "goal_id": goal.id,
                "title": goal.title,
                "status": goal.status,
                "progress_pct": goal.progress_pct,
                "current_phase": goal.current_phase,
                "cost_usd": goal.cost_usd,
                "max_cost_usd": goal.max_cost_usd,
                "orchestrator_model": goal.orchestrator_model,
                "pause_reason": goal.pause_reason,
                "final_summary": goal.final_summary,
            },
        )

    def _persist_run(self, run: SubagentRunRecord) -> None:
        if self.database is not None and hasattr(self.database, "upsert_subagent_run"):
            self.database.upsert_subagent_run(run)
        goal = self._goals.get(run.goal_id)
        sub_title = ""
        if goal:
            for s in goal.subtasks:
                if s.id == run.subtask_id:
                    sub_title = s.title
                    break
        self._emit_ws(
            "goal.subagent",
            {
                "goal_id": run.goal_id,
                "subtask_id": run.subtask_id,
                "run_id": run.id,
                "title": sub_title,
                "model": run.model,
                "role_profile": run.role_profile,
                "status": run.status,
                "iterations": run.iterations,
                "max_iterations": run.max_iterations,
                "cost_usd": run.cost_usd,
                "last_tool": run.last_tool,
                "error": run.error,
            },
        )

    def _persist_event(self, ev: GoalEventRecord) -> None:
        if self.database is not None and hasattr(self.database, "append_goal_event"):
            self.database.append_goal_event(ev)
        self._emit_ws(
            "goal.intervention",
            {
                "goal_id": ev.goal_id,
                "subtask_id": ev.subtask_id,
                "run_id": ev.run_id,
                "kind": ev.kind,
                "message": ev.message,
                "ts": ev.ts,
            },
        )

    def list_goals(self, limit: int = 30) -> list[GoalRecord]:
        if self.database is not None and hasattr(self.database, "list_goals"):
            by_id = {g.id: g for g in self.database.list_goals(limit=limit)}
            by_id.update(self._goals)
            return sorted(by_id.values(), key=lambda g: g.created_at, reverse=True)[:limit]
        return sorted(self._goals.values(), key=lambda g: g.created_at, reverse=True)[:limit]

    def get_goal(self, goal_id: str) -> GoalRecord | None:
        if goal_id in self._goals:
            return self._goals[goal_id]
        if self.database is not None and hasattr(self.database, "get_goal"):
            loaded = self.database.get_goal(goal_id)
            if loaded:
                self._goals[goal_id] = loaded
            return loaded
        return None

    async def _notify_goal_status(self, goal: GoalRecord) -> None:
        if not goal.notify_on_finish and goal.status == "completed":
            return
        if goal.status == "completed":
            prefix = "🎯 Objectif accompli"
            urgency = UrgencyLevel.IMPORTANT
        elif goal.status in ("waiting_user", "paused_budget", "paused"):
            prefix = "⏸️ Objectif en pause"
            urgency = UrgencyLevel.IMPORTANT
        elif goal.status == "cancelled":
            prefix = "🛑 Objectif annulé"
            urgency = UrgencyLevel.NORMAL
        else:
            prefix = "⚠️ Échec objectif"
            urgency = UrgencyLevel.URGENT

        body = goal.final_summary or goal.pause_reason or "(aucun détail)"
        if len(body) > 900:
            body = f"{body[:900].rstrip()}…"
        msg = (
            f"{prefix} ({goal.id})\n"
            f"Objectif : {goal.title[:120]}\n"
            f"Sous-tâches : {len([s for s in goal.subtasks if s.status == 'completed'])}/{len(goal.subtasks)} | "
            f"Coût : ${goal.cost_usd:.4f}\n\n{body}"
        )
        tg = self.channels.get("telegram")
        if tg is not None:
            try:
                await tg.send(msg, urgency=int(urgency))
            except Exception:
                pass
        if self.alert_queue is not None:
            notif = NotificationRecord(
                channel="web",
                urgency=urgency,
                message=msg,
                reason=f"goal:{goal.status}",
            )
            dec = AttentionDecision(
                urgency=urgency,
                channels=["web", "desktop"],
                message=msg,
                reason=f"goal:{goal.status}",
                dedup_key=f"goal:{goal.id}:{goal.status}",
            )
            if self.alert_queue.full():
                try:
                    self.alert_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                self.alert_queue.put_nowait((notif, dec))
            except asyncio.QueueFull:
                pass

    def start_goal(
        self,
        goal_text: str,
        *,
        max_cost_usd: float = 1.5,
        orchestrator_model: str | None = None,
        allowed_models: list[str] | None = None,
        notify_on_finish: bool = True,
    ) -> GoalRecord:
        if self.policy.emergency_stop:
            raise RuntimeError(
                "Arrêt d'urgence actif : désactivez emergency_stop avant de lancer un objectif."
            )

        catalog = (
            ModelSelectionCatalog(allowed_override=allowed_models)
            if allowed_models
            else self.catalog
        )
        resolved_orch = catalog.resolve_model(
            orchestrator_model or self.router.get_role_route("orchestrator").model,
            role_profile="orchestrator",
            router=self.router,
        )
        now = utc_now_iso()
        gid = f"goal_{uuid.uuid4().hex[:10]}"
        first_line = (goal_text.strip().splitlines() or [goal_text.strip()])[0][:100]
        record = GoalRecord(
            id=gid,
            title=first_line,
            goal=goal_text.strip(),
            status="planning",
            orchestrator_model=resolved_orch,
            allowed_models=catalog.allowed_models,
            max_cost_usd=float(max_cost_usd),
            current_phase="Initialisation de l'orchestrateur",
            notify_on_finish=notify_on_finish,
            created_at=now,
            updated_at=now,
        )
        self._persist_goal(record)
        self._launch_orchestrator(record, catalog=catalog)
        return record

    def _launch_orchestrator(
        self,
        record: GoalRecord,
        *,
        catalog: ModelSelectionCatalog | None = None,
        resume_context: str | None = None,
    ) -> None:
        cat = catalog or (
            ModelSelectionCatalog(allowed_override=record.allowed_models)
            if record.allowed_models
            else self.catalog
        )
        orch = GoalOrchestrator(
            goal=record,
            router=self.router,
            tool_registry=self.tool_registry,
            catalog=cat,
            memory_engine=self.memory_engine,
            on_goal_update=self._persist_goal,
            on_subagent_update=self._persist_run,
            on_event=self._persist_event,
            watchdog_interval_s=self.watchdog_interval_s,
            default_stall_timeout_s=self.default_stall_timeout_s,
            default_step_timeout_s=self.default_step_timeout_s,
        )
        self._orchestrators[record.id] = orch

        initial_msgs: list[Message] | None = None
        if resume_context:
            initial_msgs = [
                Message(role=Role.SYSTEM, content=orch._orchestrator_system_prompt()),
                Message(
                    role=Role.USER,
                    content=(
                        f"Reprise de l'objectif :\n{record.goal}\n\n"
                        f"État actuel des sous-tâches :\n{json.dumps(orch._summarize_state(), ensure_ascii=False)}\n\n"
                        f"Nouvelle consigne / reprise :\n{resume_context}"
                    ),
                ),
            ]

        async def _runner() -> None:
            try:
                final_goal = await orch.run(initial_messages=initial_msgs)
                self._persist_goal(final_goal)
                await self._notify_goal_status(final_goal)
            finally:
                self._bg_tasks.pop(record.id, None)

        self._bg_tasks[record.id] = asyncio.create_task(_runner(), name=f"jarvis-goal-{record.id}")

    def steer_goal(
        self,
        goal_id: str,
        message: str,
        *,
        resume: bool = True,
        extra_budget_usd: float | None = None,
    ) -> GoalRecord | None:
        record = self.get_goal(goal_id)
        if not record:
            return None
        if extra_budget_usd is not None:
            record.max_cost_usd = round(record.cost_usd + float(extra_budget_usd), 4)
        orch = self._orchestrators.get(goal_id)
        task = self._bg_tasks.get(goal_id)
        if orch is not None and task is not None and not task.done():
            if message.strip():
                orch.guidance_queue.put_nowait(message)
            self._persist_goal(record)
            return record

        if resume and record.status in ("paused", "paused_budget", "waiting_user", "failed"):
            record.status = "running"
            record.pause_reason = None
            record.current_phase = "Reprise en cours"
            self._persist_goal(record)
            self._launch_orchestrator(
                record, resume_context=message or "Reprends là où tu t'es arrêté."
            )
        return record

    def pause_goal(self, goal_id: str) -> GoalRecord | None:
        record = self.get_goal(goal_id)
        if not record:
            return None
        orch = self._orchestrators.get(goal_id)
        if orch is not None:
            orch.pause_event.set()
        if record.status in ("planning", "running", "verifying"):
            record.status = "paused"
            record.pause_reason = "Pause demandée par l'utilisateur."
            self._persist_goal(record)
        return record

    def resume_goal(
        self, goal_id: str, *, extra_budget_usd: float | None = None
    ) -> GoalRecord | None:
        return self.steer_goal(
            goal_id,
            "Reprends l'exécution des sous-tâches restantes jusqu'à vérification finale.",
            resume=True,
            extra_budget_usd=extra_budget_usd,
        )

    def cancel_goal(self, goal_id: str) -> GoalRecord | None:
        record = self.get_goal(goal_id)
        if not record:
            return None
        orch = self._orchestrators.get(goal_id)
        if orch is not None:
            orch.cancel_event.set()
        task = self._bg_tasks.get(goal_id)
        if task is not None and not task.done():
            task.cancel()
        if record.status not in ("completed", "failed", "cancelled"):
            record.status = "cancelled"
            record.current_phase = "Annulé"
            record.finished_at = utc_now_iso()
            self._persist_goal(record)
        return record

    def emergency_stop_all(self) -> list[str]:
        stopped: list[str] = []
        for gid in list(self._bg_tasks.keys()):
            if self.cancel_goal(gid) is not None:
                stopped.append(gid)
        return stopped


class GoalToolkit:
    """Outils exposes a l'agent principal Jarvis pour piloter des objectifs autonomes."""

    def __init__(self, manager: GoalManager) -> None:
        self.manager = manager

    async def start_goal(self, args: StartGoalArgs) -> dict[str, Any]:
        try:
            rec = self.manager.start_goal(
                goal_text=args.goal,
                max_cost_usd=args.max_cost_usd,
                orchestrator_model=args.orchestrator_model,
                notify_on_finish=args.notify_on_finish,
            )
            return {
                "ok": True,
                "goal_id": rec.id,
                "status": rec.status,
                "orchestrator_model": rec.orchestrator_model,
                "max_cost_usd": rec.max_cost_usd,
                "message": (
                    f"Objectif {rec.id} lancé en orchestration autonome "
                    f"(modèle orchestrateur : {rec.orchestrator_model}, budget max : ${rec.max_cost_usd:.2f}). "
                    "Je supervise les sous-agents jusqu'à vérification complète."
                ),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def list_goals(self, args: ListGoalsArgs) -> dict[str, Any]:
        goals = self.manager.list_goals(limit=args.limit)
        return {
            "ok": True,
            "count": len(goals),
            "goals": [
                {
                    "id": g.id,
                    "title": g.title,
                    "status": g.status,
                    "progress_pct": g.progress_pct,
                    "cost_usd": g.cost_usd,
                    "current_phase": g.current_phase,
                }
                for g in goals
            ],
        }

    async def get_goal_status(self, args: GoalIdArgs) -> dict[str, Any]:
        g = self.manager.get_goal(args.goal_id)
        if not g:
            return {"ok": False, "error": f"Objectif '{args.goal_id}' introuvable."}
        return {"ok": True, "goal": g.model_dump()}

    async def steer_goal(self, args: SteerGoalArgs) -> dict[str, Any]:
        g = self.manager.steer_goal(
            args.goal_id,
            args.message,
            resume=args.resume,
            extra_budget_usd=args.extra_budget_usd,
        )
        if not g:
            return {"ok": False, "error": f"Objectif '{args.goal_id}' introuvable."}
        return {"ok": True, "goal_id": g.id, "status": g.status}

    async def pause_goal(self, args: GoalIdArgs) -> dict[str, Any]:
        g = self.manager.pause_goal(args.goal_id)
        if not g:
            return {"ok": False, "error": f"Objectif '{args.goal_id}' introuvable."}
        return {"ok": True, "goal_id": g.id, "status": g.status}

    async def resume_goal(self, args: ResumeGoalArgs) -> dict[str, Any]:
        g = self.manager.resume_goal(args.goal_id, extra_budget_usd=args.extra_budget_usd)
        if not g:
            return {"ok": False, "error": f"Objectif '{args.goal_id}' introuvable."}
        return {"ok": True, "goal_id": g.id, "status": g.status}

    async def cancel_goal(self, args: GoalIdArgs) -> dict[str, Any]:
        g = self.manager.cancel_goal(args.goal_id)
        if not g:
            return {"ok": False, "error": f"Objectif '{args.goal_id}' introuvable."}
        return {"ok": True, "goal_id": g.id, "status": g.status}

    def register_all(self, registry: ToolRegistry) -> None:
        tools: list[tuple[str, str, type[BaseModel], Any, RiskLevel]] = [
            (
                "start_goal",
                (
                    "Reserve uniquement aux projets complexes ou taches longues multi-etapes (ne pas utiliser pour "
                    "une commande simple, un lancement d'app/GUI ou une verification ponctuelle : preferer delegate_subagent). "
                    "Lance un objectif autonome complet orchestre jusqu'a verification finale."
                ),
                StartGoalArgs,
                self.start_goal,
                RiskLevel.REVERSIBLE_WRITE,
            ),
            (
                "list_goals",
                "Liste les objectifs autonomes en cours, en pause ou termines.",
                ListGoalsArgs,
                self.list_goals,
                RiskLevel.READ_ONLY,
            ),
            (
                "get_goal_status",
                "Retourne l'etat detaille d'un objectif, ses sous-taches, sous-agents et interventions.",
                GoalIdArgs,
                self.get_goal_status,
                RiskLevel.READ_ONLY,
            ),
            (
                "steer_goal",
                "Envoie une nouvelle instruction ou precision a un objectif actif ou en pause.",
                SteerGoalArgs,
                self.steer_goal,
                RiskLevel.REVERSIBLE_WRITE,
            ),
            (
                "pause_goal",
                "Met en pause proprement un objectif autonome avec sauvegarde de checkpoint.",
                GoalIdArgs,
                self.pause_goal,
                RiskLevel.REVERSIBLE_WRITE,
            ),
            (
                "resume_goal",
                "Reprend un objectif en pause (et optionnellement augmente son budget).",
                ResumeGoalArgs,
                self.resume_goal,
                RiskLevel.REVERSIBLE_WRITE,
            ),
            (
                "cancel_goal",
                "Annule definitivement un objectif autonome et stoppe tous ses sous-agents.",
                GoalIdArgs,
                self.cancel_goal,
                RiskLevel.REVERSIBLE_WRITE,
            ),
        ]
        for name, desc, args_m, fn, risk in tools:

            def _make(h: Any, m: type[BaseModel]) -> Any:
                async def _w(**kwargs: Any) -> dict[str, Any]:
                    parsed = m.model_validate(kwargs)
                    return await h(parsed)

                return _w

            registry.register(
                RegistryToolSpec(
                    name=name,
                    description=desc,
                    handler=_make(fn, args_m),
                    args_model=args_m,
                    risk=risk,
                )
            )
