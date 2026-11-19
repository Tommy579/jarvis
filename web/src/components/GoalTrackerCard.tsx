import { useState, type FormEvent } from "react";
import { apiClient } from "../api/client";
import { formatModelLabel } from "../lib/modelLabels";
import { Icon } from "./Icon";
import { useJarvisStore } from "../store/useJarvisStore";
import { toast } from "../store/useToastStore";
import type { GoalRecord } from "../types/generated";

interface GoalTrackerCardProps {
  goal: GoalRecord;
  compact?: boolean;
  onOpenGoals?: () => void;
}

const STATUS_META: Record<GoalRecord["status"], { label: string; cls: string }> = {
  planning: { label: "Planification", cls: "badge badge--info" },
  running: { label: "En cours", cls: "badge badge--info" },
  verifying: { label: "Vérification", cls: "badge badge--warn" },
  waiting_user: { label: "Attente réponse", cls: "badge badge--warn" },
  waiting_approval: { label: "Approbation requise", cls: "badge badge--warn" },
  paused: { label: "En pause", cls: "badge" },
  paused_budget: { label: "Budget atteint", cls: "badge badge--danger" },
  completed: { label: "Accompli", cls: "badge badge--ok" },
  failed: { label: "Échec", cls: "badge badge--danger" },
  cancelled: { label: "Annulé", cls: "badge" },
};

const SUBTASK_BADGE: Record<string, string> = {
  pending: "En attente",
  running: "Actif",
  completed: "OK",
  stalled: "Watchdog",
  failed: "Erreur",
  skipped: "Ignorée",
};

export function GoalTrackerCard({ goal, compact = false, onOpenGoals }: GoalTrackerCardProps) {
  const upsertGoal = useJarvisStore((s) => s.upsertGoal);
  const liveSubagents = useJarvisStore((s) => s.liveSubagents);
  const [steerText, setSteerText] = useState("");
  const [extraBudget, setExtraBudget] = useState("0.50");
  const [busy, setBusy] = useState(false);

  const meta = STATUS_META[goal.status] ?? { label: goal.status, cls: "badge" };
  const isActive =
    goal.status === "planning" ||
    goal.status === "running" ||
    goal.status === "verifying" ||
    goal.status === "waiting_approval";
  const isResumable =
    goal.status === "paused" ||
    goal.status === "paused_budget" ||
    goal.status === "waiting_user" ||
    goal.status === "failed";

  const activeRuns = Object.values(liveSubagents).filter(
    (r) => r.goal_id === goal.id && r.status === "running"
  );

  async function refreshFull() {
    try {
      const full = await apiClient.getGoal(goal.id);
      upsertGoal(full);
    } catch {
      // ignore
    }
  }

  async function handlePause() {
    setBusy(true);
    try {
      const updated = await apiClient.pauseGoal(goal.id);
      upsertGoal(updated);
      toast.info("Objectif mis en pause.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Erreur pause");
    } finally {
      setBusy(false);
    }
  }

  async function handleResume() {
    setBusy(true);
    try {
      const updated = await apiClient.resumeGoal(goal.id);
      upsertGoal(updated);
      toast.ok("Objectif relancé.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Erreur reprise");
    } finally {
      setBusy(false);
    }
  }

  async function handleCancel() {
    setBusy(true);
    try {
      const updated = await apiClient.cancelGoal(goal.id);
      upsertGoal(updated);
      toast.info("Objectif annulé.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Erreur annulation");
    } finally {
      setBusy(false);
    }
  }

  async function handleSteerSubmit(e: FormEvent) {
    e.preventDefault();
    if (!steerText.trim() && goal.status !== "paused_budget") return;
    setBusy(true);
    try {
      const parsedBudget =
        goal.status === "paused_budget" ? Number.parseFloat(extraBudget) || 0.5 : null;
      const updated = await apiClient.steerGoal(goal.id, {
        message: steerText.trim() || "Continue avec la rallonge budgétaire.",
        resume: true,
        extra_budget_usd: parsedBudget,
      });
      upsertGoal(updated);
      setSteerText("");
      toast.ok("Consigne transmise à l'orchestrateur.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Erreur d'envoi");
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="card goal-card" aria-label={`Objectif ${goal.title}`}>
      <div className="goal-card__head">
        <div className="goal-card__title-wrap">
          <span className={meta.cls}>{meta.label}</span>
          <h3 className="goal-card__title">{goal.title}</h3>
        </div>
        <div className="goal-card__metrics">
          <span className="tag" title={goal.orchestrator_model}>
            {formatModelLabel(goal.orchestrator_model)}
          </span>
          <span className="goal-card__cost" title="Coût / Plafond">
            ${goal.cost_usd.toFixed(4)} / ${goal.max_cost_usd.toFixed(2)}
          </span>
          <span className="goal-card__pct">{goal.progress_pct}%</span>
        </div>
      </div>

      <div className="goal-progress" role="progressbar" aria-valuenow={goal.progress_pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="goal-progress__fill" style={{ width: `${Math.min(100, Math.max(0, goal.progress_pct))}%` }} />
      </div>

      <div className="goal-card__phase">
        <span>{goal.current_phase || "Supervision active"}</span>
        {activeRuns.length > 0 && (
          <span className="goal-card__workers">
            {activeRuns.length} sous-agent(s) actif(s)
          </span>
        )}
      </div>

      {goal.pause_reason && (
        <div className="goal-card__notice" role="status">
          {goal.pause_reason}
        </div>
      )}

      {goal.final_summary && goal.status === "completed" && (
        <p className="goal-card__summary">{goal.final_summary}</p>
      )}

      {!compact && goal.subtasks.length > 0 && (
        <div className="goal-subtasks">
          {goal.subtasks.map((sub) => {
            const live = Object.values(liveSubagents).find(
              (r) => r.subtask_id === sub.id && r.status === "running"
            );
            return (
              <div key={sub.id} className={`goal-subtask goal-subtask--${sub.status}`}>
                <div className="goal-subtask__top">
                  <strong>{sub.title}</strong>
                  <span className="tag" title={sub.assigned_model || "auto"}>
                    {sub.assigned_model ? formatModelLabel(sub.assigned_model) : "auto"}
                  </span>
                  <span className="badge">{SUBTASK_BADGE[sub.status] ?? sub.status}</span>
                </div>
                <div className="goal-subtask__meta">
                  <span>Profil : {sub.role_profile}</span>
                  <span>Tentative {sub.attempt}/{sub.max_attempts}</span>
                  {live && (
                    <span>
                      Itération {live.iterations}/{live.max_iterations}
                      {live.last_tool ? ` · outil: ${live.last_tool}` : ""}
                    </span>
                  )}
                </div>
                {sub.error && <div className="goal-subtask__err">{sub.error}</div>}
                {sub.result && sub.status === "completed" && (
                  <div className="goal-subtask__res">{sub.result.slice(0, 220)}</div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {!compact && goal.events.length > 0 && (
        <details className="goal-events">
          <summary>Journal de supervision ({goal.events.length})</summary>
          <ul>
            {goal.events.slice(0, 15).map((ev) => (
              <li key={ev.id}>
                <span className="badge">{ev.kind}</span> {ev.message}
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="goal-card__actions">
        {isActive && (
          <button type="button" className="btn btn--sm" onClick={handlePause} disabled={busy}>
            Pause
          </button>
        )}
        {isResumable && (
          <button type="button" className="btn btn--primary btn--sm" onClick={handleResume} disabled={busy}>
            Reprendre
          </button>
        )}
        {(isActive || isResumable) && (
          <button type="button" className="btn btn--danger btn--sm" onClick={handleCancel} disabled={busy}>
            Arrêter
          </button>
        )}
        {compact && onOpenGoals && (
          <button type="button" className="btn btn--sm" onClick={onOpenGoals}>
            Détails
          </button>
        )}
        {!compact && (
          <button type="button" className="btn btn--sm" onClick={refreshFull} title="Actualiser">
            <Icon name="refresh" size={14} />
          </button>
        )}
      </div>

      {!compact && (isActive || isResumable) && (
        <form className="goal-steer" onSubmit={handleSteerSubmit}>
          <input
            type="text"
            className="input"
            placeholder={
              goal.status === "waiting_user"
                ? "Répondre à Jarvis pour débloquer l'objectif…"
                : "Envoyer une consigne de réorientation à l'orchestrateur…"
            }
            value={steerText}
            onChange={(e) => setSteerText(e.target.value)}
            disabled={busy}
          />
          {goal.status === "paused_budget" && (
            <label className="goal-steer__budget">
              <span>+$</span>
              <input
                type="number"
                step="0.25"
                min="0.10"
                max="20"
                value={extraBudget}
                onChange={(e) => setExtraBudget(e.target.value)}
                disabled={busy}
              />
            </label>
          )}
          <button type="submit" className="btn btn--primary btn--sm" disabled={busy}>
            Envoyer
          </button>
        </form>
      )}
    </article>
  );
}
