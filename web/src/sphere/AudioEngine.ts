import type { AudioMetrics } from "../store/useOmiStore";

/** Seuil plancher de barge-in : en dessous, c'est du souffle ou une fuite faible. */
export const BARGE_IN_BASE_THRESHOLD = 0.04;
/** Part du niveau de lecture TTS qu'un vrai utilisateur doit depasser pour couper la parole. */
export const BARGE_IN_ECHO_RATIO = 0.55;
/** Duree de parole continue exigee avant de couper (evite les clics et la reverberation). */
export const BARGE_IN_MIN_MS = 400;
/** Fenetre de grace apres le demarrage du TTS (convergence de l'annulation d'echo). */
export const BARGE_IN_GRACE_MS = 350;

/**
 * Seuil de detection adapte au volume effectivement joue : sur haut-parleur, la fuite
 * acoustique croit avec le volume, donc un seuil fixe finit toujours par etre franchi.
 */
export function bargeInThreshold(playbackLevel: number): number {
  return Math.max(BARGE_IN_BASE_THRESHOLD, playbackLevel * BARGE_IN_ECHO_RATIO);
}

/** RMS normalise d'un bloc PCM (0..1). */
export function computeRms(samples: Float32Array): number {
  const n = samples.length;
  if (n === 0) return 0;
  let sumSq = 0;
  for (let i = 0; i < n; i += 1) {
    const v = samples[i];
    sumSq += v * v;
  }
  return Math.min(1, Math.sqrt(sumSq / n));
}

/** Tokens comparables : minuscules, sans accents ni ponctuation, pluriels simples neutralises. */
export function normalizeSpeechTokens(text: string): string[] {
  return (text || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .split(" ")
    .filter((token) => token.length > 1)
    .map((token) => (token.length > 3 ? token.replace(/[sx]$/, "") : token));
}

/**
 * Detecte si une transcription n'est que l'echo de la voix de Jarvis.
 * La reconnaissance vocale du navigateur n'utilise pas la meme annulation d'echo que
 * getUserMedia : sur haut-parleur, elle transcrit Jarvis lui-meme.
 */
export function isSelfEcho(spokenText: string, transcript: string): boolean {
  const spoken = new Set(normalizeSpeechTokens(spokenText));
  const heard = normalizeSpeechTokens(transcript);
  if (spoken.size === 0 || heard.length === 0) return false;
  let hits = 0;
  for (const token of heard) {
    if (spoken.has(token)) hits += 1;
  }
  return hits / heard.length >= 0.7;
}

export function computeFrequencyBands(bins: Uint8Array): AudioMetrics {
  if (!bins || bins.length === 0) {
    return { amplitude: 0, low: 0, mid: 0, high: 0 };
  }
  const len = bins.length;
  const lowEnd = Math.max(1, Math.floor(len * 0.12));
  const midEnd = Math.max(lowEnd + 1, Math.floor(len * 0.45));

  const avg = (start: number, end: number): number => {
    let sum = 0;
    const count = Math.max(1, end - start);
    for (let i = start; i < end; i += 1) {
      sum += bins[i] / 255;
    }
    return Math.min(1, sum / count);
  };

  const low = avg(0, lowEnd);
  const mid = avg(lowEnd, midEnd);
  const high = avg(midEnd, len);
  const amplitude = Math.min(1, low * 0.45 + mid * 0.35 + high * 0.2);

  return { amplitude, low, mid, high };
}

export function smoothAmplitude(
  prev: number,
  target: number,
  dtMs: number,
  attackMs = 30,
  releaseMs = 150
): number {
  const safeDt = Math.max(0, dtMs);
  const tau = target >= prev ? attackMs : releaseMs;
  if (tau <= 0) return target;
  const alpha = 1 - Math.exp(-safeDt / tau);
  return prev + (target - prev) * alpha;
}

export function decodeBase64Pcm16(base64: string): Float32Array {
  const binary = atob(base64);
  const byteLength = binary.length;
  const samples = Math.floor(byteLength / 2);
  const out = new Float32Array(samples);
  for (let i = 0; i < samples; i += 1) {
    const lo = binary.charCodeAt(i * 2);
    const hi = binary.charCodeAt(i * 2 + 1);
    let val = (hi << 8) | lo;
    if (val >= 0x8000) val -= 0x10000;
    out[i] = val / 32768;
  }
  return out;
}

export function encodeWavPcm16(
  samples: Float32Array,
  inputSampleRate: number,
  targetSampleRate = 16000
): string {
  const safeInRate = Math.max(1, inputSampleRate);
  const ratio = safeInRate / targetSampleRate;
  const outLength = Math.max(1, Math.floor(samples.length / ratio));
  const buffer = new ArrayBuffer(44 + outLength * 2);
  const view = new DataView(buffer);

  const writeStr = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i += 1) {
      view.setUint8(offset + i, str.charCodeAt(i));
    }
  };

  writeStr(0, "RIFF");
  view.setUint32(4, 36 + outLength * 2, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, targetSampleRate, true);
  view.setUint32(28, targetSampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, "data");
  view.setUint32(40, outLength * 2, true);

  let offset = 44;
  for (let i = 0; i < outLength; i += 1) {
    const srcIdx = Math.min(samples.length - 1, Math.floor(i * ratio));
    const clamped = Math.max(-1, Math.min(1, samples[srcIdx] ?? 0));
    view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    offset += 2;
  }

  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const slice = bytes.subarray(i, i + chunkSize);
    binary += String.fromCharCode(...slice);
  }
  return btoa(binary);
}

export interface MicrophoneCallbacks {
  onUtterance?: (wavBase64: string, mimeType: string) => void;
  onSpeechChange?: (speaking: boolean) => void;
  onLiveTranscript?: (text: string, isFinal: boolean) => void;
  onBargeIn?: () => void;
}

export class AudioEngine {
  private ctx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private bins: Uint8Array<ArrayBuffer> | null = null;
  private micStream: MediaStream | null = null;
  private micSource: MediaStreamAudioSourceNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private silentGain: GainNode | null = null;
  private vadFlush: ((minSpeechMs: number) => void) | null = null;
  private activeSources: Set<AudioBufferSourceNode> = new Set();
  private nextStartTime = 0;
  private synthSpeaking = false;
  private speechPhase = 0;
  private speakAbort: AbortController | null = null;
  private speechRecognizer: any = null;
  private smoothed: AudioMetrics = { amplitude: 0, low: 0, mid: 0, high: 0 };
  /** Niveau RMS du dernier fragment TTS joue : sert de reference pour le seuil de barge-in. */
  private playbackLevel = 0;
  /** Instant (horloge AudioContext) du demarrage de la lecture en cours. */
  private playbackStartedAt = 0;
  /** Texte que Jarvis est en train de lire, pour reconnaitre sa propre voix dans les transcriptions. */
  private spokenText = "";
  public onPlaybackEnded?: () => void;

  /** Renseigne le texte lu en cours (utile quand le TTS arrive en flux, sans passer par speakText). */
  setSpokenText(text: string): void {
    this.spokenText = text || "";
  }

  /** Indique si la fenetre de grace apres le demarrage du TTS est passee. */
  private isBargeInAllowed(): boolean {
    if (!this.ctx) return true;
    return this.ctx.currentTime - this.playbackStartedAt >= BARGE_IN_GRACE_MS / 1000;
  }

  /** Fait decroitre la reference de niveau quand plus aucun fragment n'est programme. */
  private decayPlaybackLevel(chunkMs: number): void {
    if (!this.ctx) return;
    if (this.ctx.currentTime > this.nextStartTime + 0.2) {
      this.playbackLevel *= Math.exp(-chunkMs / 250);
      if (this.playbackLevel < 0.001) this.playbackLevel = 0;
    }
  }

  unlock(): void {
    this.ensureContext();
  }

  private ensureContext(): AudioContext | null {
    if (typeof window === "undefined") return null;
    const AudioCtx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtx) return null;
    if (!this.ctx) {
      this.ctx = new AudioCtx();
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 256;
      // Ne pas connecter analyser -> destination pour eviter tout retour micro vers les haut-parleurs.
      this.bins = new Uint8Array(new ArrayBuffer(this.analyser.frequencyBinCount));
      this.nextStartTime = this.ctx.currentTime;
    }
    if (this.ctx.state === "suspended") {
      this.ctx.resume().catch(() => undefined);
    }
    return this.ctx;
  }

  playTtsChunk(base64Audio: string, sampleRate = 16000): void {
    const ctx = this.ensureContext();
    if (!ctx || !this.analyser || !base64Audio) return;

    try {
      const pcm = decodeBase64Pcm16(base64Audio);
      if (pcm.length === 0) return;
      const buffer = ctx.createBuffer(1, pcm.length, sampleRate);
      buffer.getChannelData(0).set(pcm);

      // Reference de volume : sur haut-parleur, la fuite micro suit le niveau joue.
      const level = computeRms(pcm);
      if (level > 0) {
        this.playbackLevel = Math.max(level, this.playbackLevel * 0.5);
      }

      const src = ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(this.analyser);
      src.connect(ctx.destination);

      const now = ctx.currentTime;
      const startAt = Math.max(now, this.nextStartTime);
      if (this.activeSources.size === 0) {
        this.playbackStartedAt = startAt;
      }
      src.start(startAt);
      this.nextStartTime = startAt + buffer.duration;
      this.activeSources.add(src);
      src.onended = () => {
        this.activeSources.delete(src);
        if (this.activeSources.size === 0) {
          this.onPlaybackEnded?.();
        }
      };
    } catch {
      // Ignorer un fragment audio invalide
    }
  }

  speakText(
    text: string,
    fetchTts?: (text: string) => Promise<{ audio_base64: string; sample_rate?: number }>
  ): void {
    const cleaned = text
      .replace(/<untrusted[^>]*>[\s\S]*?<\/untrusted>/g, "")
      .replace(/```[\s\S]*?```/g, "")
      .replace(/[*_#~>`|]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (!cleaned || !fetchTts) return;

    this.stopPlayback();
    this.ensureContext();
    this.spokenText = cleaned;

    const controller = new AbortController();
    this.speakAbort = controller;
    fetchTts(cleaned)
      .then((res) => {
        if (controller.signal.aborted) return;
        if (res?.audio_base64) {
          this.playTtsChunk(res.audio_base64, res.sample_rate ?? 16000);
        }
      })
      .catch(() => undefined);
  }

  async startMicrophone(callbacks?: MicrophoneCallbacks): Promise<boolean> {
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      return false;
    }
    this.stopMicrophone();
    const ctx = this.ensureContext();
    if (!ctx || !this.analyser) return false;
    try {
      this.micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      this.micSource = ctx.createMediaStreamSource(this.micStream);
      this.micSource.connect(this.analyser);

      if (typeof window !== "undefined") {
        const SpeechRecognition =
          (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
        if (SpeechRecognition) {
          try {
            let currentUtteranceStartIndex = 0;
            const rec = new SpeechRecognition();
            rec.continuous = true;
            rec.interimResults = true;
            rec.lang = "fr-FR";
            rec.onresult = (ev: any) => {
              let full = "";
              let isFinal = false;
              for (let i = currentUtteranceStartIndex; i < ev.results.length; i += 1) {
                const res = ev.results[i];
                if (res[0]?.transcript) {
                  full += (full ? " " : "") + res[0].transcript;
                  if (res.isFinal) isFinal = true;
                }
              }
              const trimmed = full.trim();

              if (this.isPlaying()) {
                // Sur haut-parleur, cette API transcrit Jarvis lui-meme : on ignore tout ce qui
                // ressemble au texte lu, ainsi que la fenetre de convergence de l'annulation d'echo.
                if (!trimmed || !this.isBargeInAllowed() || isSelfEcho(this.spokenText, trimmed)) {
                  return;
                }
                this.stopPlayback();
                callbacks?.onBargeIn?.();
                currentUtteranceStartIndex = ev.results.length;
                try {
                  rec.abort();
                } catch {
                  // ignore
                }
                return;
              }
              if (trimmed) {
                callbacks?.onLiveTranscript?.(trimmed, isFinal);
              }
            };
            rec.onerror = () => undefined;
            rec.onend = () => {
              currentUtteranceStartIndex = 0;
              if (this.micStream && this.speechRecognizer === rec) {
                try {
                  rec.start();
                } catch {
                  // ignore
                }
              }
            };
            rec.start();
            this.speechRecognizer = rec;
          } catch {
            // ignore
          }
        }
      }

      if (typeof ctx.createScriptProcessor === "function") {
        const processor = ctx.createScriptProcessor(4096, 1, 1);
        const silentGain = ctx.createGain();
        silentGain.gain.value = 0;

        let preRollChunks: Float32Array[] = [];
        let recordedChunks: Float32Array[] = [];
        let totalRecordedMs = 0;
        let inSpeech = false;
        let silenceMs = 0;
        let bargeInMs = 0;
        let bargeInPreroll: Float32Array[] = [];

        const finalizeUtterance = (minSpeechMs = 150) => {
          if (this.speechRecognizer) {
            try {
              this.speechRecognizer.abort();
            } catch {
              // ignore
            }
          }
          const chunks = recordedChunks;
          const totalMs = totalRecordedMs;
          recordedChunks = [];
          totalRecordedMs = 0;
          preRollChunks = [];
          bargeInMs = 0;
          bargeInPreroll = [];
          if (inSpeech) {
            inSpeech = false;
            callbacks?.onSpeechChange?.(false);
          }
          if (totalMs < minSpeechMs || chunks.length === 0) return;

          const totalSamples = chunks.reduce((acc, c) => acc + c.length, 0);
          const merged = new Float32Array(totalSamples);
          let pos = 0;
          for (const chunk of chunks) {
            merged.set(chunk, pos);
            pos += chunk.length;
          }
          const wavBase64 = encodeWavPcm16(merged, ctx.sampleRate, 16000);
          callbacks?.onUtterance?.(wavBase64, "audio/wav");
        };

        this.vadFlush = finalizeUtterance;

        processor.onaudioprocess = (ev: AudioProcessingEvent) => {
          const input = ev.inputBuffer.getChannelData(0);
          const rms = computeRms(input);
          const copy = new Float32Array(input);
          const chunkMs = (copy.length / ctx.sampleRate) * 1000;

          if (this.isPlaying()) {
            this.decayPlaybackLevel(chunkMs);
            // Seuil relatif au volume joue : une fuite acoustique ne doit jamais suffire.
            const gate = bargeInThreshold(this.playbackLevel);
            if (this.isBargeInAllowed() && rms >= gate) {
              bargeInMs += chunkMs;
              bargeInPreroll.push(copy);
              if (bargeInPreroll.length > 8) {
                bargeInPreroll.shift();
              }
              if (bargeInMs >= BARGE_IN_MIN_MS) {
                this.stopPlayback();
                callbacks?.onBargeIn?.();
                if (this.speechRecognizer) {
                  try {
                    this.speechRecognizer.abort();
                  } catch {
                    // ignore
                  }
                }
                preRollChunks = [];
                recordedChunks = [...bargeInPreroll];
                totalRecordedMs = bargeInMs;
                inSpeech = true;
                silenceMs = 0;
                bargeInMs = 0;
                bargeInPreroll = [];
                callbacks?.onSpeechChange?.(true);
              }
            } else {
              bargeInMs = 0;
              bargeInPreroll = [];
            }
            return;
          }
          bargeInMs = 0;
          bargeInPreroll = [];

          if (!inSpeech) {
            preRollChunks.push(copy);
            if (preRollChunks.length > 4) {
              preRollChunks.shift();
            }
          } else if (totalRecordedMs < 30000) {
            recordedChunks.push(copy);
            totalRecordedMs += chunkMs;
          }

          if (rms >= 0.008) {
            if (!inSpeech) {
              inSpeech = true;
              callbacks?.onSpeechChange?.(true);
              recordedChunks = [...preRollChunks, copy];
              preRollChunks = [];
              totalRecordedMs = recordedChunks.reduce(
                (acc, c) => acc + (c.length / ctx.sampleRate) * 1000,
                0
              );
            }
            silenceMs = 0;
          } else if (inSpeech) {
            silenceMs += chunkMs;
            if (silenceMs >= 1600) {
              finalizeUtterance(300);
            }
          }
        };

        this.micSource.connect(processor);
        processor.connect(silentGain);
        silentGain.connect(ctx.destination);
        this.processor = processor;
        this.silentGain = silentGain;
      }

      return true;
    } catch {
      this.stopMicrophone();
      return false;
    }
  }

  stopMicrophone(): void {
    if (this.speechRecognizer) {
      const rec = this.speechRecognizer;
      this.speechRecognizer = null;
      try {
        rec.onend = null;
        rec.stop();
      } catch {
        // ignore
      }
    }
    const flush = this.vadFlush;
    this.vadFlush = null;
    if (flush) {
      flush(250);
    }
    if (this.processor) {
      this.processor.onaudioprocess = null;
      this.processor.disconnect();
      this.processor = null;
    }
    if (this.silentGain) {
      this.silentGain.disconnect();
      this.silentGain = null;
    }
    this.micSource?.disconnect();
    this.micSource = null;
    if (this.micStream) {
      for (const track of this.micStream.getTracks()) {
        track.stop();
      }
      this.micStream = null;
    }
  }

  isPlaying(): boolean {
    return this.activeSources.size > 0 || this.synthSpeaking;
  }

  stopPlayback(): void {
    if (this.speakAbort) {
      this.speakAbort.abort();
      this.speakAbort = null;
    }
    this.synthSpeaking = false;
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      try {
        window.speechSynthesis.cancel();
      } catch {
        // ignore
      }
    }
    for (const src of this.activeSources) {
      try {
        src.stop();
      } catch {
        // already stopped
      }
    }
    this.activeSources.clear();
    this.playbackLevel = 0;
    this.spokenText = "";
    if (this.ctx) {
      this.nextStartTime = this.ctx.currentTime;
    }
    this.smoothed = { amplitude: 0, low: 0, mid: 0, high: 0 };
  }

  sample(dtMs: number, externalMicLevel = 0): AudioMetrics {
    let raw: AudioMetrics = { amplitude: 0, low: 0, mid: 0, high: 0 };
    if (this.analyser && this.bins) {
      this.analyser.getByteFrequencyData(this.bins);
      raw = computeFrequencyBands(this.bins);
    }
    if (this.synthSpeaking && raw.amplitude < 0.05) {
      this.speechPhase += dtMs * 0.012;
      const pulse = 0.28 + 0.18 * Math.abs(Math.sin(this.speechPhase));
      raw = {
        amplitude: pulse,
        low: pulse * 0.85,
        mid: pulse * 0.7,
        high: pulse * 0.45,
      };
    }
    if (externalMicLevel > raw.amplitude) {
      raw = {
        amplitude: Math.min(1, externalMicLevel),
        low: Math.max(raw.low, externalMicLevel * 0.8),
        mid: Math.max(raw.mid, externalMicLevel * 0.6),
        high: Math.max(raw.high, externalMicLevel * 0.4),
      };
    }

    this.smoothed = {
      amplitude: smoothAmplitude(this.smoothed.amplitude, raw.amplitude, dtMs, 30, 150),
      low: smoothAmplitude(this.smoothed.low, raw.low, dtMs, 30, 150),
      mid: smoothAmplitude(this.smoothed.mid, raw.mid, dtMs, 30, 150),
      high: smoothAmplitude(this.smoothed.high, raw.high, dtMs, 30, 150),
    };
    return this.smoothed;
  }
}
