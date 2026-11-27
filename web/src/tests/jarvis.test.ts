import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AudioEngine,
  BARGE_IN_BASE_THRESHOLD,
  bargeInThreshold,
  computeFrequencyBands,
  computeRms,
  decodeBase64Pcm16,
  encodeWavPcm16,
  isSelfEcho,
  smoothAmplitude,
  SPEECH_BASE_THRESHOLD,
  SPEECH_STANDBY_THRESHOLD,
  speechGateThreshold,
  standbySpeechThresholdFromWake,
} from "../sphere/AudioEngine";
import {
  extractWakeWord,
  isStopRecordCommand,
  shouldAutoStopVoiceRecord,
  VOICE_INACTIVITY_TIMEOUT_MS,
} from "../lib/voiceCommands";
import { useJarvisStore } from "../store/useJarvisStore";
import type { SphereState } from "../types/generated";
import { JarvisWsClient } from "../ws/client";

function resetStore() {
  useJarvisStore.setState({
    sphereState: "idle",
    wsConnected: false,
    theme: "amber",
    activeModel: "deepseek/deepseek-v4.1-flash-1017",
    partialTranscript: "",
    finalTranscript: "",
    streamingText: "",
    streamingMessageId: null,
    streamingStepBoundary: false,
    streamingStepCount: 0,
    messages: [],
    pendingTools: {},
    approvals: [],
    alerts: [],
    goals: {},
    liveSubagents: {},
    goalMode: false,
    audio: { amplitude: 0, low: 0, mid: 0, high: 0 },
    micLevel: 0,
    lastError: null,
  });
}

describe("useJarvisStore", () => {
  beforeEach(() => {
    resetStore();
  });

  it("gere les 6 etats de la sphere", () => {
    const states: SphereState[] = ["idle", "listening", "thinking", "speaking", "alert", "offline"];
    for (const value of states) {
      useJarvisStore.getState().handleServerEvent({
        type: "state",
        payload: { value },
      });
      expect(useJarvisStore.getState().sphereState).toBe(value);
    }
  });

  it("attache les tool.call et tool.result au message assistant.done", () => {
    const store = useJarvisStore.getState();

    store.handleServerEvent({
      type: "tool.call",
      payload: {
        id: "tool_1",
        name: "memory_search",
        args: { query: "calendrier" },
        risk: 0,
      },
    });
    store.handleServerEvent({
      type: "tool.result",
      payload: {
        id: "tool_1",
        ok: true,
        summary: "1 evenement trouve.",
      },
    });
    store.handleServerEvent({
      type: "assistant.delta",
      payload: { message_id: "msg_10", text: "Vous avez une reunion " },
    });
    store.handleServerEvent({
      type: "assistant.delta",
      payload: { message_id: "msg_10", text: "a 14h." },
    });
    store.handleServerEvent({
      type: "assistant.done",
      payload: {
        message_id: "msg_10",
        usage: {
          model: "deepseek/deepseek-v4.1-flash-1017",
          tokens_in: 40,
          tokens_out: 12,
          cost: 0.0002,
        },
      },
    });

    const state = useJarvisStore.getState();
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].content).toBe("Vous avez une reunion a 14h.");
    expect(state.messages[0].tools).toHaveLength(1);
    expect(state.messages[0].tools[0].summary).toBe("1 evenement trouve.");
    expect(Object.keys(state.pendingTools)).toHaveLength(0);
  });

  it("remplace l'etape affichee a chaque nouvelle iteration sans additionner les etapes", () => {
    const store = useJarvisStore.getState();

    store.handleServerEvent({ type: "state", payload: { value: "thinking" } });
    store.handleServerEvent({
      type: "assistant.delta",
      payload: { message_id: "msg_steps", text: "Je localise le code du bot." },
    });
    expect(useJarvisStore.getState().streamingText).toBe("Je localise le code du bot.");
    expect(useJarvisStore.getState().streamingStepCount).toBe(1);

    store.handleServerEvent({
      type: "tool.call",
      payload: { id: "tc_1", name: "search_files", args: { query: "bot.py" }, risk: 0 },
    });
    expect(useJarvisStore.getState().streamingText).toBe("Je localise le code du bot.");
    expect(useJarvisStore.getState().streamingStepBoundary).toBe(true);

    store.handleServerEvent({
      type: "tool.result",
      payload: { id: "tc_1", ok: true, summary: "bot.py trouve" },
    });
    store.handleServerEvent({
      type: "assistant.delta",
      payload: { message_id: "msg_steps", text: "Je lis le code du cycle " },
    });
    store.handleServerEvent({
      type: "assistant.delta",
      payload: { message_id: "msg_steps", text: "principal." },
    });
    expect(useJarvisStore.getState().streamingText).toBe("Je lis le code du cycle principal.");
    expect(useJarvisStore.getState().streamingStepCount).toBe(2);

    store.handleServerEvent({
      type: "tool.call",
      payload: { id: "tc_2", name: "read_file", args: { file_path: "bot.py" }, risk: 0 },
    });
    store.handleServerEvent({
      type: "tool.result",
      payload: { id: "tc_2", ok: true, summary: "lu" },
    });
    store.handleServerEvent({
      type: "assistant.delta",
      payload: { message_id: "msg_steps", text: "Diagnostic terminé : le correctif est en place." },
    });
    expect(useJarvisStore.getState().streamingText).toBe(
      "Diagnostic terminé : le correctif est en place."
    );
    expect(useJarvisStore.getState().streamingStepCount).toBe(3);

    store.handleServerEvent({
      type: "assistant.done",
      payload: {
        message_id: "msg_steps",
        usage: {
          model: "z-ai/glm-5.3",
          tokens_in: 120,
          tokens_out: 45,
          cost: 0.001,
        },
      },
    });

    const finalState = useJarvisStore.getState();
    expect(finalState.streamingText).toBe("");
    expect(finalState.streamingMessageId).toBeNull();
    expect(finalState.streamingStepBoundary).toBe(false);
    expect(finalState.streamingStepCount).toBe(0);
    expect(finalState.messages).toHaveLength(1);
    expect(finalState.messages[0].content).toBe(
      "Diagnostic terminé : le correctif est en place."
    );
    expect(finalState.messages[0].tools).toHaveLength(2);
  });

  it("reinitialise les transcriptions vocales des que Jarvis repond", () => {
    const store = useJarvisStore.getState();
    store.handleServerEvent({
      type: "transcript.final",
      payload: { text: "Quelle heure est-il ?", speaker: "user" },
    });
    expect(useJarvisStore.getState().finalTranscript).toBe("Quelle heure est-il ?");

    store.handleServerEvent({
      type: "assistant.delta",
      payload: { message_id: "msg_20", text: "Il est " },
    });
    expect(useJarvisStore.getState().finalTranscript).toBe("");
    expect(useJarvisStore.getState().partialTranscript).toBe("");
  });

  it("ecrase le message et reinitialise le prompt lors d'une interruption", () => {
    const store = useJarvisStore.getState();
    store.setMessages([]);
    store.appendUserMessage("Premier prompt interrompu");
    expect(useJarvisStore.getState().messages).toHaveLength(1);

    store.interruptSpeech();
    expect(useJarvisStore.getState().streamingText).toBe("");
    expect(useJarvisStore.getState().lastInterruptedAt).not.toBeNull();

    store.appendUserMessage("Nouveau prompt qui ecrase", true);
    const msgs = useJarvisStore.getState().messages;
    expect(msgs).toHaveLength(1);
    expect(msgs[0].content).toBe("Nouveau prompt qui ecrase");
  });

  it("gere les demandes d'approbation et l'acquittement d'alertes", () => {
    const store = useJarvisStore.getState();

    store.handleServerEvent({
      type: "approval.request",
      payload: {
        id: "appr_1",
        description: "Redemarrer le service systemd",
        risk: 3,
        expires_at: new Date(Date.now() + 60_000).toISOString(),
      },
    });
    expect(useJarvisStore.getState().approvals[0].status).toBe("pending");

    useJarvisStore.getState().resolveApprovalLocal("appr_1", true);
    expect(useJarvisStore.getState().approvals[0].status).toBe("approved");

    store.handleServerEvent({
      type: "alert",
      payload: {
        id: "alert_1",
        urgency: 4,
        message: "Alerte critique serveur",
        reason: "Charge elevee",
      },
    });
    expect(useJarvisStore.getState().sphereState).toBe("alert");

    useJarvisStore.getState().ackAlertLocal("alert_1", false);
    expect(useJarvisStore.getState().alerts[0].status).toBe("acked");
    expect(useJarvisStore.getState().sphereState).toBe("idle");
  });

  it("met a jour les objectifs, sous-agents et interventions watchdog en temps reel", () => {
    const store = useJarvisStore.getState();
    store.setGoalMode(true);
    expect(useJarvisStore.getState().goalMode).toBe(true);

    store.handleServerEvent({
      type: "goal.updated",
      payload: {
        goal_id: "goal_abc",
        title: "Audit repo",
        status: "running",
        progress_pct: 50,
        current_phase: "Execution parallele",
        cost_usd: 0.012,
        max_cost_usd: 1.5,
        orchestrator_model: "gemini-4-argon",
        pause_reason: null,
        final_summary: "",
      },
    });
    store.handleServerEvent({
      type: "goal.subagent",
      payload: {
        goal_id: "goal_abc",
        subtask_id: "s1",
        run_id: "run_1",
        title: "Analyse",
        model: "deepseek/deepseek-v4.1-flash-1017",
        role_profile: "coder",
        status: "running",
        iterations: 3,
        max_iterations: 20,
        cost_usd: 0.002,
        last_tool: "read_file",
        error: null,
      },
    });
    store.handleServerEvent({
      type: "goal.intervention",
      payload: {
        goal_id: "goal_abc",
        subtask_id: "s1",
        run_id: "run_1",
        kind: "switch_model",
        message: "Relance sur gemini-4-argon",
        ts: "2026-11-16T01:00:00Z",
      },
    });

    const state = useJarvisStore.getState();
    expect(state.goals["goal_abc"]?.progress_pct).toBe(50);
    expect(state.liveSubagents["run_1"]?.model).toBe("deepseek/deepseek-v4.1-flash-1017");
    expect(state.goals["goal_abc"]?.events).toHaveLength(1);
    expect(state.goals["goal_abc"]?.events[0].message).toContain("gemini-4-argon");
  });
});

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;

  constructor(public url: string) {
    MockWebSocket.instances.push(this);
  }

  open() {
    this.readyState = WebSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  emitMessage(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }

  send(payload: string) {
    this.sent.push(payload);
  }

  close() {
    this.readyState = 3;
    this.onclose?.({} as CloseEvent);
  }
}

describe("JarvisWsClient", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("met en file hors ligne et vide la file a l'ouverture", () => {
    const events: string[] = [];
    const client = new JarvisWsClient({
      url: "ws://localhost:8765/ws",
      token: "secret-tok",
      onEvent: (ev) => events.push(ev.type),
      WebSocketImpl: MockWebSocket as unknown as typeof WebSocket,
    });

    const sentNow = client.send({ type: "user.text", payload: { text: "bonjour" } });
    expect(sentNow).toBe(false);
    expect(client.queuedCount()).toBe(1);

    client.connect();
    const sock = MockWebSocket.instances[0];
    expect(sock.url).toContain("token=secret-tok");

    sock.open();
    expect(client.queuedCount()).toBe(0);
    expect(sock.sent).toHaveLength(1);
    expect(JSON.parse(sock.sent[0])).toEqual({
      type: "user.text",
      payload: { text: "bonjour" },
    });
  });

  it("reprogramme la reconnexion exponentielle et transmet tts.chunk", () => {
    const ttsCalls: Array<{ audio: string; seq: number; isLast: boolean }> = [];
    const onConn = vi.fn();
    const client = new JarvisWsClient({
      url: "ws://localhost:8765/ws",
      baseDelayMs: 500,
      maxDelayMs: 4000,
      onEvent: () => undefined,
      onConnectionChange: onConn,
      onTtsChunk: (audio, seq, isLast) => ttsCalls.push({ audio, seq, isLast }),
      WebSocketImpl: MockWebSocket as unknown as typeof WebSocket,
    });

    client.connect();
    const sock1 = MockWebSocket.instances[0];
    sock1.open();
    expect(onConn).toHaveBeenLastCalledWith(true);

    sock1.emitMessage({
      type: "tts.chunk",
      payload: { audio: "AAAA", seq: 2, is_last: true },
    });
    expect(ttsCalls).toEqual([{ audio: "AAAA", seq: 2, isLast: true }]);

    sock1.close();
    expect(onConn).toHaveBeenLastCalledWith(false);
    expect(MockWebSocket.instances).toHaveLength(1);

    vi.advanceTimersByTime(500);
    expect(MockWebSocket.instances).toHaveLength(2);

    const sock2 = MockWebSocket.instances[1];
    sock2.close();
    vi.advanceTimersByTime(999);
    expect(MockWebSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(MockWebSocket.instances).toHaveLength(3);

    client.disconnect();
  });

  it("ne reconnexionne pas sur erreur d'authentification 4401 et borne la file hors ligne", () => {
    const client = new JarvisWsClient({
      url: "ws://localhost:8765/ws",
      baseDelayMs: 100,
      maxDelayMs: 1000,
      onEvent: () => undefined,
      WebSocketImpl: MockWebSocket as unknown as typeof WebSocket,
    });
    for (let i = 0; i < 60; i += 1) {
      client.send({ type: "user.text", payload: { text: `msg-${i}` } });
    }
    expect(client.queuedCount()).toBe(50);

    client.connect();
    const sock = MockWebSocket.instances[0];
    sock.readyState = 3;
    sock.onclose?.({ code: 4401 } as CloseEvent);
    vi.advanceTimersByTime(5000);
    expect(MockWebSocket.instances).toHaveLength(1);
  });
});

describe("AudioEngine helpers", () => {
  it("calcule les bandes grave/medium/aigu normalisees", () => {
    const bins = new Uint8Array(128);
    bins.fill(255, 0, 15);
    bins.fill(128, 15, 57);
    bins.fill(64, 57, 128);

    const bands = computeFrequencyBands(bins);
    expect(bands.low).toBeGreaterThan(0.95);
    expect(bands.mid).toBeCloseTo(128 / 255, 1);
    expect(bands.high).toBeCloseTo(64 / 255, 1);
    expect(bands.amplitude).toBeGreaterThan(0.5);
    expect(bands.amplitude).toBeLessThanOrEqual(1);
  });

  it("applique l'attaque 30ms plus rapide que le relachement 150ms", () => {
    const afterAttack = smoothAmplitude(0, 1, 30, 30, 150);
    const afterRelease = smoothAmplitude(1, 0, 30, 30, 150);
    const attackDelta = afterAttack;
    const releaseDelta = 1 - afterRelease;

    expect(attackDelta).toBeCloseTo(1 - Math.exp(-1), 4);
    expect(releaseDelta).toBeCloseTo(1 - Math.exp(-30 / 150), 4);
    expect(attackDelta).toBeGreaterThan(releaseDelta);
  });

  it("decode un buffer base64 PCM16 little-endian signe", () => {
    const raw = new Uint8Array([0x00, 0x40, 0x00, 0xc0]);
    const b64 = btoa(String.fromCharCode(...raw));
    const samples = decodeBase64Pcm16(b64);

    expect(samples).toHaveLength(2);
    expect(samples[0]).toBeCloseTo(0.5, 4);
    expect(samples[1]).toBeCloseTo(-0.5, 4);
  });

  it("encode un buffer Float32 en WAV PCM16 base64 avec en-tete RIFF", () => {
    const input = new Float32Array([0, 0.5, -0.5, 0.25, -0.25, 0]);
    const wavB64 = encodeWavPcm16(input, 48000, 16000);
    const decoded = atob(wavB64);

    expect(decoded.slice(0, 4)).toBe("RIFF");
    expect(decoded.slice(8, 12)).toBe("WAVE");
    expect(decoded.slice(12, 16)).toBe("fmt ");
    expect(decoded.slice(36, 40)).toBe("data");
  });

  it("synthetise et lit le texte de reponse avec speakText", async () => {
    const engine = new AudioEngine();
    const playSpy = vi.spyOn(engine, "playTtsChunk").mockImplementation(() => undefined);
    const fetchTts = vi.fn().mockResolvedValue({ audio_base64: "AAAA", sample_rate: 16000 });

    engine.speakText("Bonjour **Tom**, voici la réponse.", fetchTts);
    await Promise.resolve();

    expect(fetchTts).toHaveBeenCalledWith("Bonjour Tom , voici la réponse.");
    expect(playSpy).toHaveBeenCalledWith("AAAA", 16000);
  });

  it("coupe la parole immediatement avec stopPlayback", () => {
    const engine = new AudioEngine();
    expect(engine.isPlaying()).toBe(false);
    engine.stopPlayback();
    expect(engine.isPlaying()).toBe(false);
  });

  it("adapte le seuil de barge-in au volume joue sans descendre sous le plancher", () => {
    expect(bargeInThreshold(0)).toBe(BARGE_IN_BASE_THRESHOLD);
    expect(bargeInThreshold(0.2)).toBeCloseTo(0.13, 5);
    expect(bargeInThreshold(1)).toBeGreaterThan(bargeInThreshold(0.5));
    expect(bargeInThreshold(0.01)).toBe(BARGE_IN_BASE_THRESHOLD);
    expect(speechGateThreshold(0.002)).toBe(SPEECH_BASE_THRESHOLD);
    expect(speechGateThreshold(0.02)).toBeCloseTo(0.046, 5);
    expect(speechGateThreshold(0.002, true)).toBe(SPEECH_STANDBY_THRESHOLD);
    expect(speechGateThreshold(0.02, true)).toBeCloseTo(0.036, 5);
  });

  it("reconnait l'echo de sa propre voix et pas une vraie interruption", () => {
    const spoken = "Je vais arrêter le serveur de test dans quelques instants.";
    expect(isSelfEcho(spoken, "je vais arreter le serveur de test")).toBe(true);
    expect(isSelfEcho(spoken, "Jarvis, arrête tout, stop")).toBe(false);
    expect(isSelfEcho(spoken, "stop")).toBe(false);
    expect(isSelfEcho("", "n'importe quoi")).toBe(false);
  });

  it("calcule un RMS normalise", () => {
    expect(computeRms(new Float32Array([0, 0, 0]))).toBe(0);
    expect(computeRms(new Float32Array([1, -1, 1, -1]))).toBeCloseTo(1, 5);
    expect(computeRms(new Float32Array([0.5, -0.5]))).toBeCloseTo(0.5, 5);
  });

  it("detecte le wake word jarvis et extrait la requete eventuelle", () => {
    expect(extractWakeWord("Bonjour tout le monde")).toEqual({ triggered: false, query: "" });
    expect(extractWakeWord("Jarvis")).toEqual({ triggered: true, query: "" });
    expect(extractWakeWord("Jarvice")).toEqual({ triggered: true, query: "" });
    expect(extractWakeWord("J'arvis, quelle heure est-il ?")).toEqual({
      triggered: true,
      query: "quelle heure est-il ?",
    });
    expect(extractWakeWord("Hey Jarvis, quelle heure est-il ?")).toEqual({
      triggered: true,
      query: "quelle heure est-il ?",
    });
    expect(extractWakeWord("dis jarvis: allume la lumière")).toEqual({
      triggered: true,
      query: "allume la lumière",
    });
  });

  it("reconnait la commande coupe le record sans faux positif sur phrases longues", () => {
    expect(isStopRecordCommand("coupe le record")).toBe(true);
    expect(isStopRecordCommand("Coupe le record !")).toBe(true);
    expect(isStopRecordCommand("arrête l'enregistrement")).toBe(true);
    expect(isStopRecordCommand("Jarvis tu peux couper le micro s'il te plaît")).toBe(true);
    expect(isStopRecordCommand("Arrête de m'écouter maintenant")).toBe(true);
    expect(isStopRecordCommand("Mets-toi en veille")).toBe(true);
    expect(isStopRecordCommand("Pause micro")).toBe(true);
    expect(isStopRecordCommand("Fin de l'enregistrement")).toBe(true);
    expect(isStopRecordCommand("C'est tout pour le moment")).toBe(true);
    expect(isStopRecordCommand("enregistre une nouvelle note")).toBe(false);
    expect(isStopRecordCommand("comment fonctionne l'enregistrement audio ?")).toBe(false);
    expect(
      isStopRecordCommand(
        "peux-tu m'expliquer comment on coupe le record dans un logiciel audio professionnel"
      )
    ).toBe(false);
  });

  it("gere les transitions voiceMode dans le store", () => {
    resetStore();
    useJarvisStore.getState().setWakeWordEnabled(true);
    expect(useJarvisStore.getState().wakeWordEnabled).toBe(true);
    useJarvisStore.getState().setVoiceMode("standby");
    expect(useJarvisStore.getState().voiceMode).toBe("standby");
    useJarvisStore.getState().setVoiceMode("active");
    expect(useJarvisStore.getState().voiceMode).toBe("active");
    useJarvisStore.getState().setVoiceMode("off");
    expect(useJarvisStore.getState().voiceMode).toBe("off");
  });

  it("coupe le record apres 10s sans voix reconnue sauf pendant lecture ou reflexion", () => {
    const base = {
      voiceMode: "active" as const,
      sphereState: "listening",
      isTranscribing: false,
      isPlayingAudio: false,
      lastRecognizedAtMs: 1000,
    };
    expect(
      shouldAutoStopVoiceRecord({ ...base, nowMs: 1000 + VOICE_INACTIVITY_TIMEOUT_MS - 1 })
    ).toBe(false);
    expect(
      shouldAutoStopVoiceRecord({ ...base, nowMs: 1000 + VOICE_INACTIVITY_TIMEOUT_MS })
    ).toBe(true);
    expect(
      shouldAutoStopVoiceRecord({
        ...base,
        voiceMode: "standby",
        nowMs: 1000 + VOICE_INACTIVITY_TIMEOUT_MS + 5000,
      })
    ).toBe(false);
    expect(
      shouldAutoStopVoiceRecord({
        ...base,
        isTranscribing: true,
        nowMs: 1000 + VOICE_INACTIVITY_TIMEOUT_MS + 5000,
      })
    ).toBe(false);
    expect(
      shouldAutoStopVoiceRecord({
        ...base,
        isPlayingAudio: true,
        nowMs: 1000 + VOICE_INACTIVITY_TIMEOUT_MS + 5000,
      })
    ).toBe(false);
    expect(
      shouldAutoStopVoiceRecord({
        ...base,
        sphereState: "thinking",
        nowMs: 1000 + VOICE_INACTIVITY_TIMEOUT_MS + 5000,
      })
    ).toBe(false);
  });

  it("ajuste et borne wakeWordThreshold dans le store et AudioEngine", () => {
    resetStore();
    expect(standbySpeechThresholdFromWake(0.38)).toBeCloseTo(SPEECH_STANDBY_THRESHOLD, 5);
    expect(standbySpeechThresholdFromWake(0.15)).toBeCloseTo(0.004, 5);
    expect(standbySpeechThresholdFromWake(0.75)).toBeCloseTo(0.022, 5);

    useJarvisStore.getState().setWakeWordThreshold(0.1);
    expect(useJarvisStore.getState().wakeWordThreshold).toBe(0.15);
    useJarvisStore.getState().setWakeWordThreshold(0.95);
    expect(useJarvisStore.getState().wakeWordThreshold).toBe(0.75);
    useJarvisStore.getState().setWakeWordThreshold(0.22);
    expect(useJarvisStore.getState().wakeWordThreshold).toBe(0.22);
  });
});
