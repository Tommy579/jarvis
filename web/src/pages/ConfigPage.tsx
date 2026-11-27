import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import { Icon } from "../components/Icon";
import { VoiceProfilesCard } from "../components/VoiceProfilesCard";
import { QueryErrorBanner } from "../components/QueryErrorBanner";
import { PageHeader, Segmented } from "../components/ui";
import { setSchemePreference, useColorScheme, type SchemePreference } from "../lib/colorScheme";
import { useJarvisStore, type JarvisTheme, type ThinkingMode } from "../store/useJarvisStore";
import { errorMessage, toast } from "../store/useToastStore";
import type { RoleModelConfig } from "../types/generated";

const ROLE_LABELS: Record<string, string> = {
  agent: "Agent principal",
  orchestrator: "Orchestrateur",
  triage: "Triage",
  vision: "Vision",
  memory: "Mémoire",
  deep: "Raisonnement profond",
};

const THINKING_OPTIONS: { value: ThinkingMode; label: string }[] = [
  { value: "auto", label: "Raisonnement : Auto" },
  { value: "off", label: "Raisonnement : Off" },
  { value: "low", label: "Raisonnement : Léger" },
  { value: "medium", label: "Raisonnement : Moyen" },
  { value: "high", label: "Raisonnement : Profond" },
];

type ConfigTab = "appearance" | "models" | "autonomy" | "integrations" | "alerts";

const CONFIG_TABS: { value: ConfigTab; label: string }[] = [
  { value: "appearance", label: "Apparence & Système" },
  { value: "models", label: "Modèles & Coûts" },
  { value: "autonomy", label: "Autonomie" },
  { value: "integrations", label: "Intégrations" },
  { value: "alerts", label: "Alertes, Voix & Capteurs" },
];

const HASH_TO_TAB: Record<string, ConfigTab> = {
  "#apparence": "appearance",
  "#systeme": "appearance",
  "#modeles": "models",
  "#couts": "models",
  "#autonomie": "autonomy",
  "#integrations": "integrations",
  "#attention": "alerts",
  "#notifications": "alerts",
  "#voix": "alerts",
  "#capteurs": "alerts",
};

function isSafeHttpUrl(raw: string | null | undefined): boolean {
  if (!raw) return false;
  try {
    const parsed = new URL(raw);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

export function ConfigPage() {
  const qc = useQueryClient();
  const [activeTab, setActiveTab] = useState<ConfigTab>(() => {
    if (typeof window !== "undefined") {
      return HASH_TO_TAB[window.location.hash] ?? "appearance";
    }
    return "appearance";
  });
  const theme = useJarvisStore((s) => s.theme);
  const setTheme = useJarvisStore((s) => s.setTheme);
  const setActiveModel = useJarvisStore((s) => s.setActiveModel);
  const setThinkingMode = useJarvisStore((s) => s.setThinkingMode);
  const setSensors = useJarvisStore((s) => s.setSensors);
  const wakeWordEnabled = useJarvisStore((s) => s.wakeWordEnabled);
  const setWakeWordEnabled = useJarvisStore((s) => s.setWakeWordEnabled);
  const wakeWordThreshold = useJarvisStore((s) => s.wakeWordThreshold);
  const setWakeWordThreshold = useJarvisStore((s) => s.setWakeWordThreshold);
  const audioDuckingEnabled = useJarvisStore((s) => s.audioDuckingEnabled);
  const setAudioDuckingEnabled = useJarvisStore((s) => s.setAudioDuckingEnabled);
  const { preference: schemePref, system: systemScheme } = useColorScheme();

  const [newBlacklistApp, setNewBlacklistApp] = useState("");
  const [allowedDomainsDraft, setAllowedDomainsDraft] = useState("");
  const [domainsInit, setDomainsInit] = useState(false);
  const [runningEvals, setRunningEvals] = useState(false);
  const [editingIntId, setEditingIntId] = useState<string | null>(null);
  const [cfgToken, setCfgToken] = useState("");
  const [cfgUrl, setCfgUrl] = useState("");
  const [cfgCsv, setCfgCsv] = useState("");
  const [cfgClientId, setCfgClientId] = useState("");
  const [cfgClientSecret, setCfgClientSecret] = useState("");
  const [showManualToken, setShowManualToken] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const oauthStatus = params.get("oauth");
    if (!oauthStatus) return;
    if (oauthStatus === "success") {
      const provider = params.get("provider") || "OAuth";
      toast.ok(`Connexion ${provider} réussie.`);
      void qc.invalidateQueries({ queryKey: ["integrations"] });
    } else if (oauthStatus === "error") {
      const reason = params.get("reason") || "autorisation refusée";
      toast.error(`Échec OAuth : ${reason}`);
    }
    window.history.replaceState({}, "", window.location.pathname + window.location.hash);
  }, [qc]);

  const modelsQuery = useQuery({
    queryKey: ["models"],
    queryFn: apiClient.getModels,
    enabled: activeTab === "models",
  });
  const usageQuery = useQuery({
    queryKey: ["usage"],
    queryFn: apiClient.getDailyUsage,
    enabled: activeTab === "models",
  });
  const budgetQuery = useQuery({
    queryKey: ["budget"],
    queryFn: apiClient.getDailyBudget,
    enabled: activeTab === "models",
  });
  const [dailyBudgetDraft, setDailyBudgetDraft] = useState("5.00");

  useEffect(() => {
    if (budgetQuery.data) {
      setDailyBudgetDraft(String(budgetQuery.data.daily_budget_usd));
    }
  }, [budgetQuery.data]);
  const evalsQuery = useQuery({
    queryKey: ["evals"],
    queryFn: apiClient.getLatestEvals,
    retry: false,
    enabled: activeTab === "models",
  });
  const integrationsQuery = useQuery({
    queryKey: ["integrations"],
    queryFn: apiClient.getIntegrations,
    enabled: activeTab === "integrations",
  });
  const rulesQuery = useQuery({
    queryKey: ["rules"],
    queryFn: apiClient.getRules,
    enabled: activeTab === "alerts",
  });
  const policyQuery = useQuery({
    queryKey: ["notification_policy"],
    queryFn: apiClient.getNotificationPolicy,
    enabled: activeTab === "alerts",
  });
  const ordersQuery = useQuery({
    queryKey: ["notification_orders"],
    queryFn: () => apiClient.getNotificationOrders(true),
    enabled: activeTab === "alerts",
  });
  const blacklistQuery = useQuery({
    queryKey: ["blacklist"],
    queryFn: apiClient.getSensorsBlacklist,
    enabled: activeTab === "alerts",
  });
  const autostartQuery = useQuery({
    queryKey: ["autostart"],
    queryFn: apiClient.getAutostart,
    enabled: activeTab === "appearance",
  });
  const autonomyQuery = useQuery({
    queryKey: ["autonomy_policy"],
    queryFn: apiClient.getAutonomyPolicy,
    enabled: activeTab === "autonomy",
  });
  const tasksQuery = useQuery({
    queryKey: ["autonomous_tasks"],
    queryFn: () => apiClient.getAutonomousTasks(10),
    enabled: activeTab === "autonomy",
    refetchInterval: activeTab === "autonomy" ? 5000 : false,
  });

  useEffect(() => {
    if (autonomyQuery.data && !domainsInit) {
      setAllowedDomainsDraft(autonomyQuery.data.allowed_domains.join(", "));
      setDomainsInit(true);
    }
  }, [autonomyQuery.data, domainsInit]);

  const maxCost = Math.max(0.001, ...(usageQuery.data ?? []).map((u) => u.cost));

  const runMutation = async (fn: () => Promise<string | void>) => {
    try {
      const msg = await fn();
      if (msg) toast.ok(msg);
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  return (
    <section className="page" aria-label="Modèles, intégrations, règles et capteurs">
      <PageHeader
        title="Modèles, Intégrations, Règles et Capteurs"
        description="Apparence de l'interface, routage des modèles, coûts, connecteurs et règles de confidentialité."
      />

      <div className="tabs-bar">
        <Segmented<ConfigTab>
          label="Catégories de configuration"
          value={activeTab}
          onChange={setActiveTab}
          options={CONFIG_TABS}
        />
      </div>

      <div className="settings__sections">
        {activeTab === "appearance" && (
          <>
            <QueryErrorBanner
              query={autostartQuery}
              fallback="Impossible de charger le statut du lancement automatique."
            />
            {/* Apparence */}
          <section id="apparence" className="settings__section">
            <h2>Apparence</h2>
            <p>
              Le thème suit automatiquement le réglage du système. Vous pouvez aussi forcer le
              mode clair ou sombre pour cet appareil.
            </p>
            <div className="card card--flush">
              <div className="setting">
                <div className="setting__text">
                  <div className="setting__label">Mode d'affichage</div>
                  <div className="setting__desc">
                    Actuellement synchronisé avec le système ({systemScheme === "dark" ? "sombre" : "clair"}).
                  </div>
                </div>
                <div className="setting__control">
                  <Segmented<SchemePreference>
                    label="Mode d'affichage"
                    value={schemePref}
                    onChange={setSchemePreference}
                    options={[
                      {
                        value: "system",
                        label: `Système (${systemScheme === "dark" ? "sombre" : "clair"})`,
                      },
                      {
                        value: "light",
                        label: (
                          <>
                            <Icon name="sun" size={14} /> Clair
                          </>
                        ),
                      },
                      {
                        value: "dark",
                        label: (
                          <>
                            <Icon name="moon" size={14} /> Sombre
                          </>
                        ),
                      },
                    ]}
                  />
                </div>
              </div>

              <div className="setting" style={{ borderTop: "1px solid var(--line)" }}>
                <div className="setting__text">
                  <div className="setting__label">Couleur d'accent et sphère</div>
                  <div className="setting__desc">
                    Teinte de la sphère 3D et des contrôles principaux.
                  </div>
                </div>
                <div className="setting__control">
                  <Segmented<JarvisTheme>
                    label="Couleur d'accent"
                    value={theme}
                    onChange={setTheme}
                    options={[
                      { value: "jarvis-blue", label: "Bleu Jarvis" },
                      { value: "amber", label: "Ambre et laiton" },
                    ]}
                  />
                </div>
              </div>
            </div>
          </section>

          {/* Systeme */}
          <section id="systeme" className="settings__section">
            <h2>Système et démarrage</h2>
            <p>
              Contrôle le lancement automatique de Jarvis (backend et interface) à l'ouverture de
              session.
            </p>
            <div className="card card--flush">
              <div className="setting">
                <div className="setting__text">
                  <div className="setting__label">Lancement au démarrage du système</div>
                  <div className="setting__desc">
                    {autostartQuery.data
                      ? autostartQuery.data.detail ||
                        (autostartQuery.data.enabled
                          ? "Activé au démarrage de la session."
                          : "Désactivé au démarrage de la session.")
                      : autostartQuery.isError
                        ? "Statut de démarrage automatique indisponible sur cet environnement."
                        : "Chargement du statut de démarrage automatique…"}
                  </div>
                </div>
                <div className="setting__control">
                  {autostartQuery.data && (
                    <Segmented<"on" | "off">
                      label="Lancement au démarrage du système"
                      value={autostartQuery.data.enabled ? "on" : "off"}
                      onChange={(next) => {
                        if (!autostartQuery.data?.supported) return;
                        const nextEnabled = next === "on";
                        void runMutation(async () => {
                          await apiClient.updateAutostart(nextEnabled);
                          await qc.invalidateQueries({ queryKey: ["autostart"] });
                          return nextEnabled
                            ? "Lancement automatique activé."
                            : "Lancement automatique désactivé.";
                        });
                      }}
                      options={[
                        { value: "on", label: "Activé" },
                        { value: "off", label: "Désactivé" },
                      ]}
                    />
                  )}
                </div>
              </div>
            </div>
            </section>
          </>
        )}

        {activeTab === "autonomy" && (
          <>
            <QueryErrorBanner
              query={autonomyQuery}
              fallback="Impossible de charger la politique d'autonomie."
            />
            <QueryErrorBanner
              query={tasksQuery}
              fallback="Impossible de charger les tâches autonomes récentes."
            />
            {/* Autonomie */}
          <section id="autonomie" className="settings__section">
            <h2>Autonomie &amp; Contrôle PC</h2>
            <p>
              Autorise l'agent à interagir de façon autonome (niveaux 0 à 2) avec le navigateur et
              les applications sans captures d'écran, tout en bloquant le niveau 3.
            </p>
            <div className="card card--flush">
              <div className="setting">
                <div className="setting__text">
                  <div className="setting__label">Mode d'exécution</div>
                  <div className="setting__desc">
                    En mode autonome, les clics et saisies navigateur (niveau 2) sont approuvés
                    automatiquement sur les domaines autorisés.
                  </div>
                </div>
                <div className="setting__control">
                  {autonomyQuery.data ? (
                    <Segmented<"supervised" | "autonomous">
                      label="Mode d'exécution"
                      value={autonomyQuery.data.mode}
                      onChange={(nextMode) => {
                        void runMutation(async () => {
                          await apiClient.updateAutonomyPolicy({
                            mode: nextMode,
                            emergency_stop: false,
                          });
                          await qc.invalidateQueries({ queryKey: ["autonomy_policy"] });
                          return nextMode === "autonomous"
                            ? "Mode autonome activé."
                            : "Mode supervisé activé.";
                        });
                      }}
                      options={[
                        { value: "supervised", label: "Supervisé" },
                        { value: "autonomous", label: "Autonome" },
                      ]}
                    />
                  ) : autonomyQuery.isError ? (
                    <span className="badge">Indisponible</span>
                  ) : null}
                </div>
              </div>

              <div className="setting" style={{ borderTop: "1px solid var(--line)" }}>
                <div className="setting__text">
                  <div className="setting__label">Domaines autorisés (liste blanche)</div>
                  <div className="setting__desc">
                    Séparés par des virgules (vide = tous les domaines sauf liste noire).
                  </div>
                </div>
                <div className="setting__control" style={{ display: "flex", gap: "0.5rem" }}>
                  <input
                    className="input"
                    type="text"
                    placeholder="ex: moodle.univ.fr, quiz.example.com"
                    value={allowedDomainsDraft}
                    onChange={(e) => setAllowedDomainsDraft(e.target.value)}
                  />
                  <button
                    type="button"
                    className="btn"
                    onClick={() => {
                      const domains = allowedDomainsDraft
                        .split(",")
                        .map((d) => d.trim())
                        .filter(Boolean);
                      void runMutation(async () => {
                        await apiClient.updateAutonomyPolicy({ allowed_domains: domains });
                        await qc.invalidateQueries({ queryKey: ["autonomy_policy"] });
                        return "Liste blanche de domaines enregistrée.";
                      });
                    }}
                  >
                    Enregistrer
                  </button>
                </div>
              </div>

              <div className="setting" style={{ borderTop: "1px solid var(--line)" }}>
                <div className="setting__text">
                  <div className="setting__label">Arrêt d'urgence (Kill Switch)</div>
                  <div className="setting__desc">
                    {autonomyQuery.data?.emergency_stop
                      ? "Arrêt d'urgence ACTIF — toutes les tâches autonomes sont bloquées."
                      : "Stoppe immédiatement toutes les tâches autonomes en cours."}
                  </div>
                </div>
                <div className="setting__control" style={{ display: "flex", gap: "0.5rem" }}>
                  {autonomyQuery.data?.emergency_stop ? (
                    <button
                      type="button"
                      className="btn"
                      onClick={() => {
                        void runMutation(async () => {
                          await apiClient.updateAutonomyPolicy({ emergency_stop: false });
                          await qc.invalidateQueries({ queryKey: ["autonomy_policy"] });
                          return "Arrêt d'urgence levé.";
                        });
                      }}
                    >
                      Réactiver
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="btn btn--danger"
                      onClick={() => {
                        void runMutation(async () => {
                          const res = await apiClient.triggerEmergencyStop();
                          await qc.invalidateQueries({ queryKey: ["autonomy_policy"] });
                          await qc.invalidateQueries({ queryKey: ["autonomous_tasks"] });
                          return `Arrêt d'urgence activé (${res.cancelled_tasks.length} tâche(s) annulée(s)).`;
                        });
                      }}
                    >
                      Arrêt d'urgence
                    </button>
                  )}
                </div>
              </div>
            </div>

            {tasksQuery.data && tasksQuery.data.length > 0 && (
              <div className="card card--flush" style={{ marginTop: "0.75rem" }}>
                <div className="card__head">
                  <h3 className="card__title">Tâches autonomes récentes</h3>
                </div>
                <ul className="list">
                  {tasksQuery.data.slice(0, 5).map((t) => (
                    <li key={t.id} className="setting">
                      <div className="setting__text">
                        <div className="setting__label">
                          [{t.status}] {t.goal.slice(0, 80)}
                        </div>
                        <div className="setting__desc">
                          {t.max_iterations > 0
                            ? `${t.iterations}/${t.max_iterations}`
                            : t.iterations}{" "}
                          étapes · ${t.cost_usd.toFixed(4)}
                          {t.current_step ? ` · ${t.current_step}` : ""}
                        </div>
                      </div>
                      {(t.status === "running" || t.status === "queued") && (
                        <button
                          type="button"
                          className="btn"
                          onClick={() => {
                            void runMutation(async () => {
                              await apiClient.cancelAutonomousTask(t.id);
                              await qc.invalidateQueries({ queryKey: ["autonomous_tasks"] });
                              return `Tâche ${t.id} annulée.`;
                            });
                          }}
                        >
                          Annuler
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            </section>
          </>
        )}

        {activeTab === "models" && (
          <>
            <QueryErrorBanner
              query={modelsQuery}
              fallback="Impossible de charger la configuration des modèles."
            />
            <QueryErrorBanner
              query={usageQuery}
              fallback="Impossible de charger la consommation du jour."
            />
            <QueryErrorBanner
              query={budgetQuery}
              fallback="Impossible de charger le plafond budgétaire journalier."
            />
            {/* Modeles */}
          <section id="modeles" className="settings__section">
            <h2>Modèles par rôle</h2>
            <p>
              Identifiants Ollama local, OpenRouter ou Gemini utilisés pour chaque rôle et chaîne de
              repli.
            </p>
            <datalist id="jarvis-model-suggestions">
              <option value="qwen3.5:2b">Ollama local · Qwen 3.5 2B</option>
              <option value="qwen3.5:0.8b">Ollama local · Qwen 3.5 0.8B</option>
              <option value="gemma4:31b-cloud">Ollama · Gemma 4 31B</option>
              <option value="nemotron-3-ultra:cloud">Ollama · Nemotron 3 Ultra</option>
              <option value="minimax-m3:cloud">Ollama · MiniMax M3</option>
              <option value="gemini-4-hydrogen">Vertex AI · Gemini 4 Hydrogen</option>
              <option value="gemini-4-argon">Vertex AI · Gemini 4 Argon</option>
              <option value="gemini-4-carbon">Vertex AI · Gemini 4 Carbon</option>
              <option value="gemini-3.5-flash-lite">Vertex AI · Gemini 3.5 Flash Lite</option>
              <option value="gemini-3-flash-preview">Vertex AI · Gemini 3 Flash Preview</option>
              <option value="gemini-3.1-pro-preview">Vertex AI · Gemini 3.1 Pro Preview</option>
              <option value="deepseek/deepseek-v4.1-flash-1017">OpenRouter · DeepSeek v4.1 Flash</option>
              <option value="z-ai/glm-5.3">OpenRouter · GLM 5.3</option>
              <option value="minimax/minimax-m3">OpenRouter · MiniMax M3</option>
              <option value="moonshotai/kimi-k3">OpenRouter · Kimi K3</option>
            </datalist>
            <div className="card card--flush">
              {!modelsQuery.isLoading && !modelsQuery.isError && (modelsQuery.data ?? []).length === 0 && (
                <div className="muted" style={{ fontSize: "var(--fs-sm)", padding: "var(--sp-4)" }}>
                  Aucun rôle de modèle configuré.
                </div>
              )}
              <ul className="list">
                {(modelsQuery.data ?? []).map((cfg) => (
                  <li key={cfg.role} className="model-row">
                    <div>
                      <div className="model-row__role">{ROLE_LABELS[cfg.role] ?? cfg.role}</div>
                      <span className="badge mono">{cfg.provider}</span>
                    </div>
                    <div className="model-row__controls">
                      <input
                        key={`${cfg.role}:${cfg.model}`}
                        type="text"
                        list="jarvis-model-suggestions"
                        className="input mono"
                        aria-label={`Modèle pour le rôle ${cfg.role}`}
                        defaultValue={cfg.model}
                        onBlur={(e) => {
                          const val = e.target.value.trim();
                          if (val && val !== cfg.model) {
                            void runMutation(async () => {
                              const updated = await apiClient.updateRoleModel(
                                cfg.role,
                                val,
                                cfg.fallback,
                                cfg.thinking_mode
                              );
                              qc.setQueryData<RoleModelConfig[]>(["models"], (prev) =>
                                prev?.map((item) => (item.role === updated.role ? updated : item))
                              );
                              if (cfg.role === "agent") setActiveModel(val);
                              await qc.invalidateQueries({ queryKey: ["models"] });
                              return `Modèle ${cfg.role} mis à jour.`;
                            });
                          }
                        }}
                      />
                      <select
                        className="select select--sm"
                        aria-label={`Raisonnement pour le rôle ${cfg.role}`}
                        value={cfg.thinking_mode ?? "auto"}
                        onChange={(e) => {
                          const nextThinking = e.target.value as ThinkingMode;
                          void runMutation(async () => {
                            const updated = await apiClient.updateRoleModel(
                              cfg.role,
                              cfg.model,
                              cfg.fallback,
                              nextThinking
                            );
                            qc.setQueryData<RoleModelConfig[]>(["models"], (prev) =>
                              prev?.map((item) => (item.role === updated.role ? updated : item))
                            );
                            if (cfg.role === "agent") setThinkingMode(updated.thinking_mode);
                            await qc.invalidateQueries({ queryKey: ["models"] });
                            return `Raisonnement ${cfg.role} mis à jour.`;
                          });
                        }}
                      >
                        {THINKING_OPTIONS.map((opt) => (
                          <option key={opt.value} value={opt.value}>
                            {opt.label}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="model-row__fallback mono">
                      Repli : {cfg.fallback.length > 0 ? cfg.fallback.join(" → ") : "aucun"}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </section>

          {/* Couts et evals */}
          <section id="couts" className="settings__section">
            <div className="cluster" style={{ justifyContent: "space-between" }}>
              <div>
                <h2 style={{ fontSize: "var(--fs-lg)" }}>Coûts journaliers et banc d'essai</h2>
              </div>
              <button
                type="button"
                className="btn"
                disabled={runningEvals}
                onClick={() => {
                  setRunningEvals(true);
                  void runMutation(async () => {
                    const suite = await apiClient.runEvals();
                    qc.setQueryData(["evals"], suite);
                    return `Banc d'essai terminé : ${suite.passed}/${suite.total} réussis.`;
                  }).finally(() => setRunningEvals(false));
                }}
              >
                {runningEvals ? "Exécution…" : "Lancer evals"}
              </button>
            </div>

            <div className="card stack">
              <form
                className="cluster"
                style={{ justifyContent: "space-between", alignItems: "flex-end", gap: "var(--sp-3)" }}
                onSubmit={(e) => {
                  e.preventDefault();
                  const parsed = Number.parseFloat(dailyBudgetDraft);
                  if (!Number.isFinite(parsed) || parsed < 0) return;
                  void runMutation(async () => {
                    const res = await apiClient.updateDailyBudget(parsed);
                    qc.setQueryData(["budget"], res);
                    return res.daily_budget_usd === 0
                      ? "Plafond journalier désactivé (illimité)."
                      : `Plafond journalier réglé à $${res.daily_budget_usd.toFixed(2)}.`;
                  });
                }}
              >
                <label className="stack" style={{ gap: 4, flex: "1 1 220px" }}>
                  <span style={{ fontSize: "var(--fs-xs)" }} className="muted">
                    Plafond journalier par fournisseur LLM (USD) — 0 = illimité
                  </span>
                  <input
                    type="number"
                    className="input"
                    min="0"
                    max="1000"
                    step="0.5"
                    value={dailyBudgetDraft}
                    onChange={(e) => setDailyBudgetDraft(e.target.value)}
                  />
                </label>
                <div className="cluster" style={{ gap: "var(--sp-2)" }}>
                  <button type="submit" className="btn btn--primary btn--sm">
                    Enregistrer
                  </button>
                  <button
                    type="button"
                    className="btn btn--sm"
                    onClick={() => {
                      setDailyBudgetDraft("0");
                      void runMutation(async () => {
                        const res = await apiClient.updateDailyBudget(0);
                        qc.setQueryData(["budget"], res);
                        return "Plafond journalier désactivé (illimité).";
                      });
                    }}
                  >
                    Illimité (0)
                  </button>
                </div>
              </form>

              {(usageQuery.data ?? []).length === 0 ? (
                <div className="muted" style={{ fontSize: "var(--fs-sm)" }}>
                  {usageQuery.isError
                    ? "Consommation indisponible : le service jarvis-core n'a pas répondu."
                    : "Aucune consommation enregistrée pour l'instant."}
                </div>
              ) : (
                (usageQuery.data ?? []).map((row) => (
                  <div key={`${row.date}-${row.model}`} className="usage-bar">
                    <span className="mono muted">{row.date}</span>
                    <div className="usage-bar__track">
                      <div
                        className="usage-bar__fill"
                        style={{ width: `${Math.min(100, (row.cost / maxCost) * 100)}%` }}
                      />
                    </div>
                    <span className="mono" style={{ textAlign: "right" }}>
                      ${row.cost.toFixed(4)}
                    </span>
                  </div>
                ))
              )}

              {evalsQuery.data ? (
                <div
                  className="stack"
                  style={{
                    marginTop: "var(--sp-2)",
                    paddingTop: "var(--sp-3)",
                    borderTop: "1px solid var(--line)",
                  }}
                >
                  <div className="cluster" style={{ justifyContent: "space-between" }}>
                    <strong>Banc d'essai</strong>
                    <span className="badge badge--ok mono">
                      {evalsQuery.data.passed}/{evalsQuery.data.total} réussis
                    </span>
                  </div>
                  {evalsQuery.data.scenarios.map((sc) => (
                    <div
                      key={sc.id}
                      className="cluster"
                      style={{ justifyContent: "space-between", fontSize: "var(--fs-sm)" }}
                    >
                      <span>{sc.title}</span>
                      <span className={`badge mono ${sc.passed ? "badge--ok" : "badge--danger"}`}>
                        {sc.passed ? "OK" : "ÉCHEC"} · {Math.round(sc.latency_ms)} ms
                      </span>
                    </div>
                  ))}
                </div>
              ) : !evalsQuery.isLoading ? (
                <div className="mono muted" style={{ fontSize: "var(--fs-xs)" }}>
                  {evalsQuery.isError ? (
                    "Résultats du banc d'essai indisponibles."
                  ) : (
                    <>
                      Banc d'essai jamais exécuté (ou lancez{" "}
                      <span className="kbd">jarvis eval --save</span>).
                    </>
                  )}
                </div>
              ) : null}
            </div>
            </section>
          </>
        )}

        {activeTab === "integrations" && (
          <>
            <QueryErrorBanner
              query={integrationsQuery}
              fallback="Impossible de charger les intégrations externes."
            />
            {/* Integrations */}
          <section id="integrations" className="settings__section">
            <h2>Intégrations externes</h2>
            <p>Connecteurs locaux synchronisés avec la mémoire d'JARVIS.</p>
            {!integrationsQuery.isLoading &&
              !integrationsQuery.isError &&
              (integrationsQuery.data ?? []).length === 0 && (
                <div className="muted" style={{ fontSize: "var(--fs-sm)" }}>
                  Aucune intégration disponible sur cette version de jarvis-core.
                </div>
              )}
            <div className="grid-2">
              {(integrationsQuery.data ?? []).map((item) => {
                const isEditing = editingIntId === item.id;
                const isOAuth = item.auth_type === "oauth";
                const isUrlAuth = item.auth_type === "url";
                const isFinance = item.auth_type === "csv" || item.id === "finance";
                const needsTokenWithUrl =
                  item.id === "home_assistant" || item.id === "health";
                const providerName =
                  item.oauth_provider === "google"
                    ? "Google"
                    : item.oauth_provider === "microsoft"
                      ? "Microsoft / Outlook"
                      : item.oauth_provider === "spotify"
                        ? "Spotify"
                        : item.name;
                const connectOAuth = () =>
                  void runMutation(async () => {
                    const res = await apiClient.getIntegrationOAuthUrl(item.id);
                    if (res.mode === "redirect" && res.url) {
                      if (!isSafeHttpUrl(res.url)) {
                        throw new Error("URL OAuth invalide (protocole non autorisé).");
                      }
                      window.location.href = res.url;
                      return;
                    }
                    if (res.url) {
                      if (!isSafeHttpUrl(res.url)) {
                        throw new Error("URL d'autorisation invalide (protocole non autorisé).");
                      }
                      window.open(res.url, "_blank", "noopener,noreferrer");
                      return `Lien ouvert pour ${item.name}.`;
                    }
                    setEditingIntId(item.id);
                    return `Renseigne d'abord le Client ID / Secret pour ${providerName}.`;
                  });
                return (
                  <div key={item.id} className="card stack">
                    <div className="cluster" style={{ justifyContent: "space-between" }}>
                      <div className="cluster">
                        <strong>{item.name}</strong>
                        <span className={`badge ${item.connected ? "badge--ok" : ""}`}>
                          {item.connected ? "Connecté" : "Non configuré"}
                        </span>
                      </div>
                      <input
                        type="checkbox"
                        className="switch"
                        aria-label={`Activer ${item.name}`}
                        checked={item.enabled}
                        onChange={() =>
                          void runMutation(async () => {
                            const updated = await apiClient.toggleIntegration(item.id);
                            qc.invalidateQueries({ queryKey: ["integrations"] });
                            return `${updated.name} ${updated.enabled ? "activé" : "désactivé"}.`;
                          })
                        }
                      />
                    </div>
                    <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
                      {item.description}
                    </p>
                    <div
                      className="cluster"
                      style={{ justifyContent: "space-between", marginTop: "auto" }}
                    >
                      <span className="mono muted" style={{ fontSize: "var(--fs-xs)" }}>
                        Priorité {item.priority} ·{" "}
                        {item.last_sync
                          ? new Date(item.last_sync).toLocaleString("fr-FR")
                          : "jamais synchronisé"}
                      </span>
                      <div className="cluster">
                        {isOAuth && !item.connected && (item.oauth_client_configured || item.authorize_url) && (
                          <button
                            type="button"
                            className="btn btn--primary btn--sm"
                            onClick={connectOAuth}
                          >
                            Connecter ({providerName})
                          </button>
                        )}
                        <button
                          type="button"
                          className="btn btn--sm"
                          onClick={() =>
                            void runMutation(async () => {
                              const updated = await apiClient.syncIntegration(item.id);
                              qc.invalidateQueries({ queryKey: ["integrations"] });
                              return `${updated.name} synchronisé.`;
                            })
                          }
                        >
                          Synchroniser
                        </button>
                        <button
                          type="button"
                          className="btn btn--ghost btn--sm"
                          onClick={() => {
                            setEditingIntId(isEditing ? null : item.id);
                            setCfgToken("");
                            setCfgUrl("");
                            setCfgCsv("");
                            setCfgClientId("");
                            setCfgClientSecret("");
                            setShowManualToken(false);
                          }}
                        >
                          {isEditing ? "Fermer" : "Configurer"}
                        </button>
                      </div>
                    </div>

                    {isEditing && (
                      <form
                        className="stack"
                        style={{
                          paddingTop: "var(--sp-3)",
                          borderTop: "1px solid var(--line)",
                        }}
                        onSubmit={(e) => {
                          e.preventDefault();
                          void runMutation(async () => {
                            const updated = await apiClient.configureIntegration(item.id, {
                              token: cfgToken,
                              url: cfgUrl,
                              csv_text: cfgCsv,
                              client_id: cfgClientId,
                              client_secret: cfgClientSecret,
                            });
                            setEditingIntId(null);
                            setCfgToken("");
                            setCfgUrl("");
                            setCfgCsv("");
                            setCfgClientId("");
                            setCfgClientSecret("");
                            setShowManualToken(false);
                            qc.invalidateQueries({ queryKey: ["integrations"] });
                            return `${updated.name} configuré et synchronisé.`;
                          });
                        }}
                      >
                        {isOAuth ? (
                          <>
                            <div className="cluster">
                              <button
                                type="button"
                                className="btn btn--primary btn--sm"
                                onClick={connectOAuth}
                              >
                                {item.connected
                                  ? `Reconnecter via ${providerName}`
                                  : `Connecter avec ${providerName}`}
                              </button>
                              <button
                                type="button"
                                className="btn btn--ghost btn--sm"
                                onClick={() => setShowManualToken((v) => !v)}
                              >
                                {showManualToken ? "Masquer saisie manuelle" : "Jeton manuel"}
                              </button>
                            </div>
                            {!item.oauth_client_configured && (
                              <div className="stack">
                                <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>
                                  OAuth direct : renseigne une seule fois Client ID et Client Secret {providerName} (ou utilise le jeton manuel).
                                </span>
                                <input
                                  type="text"
                                  className="input input--sm mono"
                                  placeholder={`Client ID ${providerName}`}
                                  value={cfgClientId}
                                  onChange={(e) => setCfgClientId(e.target.value)}
                                />
                                <input
                                  type="password"
                                  className="input input--sm mono"
                                  placeholder={`Client Secret ${providerName}`}
                                  value={cfgClientSecret}
                                  onChange={(e) => setCfgClientSecret(e.target.value)}
                                />
                              </div>
                            )}
                            {showManualToken && (
                              <input
                                type="password"
                                className="input input--sm mono"
                                placeholder="Jeton OAuth (optionnel)"
                                value={cfgToken}
                                onChange={(e) => setCfgToken(e.target.value)}
                              />
                            )}
                            {(!item.oauth_client_configured || showManualToken) && (
                              <button type="submit" className="btn btn--sm">
                                Enregistrer
                              </button>
                            )}
                          </>
                        ) : (
                          <>
                            {isSafeHttpUrl(item.authorize_url) && item.authorize_url && (
                              <div>
                                <a
                                  href={item.authorize_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="btn btn--ghost btn--sm"
                                >
                                  {isUrlAuth
                                    ? `Ouvrir ${item.name}`
                                    : `Obtenir la clé sur ${item.name}`}
                                </a>
                              </div>
                            )}
                            {isUrlAuth && (
                              <input
                                type="text"
                                className="input input--sm mono"
                                placeholder={
                                  item.id === "apple_calendar"
                                    ? "Lien .ics / webcal:// (ex : webcal://p12-caldav.icloud.com/...)"
                                    : item.id === "github_probes"
                                      ? "URLs à surveiller (séparées par virgule)"
                                      : "URL Home Assistant (ex : http://192.168.1.50:8123)"
                                }
                                value={cfgUrl}
                                onChange={(e) => setCfgUrl(e.target.value)}
                              />
                            )}
                            {isFinance ? (
                              <textarea
                                className="textarea mono"
                                rows={3}
                                placeholder={"date,label,amount\n2026-11-01,Courses,-45.20"}
                                value={cfgCsv}
                                onChange={(e) => setCfgCsv(e.target.value)}
                              />
                            ) : (
                              (!isUrlAuth || needsTokenWithUrl) && (
                                <input
                                  type="password"
                                  className="input input--sm mono"
                                  placeholder="Jeton / Clé API (stocké dans .env local)"
                                  value={cfgToken}
                                  onChange={(e) => setCfgToken(e.target.value)}
                                />
                              )
                            )}
                            <button type="submit" className="btn btn--primary btn--sm">
                              Enregistrer et tester
                            </button>
                          </>
                        )}
                      </form>
                    )}
                  </div>
                );
              })}
            </div>
            </section>
          </>
        )}

        {activeTab === "alerts" && (
          <>
            <QueryErrorBanner
              query={rulesQuery}
              fallback="Impossible de charger les règles d'attention."
            />
            <QueryErrorBanner
              query={policyQuery}
              fallback="Impossible de charger la politique de notification."
            />
            <QueryErrorBanner
              query={ordersQuery}
              fallback="Impossible de charger les ordres de notification."
            />
            <QueryErrorBanner
              query={blacklistQuery}
              fallback="Impossible de charger la liste noire des capteurs."
            />
            {/* Regles d'attention */}
          {rulesQuery.data && (
            <section id="attention" className="settings__section">
              <h2>Règles d'attention</h2>
              <p>Plages horaires silencieuses et plafond quotidien d'interruptions.</p>
              <div className="card stack">
                <div className="grid-2">
                  <label className="field">
                    <span className="field__label">Début des heures calmes</span>
                    <input
                      key={`qs:${rulesQuery.data.quiet_start}`}
                      type="text"
                      className="input mono"
                      defaultValue={rulesQuery.data.quiet_start}
                      onBlur={(e) => {
                        const val = e.target.value.trim();
                        if (!val || val === rulesQuery.data!.quiet_start) return;
                        void runMutation(async () => {
                          await apiClient.updateRules({
                            ...rulesQuery.data!,
                            quiet_start: val,
                          });
                          qc.invalidateQueries({ queryKey: ["rules"] });
                          qc.invalidateQueries({ queryKey: ["notification_policy"] });
                          return "Heures calmes mises à jour.";
                        });
                      }}
                    />
                  </label>
                  <label className="field">
                    <span className="field__label">Fin des heures calmes</span>
                    <input
                      key={`qe:${rulesQuery.data.quiet_end}`}
                      type="text"
                      className="input mono"
                      defaultValue={rulesQuery.data.quiet_end}
                      onBlur={(e) => {
                        const val = e.target.value.trim();
                        if (!val || val === rulesQuery.data!.quiet_end) return;
                        void runMutation(async () => {
                          await apiClient.updateRules({
                            ...rulesQuery.data!,
                            quiet_end: val,
                          });
                          qc.invalidateQueries({ queryKey: ["rules"] });
                          qc.invalidateQueries({ queryKey: ["notification_policy"] });
                          return "Heures calmes mises à jour.";
                        });
                      }}
                    />
                  </label>
                </div>
                <div className="mono muted" style={{ fontSize: "var(--fs-xs)" }}>
                  Urgence minimale la nuit : Niveau {policyQuery.data?.quiet_allow_urgency ?? 4} ·
                  Contacts prioritaires :{" "}
                  {rulesQuery.data.vip_contacts.map((v) => v.name).join(", ") || "aucun"}
                </div>
              </div>
            </section>
          )}

          {/* Notifications, approbations et ordres persistants */}
          {policyQuery.data && (
            <section id="notifications" className="settings__section">
              <h2>Notifications, approbations et ordres persistants</h2>
              <p>
                Canaux d'alerte actifs (Interface Web, Voix locale, Telegram), délai d'approbation
                et consignes durables de notification.
              </p>

              <div className="card stack">
                <div className="cluster" style={{ justifyContent: "space-between" }}>
                  <div>
                    <strong>Notifications proactives</strong>
                    <div className="muted" style={{ fontSize: "var(--fs-xs)" }}>
                      Active ou suspend l'envoi des alertes et rappels proactifs.
                    </div>
                  </div>
                  <Segmented<"on" | "off">
                    label="Notifications proactives"
                    value={policyQuery.data.enabled ? "on" : "off"}
                    options={[
                      { value: "on", label: "Activées" },
                      { value: "off", label: "En pause" },
                    ]}
                    onChange={(val) => {
                      void runMutation(async () => {
                        await apiClient.updateNotificationPolicy({
                          enabled: val === "on",
                        });
                        qc.invalidateQueries({ queryKey: ["notification_policy"] });
                        return val === "on"
                          ? "Notifications proactives activées."
                          : "Notifications proactives en pause.";
                      });
                    }}
                  />
                </div>

                <div className="cluster" style={{ justifyContent: "space-between" }}>
                  <div>
                    <strong>Seuil pendant les heures calmes</strong>
                    <div className="muted" style={{ fontSize: "var(--fs-xs)" }}>
                      Niveau d'urgence minimal autorisé à vous interrompre la nuit.
                    </div>
                  </div>
                  <Segmented<"3" | "4">
                    label="Seuil pendant les heures calmes"
                    value={policyQuery.data.quiet_allow_urgency <= 3 ? "3" : "4"}
                    options={[
                      { value: "3", label: "Urgent & Critique (N3+)" },
                      { value: "4", label: "Critique uniquement (N4)" },
                    ]}
                    onChange={(val) => {
                      const urgency = parseInt(val, 10);
                      void runMutation(async () => {
                        await apiClient.updateNotificationPolicy({
                          quiet_allow_urgency: urgency,
                        });
                        qc.invalidateQueries({ queryKey: ["notification_policy"] });
                        return "Seuil nocturne mis à jour.";
                      });
                    }}
                  />
                </div>

                <div className="grid-2" style={{ marginTop: "var(--sp-2)" }}>
                  <label className="field">
                    <span className="field__label">Expiration approbations (secondes)</span>
                    <input
                      key={`ttl:${policyQuery.data.approval_ttl_seconds}`}
                      type="number"
                      min={60}
                      max={3600}
                      className="input mono"
                      defaultValue={policyQuery.data.approval_ttl_seconds}
                      onBlur={(e) => {
                        const num = parseInt(e.target.value, 10);
                        if (!isNaN(num) && num !== policyQuery.data!.approval_ttl_seconds) {
                          void runMutation(async () => {
                            await apiClient.updateNotificationPolicy({
                              approval_ttl_seconds: num,
                            });
                            qc.invalidateQueries({ queryKey: ["notification_policy"] });
                            return "Délai d'expiration mis à jour.";
                          });
                        }
                      }}
                    />
                  </label>
                </div>

                <div className="muted" style={{ fontSize: "var(--fs-xs)" }}>
                  Canaux de transmission actifs :{" "}
                  <code className="mono">Interface Web & Voix locale → Telegram</code>
                </div>
              </div>

              {/* Ordres actifs */}
              <div className="card stack" style={{ marginTop: "var(--sp-3)" }}>
                <h3>Ordres de notification actifs ({ordersQuery.data?.length ?? 0})</h3>
                <p style={{ fontSize: "var(--fs-xs)" }}>
                  Ordres formulés dans la conversation qui persistent après redémarrage.
                </p>

                {(!ordersQuery.data || ordersQuery.data.length === 0) ? (
                  <div className="muted" style={{ fontSize: "var(--fs-sm)" }}>
                    Aucun ordre persistant enregistré pour le moment.
                  </div>
                ) : (
                  <div className="stack" style={{ gap: "var(--sp-2)" }}>
                    {ordersQuery.data.map((order) => (
                      <div
                        key={order.id}
                        className="cluster"
                        style={{
                          justifyContent: "space-between",
                          padding: "var(--sp-2)",
                          background: "var(--bg-card)",
                          borderRadius: "var(--r-md)",
                          border: "1px solid var(--line)",
                        }}
                      >
                        <div>
                          <strong>{order.note || order.text_match || `${order.source_glob}:${order.event_type_glob}`}</strong>
                          <div className="muted mono" style={{ fontSize: "var(--fs-xs)" }}>
                            Urgence {order.min_urgency} · Déclenché {order.fired_count}{order.max_fires ? `/${order.max_fires}` : ""} fois
                            {order.expires_at ? ` · Expire : ${order.expires_at.slice(0, 10)}` : ""}
                          </div>
                        </div>
                        <button
                          type="button"
                          className="btn btn--sm"
                          onClick={() => {
                            void runMutation(async () => {
                              await apiClient.deleteNotificationOrder(order.id);
                              qc.invalidateQueries({ queryKey: ["notification_orders"] });
                              return "Ordre révoqué.";
                            });
                          }}
                        >
                          Révoquer
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>
          )}

          {/* Capteurs et liste noire */}
          {blacklistQuery.data && (
            <section id="capteurs" className="settings__section">
              <div className="cluster" style={{ justifyContent: "space-between" }}>
                <div>
                  <h2 style={{ fontSize: "var(--fs-lg)" }}>Capteurs et liste noire</h2>
                </div>
                <button
                  type="button"
                  className="btn btn--primary"
                  onClick={() => {
                    const nextPaused = !blacklistQuery.data!.global_paused;
                    void runMutation(async () => {
                      const updated = await apiClient.updateSensorsBlacklist({
                        ...blacklistQuery.data!,
                        global_paused: nextPaused,
                        sensors: blacklistQuery.data!.sensors.map((s) => ({
                          ...s,
                          paused: nextPaused,
                          active: !nextPaused,
                        })),
                      });
                      setSensors(updated.sensors);
                      qc.invalidateQueries({ queryKey: ["blacklist"] });
                      return nextPaused ? "Capteurs mis en pause." : "Capteurs réactivés.";
                    });
                  }}
                >
                  {blacklistQuery.data.global_paused ? "Reactiver tout" : "Pause globale"}
                </button>
              </div>
              <p>
                Applications exclues de toute capture d'écran ou d'analyse contextuelle.
              </p>

              <div className="card card--flush">
                <div className="setting">
                  <div className="setting__text">
                    <div className="setting__label">Mot d'activation vocal (« Jarvis »)</div>
                    <div className="setting__desc">
                      En mode wake word, le micro attend « Jarvis » pour démarrer et « coupe le record » pour revenir en veille.
                    </div>
                  </div>
                  <div className="setting__control">
                    <Segmented<"wake" | "direct">
                      label="Mot d'activation vocal"
                      value={wakeWordEnabled ? "wake" : "direct"}
                      onChange={(mode) => {
                        const enabled = mode === "wake";
                        setWakeWordEnabled(enabled);
                        toast.ok(
                          enabled
                            ? "Wake word « Jarvis » activé."
                            : "Écoute directe activée (sans mot d'activation)."
                        );
                      }}
                      options={[
                        { value: "wake", label: "Wake word « Jarvis »" },
                        { value: "direct", label: "Écoute directe" },
                      ]}
                    />
                  </div>
                </div>

                <div className="setting" style={{ borderTop: "1px solid var(--line)" }}>
                  <div className="setting__text">
                    <div className="setting__label">
                      Seuil de détection du wake word ({Math.round(wakeWordThreshold * 100)} %)
                    </div>
                    <div className="setting__desc">
                      Plus le seuil est bas, plus Jarvis réagit facilement à voix basse ; plus il est haut, moins les bruits ambiants déclenchent l'écoute.
                    </div>
                  </div>
                  <div
                    className="setting__control stack"
                    style={{ alignItems: "flex-end", gap: "0.45rem" }}
                  >
                    <div className="cluster">
                      {[
                        { value: 0.22, label: "Sensible (22 %)" },
                        { value: 0.38, label: "Équilibré (38 %)" },
                        { value: 0.58, label: "Strict (58 %)" },
                      ].map((preset) => (
                        <button
                          key={preset.value}
                          type="button"
                          className={
                            Math.abs(wakeWordThreshold - preset.value) < 0.025
                              ? "btn btn--primary"
                              : "btn"
                          }
                          onClick={() => setWakeWordThreshold(preset.value)}
                        >
                          {preset.label}
                        </button>
                      ))}
                    </div>
                    <input
                      type="range"
                      min={0.15}
                      max={0.75}
                      step={0.02}
                      value={wakeWordThreshold}
                      aria-label="Seuil de détection du wake word"
                      style={{ width: "210px" }}
                      onChange={(e) => setWakeWordThreshold(Number(e.target.value))}
                    />
                  </div>
                </div>

                <div className="setting" style={{ borderTop: "1px solid var(--line)" }}>
                  <div className="setting__text">
                    <div className="setting__label">Atténuer les autres sons quand Jarvis parle</div>
                    <div className="setting__desc">
                      Baisse automatiquement le volume des autres applications (musique, vidéos, navigateur) pendant que Jarvis parle puis le rétablit juste après.
                    </div>
                  </div>
                  <div className="setting__control">
                    <Segmented<"on" | "off">
                      label="Atténuation audio pendant la parole de Jarvis"
                      value={audioDuckingEnabled ? "on" : "off"}
                      onChange={(next) => {
                        const enabled = next === "on";
                        setAudioDuckingEnabled(enabled);
                        if (!enabled) {
                          void apiClient.setAudioDucking(false).catch(() => undefined);
                        }
                        toast.ok(
                          enabled
                            ? "Atténuation des autres sons activée."
                            : "Atténuation des autres sons désactivée."
                        );
                      }}
                      options={[
                        { value: "on", label: "Activé" },
                        { value: "off", label: "Désactivé" },
                      ]}
                    />
                  </div>
                </div>
              </div>

              <div className="card stack">
                <div className="cluster">
                  {blacklistQuery.data.apps.map((appName) => (
                    <span key={appName} className="tag">
                      {appName}
                      <button
                        type="button"
                        className="icon-btn icon-btn--sm"
                        aria-label={`Retirer ${appName}`}
                        onClick={() => {
                          void runMutation(async () => {
                            const updated = await apiClient.updateSensorsBlacklist({
                              ...blacklistQuery.data!,
                              apps: blacklistQuery.data!.apps.filter((a) => a !== appName),
                            });
                            setSensors(updated.sensors);
                            qc.invalidateQueries({ queryKey: ["blacklist"] });
                            return `Application ${appName} retirée.`;
                          });
                        }}
                      >
                        <Icon name="x" size={12} />
                      </button>
                    </span>
                  ))}
                </div>

                <form
                  className="cluster"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const val = newBlacklistApp.trim().toLowerCase();
                    if (!val) return;
                    void runMutation(async () => {
                      const nextApps = Array.from(new Set([...blacklistQuery.data!.apps, val]));
                      await apiClient.updateSensorsBlacklist({
                        ...blacklistQuery.data!,
                        apps: nextApps,
                      });
                      setNewBlacklistApp("");
                      qc.invalidateQueries({ queryKey: ["blacklist"] });
                      return `Application ${val} exclue.`;
                    });
                  }}
                >
                  <input
                    type="text"
                    className="input"
                    style={{ flex: 1 }}
                    placeholder="Application à exclure (ex : signal)"
                    aria-label="Application à exclure"
                    value={newBlacklistApp}
                    onChange={(e) => setNewBlacklistApp(e.target.value)}
                  />
                  <button type="submit" className="btn">
                    Exclure
                  </button>
                </form>
              </div>
            </section>
          )}

          {/* Voix reconnues */}
          <section id="voix" className="settings__section">
            <h2 style={{ fontSize: "var(--fs-lg)" }}>Voix reconnues</h2>
            <p>
              Ajoutez votre voix ainsi que celles de votre entourage (compagne, amis) pour que Jarvis
              ignore automatiquement toute autre source sonore.
            </p>
            <VoiceProfilesCard />
          </section>
          </>
        )}
      </div>
    </section>
  );
}
