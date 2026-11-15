import { create } from "zustand";
import type {
  AlertPayload,
  AlertRecord,
  ApprovalItem,
  ApprovalRequestPayload,
  ChatMessage,
  SensorName,
  SensorState,
  ServerWsEvent,
  SphereState,
  ToolCallPayload,
  ToolCallRecord,
  ToolResultPayload,
  UsageStats,
} from "../types/generated";

export type OmiTheme = "amber" | "jarvis-blue";

export interface AudioMetrics {
  amplitude: number;
  low: number;
  mid: number;
  high: number;
}

export interface OmiStoreState {
  sphereState: SphereState;
  wsConnected: boolean;
  theme: OmiTheme;
  activeModel: string;
  partialTranscript: string;
  finalTranscript: string;
  streamingText: string;
  streamingMessageId: string | null;
  messages: ChatMessage[];
  pendingTools: Record<string, ToolCallRecord>;
  approvals: ApprovalItem[];
  alerts: AlertRecord[];
  sensors: SensorState[];
  audio: AudioMetrics;
  micLevel: number;
  lastError: string | null;
  lastInterruptedAt: number | null;
  sessionStartedAt: string;

  setTheme: (theme: OmiTheme) => void;
  setActiveModel: (model: string) => void;
  setWsConnected: (connected: boolean) => void;
  setSphereState: (state: SphereState) => void;
  setAudioMetrics: (metrics: AudioMetrics) => void;
  setMicLevel: (level: number) => void;
  setMessages: (messages: ChatMessage[]) => void;
  setApprovals: (approvals: ApprovalItem[]) => void;
  setAlerts: (alerts: AlertRecord[]) => void;
  setSensors: (sensors: SensorState[]) => void;
  toggleSensorLocal: (sensor: SensorName, paused: boolean) => void;
  resolveApprovalLocal: (id: string, approved: boolean) => void;
  ackAlertLocal: (id: string, tooNoisy?: boolean) => void;
  appendUserMessage: (text: string, overwriteLast?: boolean) => void;
  interruptSpeech: () => void;
  clearTranscripts: () => void;
  handleServerEvent: (event: ServerWsEvent) => void;
  setError: (err: string | null) => void;
}

const DEFAULT_SENSORS: SensorState[] = [
  { sensor: "screen", active: true, paused: false, detail: "Capture a la demande" },
  { sensor: "mic", active: false, paused: true, detail: "Micro en pause" },
  { sensor: "camera", active: false, paused: true, detail: "Presence desactivee" },
];

function initialTheme(): OmiTheme {
  if (typeof document === "undefined") return "jarvis-blue";
  return document.documentElement.getAttribute("data-theme") === "amber" ? "amber" : "jarvis-blue";
}

export const useOmiStore = create<OmiStoreState>((set, get) => ({
  sphereState: "idle",
  wsConnected: false,
  theme: initialTheme(),
  activeModel: "deepseek/deepseek-v4.1-flash-1017",
  partialTranscript: "",
  finalTranscript: "",
  streamingText: "",
  streamingMessageId: null,
  messages: [],
  pendingTools: {},
  approvals: [],
  alerts: [],
  sensors: DEFAULT_SENSORS,
  audio: { amplitude: 0, low: 0, mid: 0, high: 0 },
  micLevel: 0,
  lastError: null,
  lastInterruptedAt: null,
  sessionStartedAt: new Date().toISOString(),

  setTheme: (theme) => {
    if (typeof document !== "undefined") {
      document.documentElement.setAttribute("data-theme", theme === "amber" ? "amber" : "jarvis-blue");
      try {
        window.localStorage.setItem("omi_accent", theme);
      } catch {
        // stockage indisponible
      }
    }
    set({ theme });
  },

  setActiveModel: (activeModel) => set({ activeModel }),

  setWsConnected: (wsConnected) =>
    set((prev) => ({
      wsConnected,
      sphereState: wsConnected ? (prev.sphereState === "offline" ? "idle" : prev.sphereState) : "offline",
    })),

  setSphereState: (sphereState) => set({ sphereState }),
  setAudioMetrics: (audio) => set({ audio }),
  setMicLevel: (micLevel) => set({ micLevel }),
  setMessages: (messages) => set({ messages }),
  setApprovals: (approvals) => set({ approvals }),
  setAlerts: (alerts) => set({ alerts }),
  setSensors: (sensors) => set({ sensors }),

  toggleSensorLocal: (sensor, paused) =>
    set((prev) => ({
      sensors: prev.sensors.map((s) => (s.sensor === sensor ? { ...s, paused, active: !paused } : s)),
    })),

  resolveApprovalLocal: (id, approved) =>
    set((prev) => ({
      approvals: prev.approvals.map((a) =>
        a.id === id ? { ...a, status: approved ? "approved" : "denied" } : a
      ),
    })),

  ackAlertLocal: (id, tooNoisy = false) =>
    set((prev) => ({
      alerts: prev.alerts.map((a) =>
        a.id === id
          ? {
              ...a,
              status: tooNoisy ? "too_noisy" : "acked",
              acked_at: new Date().toISOString(),
            }
          : a
      ),
      sphereState: prev.sphereState === "alert" ? "idle" : prev.sphereState,
    })),

  appendUserMessage: (text, overwriteLast = false) =>
    set((prev) => {
      const newMsg: ChatMessage = {
        id: `user_${Date.now()}`,
        role: "user",
        content: text,
        created_at: new Date().toISOString(),
        usage: null,
        tools: [],
      };
      if (overwriteLast && prev.messages.length > 0) {
        const lastMsg = prev.messages[prev.messages.length - 1];
        if (lastMsg.role === "assistant" && prev.messages.length >= 2) {
          return { messages: [...prev.messages.slice(0, -2), newMsg] };
        } else if (lastMsg.role === "user") {
          return { messages: [...prev.messages.slice(0, -1), newMsg] };
        }
      }
      return {
        messages: [...prev.messages, newMsg],
      };
    }),

  interruptSpeech: () =>
    set((prev) => ({
      sphereState: prev.wsConnected ? "idle" : "offline",
      streamingText: "",
      streamingMessageId: null,
      partialTranscript: "",
      finalTranscript: "",
      lastInterruptedAt: Date.now(),
      audio: { amplitude: 0, low: 0, mid: 0, high: 0 },
    })),

  clearTranscripts: () =>
    set({
      partialTranscript: "",
      finalTranscript: "",
    }),

  setError: (lastError) => set({ lastError }),

  handleServerEvent: (event) => {
    switch (event.type) {
      case "state": {
        set({ sphereState: event.payload.value });
        break;
      }
      case "transcript.partial": {
        set({ partialTranscript: event.payload.text });
        break;
      }
      case "transcript.final": {
        set({
          partialTranscript: "",
          finalTranscript: event.payload.text,
        });
        break;
      }
      case "assistant.delta": {
        const { message_id, text } = event.payload;
        set((prev) => ({
          streamingMessageId: message_id,
          streamingText:
            prev.streamingMessageId === message_id ? prev.streamingText + text : text,
          partialTranscript: "",
          finalTranscript: "",
        }));
        break;
      }
      case "assistant.done": {
        const { message_id, usage } = event.payload as { message_id: string; usage: UsageStats };
        const state = get();
        const completedText =
          state.streamingMessageId === message_id && state.streamingText
            ? state.streamingText
            : "Reponse recue.";
        const attachedTools = Object.values(state.pendingTools);
        const existingIdx = state.messages.findIndex((m) => m.id === message_id);
        const message: ChatMessage = {
          id: message_id,
          role: "assistant",
          content: completedText,
          created_at: new Date().toISOString(),
          usage,
          tools: attachedTools,
        };
        const updatedMessages =
          existingIdx >= 0
            ? state.messages.map((m, i) => (i === existingIdx ? message : m))
            : [...state.messages, message];

        set({
          messages: updatedMessages,
          streamingMessageId: null,
          streamingText: "",
          pendingTools: {},
          partialTranscript: "",
          finalTranscript: "",
        });
        break;
      }
      case "tool.call": {
        const p = event.payload as ToolCallPayload;
        const record: ToolCallRecord = {
          id: p.id,
          name: p.name,
          args: p.args,
          risk: p.risk,
          ok: true,
          summary: "En cours d'execution",
          ts: new Date().toISOString(),
        };
        set((prev) => ({
          pendingTools: { ...prev.pendingTools, [p.id]: record },
        }));
        break;
      }
      case "tool.result": {
        const p = event.payload as ToolResultPayload;
        set((prev) => {
          const existing = prev.pendingTools[p.id];
          if (!existing) return prev;
          return {
            pendingTools: {
              ...prev.pendingTools,
              [p.id]: { ...existing, ok: p.ok, summary: p.summary },
            },
          };
        });
        break;
      }
      case "mic.level": {
        set({ micLevel: event.payload.value });
        break;
      }
      case "approval.request": {
        const p = event.payload as ApprovalRequestPayload;
        const item: ApprovalItem = {
          id: p.id,
          tool_name: "action_protegee",
          description: p.description,
          args: {},
          risk: p.risk,
          created_at: new Date().toISOString(),
          expires_at: p.expires_at,
          status: "pending",
        };
        set((prev) => ({
          approvals: [item, ...prev.approvals.filter((a) => a.id !== p.id)],
        }));
        break;
      }
      case "alert": {
        const p = event.payload as AlertPayload;
        const rec: AlertRecord = {
          id: p.id,
          ts: new Date().toISOString(),
          channel: "web",
          urgency: p.urgency,
          message: p.message,
          reason: p.reason,
          status: "sent",
          acked_at: null,
        };
        set((prev) => ({
          alerts: [rec, ...prev.alerts.filter((a) => a.id !== p.id)],
          sphereState: p.urgency >= 3 ? "alert" : prev.sphereState,
        }));
        break;
      }
      case "tts.chunk": {
        // Consomme par AudioEngine dans SphereHost / App
        break;
      }
    }
  },
}));
