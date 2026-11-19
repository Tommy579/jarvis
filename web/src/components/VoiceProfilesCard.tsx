import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import { computeRms, encodeWavPcm16 } from "../sphere/AudioEngine";
import { errorMessage, toast } from "../store/useToastStore";
import type { VoiceVerifyResult } from "../types/generated";
import { Icon } from "./Icon";
import { Segmented } from "./ui";

const ROLE_OPTIONS = [
  { value: "propriétaire", label: "Propriétaire" },
  { value: "compagne", label: "Compagne / Compagnon" },
  { value: "famille", label: "Famille" },
  { value: "ami", label: "Ami(e)" },
];

const THRESHOLD_PRESETS = [
  { value: 0.4, label: "Tolérant (40 %)" },
  { value: 0.48, label: "Équilibré (48 %)" },
  { value: 0.58, label: "Strict (58 %)" },
];

const MAX_RECORD_SEC = 15;

const READING_PROMPTS = [
  "Bonjour Jarvis, vérifie mes prochains rendez-vous, la météo du jour et rappelle-moi d'acheter du café.",
  "Jarvis, consulte mes messages importants, lance ma musique calme et préviens-moi avant ma réunion de quinze heures.",
  "Hey Jarvis, note cette idée dans ma mémoire, règle une minuterie de dix minutes et donne-moi l'heure exacte.",
  "Salut Jarvis, résume les actualités de ce matin et vérifie si j'ai reçu un nouveau mail urgent.",
];

const TEST_READING_PROMPT = "Bonjour Jarvis, est-ce que tu reconnais bien ma voix maintenant ?";

type RecordTarget =
  | { kind: "enroll"; name: string; role: string }
  | { kind: "sample"; profileId: string; profileName: string }
  | { kind: "test" };

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(new Error("Impossible de lire le fichier audio."));
    reader.readAsDataURL(file);
  });
}

export function VoiceProfilesCard() {
  const qc = useQueryClient();
  const profilesQuery = useQuery({
    queryKey: ["voice_profiles"],
    queryFn: apiClient.getVoiceProfiles,
  });

  const [newName, setNewName] = useState("");
  const [newRole, setNewRole] = useState("propriétaire");
  const [promptIndex, setPromptIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [recordTarget, setRecordTarget] = useState<RecordTarget | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [micLevel, setMicLevel] = useState(0);
  const [testResult, setTestResult] = useState<VoiceVerifyResult | null>(null);

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const procRef = useRef<ScriptProcessorNode | null>(null);
  const chunksRef = useRef<Float32Array[]>([]);
  const sampleRateRef = useRef<number>(16000);
  const timerRef = useRef<number | null>(null);
  const targetRef = useRef<RecordTarget | null>(null);

  const cleanupRecorder = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (procRef.current) {
      try {
        procRef.current.disconnect();
      } catch {
        // ignore
      }
      procRef.current.onaudioprocess = null;
      procRef.current = null;
    }
    if (streamRef.current) {
      for (const track of streamRef.current.getTracks()) {
        track.stop();
      }
      streamRef.current = null;
    }
    if (ctxRef.current) {
      void ctxRef.current.close().catch(() => undefined);
      ctxRef.current = null;
    }
    setMicLevel(0);
  }, []);

  useEffect(() => cleanupRecorder, [cleanupRecorder]);

  const submitRecordedWav = useCallback(
    async (target: RecordTarget, wavBase64: string) => {
      setBusy(true);
      try {
        if (target.kind === "enroll") {
          await apiClient.enrollVoiceProfile({
            name: target.name,
            role: target.role,
            audio_base64: wavBase64,
          });
          setNewName("");
          await qc.invalidateQueries({ queryKey: ["voice_profiles"] });
          toast.ok(`Voix de ${target.name} enregistrée.`);
        } else if (target.kind === "sample") {
          await apiClient.addVoiceProfileSample(target.profileId, wavBase64);
          await qc.invalidateQueries({ queryKey: ["voice_profiles"] });
          toast.ok(`Nouvel échantillon ajouté pour ${target.profileName}.`);
        } else {
          const res = await apiClient.verifyVoiceSample(wavBase64);
          setTestResult(res);
        }
      } catch (err) {
        toast.error(errorMessage(err));
      } finally {
        setBusy(false);
      }
    },
    [qc]
  );

  const stopRecording = useCallback(
    (discard = false) => {
      const target = targetRef.current;
      const chunks = chunksRef.current;
      const sampleRate = sampleRateRef.current;
      chunksRef.current = [];
      targetRef.current = null;
      setRecordTarget(null);
      cleanupRecorder();

      if (discard || !target || chunks.length === 0) return;

      const total = chunks.reduce((acc, c) => acc + c.length, 0);
      const merged = new Float32Array(total);
      let pos = 0;
      for (const chunk of chunks) {
        merged.set(chunk, pos);
        pos += chunk.length;
      }
      const wavBase64 = encodeWavPcm16(merged, sampleRate, 16000);
      void submitRecordedWav(target, wavBase64);
    },
    [cleanupRecorder, submitRecordedWav]
  );

  const startRecording = async (target: RecordTarget) => {
    if (recordTarget || busy) return;
    if (target.kind === "enroll" && !target.name.trim()) {
      toast.error("Indiquez d'abord un prénom ou nom pour cette voix.");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      toast.error("Capture microphone indisponible dans ce navigateur.");
      return;
    }
    setTestResult(null);

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      const AudioCtx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AudioCtx) {
        for (const t of stream.getTracks()) t.stop();
        toast.error("Web Audio API indisponible.");
        return;
      }
      const ctx = new AudioCtx();
      if (ctx.state === "suspended") {
        await ctx.resume();
      }
      const source = ctx.createMediaStreamSource(stream);
      const processor = ctx.createScriptProcessor(4096, 1, 1);
      const silentGain = ctx.createGain();
      silentGain.gain.value = 0;

      chunksRef.current = [];
      sampleRateRef.current = ctx.sampleRate;
      targetRef.current = target;
      streamRef.current = stream;
      ctxRef.current = ctx;
      procRef.current = processor;

      processor.onaudioprocess = (ev: AudioProcessingEvent) => {
        const input = ev.inputBuffer.getChannelData(0);
        chunksRef.current.push(new Float32Array(input));
        setMicLevel(Math.min(1, computeRms(input) * 5));
      };

      source.connect(processor);
      processor.connect(silentGain);
      silentGain.connect(ctx.destination);

      setRecordTarget(target);
      setElapsedSec(0);
      const startedAt = Date.now();
      timerRef.current = window.setInterval(() => {
        const sec = Math.floor((Date.now() - startedAt) / 1000);
        setElapsedSec(sec);
        if (sec >= MAX_RECORD_SEC) {
          stopRecording(false);
        }
      }, 250);
    } catch (err) {
      cleanupRecorder();
      toast.error(errorMessage(err));
    }
  };

  const handleUploadFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    const cleanedName = newName.trim();
    if (!cleanedName) {
      toast.error("Indiquez un nom avant d'importer un fichier audio.");
      return;
    }
    setBusy(true);
    try {
      const b64 = await fileToBase64(file);
      await apiClient.enrollVoiceProfile({
        name: cleanedName,
        role: newRole,
        audio_base64: b64,
        mime_type: file.type || "audio/wav",
      });
      setNewName("");
      await qc.invalidateQueries({ queryKey: ["voice_profiles"] });
      toast.ok(`Voix de ${cleanedName} importée et enregistrée.`);
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const cfg = profilesQuery.data;
  const profiles = cfg?.profiles ?? [];
  const enabled = cfg?.enabled ?? false;
  const threshold = cfg?.threshold ?? 0.48;

  return (
    <div className="stack">
      <div className="card card--flush">
        <div className="setting">
          <div className="setting__text">
            <div className="setting__label">Filtrage par reconnaissance vocale</div>
            <div className="setting__desc">
              {profiles.length === 0
                ? "Enregistrez au moins une voix ci-dessous pour que Jarvis ignore les autres personnes et les bruits externes."
                : enabled
                  ? `Actif : seules les ${profiles.length} voix autorisées déclenchent la transcription.`
                  : "Désactivé : Jarvis écoute toutes les voix captées par le micro."}
            </div>
          </div>
          <div className="setting__control">
            <Segmented<"on" | "off">
              label="Filtrage vocal"
              value={enabled ? "on" : "off"}
              onChange={(next) => {
                const wantEnabled = next === "on";
                if (wantEnabled && profiles.length === 0) {
                  toast.error("Ajoutez d'abord au moins une voix ci-dessous.");
                  return;
                }
                void apiClient
                  .updateVoiceProfileSettings({ enabled: wantEnabled })
                  .then(() => qc.invalidateQueries({ queryKey: ["voice_profiles"] }))
                  .then(() =>
                    toast.ok(
                      wantEnabled
                        ? "Filtrage vocal activé."
                        : "Filtrage vocal désactivé."
                    )
                  )
                  .catch((err) => toast.error(errorMessage(err)));
              }}
              options={[
                { value: "on", label: "Activé" },
                { value: "off", label: "Désactivé" },
              ]}
            />
          </div>
        </div>

        <div className="setting" style={{ borderTop: "1px solid var(--line)" }}>
          <div className="setting__text">
            <div className="setting__label">
              Seuil de tolérance ({Math.round(threshold * 100)} %)
            </div>
            <div className="setting__desc">
              Plus le seuil est élevé, plus Jarvis est strict (moins de faux positifs, mais exige une voix nette).
            </div>
          </div>
          <div className="setting__control stack" style={{ alignItems: "flex-end", gap: "0.45rem" }}>
            <div className="cluster">
              {THRESHOLD_PRESETS.map((preset) => (
                <button
                  key={preset.value}
                  type="button"
                  className={
                    Math.abs(threshold - preset.value) < 0.025 ? "btn btn--primary" : "btn"
                  }
                  onClick={() => {
                    void apiClient
                      .updateVoiceProfileSettings({ threshold: preset.value })
                      .then(() => qc.invalidateQueries({ queryKey: ["voice_profiles"] }))
                      .catch((err) => toast.error(errorMessage(err)));
                  }}
                >
                  {preset.label}
                </button>
              ))}
            </div>
            <input
              type="range"
              min={0.3}
              max={0.75}
              step={0.02}
              value={threshold}
              aria-label="Seuil de similarité vocale"
              style={{ width: "210px" }}
              onChange={(e) => {
                const val = Number(e.target.value);
                void apiClient
                  .updateVoiceProfileSettings({ threshold: val })
                  .then(() => qc.invalidateQueries({ queryKey: ["voice_profiles"] }))
                  .catch((err) => toast.error(errorMessage(err)));
              }}
            />
          </div>
        </div>
      </div>

      {/* Ajout d'une nouvelle voix */}
      <div className="card stack">
        <div className="setting__text">
          <div className="setting__label">Ajouter une voix reconnue</div>
          <div className="setting__desc">
            Cliquez sur « Enregistrer au micro » puis lisez la phrase ci-dessous à voix normale (5 à 10 secondes).
          </div>
        </div>

        <div
          style={{
            padding: "0.75rem 0.9rem",
            borderRadius: "var(--radius-sm)",
            border: "1px solid var(--line)",
            background: "var(--surface-2, rgba(255,255,255,0.03))",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "0.75rem",
            flexWrap: "wrap",
          }}
        >
          <div style={{ flex: "1 1 260px" }}>
            <div
              style={{
                fontSize: "0.75rem",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                opacity: 0.7,
                marginBottom: "0.25rem",
              }}
            >
              {recordTarget?.kind === "test" ? "Phrase de test à lire" : "Phrase à lire à voix haute"}
            </div>
            <div style={{ fontSize: "0.98rem", fontWeight: 500, lineHeight: 1.45 }}>
              « {recordTarget?.kind === "test" ? TEST_READING_PROMPT : READING_PROMPTS[promptIndex]} »
            </div>
          </div>
          {recordTarget?.kind !== "test" && (
            <button
              type="button"
              className="btn"
              onClick={() => setPromptIndex((idx) => (idx + 1) % READING_PROMPTS.length)}
            >
              <Icon name="refresh" size={14} /> Autre phrase
            </button>
          )}
        </div>

        <div className="cluster" style={{ alignItems: "center" }}>
          <input
            type="text"
            className="input"
            style={{ flex: "1 1 180px" }}
            placeholder="Prénom (ex : Tom, Léa, Alex)"
            aria-label="Nom de la personne"
            value={newName}
            disabled={Boolean(recordTarget) || busy}
            onChange={(e) => setNewName(e.target.value)}
          />
          <select
            className="input"
            aria-label="Rôle de la personne"
            style={{ width: "auto" }}
            value={newRole}
            disabled={Boolean(recordTarget) || busy}
            onChange={(e) => setNewRole(e.target.value)}
          >
            {ROLE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>

          {recordTarget?.kind === "enroll" ? (
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => stopRecording(false)}
            >
              <Icon name="stop" size={14} /> Terminer ({elapsedSec}s)
            </button>
          ) : (
            <button
              type="button"
              className="btn btn--primary"
              disabled={Boolean(recordTarget) || busy}
              onClick={() => {
                void startRecording({ kind: "enroll", name: newName.trim(), role: newRole });
              }}
            >
              <Icon name="mic" size={14} /> Enregistrer au micro
            </button>
          )}

          <input
            ref={fileInputRef}
            type="file"
            accept="audio/*"
            style={{ display: "none" }}
            onChange={(e) => void handleUploadFile(e)}
          />
          <button
            type="button"
            className="btn"
            disabled={Boolean(recordTarget) || busy}
            onClick={() => fileInputRef.current?.click()}
          >
            Importer un audio
          </button>
        </div>

        {recordTarget && (
          <div
            className="cluster"
            style={{
              alignItems: "center",
              justifyContent: "space-between",
              padding: "0.55rem 0.75rem",
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--accent-line, var(--line))",
              background: "var(--surface-2, rgba(255,255,255,0.03))",
            }}
          >
            <div className="cluster" style={{ alignItems: "center", flex: 1 }}>
              <Icon name="mic" size={15} />
              <span style={{ fontSize: "0.9rem" }}>
                {recordTarget.kind === "enroll"
                  ? `Enregistrement de ${recordTarget.name}… (${elapsedSec}/${MAX_RECORD_SEC} s)`
                  : recordTarget.kind === "sample"
                    ? `Nouvel échantillon pour ${recordTarget.profileName}… (${elapsedSec}/${MAX_RECORD_SEC} s)`
                    : `Test vocal en cours… (${elapsedSec}/${MAX_RECORD_SEC} s)`}
              </span>
              <div
                style={{
                  flex: 1,
                  maxWidth: "160px",
                  height: "6px",
                  borderRadius: "999px",
                  background: "var(--line)",
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: `${Math.round(micLevel * 100)}%`,
                    height: "100%",
                    background: "var(--accent)",
                    transition: "width 80ms linear",
                  }}
                />
              </div>
            </div>
            <div className="cluster">
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => stopRecording(false)}
              >
                <Icon name="check" size={14} /> Valider
              </button>
              <button type="button" className="btn" onClick={() => stopRecording(true)}>
                Annuler
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Liste des voix enregistrées + test rapide */}
      {profiles.length > 0 && (
        <div className="card card--flush">
          {profiles.map((profile, idx) => (
            <div
              key={profile.id}
              className="setting"
              style={idx > 0 ? { borderTop: "1px solid var(--line)" } : undefined}
            >
              <div className="setting__text">
                <div className="cluster" style={{ alignItems: "center", gap: "0.5rem" }}>
                  <span className="setting__label">{profile.name}</span>
                  <span className="badge">{profile.role}</span>
                </div>
                <div className="setting__desc">
                  {profile.sample_count} échantillon{profile.sample_count > 1 ? "s" : ""} ·{" "}
                  {profile.duration_sec.toFixed(1)} s de parole analysée
                </div>
              </div>
              <div className="setting__control cluster">
                {recordTarget?.kind === "sample" && recordTarget.profileId === profile.id ? (
                  <button
                    type="button"
                    className="btn btn--primary"
                    onClick={() => stopRecording(false)}
                  >
                    <Icon name="stop" size={14} /> Terminer ({elapsedSec}s)
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn"
                    disabled={Boolean(recordTarget) || busy}
                    title="Améliorer la précision dans une autre pièce ou à une autre distance"
                    onClick={() => {
                      void startRecording({
                        kind: "sample",
                        profileId: profile.id,
                        profileName: profile.name,
                      });
                    }}
                  >
                    <Icon name="plus" size={14} /> Ajouter un échantillon
                  </button>
                )}
                <button
                  type="button"
                  className="btn"
                  disabled={Boolean(recordTarget) || busy}
                  aria-label={`Supprimer la voix de ${profile.name}`}
                  onClick={() => {
                    void apiClient
                      .deleteVoiceProfile(profile.id)
                      .then(() => qc.invalidateQueries({ queryKey: ["voice_profiles"] }))
                      .then(() => toast.ok(`Voix de ${profile.name} supprimée.`))
                      .catch((err) => toast.error(errorMessage(err)));
                  }}
                >
                  <Icon name="trash" size={14} />
                </button>
              </div>
            </div>
          ))}

          <div
            className="setting"
            style={{ borderTop: "1px solid var(--line)", alignItems: "center" }}
          >
            <div className="setting__text">
              <div className="setting__label">Tester la reconnaissance</div>
              <div className="setting__desc">
                {testResult
                  ? testResult.accepted
                    ? `Reconnu : ${testResult.matched_name} (similarité ${Math.round(testResult.score * 100)} % ≥ seuil ${Math.round(testResult.threshold * 100)} %)`
                    : `Voix ignorée (meilleur score : ${Math.round(testResult.score * 100)} % < seuil ${Math.round(testResult.threshold * 100)} %)`
                  : "Vérifiez en 2 secondes si votre voix ou celle d'un invité est bien reconnue."}
              </div>
            </div>
            <div className="setting__control">
              {recordTarget?.kind === "test" ? (
                <button
                  type="button"
                  className="btn btn--primary"
                  onClick={() => stopRecording(false)}
                >
                  <Icon name="stop" size={14} /> Analyser ({elapsedSec}s)
                </button>
              ) : (
                <button
                  type="button"
                  className="btn"
                  disabled={Boolean(recordTarget) || busy}
                  onClick={() => {
                    void startRecording({ kind: "test" });
                  }}
                >
                  <Icon name="shield" size={14} /> Tester une voix
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
