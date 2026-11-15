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
} from "../sphere/AudioEngine";
import { useOmiStore } from "../store/useOmiStore";
import type { SphereState } from "../types/generated";
import { OmiWsClient } from "../ws/client";

function resetStore() {
  useOmiStore.setState({
    sphereState: "idle",
    wsConnected: false,
    theme: "amber",
    activeModel: "deepseek/deepseek-v4.1-flash-1017",
    partialTranscript: "",
    finalTranscript: "",
    streamingText: "",
    streamingMessageId: null,
    messages: [],
    pendingTools: {},
    approvals: [],
    alerts: [],
    audio: { amplitude: 0, low: 0, mid: 0, high: 0 },
    micLevel: 0,
    lastError: null,
  });
}

describe("useOmiStore", () => {
  beforeEach(() => {
    resetStore();
  });

  it("gere les 6 etats de la sphere", () => {
    const states: SphereState[] = ["idle", "listening", "thinking", "speaking", "alert", "offline"];
    for (const value of states) {
      useOmiStore.getState().handleServerEvent({
        type: "state",
        payload: { value },
      });
      expect(useOmiStore.getState().sphereState).toBe(value);
    }
  });

  it("attache les tool.call et tool.result au message assistant.done", () => {
    const store = useOmiStore.getState();

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

    const state = useOmiStore.getState();
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].content).toBe("Vous avez une reunion a 14h.");
    expect(state.messages[0].tools).toHaveLength(1);
    expect(state.messages[0].tools[0].summary).toBe("1 evenement trouve.");
    expect(Object.keys(state.pendingTools)).toHaveLength(0);
  });

  it("reinitialise les transcriptions vocales des que Jarvis repond", () => {
    const store = useOmiStore.getState();
    store.handleServerEvent({
      type: "transcript.final",
      payload: { text: "Quelle heure est-il ?", speaker: "user" },
    });
    expect(useOmiStore.getState().finalTranscript).toBe("Quelle heure est-il ?");

    store.handleServerEvent({
      type: "assistant.delta",
      payload: { message_id: "msg_20", text: "Il est " },
    });
    expect(useOmiStore.getState().finalTranscript).toBe("");
    expect(useOmiStore.getState().partialTranscript).toBe("");
  });

  it("ecrase le message et reinitialise le prompt lors d'une interruption", () => {
    const store = useOmiStore.getState();
    store.setMessages([]);
    store.appendUserMessage("Premier prompt interrompu");
    expect(useOmiStore.getState().messages).toHaveLength(1);

    store.interruptSpeech();
    expect(useOmiStore.getState().streamingText).toBe("");
    expect(useOmiStore.getState().lastInterruptedAt).not.toBeNull();

    store.appendUserMessage("Nouveau prompt qui ecrase", true);
    const msgs = useOmiStore.getState().messages;
    expect(msgs).toHaveLength(1);
    expect(msgs[0].content).toBe("Nouveau prompt qui ecrase");
  });

  it("gere les demandes d'approbation et l'acquittement d'alertes", () => {
    const store = useOmiStore.getState();

    store.handleServerEvent({
      type: "approval.request",
      payload: {
        id: "appr_1",
        description: "Redemarrer le service systemd",
        risk: 3,
        expires_at: new Date(Date.now() + 60_000).toISOString(),
      },
    });
    expect(useOmiStore.getState().approvals[0].status).toBe("pending");

    useOmiStore.getState().resolveApprovalLocal("appr_1", true);
    expect(useOmiStore.getState().approvals[0].status).toBe("approved");

    store.handleServerEvent({
      type: "alert",
      payload: {
        id: "alert_1",
        urgency: 4,
        message: "Alerte critique serveur",
        reason: "Charge elevee",
      },
    });
    expect(useOmiStore.getState().sphereState).toBe("alert");

    useOmiStore.getState().ackAlertLocal("alert_1", false);
    expect(useOmiStore.getState().alerts[0].status).toBe("acked");
    expect(useOmiStore.getState().sphereState).toBe("idle");
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

describe("OmiWsClient", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("met en file hors ligne et vide la file a l'ouverture", () => {
    const events: string[] = [];
    const client = new OmiWsClient({
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
    const client = new OmiWsClient({
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
    expect(bargeInThreshold(0.2)).toBeCloseTo(0.11, 5);
    expect(bargeInThreshold(1)).toBeGreaterThan(bargeInThreshold(0.5));
    expect(bargeInThreshold(0.01)).toBe(BARGE_IN_BASE_THRESHOLD);
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
});
