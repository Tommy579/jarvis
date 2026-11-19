import { useEffect, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import { GoalTrackerCard } from "../components/GoalTrackerCard";
import { Icon } from "../components/Icon";
import { ConfirmButton, EmptyState, PageHeader, Skeleton } from "../components/ui";
import { formatModelLabel } from "../lib/modelLabels";
import { useJarvisStore } from "../store/useJarvisStore";
import { toast } from "../store/useToastStore";

export function GoalsPage() {
  const goalsMap = useJarvisStore((s) => s.goals);
  const setGoals = useJarvisStore((s) => s.setGoals);
  const upsertGoal = useJarvisStore((s) => s.upsertGoal);

  const [goalPrompt, setGoalPrompt] = useState("");
  const [maxCostUsd, setMaxCostUsd] = useState("1.50");
  const [orchestratorModel, setOrchestratorModel] = useState("gemini-4-argon");
  const [submitting, setSubmitting] = useState(false);

  const goalsQuery = useQuery({
    queryKey: ["goals"],
    queryFn: () => apiClient.getGoals(30),
    refetchInterval: 4000,
  });

  const modelsQuery = useQuery({
    queryKey: ["goal-models"],
    queryFn: apiClient.getGoalModels,
  });

  useEffect(() => {
    if (goalsQuery.data) {
      setGoals(goalsQuery.data);
    }
  }, [goalsQuery.data, setGoals]);

  const goals = Object.values(goalsMap).sort((a, b) =>
    b.created_at.localeCompare(a.created_at)
  );
  const activeGoals = goals.filter((g) =>
    ["planning", "running", "verifying", "waiting_user", "waiting_approval", "paused", "paused_budget"].includes(
      g.status
    )
  );
  const finishedGoals = goals.filter(
    (g) => g.status === "completed" || g.status === "failed" || g.status === "cancelled"
  );
  const totalCostUsd = goals.reduce((acc, g) => acc + (g.cost_usd || 0), 0);

  async function handleLaunch(e: FormEvent) {
    e.preventDefault();
    const cleaned = goalPrompt.trim();
    if (!cleaned) return;
    setSubmitting(true);
    try {
      const budget = Number.parseFloat(maxCostUsd) || 1.5;
      const created = await apiClient.createGoal({
        goal: cleaned,
        max_cost_usd: budget,
        orchestrator_model: orchestratorModel || null,
        allowed_models: null,
        notify_on_finish: true,
      });
      upsertGoal(created);
      setGoalPrompt("");
      toast.ok(`Objectif ${created.id} lancé en supervision autonome.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Impossible de lancer l'objectif.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleEmergencyStop() {
    try {
      await apiClient.triggerEmergencyStop();
      await goalsQuery.refetch();
      toast.info("Arrêt d'urgence activé pour tous les objectifs.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Échec arrêt d'urgence.");
    }
  }

  return (
    <section className="page goals-page">
      <PageHeader
        title="Objectifs autonomes"
        description="Jarvis orchestre des sous-agents spécialisés sur modèles économiques jusqu'à vérification finale."
        actions={
          activeGoals.length > 0 ? (
            <ConfirmButton
              onConfirm={handleEmergencyStop}
              label="Arrêt d'urgence"
              confirmLabel="Confirmer l'arrêt"
              className="btn btn--danger btn--sm"
            />
          ) : undefined
        }
      />

      <form className="card goal-composer" onSubmit={handleLaunch}>
        <label htmlFor="goal-input" className="goal-composer__label">
          Nouvel objectif autonome
        </label>
        <textarea
          id="goal-input"
          rows={3}
          className="input goal-composer__textarea"
          placeholder="Décris précisément l'objectif à atteindre et les critères de réussite…"
          value={goalPrompt}
          onChange={(e) => setGoalPrompt(e.target.value)}
          disabled={submitting}
        />
        <div className="goal-composer__controls">
          <label className="goal-composer__field">
            <span>Orchestrateur</span>
            <select
              className="select"
              value={orchestratorModel}
              onChange={(e) => setOrchestratorModel(e.target.value)}
              disabled={submitting}
            >
              {(modelsQuery.data ?? []).map((m) => (
                <option key={m.id} value={m.id}>
                  {formatModelLabel(m.id)} (${m.in_cost_per_m}/${m.out_cost_per_m} /1M)
                </option>
              ))}
              {(!modelsQuery.data || modelsQuery.data.length === 0) && (
                <>
                  <option value="gemini-4-argon">Gemini 4 Argon</option>
                  <option value="gemini-4-hydrogen">Gemini 4 Hydrogen</option>
                  <option value="gemini-4-carbon">Gemini 4 Carbon</option>
                  <option value="deepseek/deepseek-v4.1-flash-1017">DeepSeek V4.1 Flash</option>
                  <option value="z-ai/glm-5.3">GLM 5.3</option>
                  <option value="minimax/minimax-m3">MiniMax M3</option>
                  <option value="moonshotai/kimi-k3">Kimi K3</option>
                </>
              )}
            </select>
          </label>
          <label className="goal-composer__field">
            <span>Budget max (USD)</span>
            <input
              type="number"
              className="input"
              step="0.25"
              min="0.10"
              max="25"
              value={maxCostUsd}
              onChange={(e) => setMaxCostUsd(e.target.value)}
              disabled={submitting}
            />
          </label>
          <button
            type="submit"
            className="btn btn--primary"
            disabled={submitting || !goalPrompt.trim()}
          >
            <Icon name="target" size={16} />
            Lancer l'objectif
          </button>
        </div>
      </form>

      {goalsQuery.isLoading && goals.length === 0 && (
        <div className="card">
          <Skeleton lines={4} />
        </div>
      )}

      {!goalsQuery.isLoading && goals.length === 0 && (
        <EmptyState title="Aucun objectif en cours">
          Lance un objectif ci-dessus ou active le Mode Objectif dans l'accueil ou la conversation.
        </EmptyState>
      )}

      {goals.length > 0 && (
        <div className="kpi-strip" aria-label="Synthèse des objectifs">
          <div className="kpi-pill">
            <span className="kpi-pill__label">En cours</span>
            <span className="kpi-pill__value">{activeGoals.length}</span>
          </div>
          <div className="kpi-pill">
            <span className="kpi-pill__label">Terminés</span>
            <span className="kpi-pill__value">{finishedGoals.length}</span>
          </div>
          <div className="kpi-pill">
            <span className="kpi-pill__label">Coût cumulé</span>
            <span className="kpi-pill__value mono">${totalCostUsd.toFixed(4)}</span>
          </div>
        </div>
      )}

      {activeGoals.length > 0 && (
        <div className="goals-section">
          <h2 className="goals-section__title">Supervision active ({activeGoals.length})</h2>
          <div className="goals-list">
            {activeGoals.map((g) => (
              <GoalTrackerCard key={g.id} goal={g} />
            ))}
          </div>
        </div>
      )}

      {finishedGoals.length > 0 && (
        <div className="goals-section">
          <h2 className="goals-section__title">Historique ({finishedGoals.length})</h2>
          <div className="goals-list">
            {finishedGoals.map((g) => (
              <GoalTrackerCard key={g.id} goal={g} />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
