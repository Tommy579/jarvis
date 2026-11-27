import {
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { apiClient } from "../api/client";
import { GoalTrackerCard } from "../components/GoalTrackerCard";
import { Icon } from "../components/Icon";
import { RichText } from "../components/RichText";
import { SlashCommandMenu } from "../components/SlashCommandMenu";
import { formatModelLabel } from "../lib/modelLabels";
import { useSlashComposer } from "../lib/useSlashComposer";
import { useJarvisStore, type ThinkingMode } from "../store/useJarvisStore";
import type { SensorName, SphereState } from "../types/generated";
import { AudioEngine } from "../sphere/AudioEngine";
import { SphereFallback2D } from "../sphere/SphereFallback2D";

const VoiceSphere = lazy(() =>
  import("../sphere/VoiceSphere").then((m) => ({ default: m.VoiceSphere }))
);

/** Frame caméra conservée en mémoire pour le crossfade (double buffer). */
interface CameraFrame {
  url: string;
  seq: number;
}

interface HomePageProps {
  audioEngine: AudioEngine;
  onSendText: (text: string) => void;
  onToggleSensor: (sensor: SensorName, paused: boolean) => void;
  onSelectModel: (model: string) => void;
  onSelectThinking: (mode: ThinkingMode) => void;
  onInterrupt: () => void;
  onOpenGoals?: () => void;
}

const QUICK_MODELS = [
  "qwen3.5:2b",
  "qwen3.5:0.8b",
  "gemma4:31b-cloud",
  "nemotron-3-ultra:cloud",
  "minimax-m3:cloud",
  "gemini-4-hydrogen",
  "gemini-4-argon",
  "gemini-4-carbon",
  "gemini-3.5-flash-lite",
  "gemini-3-flash-preview",
  "gemini-3.1-pro-preview",
  "deepseek/deepseek-v4.1-flash-1017",
  "z-ai/glm-5.3",
  "minimax/minimax-m3",
  "moonshotai/kimi-k3",
];

const STATE_LABELS: Record<SphereState, string> = {
  idle: "En veille",
  listening: "J'écoute",
  thinking: "Réflexion",
  speaking: "Réponse en cours",
  alert: "Alerte",
  offline: "Hors ligne",
};

const THINKING_OPTIONS: { value: ThinkingMode; label: string }[] = [
  { value: "auto", label: "Auto" },
  { value: "off", label: "Off" },
  { value: "low", label: "Léger" },
  { value: "medium", label: "Moyen" },
  { value: "high", label: "Profond" },
];

const QUICK_PROMPTS = [
  "Résume mes derniers échanges",
  "État de mes objectifs",
  "Vérifie mes alertes en attente",
];

export function HomePage({
  audioEngine,
  onSendText,
  onToggleSensor,
  onSelectModel,
  onSelectThinking,
  onInterrupt,
  onOpenGoals,
}: HomePageProps) {
  const sphereState = useJarvisStore((s) => s.sphereState);
  const theme = useJarvisStore((s) => s.theme);
  const partialTranscript = useJarvisStore((s) => s.partialTranscript);
  const finalTranscript = useJarvisStore((s) => s.finalTranscript);
  const streamingText = useJarvisStore((s) => s.streamingText);
  const streamingMessageId = useJarvisStore((s) => s.streamingMessageId);
  const streamingStepCount = useJarvisStore((s) => s.streamingStepCount);
  const messages = useJarvisStore((s) => s.messages);
  const sensors = useJarvisStore((s) => s.sensors);
  const activeModel = useJarvisStore((s) => s.activeModel);
  const thinkingMode = useJarvisStore((s) => s.thinkingMode);
  const lastInterruptedAt = useJarvisStore((s) => s.lastInterruptedAt);
  const sessionStartedAt = useJarvisStore((s) => s.sessionStartedAt);
  const goalsMap = useJarvisStore((s) => s.goals);
  const goalMode = useJarvisStore((s) => s.goalMode);
  const setGoalMode = useJarvisStore((s) => s.setGoalMode);
  const terminalMode = useJarvisStore((s) => s.terminalMode);
  const toggleTerminalMode = useJarvisStore((s) => s.toggleTerminalMode);
  const voiceMode = useJarvisStore((s) => s.voiceMode);
  const wakeWordEnabled = useJarvisStore((s) => s.wakeWordEnabled);

  const activeGoal = Object.values(goalsMap)
    .filter((g) =>
      ["planning", "running", "verifying", "waiting_user", "waiting_approval", "paused_budget"].includes(
        g.status
      )
    )
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];

  const [input, setInput] = useState("");
  const [expandedPreview, setExpandedPreview] = useState(false);
  // Aperçu caméra : double buffer (2 frames max) pour un crossfade doux à 1 Hz.
  // La frame la plus récente apparaît en fondu par-dessus la précédente, qui
  // reste affichée jusqu'à la fin de la transition (aucun flash blanc).
  const [cameraFrames, setCameraFrames] = useState<CameraFrame[]>([]);
  // Séquences des frames déjà décodées par le navigateur : une frame ne devient
  // visible qu'une fois chargée, ce qui garantit un fondu sans flash blanc.
  const [cameraLoadedSeqs, setCameraLoadedSeqs] = useState<number[]>([]);
  const cameraFramesRef = useRef<CameraFrame[]>([]);
  const cameraLoadedSeqsRef = useRef<number[]>([]);
  const cameraSeqRef = useRef(0);

  const pushCameraFrame = useCallback((url: string) => {
    const seq = cameraSeqRef.current + 1;
    cameraSeqRef.current = seq;
    const prev = cameraFramesRef.current;
    const loaded = cameraLoadedSeqsRef.current;
    const all = [...prev, { url, seq }];
    // On conserve au plus 2 frames : la plus récente, plus la dernière frame
    // réellement décodée (qui sert de fond pendant le fondu). Si la nouvelle
    // frame n'est pas encore décodée, l'ancienne reste affichée : jamais d'écran
    // vide, et le tampon ne peut pas croître indéfiniment.
    const newestLoaded = [...all].reverse().find((frame) => loaded.includes(frame.seq));
    const keep = new Set<number>([seq]);
    if (newestLoaded) keep.add(newestLoaded.seq);
    const next = all.filter((frame) => keep.has(frame.seq));
    for (const frame of all) {
      if (!keep.has(frame.seq)) URL.revokeObjectURL(frame.url);
    }
    const keptSeqs = new Set(next.map((frame) => frame.seq));
    cameraFramesRef.current = next;
    cameraLoadedSeqsRef.current = loaded.filter((s) => keptSeqs.has(s));
    setCameraFrames(next);
    setCameraLoadedSeqs(cameraLoadedSeqsRef.current);
  }, []);

  const clearCameraFrames = useCallback(() => {
    const prev = cameraFramesRef.current;
    if (prev.length === 0) return;
    for (const frame of prev) URL.revokeObjectURL(frame.url);
    cameraFramesRef.current = [];
    cameraLoadedSeqsRef.current = [];
    setCameraFrames([]);
    setCameraLoadedSeqs([]);
  }, []);
  const [modelPopoverOpen, setModelPopoverOpen] = useState(false);
  const [goalExpanded, setGoalExpanded] = useState(false);
  const [composerMenuOpen, setComposerMenuOpen] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const goalWidgetRef = useRef<HTMLDivElement | null>(null);
  const composerMenuRef = useRef<HTMLDivElement | null>(null);
  const popoverPanelRef = useRef<HTMLDivElement | null>(null);
  const modelTriggerRef = useRef<HTMLButtonElement | null>(null);
  const popoverWasOpenRef = useRef(false);
  const inputFromVoiceRef = useRef(false);
  const prevStreamingIdRef = useRef<string | null>(null);

  const cameraSensor = sensors.find((s) => s.sensor === "camera");
  const cameraActive = Boolean(cameraSensor && !cameraSensor.paused && cameraSensor.active);
  const cameraEnabled = Boolean(cameraSensor && !cameraSensor.paused);
  const screenSensor = sensors.find((s) => s.sensor === "screen");
  const screenEnabled = Boolean(screenSensor && !screenSensor.paused);
  const micSensor = sensors.find((s) => s.sensor === "mic");
  const micEnabled = Boolean(micSensor && !micSensor.paused);
  const hasSpecialComposerOption = goalMode || terminalMode === "ask" || cameraEnabled;

  // Fermeture du popover modèle au clic extérieur
  useEffect(() => {
    if (!modelPopoverOpen) return;
    const onDown = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setModelPopoverOpen(false);
      }
    };
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("mousedown", onDown);
    };
  }, [modelPopoverOpen]);

  // Fermeture des menus Objectif et Options au clic extérieur ou Échap
  useEffect(() => {
    if (!goalExpanded && !composerMenuOpen) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (goalExpanded && goalWidgetRef.current && !goalWidgetRef.current.contains(target)) {
        setGoalExpanded(false);
      }
      if (composerMenuOpen && composerMenuRef.current && !composerMenuRef.current.contains(target)) {
        setComposerMenuOpen(false);
      }
    };
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") {
        setGoalExpanded(false);
        setComposerMenuOpen(false);
      }
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [goalExpanded, composerMenuOpen]);

  // Popover modèle : piège de focus clavier (Tab / Shift+Tab) + fermeture par Échap
  useEffect(() => {
    if (!modelPopoverOpen) return;
    const panel = popoverPanelRef.current;

    const getFocusables = (): HTMLElement[] => {
      if (!panel) return [];
      const selector =
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
      return Array.from(panel.querySelectorAll<HTMLElement>(selector)).filter(
        (el) => el.getAttribute("aria-hidden") !== "true"
      );
    };

    // Focus initial sur le premier élément focusable du popover
    const initial = getFocusables()[0];
    initial?.focus();

    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        setModelPopoverOpen(false);
        return;
      }
      if (e.key !== "Tab") return;
      // Isole la navigation clavier du popover des raccourcis globaux de l'app
      // (Maj+Tab bascule sinon le mode terminal, Tab doit rester dans le dialogue).
      e.stopPropagation();
      const items = getFocusables();
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const firstEl = items[0];
      const lastEl = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;
      const inside = Boolean(active && panel && panel.contains(active));
      if (e.shiftKey) {
        if (!inside || active === firstEl) {
          e.preventDefault();
          lastEl.focus();
        }
      } else if (!inside || active === lastEl) {
        e.preventDefault();
        firstEl.focus();
      }
    };

    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
    };
  }, [modelPopoverOpen]);

  // Restitution du focus au bouton déclencheur à la fermeture du popover
  useEffect(() => {
    if (modelPopoverOpen) {
      popoverWasOpenRef.current = true;
      return;
    }
    if (popoverWasOpenRef.current) {
      popoverWasOpenRef.current = false;
      modelTriggerRef.current?.focus();
    }
  }, [modelPopoverOpen]);

  useEffect(() => {
    if (!cameraActive) {
      clearCameraFrames();
      return;
    }
    let cancelled = false;
    let inFlight = false;
    let timer: number | null = null;
    let consecutiveFailures = 0;
    const controller = new AbortController();

    const CAMERA_POLL_NOMINAL_MS = 1000;
    const CAMERA_POLL_MAX_MS = 5000;

    function scheduleNext(delay: number) {
      if (cancelled) return;
      if (timer !== null) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        timer = null;
        void pollFrame();
      }, delay);
    }

    async function pollFrame() {
      if (cancelled) return;
      if (inFlight || document.hidden) {
        scheduleNext(CAMERA_POLL_NOMINAL_MS);
        return;
      }
      inFlight = true;
      let nextDelay = CAMERA_POLL_NOMINAL_MS;
      try {
        const blob = await apiClient.fetchCameraFrameBlob(controller.signal);
        if (cancelled) return;
        // Double buffer : la frame précédente reste affichée le temps du fondu,
        // puis son object URL est révoqué (voir pushCameraFrame).
        pushCameraFrame(URL.createObjectURL(blob));
        // Succès : retour au rythme nominal
        consecutiveFailures = 0;
      } catch {
        if (cancelled) return;
        // Échec : backoff progressif 1s -> 2s -> 4s -> 5s (plafond)
        consecutiveFailures += 1;
        nextDelay = Math.min(
          CAMERA_POLL_NOMINAL_MS * 2 ** (consecutiveFailures - 1),
          CAMERA_POLL_MAX_MS
        );
      } finally {
        inFlight = false;
      }
      scheduleNext(nextDelay);
    }

    // Reprise immédiate du polling quand l'onglet redevient visible
    const onVisibilityChange = () => {
      if (cancelled || document.hidden) return;
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
      if (!inFlight) void pollFrame();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);

    void pollFrame();
    return () => {
      cancelled = true;
      controller.abort();
      document.removeEventListener("visibilitychange", onVisibilityChange);
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
      clearCameraFrames();
    };
  }, [cameraActive, clearCameraFrames, pushCameraFrame]);

  // Synchronise en direct la transcription vocale dans le champ de saisie
  useEffect(() => {
    if (partialTranscript) {
      inputFromVoiceRef.current = true;
      setInput(partialTranscript);
    } else if (finalTranscript) {
      inputFromVoiceRef.current = true;
      setInput(finalTranscript);
    }
  }, [partialTranscript, finalTranscript]);

  // Hauteur automatique du champ de saisie
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  const modelOptions = QUICK_MODELS.includes(activeModel)
    ? QUICK_MODELS
    : [activeModel, ...QUICK_MODELS];

  const lastAssistant = [...messages]
    .reverse()
    .find((m) => m.role === "assistant" && (!m.created_at || m.created_at >= sessionStartedAt));
  const lastAssistantId = lastAssistant?.id;
  const prevAssistantIdRef = useRef<string | undefined>(lastAssistantId);

  // Dès qu'on coupe la parole à Jarvis (barge-in ou interruption), le prompt précédent est immédiatement écrasé
  useEffect(() => {
    if (lastInterruptedAt) {
      inputFromVoiceRef.current = false;
      setInput("");
      useJarvisStore.getState().clearTranscripts();
    }
  }, [lastInterruptedAt]);

  // Efface le prompt vocal une seule fois au debut d'une reponse, sans ecraser la saisie manuelle en cours
  useEffect(() => {
    if (streamingMessageId && streamingMessageId !== prevStreamingIdRef.current) {
      prevStreamingIdRef.current = streamingMessageId;
      if (inputFromVoiceRef.current) {
        inputFromVoiceRef.current = false;
        setInput("");
      }
      useJarvisStore.getState().clearTranscripts();
    }
  }, [streamingMessageId]);

  useEffect(() => {
    if (lastAssistantId && lastAssistantId !== prevAssistantIdRef.current) {
      prevAssistantIdRef.current = lastAssistantId;
      if (inputFromVoiceRef.current) {
        inputFromVoiceRef.current = false;
        setInput("");
      }
      useJarvisStore.getState().clearTranscripts();
    }
  }, [lastAssistantId]);

  let who: string | null = null;
  let liveLine = "";
  if (streamingText) {
    who = "Jarvis";
    liveLine = streamingText;
  } else if (sphereState === "thinking") {
    who = "Jarvis";
    liveLine = "Jarvis réfléchit…";
  } else if (lastAssistant?.content) {
    who = "Jarvis";
    liveLine = lastAssistant.content;
  }
  const isPlaceholder = !liveLine;
  if (isPlaceholder) {
    liveLine =
      sphereState === "offline"
        ? "Le service jarvis-core ne répond pas. Les messages passeront par l'API dès son retour."
        : "Posez une question, ou activez le micro ci-dessous pour parler.";
  }

  const TRUNCATE_LIMIT = 240;
  const SUITE_MARKER_RE = /\s*(?:\.\.\.|…)?\s*(?:\[suite\]|\(suite\)|suite\.\.\.)\s*$/i;
  const hasSuiteMarker = !isPlaceholder && SUITE_MARKER_RE.test(liveLine);
  const cleanLine = isPlaceholder ? liveLine : liveLine.replace(SUITE_MARKER_RE, "").trimEnd();
  const isStructured = !isPlaceholder && (cleanLine.includes("\n| ") || cleanLine.startsWith("### "));
  const isTruncatable = !isStructured && cleanLine.length > TRUNCATE_LIMIT && !isPlaceholder;
  const displayLine =
    isTruncatable && !expandedPreview
      ? `${cleanLine.slice(0, TRUNCATE_LIMIT).trimEnd()}…`
      : cleanLine;
  const isBusy =
    sphereState === "thinking" || sphereState === "speaking" || Boolean(streamingText);
  const isWaveActive = Boolean(streamingText);
  const showSuiteBtn = isTruncatable || (hasSuiteMarker && !isBusy);

  useEffect(() => {
    setExpandedPreview(false);
  }, [streamingMessageId, streamingStepCount]);

  const submit = (rawText = input) => {
    const trimmed = rawText.trim();
    if (!trimmed) {
      inputRef.current?.focus();
      return;
    }
    onSendText(trimmed);
    inputFromVoiceRef.current = false;
    setInput("");
    resetSlashMenu();
    useJarvisStore.getState().clearTranscripts();
  };

  const {
    slashItems,
    activeSlashIndex,
    setSlashIndex,
    resetSlashMenu,
    selectSlashItem,
    handleKeyDown,
  } = useSlashComposer({
    value: input,
    setValue: (next) => {
      inputFromVoiceRef.current = false;
      setInput(next);
    },
    inputRef,
    onSubmitText: (text) => submit(text),
    onExecuteCommand: (cmd) => {
      inputFromVoiceRef.current = false;
      setInput("");
      useJarvisStore.getState().clearTranscripts();
      onSendText(cmd);
    },
  });

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit();
  };

  const currentThinkingLabel =
    THINKING_OPTIONS.find((t) => t.value === thinkingMode)?.label ?? thinkingMode;
  const pillLabel =
    sphereState === "idle" && voiceMode === "standby"
      ? "Attente « Jarvis »"
      : STATE_LABELS[sphereState] ?? sphereState;

  return (
    <section className="home" data-sphere-state={sphereState} aria-label="Accueil vocal">
      <div className="home__top">
        <span
          className="state-pill"
          data-state={sphereState}
          data-voice-mode={voiceMode}
          data-testid="sphere-state-badge"
        >
          <span className="dot" aria-hidden="true" />
          {pillLabel}
        </span>

        <div className="home__controls">
          <div style={{ position: "relative" }} ref={popoverRef}>
            <button
              ref={modelTriggerRef}
              type="button"
              className="model-trigger"
              aria-expanded={modelPopoverOpen}
              aria-haspopup="dialog"
              aria-controls="model-popover-panel"
              onClick={() => setModelPopoverOpen((v) => !v)}
            >
              <span className="model-trigger__name">{formatModelLabel(activeModel)}</span>
              <span className="model-trigger__sep" aria-hidden="true">·</span>
              <span className="model-trigger__think">{currentThinkingLabel}</span>
              <Icon name="chevron" size={13} className="model-trigger__chev" />
            </button>

            {modelPopoverOpen && (
              <div
                ref={popoverPanelRef}
                id="model-popover-panel"
                className="model-popover"
                role="dialog"
                aria-modal="false"
                aria-label="Paramètres du modèle actif"
                tabIndex={-1}
              >
                <label className="model-popover__field">
                  <span>Modèle actif</span>
                  <select
                    aria-label="Modèle agent actif"
                    className="select select--sm"
                    value={activeModel}
                    onChange={(e) => onSelectModel(e.target.value)}
                  >
                    {modelOptions.map((m) => (
                      <option key={m} value={m}>
                        {formatModelLabel(m)}
                      </option>
                    ))}
                  </select>
                </label>

                <div className="model-popover__field">
                  <span>Raisonnement</span>
                  <div className="model-popover__pills" role="radiogroup" aria-label="Mode de raisonnement du modèle">
                    {THINKING_OPTIONS.map((opt) => (
                      <button
                        key={opt.value}
                        type="button"
                        role="radio"
                        aria-checked={thinkingMode === opt.value}
                        className="model-popover__pill"
                        onClick={() => onSelectThinking(opt.value)}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>

          <div
            className="goal-widget"
            ref={goalWidgetRef}
            data-active={goalMode || Boolean(activeGoal) ? "true" : "false"}
            data-testid="home-goal-widget"
          >
            <span className="goal-widget__status">
              <span className="dot" aria-hidden="true" />
              {activeGoal
                ? `Objectif · ${activeGoal.progress_pct}%`
                : goalMode
                  ? "Objectif · Actif"
                  : "Objectif"}
            </span>
            <button
              type="button"
              className="goal-widget__toggle"
              aria-expanded={goalExpanded}
              aria-controls="home-goal-popover"
              onClick={() => setGoalExpanded((v) => !v)}
            >
              {goalExpanded ? "Réduire" : "Développer"}
            </button>

            {goalExpanded && (
              <div
                id="home-goal-popover"
                className="goal-popover"
                role="dialog"
                aria-label="Mode Objectif"
              >
                <div className="goal-popover__head">
                  <span className="goal-popover__title">Mode Objectif autonome</span>
                  <button
                    type="button"
                    className="goal-popover__toggle"
                    aria-pressed={goalMode}
                    onClick={() => setGoalMode(!goalMode)}
                  >
                    {goalMode ? "Activé" : "Désactivé"}
                  </button>
                </div>
                <p className="goal-popover__hint">
                  Chaque message lance un objectif supervisé avec sous-agents et vérification finale.
                </p>
                {activeGoal && (
                  <GoalTrackerCard goal={activeGoal} compact onOpenGoals={onOpenGoals} />
                )}
                {!activeGoal && onOpenGoals && (
                  <button type="button" className="btn btn--sm" onClick={onOpenGoals}>
                    <Icon name="target" size={14} />
                    Ouvrir la page Objectifs
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {cameraActive && (
        <figure
          className="camera-preview"
          data-loading={cameraLoadedSeqs.length === 0 ? "true" : "false"}
        >
          <div className="camera-preview__viewport">
            {cameraLoadedSeqs.length === 0 && (
              <div className="camera-preview__shimmer" aria-hidden="true">
                <span className="camera-preview__shimmer-bar" />
                <span className="camera-preview__shimmer-bar" />
                <span className="camera-preview__shimmer-bar" />
              </div>
            )}
            {cameraFrames.map((frame) => (
              <img
                key={frame.seq}
                className="camera-preview__img"
                data-active={cameraLoadedSeqs.includes(frame.seq) ? "true" : "false"}
                src={frame.url}
                alt="Flux caméra en direct"
                width={208}
                height={156}
                decoding="async"
                draggable={false}
                onLoad={() => {
                  // Le fondu ne démarre qu'après décodage : la frame sortante
                  // reste pleinement opaque pendant le chargement de la suivante.
                  requestAnimationFrame(() => {
                    if (cameraLoadedSeqsRef.current.includes(frame.seq)) return;
                    cameraLoadedSeqsRef.current = [
                      ...cameraLoadedSeqsRef.current,
                      frame.seq,
                    ];
                    setCameraLoadedSeqs(cameraLoadedSeqsRef.current);
                  });
                }}
                onError={() => {
                  // Frame illisible : on la retire pour ne pas bloquer le fondu
                  // sur une image morte (le shimmer reprend si plus rien n'est décodé).
                  const remaining = cameraFramesRef.current.filter(
                    (f) => f.seq !== frame.seq
                  );
                  cameraFramesRef.current = remaining;
                  cameraLoadedSeqsRef.current = cameraLoadedSeqsRef.current.filter(
                    (s) => s !== frame.seq
                  );
                  URL.revokeObjectURL(frame.url);
                  setCameraFrames(remaining);
                  setCameraLoadedSeqs(cameraLoadedSeqsRef.current);
                }}
              />
            ))}
            <span
              className="camera-preview__badge"
              data-live={cameraLoadedSeqs.length > 0 ? "true" : "false"}
            >
              <span className="camera-preview__badge-dot" aria-hidden="true" />
              {cameraLoadedSeqs.length > 0 ? "Live 1Hz" : "Connexion…"}
            </span>
          </div>
          <figcaption className="sr-only">
            Aperçu caméra rafraîchi une fois par seconde
          </figcaption>
        </figure>
      )}

      <div className="home__stage">
        <div className="sphere-stage" data-state={sphereState}>
          <div className="sphere-orbit" aria-hidden="true" />
          <Suspense
            fallback={
              <div
                className="sphere-wrapper"
                data-testid="jarvis-sphere"
                data-sphere-state={sphereState}
                data-renderer="canvas2d"
              >
                <SphereFallback2D
                  sphereState={sphereState}
                  theme={theme}
                  amplitude={0.12}
                  reducedMotion={false}
                />
              </div>
            }
          >
            <VoiceSphere audioEngine={audioEngine} />
          </Suspense>
        </div>

        <div className="live-line-wrap">
          {isStructured ? (
            <div
              className="live-line live-line--rich"
              aria-live="polite"
              data-testid="live-subtitles"
              data-placeholder={isPlaceholder}
              data-wave={isWaveActive ? "true" : "false"}
            >
              {who && <span className="live-line__who">{who}</span>}
              {isWaveActive ? (
                <div className="text-wave">
                  <RichText content={cleanLine} />
                </div>
              ) : (
                <RichText content={cleanLine} />
              )}
            </div>
          ) : (
            <p
              className="live-line"
              aria-live="polite"
              data-testid="live-subtitles"
              data-placeholder={isPlaceholder}
              data-expanded={expandedPreview}
              data-wave={isWaveActive ? "true" : "false"}
            >
              {who && <span className="live-line__who">{who}</span>}
              {isWaveActive ? <span className="text-wave">{displayLine}</span> : displayLine}
            </p>
          )}
          {isPlaceholder && sphereState !== "offline" && (
            <div className="quick-prompts" aria-label="Suggestions rapides">
              {QUICK_PROMPTS.map((q) => (
                <button
                  key={q}
                  type="button"
                  className="quick-prompt"
                  onClick={() => onSendText(q)}
                >
                  {q}
                </button>
              ))}
            </div>
          )}
          {showSuiteBtn && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              style={{ alignSelf: "center", fontSize: "var(--fs-xs)", padding: "2px 8px" }}
              onClick={() => {
                if (isTruncatable && !expandedPreview) {
                  setExpandedPreview(true);
                } else if (hasSuiteMarker) {
                  onSendText("Donne la suite.");
                } else {
                  setExpandedPreview(false);
                }
              }}
            >
              {isTruncatable && expandedPreview && !hasSuiteMarker ? "Réduire" : "Suite"}
            </button>
          )}
        </div>
      </div>

      <div className="composer-dock">
        <form className="composer" onSubmit={handleSubmit} noValidate>
          <SlashCommandMenu
            items={slashItems}
            selectedIndex={activeSlashIndex}
            onHover={setSlashIndex}
            onSelect={(item) => selectSlashItem(item, false)}
          />
          <div className="composer-tools" ref={composerMenuRef}>
            <button
              type="button"
              className={`composer-tools__btn ${hasSpecialComposerOption ? "composer-tools__btn--active" : ""}`}
              aria-expanded={composerMenuOpen}
              aria-haspopup="menu"
              aria-label="Options et capteurs"
              title="Options et capteurs"
              onClick={() => setComposerMenuOpen((v) => !v)}
            >
              <Icon name="sliders" size={17} />
              {hasSpecialComposerOption && <span className="composer-tools__dot" aria-hidden="true" />}
            </button>

            {composerMenuOpen && (
              <div className="composer-popover" role="menu" aria-label="Options et capteurs">
                <button
                  type="button"
                  role="menuitemcheckbox"
                  aria-checked={goalMode}
                  aria-pressed={goalMode}
                  className="composer-popover__row"
                  onClick={() => setGoalMode(!goalMode)}
                >
                  <span className="composer-popover__text">
                    <span className="composer-popover__title">Mode Objectif</span>
                    <span className="composer-popover__meta">Orchestration autonome</span>
                  </span>
                  <span className="composer-popover__pill">{goalMode ? "Actif" : "Off"}</span>
                </button>

                <button
                  type="button"
                  role="menuitemcheckbox"
                  aria-checked={terminalMode === "ask"}
                  aria-pressed={terminalMode === "ask"}
                  className="composer-popover__row"
                  onClick={() => toggleTerminalMode()}
                >
                  <span className="composer-popover__text">
                    <span className="composer-popover__title">Terminal</span>
                    <span className="composer-popover__meta">Maj+Tab · niveau 3+</span>
                  </span>
                  <span className="composer-popover__pill">
                    {terminalMode === "ask" ? "Demande" : "Auto"}
                  </span>
                </button>

                {screenSensor && (
                  <button
                    type="button"
                    role="menuitemcheckbox"
                    aria-checked={screenEnabled}
                    aria-pressed={screenEnabled}
                    className="composer-popover__row"
                    onClick={() => onToggleSensor("screen", screenEnabled)}
                  >
                    <span className="composer-popover__text">
                      <span className="composer-popover__title">Écran</span>
                      <span className="composer-popover__meta">{screenSensor.detail}</span>
                    </span>
                    <span className="composer-popover__pill">{screenEnabled ? "Actif" : "Pause"}</span>
                  </button>
                )}

                {cameraSensor && (
                  <button
                    type="button"
                    role="menuitemcheckbox"
                    aria-checked={cameraEnabled}
                    aria-pressed={cameraEnabled}
                    className="composer-popover__row"
                    onClick={() => onToggleSensor("camera", cameraEnabled)}
                  >
                    <span className="composer-popover__text">
                      <span className="composer-popover__title">Caméra</span>
                      <span className="composer-popover__meta">{cameraSensor.detail}</span>
                    </span>
                    <span className="composer-popover__pill">{cameraEnabled ? "Actif" : "Pause"}</span>
                  </button>
                )}
              </div>
            )}
          </div>
          <textarea
            ref={inputRef}
            rows={1}
            className="composer__input"
            placeholder={
              goalMode
                ? "Décris l'objectif autonome que Jarvis doit atteindre jusqu'au bout…"
                : voiceMode === "standby"
                  ? "Dites « Jarvis » pour parler, ou écrivez ici…"
                  : voiceMode === "active" && wakeWordEnabled
                    ? "À l'écoute… Dites « coupe le record » pour mettre en veille"
                    : "Écrire à Jarvis ou / pour les commandes…"
            }
            aria-label="Message pour Jarvis"
            value={input}
            onChange={(e) => {
              inputFromVoiceRef.current = false;
              setInput(e.target.value);
              resetSlashMenu();
            }}
            onKeyDown={handleKeyDown}
          />
          <button
            type="button"
            className="mic-btn"
            aria-pressed={micEnabled}
            aria-label={micEnabled ? "Couper le micro" : "Activer le micro"}
            title={micEnabled ? "Micro actif — cliquer pour couper" : "Activer le micro"}
            onClick={() => onToggleSensor("mic", micEnabled)}
          >
            <Icon name="mic" size={17} />
          </button>
          {isBusy ? (
            <button
              type="button"
              className="btn btn--danger send-btn cancel-btn"
              onClick={onInterrupt}
              aria-label="Annuler la réflexion"
              title="Annuler (Échap)"
            >
              <Icon name="stop" size={17} />
              <span className="cancel-label">Annuler</span>
            </button>
          ) : (
            <button
              type="submit"
              className="btn btn--primary send-btn"
              aria-label="Envoyer"
              disabled={!input.trim()}
            >
              <Icon name="send" size={17} />
            </button>
          )}
        </form>
      </div>
    </section>
  );
}
