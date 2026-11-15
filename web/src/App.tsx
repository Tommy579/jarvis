import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { apiClient } from "./api/client";
import { CommandPalette, type CommandItem } from "./components/CommandPalette";
import { Icon, type IconName } from "./components/Icon";
import { Toaster } from "./components/ui";
import { setSchemePreference, useColorScheme } from "./lib/colorScheme";
import { AlertsApprovalsPage } from "./pages/AlertsApprovalsPage";
import { ConfigPage } from "./pages/ConfigPage";
import { ConversationPage } from "./pages/ConversationPage";
import { HomePage } from "./pages/HomePage";
import { MemoryOnboardingPage } from "./pages/MemoryOnboardingPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { AudioEngine } from "./sphere/AudioEngine";
import { useOmiStore } from "./store/useOmiStore";
import type { SensorName } from "./types/generated";
import { OmiWsClient } from "./ws/client";

type AppRoute = "/" | "/conversation" | "/alerts" | "/memory" | "/config" | "/not-found";

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
    title: "OMI 2.0 | Accueil vocal",
    description: "Sphère vocale réactive, transcription en direct et capteurs d'OMI 2.0.",
  },
  {
    path: "/conversation",
    label: "Conversation",
    icon: "chat",
    title: "OMI 2.0 | Conversation",
    description: "Historique complet des échanges, outils appelés et coût par modèle.",
  },
  {
    path: "/alerts",
    label: "Alertes",
    icon: "bell",
    title: "OMI 2.0 | Alertes et approbations",
    description: "Validation des actions à risque et historique des alertes envoyées.",
  },
  {
    path: "/memory",
    label: "Memoire",
    icon: "memory",
    title: "OMI 2.0 | Mémoire",
    description: "Faits, wiki personnel, proches et questionnaire de profil.",
  },
  {
    path: "/config",
    label: "Configuration",
    icon: "sliders",
    title: "OMI 2.0 | Configuration",
    description: "Apparence, modèles, coûts, intégrations, règles d'attention et capteurs.",
  },
];

const SENSOR_LABELS: Record<string, string> = { screen: "écran", mic: "micro", camera: "caméra" };

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
    title: "OMI 2.0 | Page introuvable",
    description: "L'écran demandé n'existe pas dans l'interface OMI 2.0.",
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
  const wsClientRef = useRef<OmiWsClient | null>(null);
  const ttsReceivedRef = useRef(false);
  const { resolved: resolvedScheme, system: systemScheme } = useColorScheme();

  const wsConnected = useOmiStore((s) => s.wsConnected);
  const theme = useOmiStore((s) => s.theme);
  const sensors = useOmiStore((s) => s.sensors);
  const approvals = useOmiStore((s) => s.approvals);
  const lastError = useOmiStore((s) => s.lastError);

  const micSensor = sensors.find((s) => s.sensor === "mic");
  const micActive = Boolean(micSensor && !micSensor.paused && micSensor.active);
  const micActiveRef = useRef(micActive);
  micActiveRef.current = micActive;
  const startMicListeningRef = useRef<() => void>(() => undefined);
  const isTranscribingRef = useRef(false);
  const wasInterruptedRef = useRef(false);

  const setTheme = useOmiStore((s) => s.setTheme);
  const setWsConnected = useOmiStore((s) => s.setWsConnected);
  const setSphereState = useOmiStore((s) => s.setSphereState);
  const setActiveModel = useOmiStore((s) => s.setActiveModel);
  const setMessages = useOmiStore((s) => s.setMessages);
  const setApprovals = useOmiStore((s) => s.setApprovals);
  const setAlerts = useOmiStore((s) => s.setAlerts);
  const setSensors = useOmiStore((s) => s.setSensors);
  const setError = useOmiStore((s) => s.setError);
  const resolveApprovalLocal = useOmiStore((s) => s.resolveApprovalLocal);
  const ackAlertLocal = useOmiStore((s) => s.ackAlertLocal);
  const appendUserMessage = useOmiStore((s) => s.appendUserMessage);
  const interruptSpeech = useOmiStore((s) => s.interruptSpeech);
  const handleServerEvent = useOmiStore((s) => s.handleServerEvent);

  const pendingApprovalsCount = approvals.filter((a) => a.status === "pending").length;
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem("omi_sidebar_collapsed") === "1";
  });

  const toggleSidebar = useCallback((collapsed?: boolean) => {
    setSidebarCollapsed((prev) => {
      const next = typeof collapsed === "boolean" ? collapsed : !prev;
      if (typeof window !== "undefined") {
        window.localStorage.setItem("omi_sidebar_collapsed", next ? "1" : "0");
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
    const token = window.localStorage.getItem("omi_api_token") ?? undefined;
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const envWs = import.meta.env.VITE_WS_URL as string | undefined;
    const wsUrl = envWs && envWs.length > 0 ? envWs : `${protocol}://${window.location.host}/ws`;

    const client = new OmiWsClient({
      url: wsUrl,
      token,
      onEvent: (ev) => {
        if (!active) return;
        if (ev.type === "state" && ev.payload.value === "thinking") {
          ttsReceivedRef.current = false;
        }
        handleServerEvent(ev);
        if (ev.type === "alert" && ev.payload.message && ev.payload.urgency >= 2) {
          audioEngine.speakText(ev.payload.message, apiClient.speakVoice);
        }
        if (ev.type === "assistant.done") {
          if (!ttsReceivedRef.current) {
            const msgs = useOmiStore.getState().messages;
            const lastAssistant = [...msgs].reverse().find((m) => m.role === "assistant");
            if (lastAssistant?.content) {
              audioEngine.speakText(lastAssistant.content, apiClient.speakVoice);
            } else if (micActiveRef.current) {
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
        const streamed = useOmiStore.getState().streamingText;
        if (streamed) {
          audioEngine.setSpokenText(streamed);
        }
        audioEngine.playTtsChunk(audioBase64);
      },
    });
    wsClientRef.current = client;
    client.connect();

    Promise.allSettled([
      apiClient.getApprovals(),
      apiClient.getAlerts(),
      apiClient.getSensorsBlacklist(),
      apiClient.getModels(),
    ]).then(([apprRes, alertsRes, blacklistRes, modelsRes]) => {
      if (!active) return;
      if (apprRes.status === "fulfilled" && useOmiStore.getState().approvals.length === 0) {
        setApprovals(apprRes.value);
      }
      if (alertsRes.status === "fulfilled" && useOmiStore.getState().alerts.length === 0) {
        setAlerts(alertsRes.value);
      }
      if (blacklistRes.status === "fulfilled" && blacklistRes.value.sensors.length > 0) {
        setSensors(blacklistRes.value.sensors);
      }
      if (modelsRes.status === "fulfilled") {
        const agentCfg = modelsRes.value.find((m) => m.role === "agent") ?? modelsRes.value[0];
        if (agentCfg?.model) {
          setActiveModel(agentCfg.model);
        }
      }
    });

    return () => {
      active = false;
      client.disconnect();
      wsClientRef.current = null;
      audioEngine.stopMicrophone();
      audioEngine.stopPlayback();
    };
  }, [
    audioEngine,
    handleServerEvent,
    setActiveModel,
    setAlerts,
    setApprovals,
    setMessages,
    setSensors,
    setWsConnected,
  ]);

  const handleSendText = useCallback(
    async (text: string) => {
      audioEngine.unlock();
      ttsReceivedRef.current = false;
      setError(null);
      const overwrite = wasInterruptedRef.current;
      wasInterruptedRef.current = false;
      appendUserMessage(text, overwrite);
      useOmiStore.getState().clearTranscripts();
      if (wsClientRef.current && useOmiStore.getState().wsConnected) {
        wsClientRef.current.send({ type: "user.text", payload: { text } });
        return;
      }
      setSphereState("thinking");
      try {
        const saved = await apiClient.sendMessage(text);
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
    [appendUserMessage, audioEngine, setError, setMessages, setSphereState]
  );

  const handleToggleSensor = useCallback(
    async (sensor: SensorName, paused: boolean) => {
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

  const handleSelectModel = useCallback(
    (model: string) => {
      setActiveModel(model);
      wsClientRef.current?.send({ type: "settings.model", payload: { role: "agent", model } });
      apiClient.updateRoleModel("agent", model).catch(() => undefined);
    },
    [setActiveModel]
  );

  const startMicListening = useCallback(() => {
    if (!micActiveRef.current) return;
    audioEngine.stopPlayback();
    setSphereState("listening");
    audioEngine
      .startMicrophone({
        onBargeIn: () => {
          wasInterruptedRef.current = true;
          audioEngine.stopPlayback();
          interruptSpeech();
          setSphereState("listening");
        },
        onLiveTranscript: (text) => {
          useOmiStore.getState().handleServerEvent({
            type: "transcript.partial",
            payload: { text, speaker: "user" },
          });
        },
        onUtterance: (wavBase64, mimeType) => {
          if (isTranscribingRef.current) return;
          isTranscribingRef.current = true;
          setSphereState("thinking");
          apiClient
            .transcribeVoice(wavBase64, mimeType)
            .then(({ text }) => {
              isTranscribingRef.current = false;
              const cleaned = text.trim();
              if (cleaned) {
                useOmiStore.getState().handleServerEvent({
                  type: "transcript.final",
                  payload: { text: cleaned, speaker: "user" },
                });
                void handleSendText(cleaned);
              } else {
                const st = useOmiStore.getState();
                if (!audioEngine.isPlaying()) {
                  setSphereState(micActiveRef.current ? "listening" : st.wsConnected ? "idle" : "offline");
                }
              }
            })
            .catch((err) => {
              isTranscribingRef.current = false;
              setError(err instanceof Error ? err.message : "Échec de la transcription vocale.");
              const st = useOmiStore.getState();
              if (!audioEngine.isPlaying()) {
                setSphereState(micActiveRef.current ? "listening" : st.wsConnected ? "idle" : "offline");
              }
            });
        },
      })
      .then((ok) => {
        if (!ok) {
          const st = useOmiStore.getState();
          setSphereState(st.wsConnected ? "idle" : "offline");
        }
      })
      .catch(() => {
        const st = useOmiStore.getState();
        setSphereState(st.wsConnected ? "idle" : "offline");
      });
  }, [audioEngine, handleSendText, interruptSpeech, setError, setSphereState]);
  startMicListeningRef.current = startMicListening;

  useEffect(() => {
    audioEngine.onPlaybackEnded = () => {
      const st = useOmiStore.getState();
      if (st.sphereState === "speaking") {
        setSphereState(micActiveRef.current ? "listening" : st.wsConnected ? "idle" : "offline");
      }
    };
    return () => {
      audioEngine.onPlaybackEnded = undefined;
    };
  }, [audioEngine, setSphereState]);

  useEffect(() => {
    if (micActive) {
      startMicListening();
    } else {
      audioEngine.stopMicrophone();
      const st = useOmiStore.getState();
      if (st.sphereState === "listening") {
        setSphereState(st.wsConnected ? "idle" : "offline");
      }
    }
  }, [micActive, startMicListening, audioEngine, setSphereState]);

  const handleInterrupt = useCallback(() => {
    wasInterruptedRef.current = true;
    audioEngine.stopPlayback();
    interruptSpeech();
    if (wsClientRef.current && useOmiStore.getState().wsConnected) {
      wsClientRef.current.send({ type: "user.interrupt", payload: { reason: "user_cancel" } });
    }
    const st = useOmiStore.getState();
    setSphereState(micActiveRef.current ? "listening" : st.wsConnected ? "idle" : "offline");
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
      wsClientRef.current?.send({ type: "alert.ack", payload: { id } });
      if (tooNoisy) {
        await apiClient.markAlertTooNoisy(id).catch(() => undefined);
      } else {
        await apiClient.ackAlert(id).catch(() => undefined);
      }
    },
    [ackAlertLocal]
  );

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
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
      <div className="app" data-sidebar-collapsed={sidebarCollapsed ? "true" : "false"}>
        <aside className="sidebar">
          <div className="sidebar__header">
            <div className="sidebar__top">
              <div className="brand">
                <img src="/favicon.svg" alt="" width={26} height={26} />
                <span>OMI</span>
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
              title={wsConnected ? "Connecté au service omi-core" : "Service omi-core injoignable"}
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

          <p className="sidebar__footer">
            Données stockées localement dans ~/omi-data. Aucune clé API dans le navigateur.
          </p>
        </aside>

        <main className="main">
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
          {lastError && (
            <div className="banner" role="alert" style={{ maxWidth: 1080, margin: "0 auto 16px" }}>
              <span>{lastError}</span>
              <button
                type="button"
                className="icon-btn icon-btn--sm"
                aria-label="Masquer l'erreur"
                onClick={() => setError(null)}
              >
                <Icon name="x" size={14} />
              </button>
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
              {route === "/" && (
                <HomePage
                  audioEngine={audioEngine}
                  onSendText={handleSendText}
                  onToggleSensor={handleToggleSensor}
                  onSelectModel={handleSelectModel}
                  onInterrupt={handleInterrupt}
                />
              )}
              {route === "/conversation" && <ConversationPage onSendText={handleSendText} />}
              {route === "/alerts" && (
                <AlertsApprovalsPage
                  onApproveAction={handleApproveAction}
                  onAckAlert={handleAckAlert}
                />
              )}
              {route === "/memory" && <MemoryOnboardingPage />}
              {route === "/config" && <ConfigPage />}
              {route === "/not-found" && <NotFoundPage onGoHome={() => navigate("/")} />}
            </motion.div>
          </AnimatePresence>
        </main>

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
