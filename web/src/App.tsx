import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { apiClient } from "./api/client";
import { CommandPalette, type CommandItem } from "./components/CommandPalette";
import { Icon, type IconName } from "./components/Icon";
import { JarvisLogo } from "./components/JarvisLogo";
import { ErrorBanner, Skeleton, Toaster } from "./components/ui";
import { describeApiError } from "./lib/apiErrors";
import { setSchemePreference, useColorScheme } from "./lib/colorScheme";
import { getAuthToken } from "./lib/authToken";
import { formatModelLabel } from "./lib/modelLabels";
import { SLASH_COMMANDS } from "./lib/slashCommands";
import {
  extractWakeWord,
  isStopRecordCommand,
  shouldAutoStopVoiceRecord,
} from "./lib/voiceCommands";
import { HomePage } from "./pages/HomePage";
import { AudioEngine } from "./sphere/AudioEngine";
import { useJarvisStore, type ThinkingMode } from "./store/useJarvisStore";
import { toast } from "./store/useToastStore";
import type { SensorName } from "./types/generated";
import { JarvisWsClient } from "./ws/client";

const AlertsApprovalsPage = lazy(() =>
  import("./pages/AlertsApprovalsPage").then((m) => ({ default: m.AlertsApprovalsPage }))
);
const ConfigPage = lazy(() =>
  import("./pages/ConfigPage").then((m) => ({ default: m.ConfigPage }))
);
const ConversationPage = lazy(() =>
  import("./pages/ConversationPage").then((m) => ({ default: m.ConversationPage }))
);
const GoalsPage = lazy(() =>
  import("./pages/GoalsPage").then((m) => ({ default: m.GoalsPage }))
);
const MemoryOnboardingPage = lazy(() =>
  import("./pages/MemoryOnboardingPage").then((m) => ({ default: m.MemoryOnboardingPage }))
);
const NotFoundPage = lazy(() =>
  import("./pages/NotFoundPage").then((m) => ({ default: m.NotFoundPage }))
);
// three.js ne doit pas entrer dans le bundle initial : la fenetre n'est chargee qu'a l'usage.
const VisualArtifactWindow = lazy(() =>
  import("./components/VisualArtifactWindow").then((m) => ({
    default: m.VisualArtifactWindow,
  }))
);

type AppRoute = "/" | "/conversation" | "/goals" | "/alerts" | "/memory" | "/config" | "/not-found";

interface RouteMeta {
  path: Exclude<AppRoute, "/not-found">;
  label: string;
  icon: IconName;
  title: string;
  description: string;
}

const ROUTES: RouteMeta[] = [
  {
    path: "/",
    label: "Accueil",
    icon: "home",
    title: "JARVIS 2.0 | Accueil vocal",
    description: "Sphère vocale réactive, transcription en direct et capteurs de JARVIS 2.0.",
  },
  {
    path: "/conversation",
    label: "Conversation",
    icon: "chat",
    title: "JARVIS 2.0 | Conversation",
    description: "Historique complet des échanges, outils appelés et coût par modèle.",
  },
  {
    path: "/goals",
    label: "Objectifs",
    icon: "target",
    title: "JARVIS 2.0 | Objectifs autonomes",
    description: "Supervision en direct des objectifs, sous-agents multi-modèles et watchdog.",
  },
  {
    path: "/alerts",
    label: "Alertes",
    icon: "bell",
    title: "JARVIS 2.0 | Alertes et approbations",
    description: "Validation des actions à risque et historique des alertes envoyées.",
  },
  {
    path: "/memory",
    label: "Mémoire",
    icon: "memory",
    title: "JARVIS 2.0 | Mémoire",
    description: "Faits, wiki personnel, proches et questionnaire de profil.",
  },
  {
    path: "/config",
    label: "Configuration",
    icon: "sliders",
    title: "JARVIS 2.0 | Configuration",
    description: "Apparence, modèles, coûts, intégrations, règles d'attention et capteurs.",
  },
];

const SENSOR_LABELS: Record<string, string> = { screen: "écran", mic: "micro", camera: "caméra" };

/**
 * Sections chargees au montage. Chacune dispose de son propre etat d'erreur :
 * une panne reseau sur une section n'empeche plus les autres de s'afficher et
 * ne se traduit plus par une liste vide silencieuse.
 */
type BootstrapSection = "approvals" | "alerts" | "sensors" | "models" | "goals";

const BOOTSTRAP_SECTIONS: BootstrapSection[] = [
  "approvals",
  "alerts",
  "sensors",
  "models",
  "goals",
];

const SECTION_ERROR_FALLBACKS: Record<BootstrapSection, string> = {
  approvals: "Impossible de charger les approbations en attente.",
  alerts: "Impossible de charger le journal des alertes.",
  sensors: "Impossible de charger l'état des capteurs.",
  models: "Impossible de charger la configuration des modèles.",
  goals: "Impossible de charger les objectifs autonomes.",
};

/**
 * Pages qui consomment la section SANS requete propre : le bandeau de section
 * n'apparait que la. Les pages qui interrogent elles-memes l'API (Objectifs,
 * Alertes, Configuration) affichent leur propre bandeau via QueryErrorBanner,
 * ce qui evite tout doublon.
 */
const SECTION_ROUTES: Record<BootstrapSection, AppRoute[]> = {
  approvals: [],
  alerts: [],
  sensors: ["/"],
  models: ["/"],
  goals: ["/", "/conversation"],
};

type SectionErrors = Partial<Record<BootstrapSection, string>>;

function resolveRoute(pathname: string): AppRoute {
  const clean = pathname !== "/" && pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
  if (clean === "" || clean === "/") return "/";
  const match = ROUTES.find((r) => r.path === clean);
  return match ? match.path : "/not-found";
}

function getRouteMeta(route: AppRoute): { title: string; description: string } {
  const found = ROUTES.find((r) => r.path === route);
  if (found) return { title: found.title, description: found.description };
  return {
    title: "JARVIS 2.0 | Page introuvable",
    description: "L'écran demandé n'existe pas dans l'interface JARVIS 2.0.",
  };
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || tag === "BUTTON") {
    return true;
  }
  return target.isContentEditable;
}

export function App() {
  const [route, setRoute] = useState<AppRoute>(() =>
    typeof window !== "undefined" ? resolveRoute(window.location.pathname) : "/"
  );
  const [paletteOpen, setPaletteOpen] = useState(false);

  const audioEngine = useMemo(() => new AudioEngine(), []);
  const wsClientRef = useRef<JarvisWsClient | null>(null);
  const ttsReceivedRef = useRef(false);
  const spokenPrefixRef = useRef("");
  const artifactEmittedThisTurnRef = useRef(false);
  const { resolved: resolvedScheme, system: systemScheme } = useColorScheme();

  const wsConnected = useJarvisStore((s) => s.wsConnected);
  const activeArtifact = useJarvisStore((s) => s.activeArtifact);
  const sphereState = useJarvisStore((s) => s.sphereState);
  const activeModel = useJarvisStore((s) => s.activeModel);
  const wakeWordEnabled = useJarvisStore((s) => s.wakeWordEnabled);
  const wakeWordThreshold = useJarvisStore((s) => s.wakeWordThreshold);
  const voiceMode = useJarvisStore((s) => s.voiceMode);
  const theme = useJarvisStore((s) => s.theme);
  const sensors = useJarvisStore((s) => s.sensors);
  const approvals = useJarvisStore((s) => s.approvals);
  const goals = useJarvisStore((s) => s.goals);
  const lastError = useJarvisStore((s) => s.lastError);

  const micSensor = sensors.find((s) => s.sensor === "mic");
  const micActive = Boolean(micSensor && !micSensor.paused);
  const micActiveRef = useRef(micActive);
  micActiveRef.current = micActive;
  const startMicListeningRef = useRef<() => void>(() => undefined);
  const lastRecognizedSpeechAtRef = useRef<number>(Date.now());
  const isTranscribingRef = useRef(false);
  const wasInterruptedRef = useRef(false);

  const queryClient = useQueryClient();
  const setTheme = useJarvisStore((s) => s.setTheme);
  const setWsConnected = useJarvisStore((s) => s.setWsConnected);
  const setSphereState = useJarvisStore((s) => s.setSphereState);
  const setActiveModel = useJarvisStore((s) => s.setActiveModel);
  const setThinkingMode = useJarvisStore((s) => s.setThinkingMode);
  const setMessages = useJarvisStore((s) => s.setMessages);
  const setApprovals = useJarvisStore((s) => s.setApprovals);
  const setAlerts = useJarvisStore((s) => s.setAlerts);
  const setSensors = useJarvisStore((s) => s.setSensors);
  const setError = useJarvisStore((s) => s.setError);
  const setVoiceMode = useJarvisStore((s) => s.setVoiceMode);
  const resolveApprovalLocal = useJarvisStore((s) => s.resolveApprovalLocal);
  const ackAlertLocal = useJarvisStore((s) => s.ackAlertLocal);
  const appendUserMessage = useJarvisStore((s) => s.appendUserMessage);
  const interruptSpeech = useJarvisStore((s) => s.interruptSpeech);
  const handleServerEvent = useJarvisStore((s) => s.handleServerEvent);

  const pendingApprovalsCount = approvals.filter((a) => a.status === "pending").length;
  const activeGoalsCount = Object.values(goals).filter((g) =>
    ["planning", "running", "verifying", "waiting_user", "waiting_approval", "paused_budget"].includes(
      g.status
    )
  ).length;
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem("jarvis_sidebar_collapsed") === "1";
  });

  const toggleSidebar = useCallback((collapsed?: boolean) => {
    setSidebarCollapsed((prev) => {
      const next = typeof collapsed === "boolean" ? collapsed : !prev;
      if (typeof window !== "undefined") {
        window.localStorage.setItem("jarvis_sidebar_collapsed", next ? "1" : "0");
      }
      return next;
    });
  }, []);

  const navigate = useCallback((targetPath: string) => {
    const resolved = resolveRoute(targetPath);
    setRoute(resolved);
    if (typeof window !== "undefined" && window.location.pathname !== targetPath) {
      window.history.pushState(null, "", targetPath);
    }
    window.scrollTo({ top: 0 });
  }, []);

  // Erreurs de chargement isolees par section : une panne reseau sur une section
  // n'efface plus les autres et ne produit plus de liste vide trompeuse.
  const [sectionErrors, setSectionErrors] = useState<SectionErrors>({});
  const [retryingSections, setRetryingSections] = useState<
    Partial<Record<BootstrapSection, boolean>>
  >({});
  const bootstrapActiveRef = useRef(true);

  const loadBootstrapSection = useCallback(
    async (section: BootstrapSection): Promise<void> => {
      setRetryingSections((prev) => ({ ...prev, [section]: true }));
      try {
        switch (section) {
          case "approvals": {
            const list = await apiClient.getApprovals();
            if (!bootstrapActiveRef.current) return;
            if (useJarvisStore.getState().approvals.length === 0) {
              setApprovals(list);
            }
            break;
          }
          case "alerts": {
            const list = await apiClient.getAlerts();
            if (!bootstrapActiveRef.current) return;
            if (useJarvisStore.getState().alerts.length === 0) {
              setAlerts(list);
            }
            break;
          }
          case "sensors": {
            const cfg = await apiClient.getSensorsBlacklist();
            if (!bootstrapActiveRef.current) return;
            if (cfg.sensors.length > 0) {
              setSensors(cfg.sensors);
            }
            break;
          }
          case "models": {
            const roles = await apiClient.getModels();
            if (!bootstrapActiveRef.current) return;
            const agentCfg = roles.find((m) => m.role === "agent") ?? roles[0];
            if (agentCfg?.model) {
              setActiveModel(agentCfg.model);
            }
            if (agentCfg?.thinking_mode) {
              setThinkingMode(agentCfg.thinking_mode);
            }
            break;
          }
          case "goals": {
            const list = await apiClient.getGoals(30);
            if (!bootstrapActiveRef.current) return;
            useJarvisStore.getState().setGoals(list);
            break;
          }
        }
        if (!bootstrapActiveRef.current) return;
        setSectionErrors((prev) => {
          if (!(section in prev)) return prev;
          const next = { ...prev };
          delete next[section];
          return next;
        });
      } catch (err) {
        if (!bootstrapActiveRef.current) return;
        setSectionErrors((prev) => ({
          ...prev,
          [section]: describeApiError(err, SECTION_ERROR_FALLBACKS[section]),
        }));
      } finally {
        if (bootstrapActiveRef.current) {
          setRetryingSections((prev) => ({ ...prev, [section]: false }));
        }
      }
    },
    [setActiveModel, setAlerts, setApprovals, setSensors, setThinkingMode]
  );

  const retrySection = useCallback(
    (section: BootstrapSection) => {
      void loadBootstrapSection(section);
    },
    [loadBootstrapSection]
  );

  /** Sections en erreur qui concernent reellement la page affichee. */
  const visibleSectionErrors = useMemo(
    () =>
      BOOTSTRAP_SECTIONS.filter(
        (section) => Boolean(sectionErrors[section]) && SECTION_ROUTES[section].includes(route)
      ).map((section) => ({ section, message: sectionErrors[section] as string })),
    [route, sectionErrors]
  );

  useEffect(() => {
    const onPopState = () => {
      setRoute(resolveRoute(window.location.pathname));
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    const meta = getRouteMeta(route);
    document.title = pendingApprovalsCount > 0 ? `(${pendingApprovalsCount}) ${meta.title}` : meta.title;
    let tag = document.querySelector('meta[name="description"]');
    if (!tag) {
      tag = document.createElement("meta");
      tag.setAttribute("name", "description");
      document.head.appendChild(tag);
    }
    tag.setAttribute("content", meta.description);
  }, [route, pendingApprovalsCount]);

  useEffect(() => {
    let active = true;
    bootstrapActiveRef.current = true;
    const token = getAuthToken() || undefined;
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const envWs = import.meta.env.VITE_WS_URL as string | undefined;
    const wsUrl = envWs && envWs.length > 0 ? envWs : `${protocol}://${window.location.host}/ws`;

    const client = new JarvisWsClient({
      url: wsUrl,
      token,
      onEvent: (ev) => {
        if (!active) return;
        if (ev.type === "state" && ev.payload.value === "thinking") {
          ttsReceivedRef.current = false;
          spokenPrefixRef.current = "";
          artifactEmittedThisTurnRef.current = false;
        }
        if (ev.type === "visual.artifact") {
          artifactEmittedThisTurnRef.current = true;
        }
        handleServerEvent(ev);
        if (ev.type === "tool.call" && !ttsReceivedRef.current && !spokenPrefixRef.current) {
          const streamed = useJarvisStore.getState().streamingText.trim();
          if (streamed.length >= 12) {
            spokenPrefixRef.current = streamed;
            void audioEngine.speakText(streamed, apiClient.speakVoice);
          }
        }
        if (ev.type === "alert" && ev.payload.message && ev.payload.urgency >= 2) {
          void audioEngine.speakText(ev.payload.message, apiClient.speakVoice);
        }
        if (ev.type === "assistant.done") {
          const artifactNarrating =
            artifactEmittedThisTurnRef.current && useJarvisStore.getState().artifactPlaying;
          if (!ttsReceivedRef.current && !artifactNarrating) {
            const msgs = useJarvisStore.getState().messages;
            const lastAssistant = [...msgs].reverse().find((m) => m.role === "assistant");
            const fullText = lastAssistant?.content?.trim() ?? "";
            if (fullText && fullText !== spokenPrefixRef.current) {
              const remainder =
                spokenPrefixRef.current && fullText.startsWith(spokenPrefixRef.current)
                  ? fullText.slice(spokenPrefixRef.current.length).trim()
                  : fullText;
              if (remainder) {
                audioEngine.speakText(remainder, apiClient.speakVoice);
              }
            } else if (!fullText && micActiveRef.current) {
              setTimeout(() => {
                if (micActiveRef.current) {
                  startMicListeningRef.current();
                }
              }, 500);
            }
          }
          ttsReceivedRef.current = false;
        }
      },
      onConnectionChange: (connected) => {
        if (active) setWsConnected(connected);
      },
      onTtsChunk: (audioBase64) => {
        ttsReceivedRef.current = true;
        // Texte en cours de lecture : permet au moteur audio de reconnaitre son propre echo.
        const streamed = useJarvisStore.getState().streamingText;
        if (streamed) {
          audioEngine.setSpokenText(streamed);
        }
        audioEngine.playTtsChunk(audioBase64);
      },
    });
    wsClientRef.current = client;
    client.connect();

    // Chargement initial : chaque section gere son propre echec. allSettled reste
    // la ceinture de securite si un loader laissait echapper une exception.
    void Promise.allSettled(
      BOOTSTRAP_SECTIONS.map((section) => loadBootstrapSection(section))
    );

    return () => {
      active = false;
      bootstrapActiveRef.current = false;
      client.disconnect();
      wsClientRef.current = null;
      audioEngine.dispose();
    };
  }, [
    audioEngine,
    handleServerEvent,
    loadBootstrapSection,
    setActiveModel,
    setAlerts,
    setApprovals,
    setMessages,
    setSensors,
    setThinkingMode,
    setWsConnected,
  ]);

  const handleSelectModel = useCallback(
    (model: string) => {
      setActiveModel(model);
      wsClientRef.current?.send({
        type: "settings.model",
        payload: { role: "agent", model, thinking_mode: null },
      });
      apiClient
        .updateRoleModel("agent", model)
        .then(() => queryClient.invalidateQueries({ queryKey: ["models"] }))
        .catch(() => undefined);
    },
    [queryClient, setActiveModel]
  );

  const handleSelectThinking = useCallback(
    (mode: ThinkingMode) => {
      setThinkingMode(mode);
      apiClient
        .updateThinkingMode(mode)
        .then(() => queryClient.invalidateQueries({ queryKey: ["models"] }))
        .catch(() => undefined);
    },
    [queryClient, setThinkingMode]
  );

  const handleSendText = useCallback(
    async (text: string) => {
      audioEngine.unlock();
      ttsReceivedRef.current = false;
      setError(null);
      const overwrite = wasInterruptedRef.current;
      wasInterruptedRef.current = false;
      useJarvisStore.getState().clearTranscripts();

      let outgoingText = text.trim();
      const store = useJarvisStore.getState();

      // Interception des commandes slash (compatibles MoA + /goal Jarvis)
      if (outgoingText.startsWith("/")) {
        const spaceIdx = outgoingText.indexOf(" ");
        const cleanCmd = (spaceIdx === -1 ? outgoingText : outgoingText.slice(0, spaceIdx)).toLowerCase();
        const cleanArg = spaceIdx === -1 ? "" : outgoingText.slice(spaceIdx + 1).trim();

        if (cleanCmd === "/clear" || cleanCmd === "/new") {
          store.clearMessagesLocal();
          apiClient.clearMessages().catch(() => undefined);
          toast.ok("Nouvelle conversation démarrée.");
          return;
        }

        if (cleanCmd === "/skills" || cleanCmd === "/skill" || cleanCmd === "/settings" || cleanCmd === "/config") {
          navigate("/config");
          return;
        }

        if (cleanCmd === "/tint" || cleanCmd === "/theme") {
          const nextTheme = store.theme === "amber" ? "jarvis-blue" : "amber";
          setTheme(nextTheme);
          toast.ok(nextTheme === "amber" ? "Accent laiton activé." : "Accent bleu Jarvis activé.");
          return;
        }

        if (cleanCmd === "/workspace") {
          if (!cleanArg) {
            store.appendSystemMessage(
              store.workspace
                ? `Workspace actif : ${store.workspace}\nUtilisez /workspace <dossier> pour changer ou /workspace reset pour désactiver.`
                : "Aucun workspace actif. Tapez /workspace <dossier> pour définir le dossier de travail."
            );
            return;
          }
          if (["reset", "clear", "off"].includes(cleanArg.toLowerCase())) {
            store.setWorkspace(null);
            store.appendSystemMessage("Workspace réinitialisé : mode standard actif.");
            return;
          }
          const argSpace = cleanArg.indexOf(" ");
          const targetWs = argSpace === -1 ? cleanArg : cleanArg.slice(0, argSpace).trim();
          const promptAfter = argSpace === -1 ? "" : cleanArg.slice(argSpace + 1).trim();
          let resolvedWs = targetWs;
          try {
            const resolved = await apiClient.resolveWorkspace(targetWs);
            if (!resolved.found || !resolved.display) {
              store.appendSystemMessage(
                `Dossier introuvable : \`${targetWs}\`. Indiquez un chemin existant (ex: \`~/Documents/marges\`).`
              );
              return;
            }
            resolvedWs = resolved.display;
          } catch {
            // Passerelle hors ligne : conserver le chemin saisi tel quel
          }
          store.setWorkspace(resolvedWs);
          if (!promptAfter) {
            store.appendSystemMessage(`Workspace actif défini sur : ${resolvedWs}`);
            return;
          }
          outgoingText = promptAfter;
        } else if (cleanCmd === "/local" || cleanCmd === "/council" || cleanCmd === "/moa") {
          const targetModel = cleanCmd === "/local" ? "qwen3.5:2b" : "gemini-3.1-pro-preview";
          try {
            if (store.activeModel !== "qwen3.5:2b" && store.activeModel !== "gemini-3.1-pro-preview") {
              window.localStorage.setItem("jarvis_auto_model", store.activeModel);
            }
          } catch {
            // stockage indisponible
          }
          handleSelectModel(targetModel);
          if (!cleanArg) {
            store.appendSystemMessage(
              cleanCmd === "/local"
                ? `Modèle local activé (${targetModel}).`
                : `Mode conseil avancé activé (${targetModel}).`
            );
            return;
          }
          outgoingText = cleanArg;
        } else if (cleanCmd === "/ask") {
          if (!cleanArg) {
            store.appendSystemMessage(
              "Usage : `/ask <demande>` — active les notifications d'approbation pour les commandes terminal à partir du niveau 3."
            );
            return;
          }
          if (/^\/goal(?:\s+|$)/i.test(cleanArg)) {
            const goalRest = cleanArg.replace(/^\/goal\s*/i, "").trim();
            outgoingText = goalRest ? `/goal /ask ${goalRest}` : "/goal";
          }
        } else if (cleanCmd === "/auto") {
          let defaultModel = "deepseek/deepseek-v4.1-flash-1017";
          try {
            defaultModel = window.localStorage.getItem("jarvis_auto_model") || defaultModel;
          } catch {
            // stockage indisponible
          }
          handleSelectModel(defaultModel);
          store.appendSystemMessage(`Routage automatique rétabli (modèle : ${defaultModel}).`);
          return;
        } else if (cleanCmd === "/status") {
          const activeSensors = store.sensors
            .map((s) => `${s.sensor} (${s.paused ? "pause" : s.active ? "actif" : "inactif"})`)
            .join(", ");
          try {
            const [health, mcps] = await Promise.all([
              apiClient.getHealth().catch(() => null),
              apiClient.getMcpServers().catch(() => []),
            ]);
            const mcpSummary =
              mcps.length > 0
                ? mcps.map((m) => `${m.name} (${m.connected ? "connecté" : "hors ligne"})`).join(", ")
                : "aucun";
            store.appendSystemMessage(
              `### État des services\n\n` +
                `| Service | État |\n` +
                `| --- | --- |\n` +
                `| Passerelle | ${health?.status === "ok" ? "En ligne" : "Hors ligne"} |\n` +
                `| WebSocket | ${store.wsConnected ? "Connecté" : "Déconnecté"} |\n` +
                `| Modèle actif | \`${store.activeModel}\` |\n` +
                `| Raisonnement | \`${store.thinkingMode}\` |\n` +
                `| Workspace | ${store.workspace ? `\`${store.workspace}\`` : "aucun"} |\n` +
                `| Capteurs | ${activeSensors} |\n` +
                `| Serveurs MCP | ${mcpSummary} |`
            );
          } catch {
            store.appendSystemMessage("Impossible de récupérer l'état complet des services.");
          }
          return;
        } else if (cleanCmd === "/model" || cleanCmd === "/models") {
          try {
            const roles = await apiClient.getModels();
            const rows = roles.map((r) => `| ${r.role} | \`${r.model}\` |`).join("\n");
            store.appendSystemMessage(
              `### Configuration des modèles\n\n` +
                `Modèle actif : \`${store.activeModel}\` · Raisonnement : \`${store.thinkingMode}\`\n\n` +
                `| Rôle | Modèle |\n` +
                `| --- | --- |\n` +
                rows
            );
          } catch {
            store.appendSystemMessage(`Modèle actif : \`${store.activeModel}\``);
          }
          return;
        } else if (cleanCmd === "/usage" || cleanCmd === "/tokens") {
          const fmtTokens = (n: number): string => {
            if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
            if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
            return String(n);
          };
          try {
            const [daily, budget] = await Promise.all([
              apiClient.getDailyUsage(),
              apiClient.getDailyBudget().catch(() => ({ daily_budget_usd: 0 })),
            ]);
            const byModelMap = new Map<
              string,
              { model: string; tokens_in: number; tokens_out: number; cost: number }
            >();
            for (const d of daily) {
              const prev = byModelMap.get(d.model) ?? {
                model: d.model,
                tokens_in: 0,
                tokens_out: 0,
                cost: 0,
              };
              prev.tokens_in += d.tokens_in || 0;
              prev.tokens_out += d.tokens_out || 0;
              prev.cost += d.cost || 0;
              byModelMap.set(d.model, prev);
            }
            const aggregated = Array.from(byModelMap.values()).sort(
              (a, b) => b.cost - a.cost || b.tokens_in + b.tokens_out - (a.tokens_in + a.tokens_out)
            );
            const totalIn = aggregated.reduce((acc, item) => acc + item.tokens_in, 0);
            const totalOut = aggregated.reduce((acc, item) => acc + item.tokens_out, 0);
            const totalCost = aggregated.reduce((acc, item) => acc + item.cost, 0);

            const header =
              `### Usage IA du jour\n\n` +
              `Total : **${fmtTokens(totalIn)}** → **${fmtTokens(totalOut)}** jetons · ` +
              `**$${totalCost.toFixed(2)}** / $${budget.daily_budget_usd.toFixed(2)}`;

            if (aggregated.length === 0) {
              store.appendSystemMessage(`${header}\n\nAucune consommation enregistrée aujourd'hui.`);
            } else {
              const rows = aggregated
                .map(
                  (d) =>
                    `| \`${d.model}\` | ${fmtTokens(d.tokens_in)} | ${fmtTokens(d.tokens_out)} | $${d.cost.toFixed(4)} |`
                )
                .join("\n");
              store.appendSystemMessage(
                `${header}\n\n| Modèle | Entrée | Sortie | Coût |\n| --- | ---: | ---: | ---: |\n${rows}`
              );
            }
          } catch {
            store.appendSystemMessage("Impossible de récupérer les statistiques d'usage.");
          }
          return;
        } else if (cleanCmd === "/help" || cleanCmd === "/aide") {
          const rows = SLASH_COMMANDS.map((c) => {
            const signature = c.arg ? `\`${c.cmd} ${c.arg}\`` : `\`${c.cmd}\``;
            const aliases = c.aliases?.length ? ` (${c.aliases.join(", ")})` : "";
            return `| ${signature}${aliases} | ${c.desc} |`;
          }).join("\n");
          store.appendSystemMessage(
            `### Commandes disponibles\n\n| Commande | Description |\n| --- | --- |\n${rows}`
          );
          return;
        }
      }

      appendUserMessage(outgoingText, overwrite);

      const isAskMode = useJarvisStore.getState().terminalMode === "ask";
      const isSlashGoal = /^\/goal(?:\s+|$)/i.test(outgoingText);
      if (isSlashGoal || useJarvisStore.getState().goalMode) {
        let goalPrompt = isSlashGoal ? outgoingText.replace(/^\/goal\s*/i, "").trim() : outgoingText;
        if (!goalPrompt) {
          setError("Précise l'objectif à lancer après /goal.");
          return;
        }
        if (isAskMode && !/^\/ask(?:\s|$)/i.test(goalPrompt)) {
          goalPrompt = `/ask ${goalPrompt}`;
        }
        try {
          const created = await apiClient.createGoal({
            goal: goalPrompt,
            notify_on_finish: true,
          });
          useJarvisStore.getState().upsertGoal(created);
          return;
        } catch (err) {
          setError(err instanceof Error ? err.message : "Impossible de lancer l'objectif.");
          return;
        }
      }

      const activeWorkspace = useJarvisStore.getState().workspace;
      const effectiveOutgoing =
        isAskMode && !/^\/ask(?:\s|$)/i.test(outgoingText) ? `/ask ${outgoingText}` : outgoingText;
      const payloadText = activeWorkspace
        ? `[Workspace actif : ${activeWorkspace}]\n${effectiveOutgoing}`
        : effectiveOutgoing;

      if (wsClientRef.current && useJarvisStore.getState().wsConnected) {
        wsClientRef.current.send({ type: "user.text", payload: { text: payloadText } });
        return;
      }
      setSphereState("thinking");
      try {
        const saved = await apiClient.sendMessage(payloadText);
        const latest = await apiClient.getMessages();
        setMessages(latest);
        if (saved?.content) {
          audioEngine.speakText(saved.content, apiClient.speakVoice);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Échec de l'envoi du message.");
      } finally {
        setSphereState("idle");
      }
    },
    [appendUserMessage, audioEngine, handleSelectModel, navigate, setError, setMessages, setSphereState, setTheme]
  );

  const handleToggleSensor = useCallback(
    async (sensor: SensorName, paused: boolean) => {
      if (sensor === "mic" && !paused) {
        audioEngine.unlock();
      }
      setError(null);
      try {
        const cfg = await apiClient.toggleSensor(sensor, paused);
        setSensors(cfg.sensors);
      } catch (err) {
        setError(err instanceof Error ? err.message : `Impossible de modifier le capteur ${sensor}.`);
        apiClient
          .getSensorsBlacklist()
          .then((cfg) => setSensors(cfg.sensors))
          .catch(() => undefined);
      }
    },
    [setError, setSensors]
  );

  const stopActiveVoiceRecord = useCallback(
    (reasonMessage: string) => {
      const st = useJarvisStore.getState();
      audioEngine.playChime("sleep");
      useJarvisStore.getState().clearTranscripts();
      if (st.wakeWordEnabled) {
        audioEngine.setStandbyMode(true);
        setVoiceMode("standby");
        setSphereState(st.wsConnected ? "idle" : "offline");
      } else {
        void handleToggleSensor("mic", true);
      }
      toast.info(reasonMessage);
    },
    [audioEngine, handleToggleSensor, setSphereState, setVoiceMode]
  );

  const startMicListening = useCallback(() => {
    if (!micActiveRef.current) return;
    audioEngine.stopPlayback();
    const useStandby = useJarvisStore.getState().wakeWordEnabled;
    audioEngine.setWakeWordThreshold(useJarvisStore.getState().wakeWordThreshold);
    audioEngine.setStandbyMode(useStandby);
    setVoiceMode(useStandby ? "standby" : "active");
    if (useStandby) {
      const st = useJarvisStore.getState();
      setSphereState(st.wsConnected ? "idle" : "offline");
    } else {
      lastRecognizedSpeechAtRef.current = Date.now();
      setSphereState("listening");
    }

    const restoreMicSphereState = () => {
      const st = useJarvisStore.getState();
      if (!audioEngine.isPlaying()) {
        const activeListening = micActiveRef.current && st.voiceMode === "active";
        setSphereState(activeListening ? "listening" : st.wsConnected ? "idle" : "offline");
      }
    };

    audioEngine
      .startMicrophone({
        onBargeIn: () => {
          wasInterruptedRef.current = true;
          lastRecognizedSpeechAtRef.current = Date.now();
          audioEngine.stopPlayback();
          interruptSpeech();
          const st = useJarvisStore.getState();
          setSphereState(st.voiceMode === "active" ? "listening" : st.wsConnected ? "idle" : "offline");
        },
        onLiveTranscript: (text) => {
          if (useJarvisStore.getState().voiceMode !== "active") return;
          useJarvisStore.getState().handleServerEvent({
            type: "transcript.partial",
            payload: { text, speaker: "user" },
          });
        },
        onUtterance: (wavBase64, mimeType) => {
          if (isTranscribingRef.current) return;
          const storeNow = useJarvisStore.getState();
          const inStandby = storeNow.voiceMode === "standby" && storeNow.wakeWordEnabled;
          isTranscribingRef.current = true;

          if (inStandby) {
            apiClient
              .wakeCheckVoice(wavBase64, mimeType, storeNow.wakeWordThreshold)
              .catch(() => apiClient.transcribeVoice(wavBase64, mimeType))
              .then(({ text }) => {
                isTranscribingRef.current = false;
                const { triggered, query } = extractWakeWord(text);
                if (!triggered) return;
                lastRecognizedSpeechAtRef.current = Date.now();
                audioEngine.playChime("wake");
                audioEngine.setStandbyMode(false);
                setVoiceMode("active");
                if (query) {
                  useJarvisStore.getState().handleServerEvent({
                    type: "transcript.final",
                    payload: { text: query, speaker: "user" },
                  });
                  void handleSendText(query);
                } else {
                  setSphereState("listening");
                  toast.info("À l'écoute — dites « coupe le record » pour mettre en veille.");
                }
              })
              .catch(() => {
                isTranscribingRef.current = false;
              });
            return;
          }

          setSphereState("thinking");
          apiClient
            .transcribeVoice(wavBase64, mimeType)
            .then(({ text }) => {
              isTranscribingRef.current = false;
              const cleaned = text.trim();
              if (!cleaned) {
                restoreMicSphereState();
                return;
              }
              lastRecognizedSpeechAtRef.current = Date.now();
              if (useJarvisStore.getState().wakeWordEnabled && isStopRecordCommand(cleaned)) {
                stopActiveVoiceRecord("Mise en veille vocale — dites « Jarvis » pour reprendre.");
                return;
              }
              useJarvisStore.getState().handleServerEvent({
                type: "transcript.final",
                payload: { text: cleaned, speaker: "user" },
              });
              void handleSendText(cleaned);
            })
            .catch((err) => {
              isTranscribingRef.current = false;
              setError(err instanceof Error ? err.message : "Échec de la transcription vocale.");
              restoreMicSphereState();
            });
        },
      })
      .then((ok) => {
        if (!ok) {
          setVoiceMode("off");
          const st = useJarvisStore.getState();
          setSphereState(st.wsConnected ? "idle" : "offline");
        }
      })
      .catch(() => {
        setVoiceMode("off");
        const st = useJarvisStore.getState();
        setSphereState(st.wsConnected ? "idle" : "offline");
      });
  }, [
    audioEngine,
    handleSendText,
    interruptSpeech,
    setError,
    setSphereState,
    setVoiceMode,
    stopActiveVoiceRecord,
  ]);
  startMicListeningRef.current = startMicListening;

  useEffect(() => {
    audioEngine.setWakeWordThreshold(wakeWordThreshold);
  }, [audioEngine, wakeWordThreshold]);

  useEffect(() => {
    let keepaliveTimer: number | null = null;
    const syncDucking = (playing: boolean) => {
      if (keepaliveTimer !== null) {
        window.clearInterval(keepaliveTimer);
        keepaliveTimer = null;
      }
      if (playing) {
        if (!useJarvisStore.getState().audioDuckingEnabled) return;
        void apiClient.setAudioDucking(true).catch(() => undefined);
        keepaliveTimer = window.setInterval(() => {
          if (audioEngine.isPlaying() && useJarvisStore.getState().audioDuckingEnabled) {
            void apiClient.setAudioDucking(true).catch(() => undefined);
          }
        }, 12_000);
      } else {
        void apiClient.setAudioDucking(false).catch(() => undefined);
      }
    };

    audioEngine.onPlaybackStateChange = syncDucking;
    audioEngine.onPlaybackEnded = () => {
      lastRecognizedSpeechAtRef.current = Date.now();
      const st = useJarvisStore.getState();
      if (st.sphereState === "speaking") {
        const activeListening = micActiveRef.current && st.voiceMode === "active";
        setSphereState(activeListening ? "listening" : st.wsConnected ? "idle" : "offline");
      }
    };
    const restoreOnUnload = () => {
      if (audioEngine.isPlaying()) {
        void apiClient.setAudioDucking(false, 0.22, { keepalive: true }).catch(() => undefined);
      }
    };
    window.addEventListener("beforeunload", restoreOnUnload);
    return () => {
      window.removeEventListener("beforeunload", restoreOnUnload);
      if (keepaliveTimer !== null) window.clearInterval(keepaliveTimer);
      audioEngine.onPlaybackStateChange = undefined;
      audioEngine.onPlaybackEnded = undefined;
      void apiClient.setAudioDucking(false).catch(() => undefined);
    };
  }, [audioEngine, setSphereState]);

  useEffect(() => {
    if (voiceMode !== "active") return;
    lastRecognizedSpeechAtRef.current = Date.now();
    const timer = window.setInterval(() => {
      const st = useJarvisStore.getState();
      const playing = audioEngine.isPlaying();
      if (!isTranscribingRef.current && (playing || st.sphereState === "speaking" || st.sphereState === "thinking")) {
        lastRecognizedSpeechAtRef.current = Date.now();
        return;
      }
      if (
        shouldAutoStopVoiceRecord({
          voiceMode: st.voiceMode,
          sphereState: st.sphereState,
          isTranscribing: isTranscribingRef.current,
          isPlayingAudio: playing,
          lastRecognizedAtMs: lastRecognizedSpeechAtRef.current,
          nowMs: Date.now(),
        })
      ) {
        stopActiveVoiceRecord(
          st.wakeWordEnabled
            ? "Aucune voix reconnue depuis 10 s — mise en veille vocale."
            : "Aucune voix reconnue depuis 10 s — micro coupé."
        );
      }
    }, 500);
    return () => window.clearInterval(timer);
  }, [voiceMode, audioEngine, stopActiveVoiceRecord]);

  useEffect(() => {
    if (micActive) {
      startMicListening();
    } else {
      audioEngine.stopMicrophone();
      audioEngine.setStandbyMode(false);
      setVoiceMode("off");
      const st = useJarvisStore.getState();
      if (st.sphereState === "listening") {
        setSphereState(st.wsConnected ? "idle" : "offline");
      }
    }
  }, [micActive, wakeWordEnabled, startMicListening, audioEngine, setSphereState, setVoiceMode]);

  const handleInterrupt = useCallback(() => {
    wasInterruptedRef.current = true;
    audioEngine.stopPlayback();
    interruptSpeech();
    if (wsClientRef.current && useJarvisStore.getState().wsConnected) {
      wsClientRef.current.send({ type: "user.interrupt", payload: { reason: "user_cancel" } });
    }
    const st = useJarvisStore.getState();
    const activeListening = micActiveRef.current && st.voiceMode === "active";
    setSphereState(activeListening ? "listening" : st.wsConnected ? "idle" : "offline");
  }, [audioEngine, interruptSpeech, setSphereState]);

  const handleApproveAction = useCallback(
    async (id: string, approved: boolean) => {
      resolveApprovalLocal(id, approved);
      wsClientRef.current?.send({ type: "action.approve", payload: { id, approved } });
      await apiClient.decideApproval(id, approved).catch(() => undefined);
    },
    [resolveApprovalLocal]
  );

  const handleAckAlert = useCallback(
    async (id: string, tooNoisy = false) => {
      ackAlertLocal(id, tooNoisy);
      if (tooNoisy) {
        await apiClient.markAlertTooNoisy(id).catch(() => undefined);
      } else {
        wsClientRef.current?.send({ type: "alert.ack", payload: { id } });
        await apiClient.ackAlert(id).catch(() => undefined);
      }
    },
    [ackAlertLocal]
  );

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.shiftKey && e.key === "Tab") {
        e.preventDefault();
        const nextMode = useJarvisStore.getState().toggleTerminalMode();
        toast.ok(
          nextMode === "ask"
            ? "Mode Ask activé (approbation terminal niveau 3+)."
            : "Mode Auto activé (commandes terminal sans approbation)."
        );
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((prev) => !prev);
        return;
      }
      if (e.key === "Escape") {
        if (paletteOpen) {
          setPaletteOpen(false);
        } else {
          handleInterrupt();
        }
        return;
      }
      if (e.code === "Space" && !e.repeat && !paletteOpen && !isEditableTarget(e.target)) {
        e.preventDefault();
        const mic = sensors.find((s) => s.sensor === "mic");
        if (mic) {
          void handleToggleSensor("mic", !mic.paused);
        }
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [handleInterrupt, handleToggleSensor, paletteOpen, sensors]);

  const commands = useMemo<CommandItem[]>(() => {
    const navCommands: CommandItem[] = ROUTES.map((r) => ({
      id: `nav_${r.path}`,
      label: `Aller à ${r.label}`,
      hint: "Navigation",
      icon: r.icon,
      action: () => navigate(r.path),
    }));
    const sensorCommands: CommandItem[] = sensors.map((s) => ({
      id: `sensor_${s.sensor}`,
      label: `${s.paused ? "Activer" : "Mettre en pause"} le capteur ${SENSOR_LABELS[s.sensor] ?? s.sensor}`,
      hint: "Capteur",
      icon: s.sensor === "mic" ? "mic" : s.sensor === "camera" ? "camera" : "monitor",
      action: () => handleToggleSensor(s.sensor, !s.paused),
    }));
    const nextScheme = resolvedScheme === "dark" ? "light" : "dark";
    return [
      ...navCommands,
      {
        id: "toggle_scheme",
        label: nextScheme === "dark" ? "Passer en mode sombre" : "Passer en mode clair",
        hint: "Apparence",
        icon: nextScheme === "dark" ? "moon" : "sun",
        // Revenir au choix du systeme plutot que de figer une valeur identique.
        action: () => setSchemePreference(nextScheme === systemScheme ? "system" : nextScheme),
      },
      {
        id: "toggle_accent",
        label: theme === "amber" ? "Accent bleu" : "Accent laiton",
        hint: "Apparence",
        icon: "sliders",
        action: () => setTheme(theme === "amber" ? "jarvis-blue" : "amber"),
      },
      {
        id: "interrupt_voice",
        label: "Couper la parole de Jarvis",
        hint: "Échap",
        icon: "stop",
        action: handleInterrupt,
      },
      ...sensorCommands,
    ];
  }, [handleInterrupt, handleToggleSensor, navigate, resolvedScheme, sensors, setTheme, systemScheme, theme]);

  return (
    <MotionConfig reducedMotion="user">
      <div
        className="app"
        data-sidebar-collapsed={sidebarCollapsed ? "true" : "false"}
        data-sphere-state={sphereState}
      >
        <a className="skip-link" href="#main-content">
          Aller au contenu principal
        </a>
        <aside className="sidebar">
          <div className="sidebar__header">
            <div className="sidebar__top">
              <div className="brand">
                <JarvisLogo size={28} />
                <span>JARVIS</span>
                <span className="brand__ver">2.0</span>
              </div>
              <button
                type="button"
                className="icon-btn icon-btn--sm sidebar__toggle"
                aria-label="Fermer le panneau de gauche"
                title="Fermer le panneau de gauche"
                onClick={() => toggleSidebar(true)}
              >
                <Icon name="panelLeft" size={16} />
              </button>
            </div>
            <span
              className="status-pill"
              data-online={wsConnected}
              data-testid="ws-status-badge"
              title={wsConnected ? "Connecté au service jarvis-core" : "Service jarvis-core injoignable"}
            >
              <span className="dot" aria-hidden="true" />
              {wsConnected ? "Connecté" : "Hors ligne"}
            </span>
          </div>

          <button type="button" className="sidebar__search" onClick={() => setPaletteOpen(true)}>
            <Icon name="search" size={16} />
            <span>Rechercher, aller à…</span>
            <span className="kbd">Ctrl K</span>
          </button>

          <nav className="nav" aria-label="Navigation principale">
            {ROUTES.map((item) => {
              const isCurrent = route === item.path;
              return (
                <button
                  key={item.path}
                  type="button"
                  className="nav__item"
                  aria-current={isCurrent ? "page" : undefined}
                  onClick={() => navigate(item.path)}
                >
                  <Icon name={item.icon} size={20} />
                  <span className="nav__label">{item.label}</span>
                  {item.path === "/goals" && activeGoalsCount > 0 && (
                    <span
                      className="nav__badge nav__badge--accent"
                      data-testid="nav-active-goals-count"
                      aria-label={`${activeGoalsCount} objectif(s) actif(s)`}
                    >
                      {activeGoalsCount}
                    </span>
                  )}
                  {item.path === "/alerts" && pendingApprovalsCount > 0 && (
                    <span
                      className="nav__badge"
                      data-testid="nav-pending-count"
                      aria-label={`${pendingApprovalsCount} en attente`}
                    >
                      {pendingApprovalsCount}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>

          <div className="sidebar__footer">
            <div className="sidebar__model" title={activeModel}>
              <span className="sidebar__model-label">Modèle actif</span>
              <span className="sidebar__model-value">{formatModelLabel(activeModel)}</span>
            </div>
            <p className="sidebar__privacy">
              Données locales dans ~/jarvis-data. Aucune clé API exposée.
            </p>
          </div>
        </aside>

        <main className="main" id="main-content" tabIndex={-1}>
          {sidebarCollapsed && (
            <button
              type="button"
              className="icon-btn sidebar__reopen"
              aria-label="Ouvrir le panneau de gauche"
              title="Ouvrir le panneau de gauche"
              onClick={() => toggleSidebar(false)}
            >
              <Icon name="panelLeft" size={18} />
            </button>
          )}
          {(lastError || visibleSectionErrors.length > 0) && (
            <div className="app__banners" style={{ maxWidth: 1080, margin: "0 auto 16px" }}>
              {lastError && (
                <ErrorBanner
                  message={lastError}
                  onDismiss={() => setError(null)}
                  testId="global-error-banner"
                />
              )}
              {visibleSectionErrors.map(({ section, message }) => (
                <ErrorBanner
                  key={section}
                  message={message}
                  onRetry={() => retrySection(section)}
                  retrying={Boolean(retryingSections[section])}
                  testId={`section-error-${section}`}
                />
              ))}
            </div>
          )}

          <AnimatePresence mode="wait">
            <motion.div
              key={route}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.14, ease: [0.22, 1, 0.36, 1] }}
            >
              <Suspense
                fallback={
                  <section className="page">
                    <div className="card">
                      <Skeleton lines={5} />
                    </div>
                  </section>
                }
              >
                {route === "/" && (
                  <HomePage
                    audioEngine={audioEngine}
                    onSendText={handleSendText}
                    onToggleSensor={handleToggleSensor}
                    onSelectModel={handleSelectModel}
                    onSelectThinking={handleSelectThinking}
                    onInterrupt={handleInterrupt}
                    onOpenGoals={() => navigate("/goals")}
                  />
                )}
                {route === "/conversation" && (
                  <ConversationPage
                    onSendText={handleSendText}
                    onOpenGoals={() => navigate("/goals")}
                  />
                )}
                {route === "/goals" && <GoalsPage />}
                {route === "/alerts" && (
                  <AlertsApprovalsPage
                    onApproveAction={handleApproveAction}
                    onAckAlert={handleAckAlert}
                  />
                )}
                {route === "/memory" && <MemoryOnboardingPage />}
                {route === "/config" && <ConfigPage />}
                {route === "/not-found" && <NotFoundPage onGoHome={() => navigate("/")} />}
              </Suspense>
            </motion.div>
          </AnimatePresence>
        </main>

        {activeArtifact && (
          <Suspense fallback={null}>
            <VisualArtifactWindow audioEngine={audioEngine} />
          </Suspense>
        )}
        <CommandPalette
          open={paletteOpen}
          onClose={() => setPaletteOpen(false)}
          commands={commands}
        />
        <Toaster />
      </div>
    </MotionConfig>
  );
}

export default App;
