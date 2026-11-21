import { Suspense, lazy, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { GoalTrackerCard } from "../components/GoalTrackerCard";
import { Icon } from "../components/Icon";
import { RichText } from "../components/RichText";
import { SlashCommandMenu } from "../components/SlashCommandMenu";
import { formatModelLabel } from "../lib/modelLabels";
import { filterSlashCommands, type SlashCommandDef } from "../lib/slashCommands";
import { useJarvisStore, type ThinkingMode } from "../store/useJarvisStore";
import type { SensorName, SphereState } from "../types/generated";
import { AudioEngine } from "../sphere/AudioEngine";
import { SphereFallback2D } from "../sphere/SphereFallback2D";

const VoiceSphere = lazy(() =>
  import("../sphere/VoiceSphere").then((m) => ({ default: m.VoiceSphere }))
);

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

const SENSOR_META: Record<SensorName, { label: string; icon: "monitor" | "mic" | "camera" }> = {
  screen: { label: "Écran", icon: "monitor" },
  mic: { label: "Micro", icon: "mic" },
  camera: { label: "Caméra", icon: "camera" },
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
  const messages = useJarvisStore((s) => s.messages);
  const sensors = useJarvisStore((s) => s.sensors);
  const activeModel = useJarvisStore((s) => s.activeModel);
  const thinkingMode = useJarvisStore((s) => s.thinkingMode);
  const lastInterruptedAt = useJarvisStore((s) => s.lastInterruptedAt);
  const sessionStartedAt = useJarvisStore((s) => s.sessionStartedAt);
  const goalsMap = useJarvisStore((s) => s.goals);
  const goalMode = useJarvisStore((s) => s.goalMode);
  const setGoalMode = useJarvisStore((s) => s.setGoalMode);
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
  const [slashIndex, setSlashIndex] = useState(0);
  const [slashDismissed, setSlashDismissed] = useState(false);
  const [expandedPreview, setExpandedPreview] = useState(false);
  const [streamFallback, setStreamFallback] = useState(false);
  const [frameTick, setFrameTick] = useState(0);
  const [modelPopoverOpen, setModelPopoverOpen] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);

  const cameraSensor = sensors.find((s) => s.sensor === "camera");
  const cameraActive = Boolean(cameraSensor && !cameraSensor.paused && cameraSensor.active);
  const micSensor = sensors.find((s) => s.sensor === "mic");
  const micEnabled = Boolean(micSensor && !micSensor.paused);

  useEffect(() => {
    if (!modelPopoverOpen) return;
    const onDown = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setModelPopoverOpen(false);
      }
    };
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") setModelPopoverOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [modelPopoverOpen]);

  useEffect(() => {
    if (!cameraActive) {
      setStreamFallback(false);
      return;
    }
    if (!streamFallback) return;
    setFrameTick(Date.now());
    const timer = window.setInterval(() => setFrameTick(Date.now()), 100);
    return () => window.clearInterval(timer);
  }, [cameraActive, streamFallback]);

  // Synchronise en direct la transcription vocale dans le champ de saisie
  useEffect(() => {
    if (partialTranscript) {
      setInput(partialTranscript);
    } else if (finalTranscript) {
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
      setInput("");
      useJarvisStore.getState().clearTranscripts();
    }
  }, [lastInterruptedAt]);

  // Dès que Jarvis répond (streaming en direct ou nouveau message reçu), le prompt vocal est effacé
  useEffect(() => {
    if (streamingText) {
      setInput("");
      useJarvisStore.getState().clearTranscripts();
    }
  }, [streamingText]);

  useEffect(() => {
    if (lastAssistantId && lastAssistantId !== prevAssistantIdRef.current) {
      prevAssistantIdRef.current = lastAssistantId;
      setInput("");
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
  const showSuiteBtn = isTruncatable || (hasSuiteMarker && !isBusy);

  const slashItems = slashDismissed ? [] : filterSlashCommands(input);
  const activeSlashIndex = slashItems.length > 0 ? Math.min(slashIndex, slashItems.length - 1) : 0;

  const selectSlashItem = (item: SlashCommandDef, fromTab = false) => {
    if ((fromTab && item.type !== "immediate") || item.type === "goal") {
      setInput(`${item.cmd} `);
      setSlashIndex(0);
      setSlashDismissed(false);
      inputRef.current?.focus();
      return;
    }
    setInput("");
    setSlashIndex(0);
    setSlashDismissed(false);
    useJarvisStore.getState().clearTranscripts();
    onSendText(item.cmd);
  };

  const submit = () => {
    const trimmed = input.trim();
    if (!trimmed) {
      inputRef.current?.focus();
      return;
    }
    onSendText(trimmed);
    setInput("");
    setSlashIndex(0);
    setSlashDismissed(false);
    useJarvisStore.getState().clearTranscripts();
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit();
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (slashItems.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashIndex((activeSlashIndex + 1) % slashItems.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashIndex((activeSlashIndex - 1 + slashItems.length) % slashItems.length);
        return;
      }
      if (e.key === "Tab") {
        e.preventDefault();
        selectSlashItem(slashItems[activeSlashIndex], true);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setSlashDismissed(true);
        return;
      }
      if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
        e.preventDefault();
        selectSlashItem(slashItems[activeSlashIndex], false);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
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

        <div className="home__controls" ref={popoverRef}>
          <button
            type="button"
            className="model-trigger"
            aria-expanded={modelPopoverOpen}
            aria-haspopup="dialog"
            onClick={() => setModelPopoverOpen((v) => !v)}
          >
            <span className="model-trigger__name">{formatModelLabel(activeModel)}</span>
            <span className="model-trigger__sep" aria-hidden="true">·</span>
            <span className="model-trigger__think">{currentThinkingLabel}</span>
            <Icon name="chevron" size={13} className="model-trigger__chev" />
          </button>

          {modelPopoverOpen && (
            <div className="model-popover" role="dialog" aria-label="Paramètres du modèle actif">
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
      </div>

      {cameraActive && (
        <figure className="camera-preview" style={{ margin: 0 }}>
          <img
            src={streamFallback ? `/api/sensors/camera/frame?t=${frameTick}` : "/api/sensors/camera/stream"}
            alt="Flux caméra en direct"
            width={208}
            height={156}
            onError={() => {
              if (!streamFallback) setStreamFallback(true);
            }}
          />
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
            >
              {who && <span className="live-line__who">{who}</span>}
              <RichText content={cleanLine} />
            </div>
          ) : (
            <p
              className="live-line"
              aria-live="polite"
              data-testid="live-subtitles"
              data-placeholder={isPlaceholder}
              data-expanded={expandedPreview}
            >
              {who && <span className="live-line__who">{who}</span>}
              {displayLine}
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

      {activeGoal && (
        <div className="home-goal-banner">
          <GoalTrackerCard goal={activeGoal} compact onOpenGoals={onOpenGoals} />
        </div>
      )}

      <div className="composer-dock">
        <form className="composer" onSubmit={handleSubmit} noValidate>
          <SlashCommandMenu
            items={slashItems}
            selectedIndex={activeSlashIndex}
            onHover={setSlashIndex}
            onSelect={(item) => selectSlashItem(item, false)}
          />
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
              setInput(e.target.value);
              setSlashIndex(0);
              setSlashDismissed(false);
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

        <div className="composer-hint">
          <div className="sensors" aria-label="Capteurs">
            <button
              type="button"
              className={`sensor-chip goal-mode-chip ${goalMode ? "goal-mode-chip--active" : ""}`}
              aria-pressed={goalMode}
              title="Lancer chaque prompt comme objectif autonome supervisé jusqu'à vérification"
              onClick={() => setGoalMode(!goalMode)}
            >
              <span className="dot" aria-hidden="true" />
              Mode Objectif
            </button>
            {sensors.map((sensor) => {
              const meta = SENSOR_META[sensor.sensor];
              const on = !sensor.paused;
              return (
                <button
                  key={sensor.sensor}
                  type="button"
                  className="sensor-chip"
                  aria-pressed={on}
                  title={sensor.detail}
                  onClick={() => onToggleSensor(sensor.sensor, on)}
                >
                  <span className="dot" aria-hidden="true" />
                  {meta?.label ?? sensor.sensor}
                  <span className="sr-only">{on ? ", actif" : ", en pause"}</span>
                </button>
              );
            })}
          </div>
          <span className="composer-hint__keys" style={{ alignSelf: "center" }}>
            <span className="kbd">Entrée</span> envoyer · <span className="kbd">Maj</span>+
            <span className="kbd">Entrée</span> nouvelle ligne
          </span>
        </div>
      </div>
    </section>
  );
}
