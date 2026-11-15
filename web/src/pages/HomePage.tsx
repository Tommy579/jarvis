import { Suspense, lazy, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Icon } from "../components/Icon";
import { useOmiStore } from "../store/useOmiStore";
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
  onInterrupt: () => void;
}

const QUICK_MODELS = [
  "qwen3.5:2b",
  "qwen3.5:0.8b",
  "gemma4:31b-cloud",
  "nemotron-3-ultra:cloud",
  "minimax-m3:cloud",
  "gemini-3-flash-preview",
  "gemini-3.1-pro-preview",
  "deepseek/deepseek-v4.1-flash-1017",
  "z-ai/glm-5.3",
  "minimax/minimax-m3",
  "google/gemini-3.5-flash-lite",
  "google/gemini-4-hydrogen",
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

export function HomePage({
  audioEngine,
  onSendText,
  onToggleSensor,
  onSelectModel,
  onInterrupt,
}: HomePageProps) {
  const sphereState = useOmiStore((s) => s.sphereState);
  const theme = useOmiStore((s) => s.theme);
  const partialTranscript = useOmiStore((s) => s.partialTranscript);
  const finalTranscript = useOmiStore((s) => s.finalTranscript);
  const streamingText = useOmiStore((s) => s.streamingText);
  const messages = useOmiStore((s) => s.messages);
  const sensors = useOmiStore((s) => s.sensors);
  const activeModel = useOmiStore((s) => s.activeModel);
  const lastInterruptedAt = useOmiStore((s) => s.lastInterruptedAt);
  const sessionStartedAt = useOmiStore((s) => s.sessionStartedAt);

  const [input, setInput] = useState("");
  const [expandedPreview, setExpandedPreview] = useState(false);
  const [streamFallback, setStreamFallback] = useState(false);
  const [frameTick, setFrameTick] = useState(0);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  const cameraSensor = sensors.find((s) => s.sensor === "camera");
  const cameraActive = Boolean(cameraSensor && !cameraSensor.paused && cameraSensor.active);

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
      useOmiStore.getState().clearTranscripts();
    }
  }, [lastInterruptedAt]);

  // Dès que Jarvis répond (streaming en direct ou nouveau message reçu), le prompt vocal est effacé
  useEffect(() => {
    if (streamingText) {
      setInput("");
      useOmiStore.getState().clearTranscripts();
    }
  }, [streamingText]);

  useEffect(() => {
    if (lastAssistantId && lastAssistantId !== prevAssistantIdRef.current) {
      prevAssistantIdRef.current = lastAssistantId;
      setInput("");
      useOmiStore.getState().clearTranscripts();
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
        ? "Le service omi-core ne répond pas. Les messages passeront par l'API dès son retour."
        : "Posez une question, ou activez le micro ci-dessous pour parler.";
  }

  const TRUNCATE_LIMIT = 240;
  const isTruncatable = liveLine.length > TRUNCATE_LIMIT && !isPlaceholder;
  const displayLine =
    isTruncatable && !expandedPreview ? `${liveLine.slice(0, TRUNCATE_LIMIT).trimEnd()}…` : liveLine;

  const isBusy =
    sphereState === "thinking" || sphereState === "speaking" || Boolean(streamingText);

  const submit = () => {
    const trimmed = input.trim();
    if (!trimmed) {
      inputRef.current?.focus();
      return;
    }
    onSendText(trimmed);
    setInput("");
    useOmiStore.getState().clearTranscripts();
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit();
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <section className="home" aria-label="Accueil vocal">
      <div className="home__top">
        <span className="state-pill" data-state={sphereState} data-testid="sphere-state-badge">
          <span className="dot" aria-hidden="true" />
          {STATE_LABELS[sphereState] ?? sphereState}
        </span>

        <label className="model-picker">
          <span>Modèle</span>
          <select
            aria-label="Modèle agent actif"
            className="select select--sm"
            value={activeModel}
            onChange={(e) => onSelectModel(e.target.value)}
          >
            {modelOptions.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
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
        <Suspense
          fallback={
            <div
              className="sphere-wrapper"
              data-testid="omi-sphere"
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

        <div className="live-line-wrap">
          <p
            className="live-line"
            aria-live="polite"
            data-testid="live-subtitles"
            data-placeholder={isPlaceholder}
          >
            {who && <span className="live-line__who">{who}</span>}
            {displayLine}
          </p>
          {isTruncatable && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              style={{ alignSelf: "center", fontSize: "var(--fs-xs)", padding: "2px 8px" }}
              onClick={() => setExpandedPreview((prev) => !prev)}
            >
              {expandedPreview ? "Réduire" : "Suite"}
            </button>
          )}
        </div>
      </div>

      <div className="composer-dock">
        <form className="composer" onSubmit={handleSubmit} noValidate>
          <textarea
            ref={inputRef}
            rows={1}
            className="composer__input"
            placeholder="Écrire à Jarvis…"
            aria-label="Message pour Jarvis"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
          />
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
          <span style={{ alignSelf: "center" }}>
            <span className="kbd">Entrée</span> envoyer · <span className="kbd">Maj</span>+
            <span className="kbd">Entrée</span> nouvelle ligne
          </span>
        </div>
      </div>
    </section>
  );
}
