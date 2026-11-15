import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import { Icon } from "../components/Icon";
import { PageHeader, Segmented } from "../components/ui";
import { setSchemePreference, useColorScheme, type SchemePreference } from "../lib/colorScheme";
import { useOmiStore, type OmiTheme } from "../store/useOmiStore";
import { errorMessage, toast } from "../store/useToastStore";

export function ConfigPage() {
  const qc = useQueryClient();
  const theme = useOmiStore((s) => s.theme);
  const setTheme = useOmiStore((s) => s.setTheme);
  const setActiveModel = useOmiStore((s) => s.setActiveModel);
  const setSensors = useOmiStore((s) => s.setSensors);
  const { preference: schemePref, system: systemScheme } = useColorScheme();

  const [newBlacklistApp, setNewBlacklistApp] = useState("");
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

  const modelsQuery = useQuery({ queryKey: ["models"], queryFn: apiClient.getModels });
  const usageQuery = useQuery({ queryKey: ["usage"], queryFn: apiClient.getDailyUsage });
  const evalsQuery = useQuery({
    queryKey: ["evals"],
    queryFn: apiClient.getLatestEvals,
    retry: false,
  });
  const integrationsQuery = useQuery({
    queryKey: ["integrations"],
    queryFn: apiClient.getIntegrations,
  });
  const rulesQuery = useQuery({ queryKey: ["rules"], queryFn: apiClient.getRules });
  const policyQuery = useQuery({
    queryKey: ["notification_policy"],
    queryFn: apiClient.getNotificationPolicy,
  });
  const ordersQuery = useQuery({
    queryKey: ["notification_orders"],
    queryFn: () => apiClient.getNotificationOrders(true),
  });
  const blacklistQuery = useQuery({
    queryKey: ["blacklist"],
    queryFn: apiClient.getSensorsBlacklist,
  });
  const autostartQuery = useQuery({
    queryKey: ["autostart"],
    queryFn: apiClient.getAutostart,
  });

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
    <section className="page" aria-label="Modeles, integrations, regles et capteurs">
      <PageHeader
        title="Modeles, Integrations, Regles et Capteurs"
        description="Apparence de l'interface, routage des modèles, coûts, connecteurs et règles de confidentialité."
      />

      <div className="settings">
        <nav className="settings__toc" aria-label="Sections des réglages">
          <a href="#apparence">Apparence</a>
          <a href="#systeme">Système</a>
          <a href="#modeles">Modèles</a>
          <a href="#couts">Coûts et évaluations</a>
          <a href="#integrations">Intégrations</a>
          <a href="#attention">Règles d'attention</a>
          <a href="#notifications">Téléphonie et ordres</a>
          <a href="#capteurs">Capteurs et liste noire</a>
        </nav>

        <div className="settings__sections">
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
                  <Segmented<OmiTheme>
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

          {/* Modeles */}
          <section id="modeles" className="settings__section">
            <h2>Modèles par rôle</h2>
            <p>
              Identifiants Ollama local, OpenRouter ou Gemini utilisés pour chaque rôle et chaîne de
              repli.
            </p>
            <datalist id="omi-model-suggestions">
              <option value="qwen3.5:2b">Ollama local · Qwen 3.5 2B</option>
              <option value="qwen3.5:0.8b">Ollama local · Qwen 3.5 0.8B</option>
              <option value="gemma4:31b-cloud">Ollama · Gemma 4 31B</option>
              <option value="nemotron-3-ultra:cloud">Ollama · Nemotron 3 Ultra</option>
              <option value="minimax-m3:cloud">Ollama · MiniMax M3</option>
              <option value="gemini-3-flash-preview">Gemini 3 Flash Preview</option>
              <option value="gemini-3.1-pro-preview">Gemini 3.1 Pro Preview</option>
              <option value="deepseek/deepseek-v4.1-flash-1017">OpenRouter · DeepSeek v4.1 Flash</option>
              <option value="z-ai/glm-5.3">OpenRouter · GLM 5.3</option>
              <option value="minimax/minimax-m3">OpenRouter · MiniMax M3</option>
              <option value="google/gemini-3.5-flash-lite">OpenRouter · Gemini 3.5 Flash Lite</option>
            </datalist>
            <div className="card card--flush">
              <ul className="list">
                {(modelsQuery.data ?? []).map((cfg) => (
                  <li key={cfg.role} className="model-row">
                    <div>
                      <div className="model-row__role">{cfg.role}</div>
                      <span className="badge mono">{cfg.provider}</span>
                    </div>
                    <input
                      type="text"
                      list="omi-model-suggestions"
                      className="input mono"
                      aria-label={`Modèle pour le rôle ${cfg.role}`}
                      defaultValue={cfg.model}
                      onBlur={(e) => {
                        const val = e.target.value.trim();
                        if (val && val !== cfg.model) {
                          void runMutation(async () => {
                            await apiClient.updateRoleModel(cfg.role, val, cfg.fallback);
                            if (cfg.role === "agent") setActiveModel(val);
                            qc.invalidateQueries({ queryKey: ["models"] });
                            return `Modèle ${cfg.role} mis à jour.`;
                          });
                        }
                      }}
                    />
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
              {(usageQuery.data ?? []).length === 0 ? (
                <div className="muted" style={{ fontSize: "var(--fs-sm)" }}>
                  Aucune consommation enregistrée pour l'instant.
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
                  Banc d'essai jamais exécuté (ou lancez <span className="kbd">omi eval --save</span>).
                </div>
              ) : null}
            </div>
          </section>

          {/* Integrations */}
          <section id="integrations" className="settings__section">
            <h2>Intégrations externes</h2>
            <p>Connecteurs locaux synchronisés avec la mémoire d'OMI.</p>
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
                      window.location.href = res.url;
                      return;
                    }
                    if (res.url) {
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
                            {item.authorize_url && (
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
                      type="text"
                      className="input mono"
                      defaultValue={rulesQuery.data.quiet_start}
                      onBlur={(e) => {
                        void runMutation(async () => {
                          await apiClient.updateRules({
                            ...rulesQuery.data!,
                            quiet_start: e.target.value,
                          });
                          qc.invalidateQueries({ queryKey: ["rules"] });
                          return "Heures calmes mises à jour.";
                        });
                      }}
                    />
                  </label>
                  <label className="field">
                    <span className="field__label">Fin des heures calmes</span>
                    <input
                      type="text"
                      className="input mono"
                      defaultValue={rulesQuery.data.quiet_end}
                      onBlur={(e) => {
                        void runMutation(async () => {
                          await apiClient.updateRules({
                            ...rulesQuery.data!,
                            quiet_end: e.target.value,
                          });
                          qc.invalidateQueries({ queryKey: ["rules"] });
                          return "Heures calmes mises à jour.";
                        });
                      }}
                    />
                  </label>
                </div>
                <div className="mono muted" style={{ fontSize: "var(--fs-xs)" }}>
                  Plafond : {rulesQuery.data.max_calls_per_day} appels/jour (hors urgence 4) ·
                  Contacts prioritaires :{" "}
                  {rulesQuery.data.vip_contacts.map((v) => v.name).join(", ") || "aucun"}
                </div>
              </div>
            </section>
          )}

          {/* Téléphonie et ordres de notification */}
          {policyQuery.data && (
            <section id="notifications" className="settings__section">
              <h2>Téléphonie et ordres persistants</h2>
              <p>
                Politique d'intrusion par téléphone, échelle d'escalade et consignes durables de notification.
              </p>

              <div className="card stack">
                <div className="cluster" style={{ justifyContent: "space-between" }}>
                  <div>
                    <strong>Autoriser les appels téléphoniques</strong>
                    <div className="muted" style={{ fontSize: "var(--fs-xs)" }}>
                      Jarvis peut composer votre numéro pour une approbation ou une alerte critique.
                    </div>
                  </div>
                  <Segmented
                    label="Politique d'appels"
                    value={
                      !policyQuery.data.allow_calls
                        ? policyQuery.data.allow_emergency_calls
                          ? "emergency"
                          : "never"
                        : "all"
                    }
                    options={[
                      { value: "all", label: "Urgent & Critique" },
                      { value: "emergency", label: "Urgence extrême (N4)" },
                      { value: "never", label: "Désactivé" },
                    ]}
                    onChange={(val) => {
                      void runMutation(async () => {
                        const allow_calls = val === "all";
                        const allow_emergency_calls = val === "all" || val === "emergency";
                        await apiClient.updateNotificationPolicy({
                          allow_calls,
                          allow_emergency_calls,
                        });
                        qc.invalidateQueries({ queryKey: ["notification_policy"] });
                        return "Politique d'appel mise à jour.";
                      });
                    }}
                  />
                </div>

                <div className="grid-2" style={{ marginTop: "var(--sp-2)" }}>
                  <label className="field">
                    <span className="field__label">Plafond d'appels quotidiens</span>
                    <input
                      type="number"
                      min={1}
                      max={20}
                      className="input mono"
                      defaultValue={policyQuery.data.max_calls_per_day}
                      onBlur={(e) => {
                        const num = parseInt(e.target.value, 10);
                        if (!isNaN(num)) {
                          void runMutation(async () => {
                            await apiClient.updateNotificationPolicy({
                              max_calls_per_day: num,
                            });
                            qc.invalidateQueries({ queryKey: ["notification_policy"] });
                            return "Plafond d'appels mis à jour.";
                          });
                        }
                      }}
                    />
                  </label>
                  <label className="field">
                    <span className="field__label">Expiration approbations (secondes)</span>
                    <input
                      type="number"
                      min={60}
                      max={3600}
                      className="input mono"
                      defaultValue={policyQuery.data.approval_ttl_seconds}
                      onBlur={(e) => {
                        const num = parseInt(e.target.value, 10);
                        if (!isNaN(num)) {
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
                  Échelle de transmission active :{" "}
                  <code className="mono">{policyQuery.data.channel_ladder.join(" → ")}</code>
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
                    placeholder="Application a exclure (ex : signal)"
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
        </div>
      </div>
    </section>
  );
}
