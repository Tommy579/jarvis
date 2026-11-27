import { create } from "zustand";
import type {
  AlertPayload,
  AlertRecord,
  ApprovalItem,
  ApprovalRequestPayload,
  ChatMessage,
  GoalRecord,
  SensorState,
  ServerWsEvent,
  SphereState,
  SubagentStatusPayload,
  ToolCallPayload,
  ToolCallRecord,
  ToolResultPayload,
  UsageStats,
  VisualArtifactPayload,
} from "../types/generated";

export type JarvisTheme = "amber" | "jarvis-blue";
export type ThinkingMode = "auto" | "off" | "low" | "medium" | "high";
export type VoiceListenMode = "off" | "standby" | "active";
export type TerminalMode = "auto" | "ask";

export interface AudioMetrics {
  amplitude: number;
  low: number;
  mid: number;
  high: number;
}

export interface JarvisStoreState {
  sphereState: SphereState;
  wsConnected: boolean;
  theme: JarvisTheme;
  activeModel: string;
  thinkingMode: ThinkingMode;
  terminalMode: TerminalMode;
  partialTranscript: string;
  finalTranscript: string;
  streamingText: string;
  streamingMessageId: string | null;
  streamingStepBoundary: boolean;
  streamingStepCount: number;
  messages: ChatMessage[];
  pendingTools: Record<string, ToolCallRecord>;
  approvals: ApprovalItem[];
  alerts: AlertRecord[];
  goals: Record<string, GoalRecord>;
  liveSubagents: Record<string, SubagentStatusPayload>;
  goalMode: boolean;
  workspace: string | null;
  sensors: SensorState[];
  voiceMode: VoiceListenMode;
  wakeWordEnabled: boolean;
  wakeWordThreshold: number;
  audioDuckingEnabled: boolean;
  audio: AudioMetrics;
  micLevel: number;
  lastError: string | null;
  lastInterruptedAt: number | null;
  sessionStartedAt: string;
  activeArtifact: VisualArtifactPayload | null;
  artifactStepIndex: number;
  artifactPlaying: boolean;
  artifactMinimized: boolean;
  artifactViewMode: "2d" | "3d";

  setTheme: (theme: JarvisTheme) => void;
  setActiveModel: (model: string) => void;
  setThinkingMode: (mode: ThinkingMode) => void;
  setTerminalMode: (mode: TerminalMode) => void;
  toggleTerminalMode: () => TerminalMode;
  setWsConnected: (connected: boolean) => void;
  setSphereState: (state: SphereState) => void;
  setAudioMetrics: (metrics: AudioMetrics) => void;
  setMicLevel: (level: number) => void;
  setMessages: (messages: ChatMessage[]) => void;
  setApprovals: (approvals: ApprovalItem[]) => void;
  mergeApprovals: (approvals: ApprovalItem[]) => void;
  setAlerts: (alerts: AlertRecord[]) => void;
  mergeAlerts: (alerts: AlertRecord[]) => void;
  setGoals: (goals: GoalRecord[]) => void;
  upsertGoal: (goal: GoalRecord) => void;
  setGoalMode: (enabled: boolean) => void;
  setWorkspace: (workspace: string | null) => void;
  appendSystemMessage: (content: string) => void;
  clearMessagesLocal: () => void;
  setSensors: (sensors: SensorState[]) => void;
  setVoiceMode: (mode: VoiceListenMode) => void;
  setWakeWordEnabled: (enabled: boolean) => void;
  setWakeWordThreshold: (threshold: number) => void;
  setAudioDuckingEnabled: (enabled: boolean) => void;
  resolveApprovalLocal: (id: string, approved: boolean) => void;
  ackAlertLocal: (id: string, tooNoisy?: boolean) => void;
  appendUserMessage: (text: string, overwriteLast?: boolean) => void;
  interruptSpeech: () => void;
  clearTranscripts: () => void;
  handleServerEvent: (event: ServerWsEvent) => void;
  setError: (err: string | null) => void;
  setActiveArtifact: (artifact: VisualArtifactPayload | null) => void;
  setArtifactStepIndex: (index: number) => void;
  setArtifactPlaying: (playing: boolean) => void;
  setArtifactMinimized: (minimized: boolean) => void;
  setArtifactViewMode: (mode: "2d" | "3d") => void;
  closeArtifact: () => void;
}

const DEFAULT_SENSORS: SensorState[] = [
  { sensor: "screen", active: true, paused: false, detail: "Capture a la demande" },
  { sensor: "mic", active: false, paused: true, detail: "Micro en pause" },
  { sensor: "camera", active: false, paused: true, detail: "Presence desactivee" },
];

function initialTheme(): JarvisTheme {
  if (typeof document === "undefined") return "jarvis-blue";
  return document.documentElement.getAttribute("data-theme") === "amber" ? "amber" : "jarvis-blue";
}

function initialWorkspace(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem("jarvis_workspace") || null;
  } catch {
    return null;
  }
}

function initialThinkingMode(): ThinkingMode {
  if (typeof window === "undefined") return "auto";
  try {
    const val = window.localStorage.getItem("jarvis_thinking_mode");
    if (val === "auto" || val === "off" || val === "low" || val === "medium" || val === "high") {
      return val;
    }
  } catch {
    // stockage indisponible
  }
  return "auto";
}

function initialWakeWordEnabled(): boolean {
  if (typeof window === "undefined") return true;
  try {
    const val = window.localStorage.getItem("jarvis_wake_word");
    return val === null ? true : val === "1";
  } catch {
    return true;
  }
}

function clampWakeWordThreshold(value: number): number {
  if (!Number.isFinite(value)) return 0.38;
  return Math.round(Math.min(0.75, Math.max(0.15, value)) * 100) / 100;
}

function initialWakeWordThreshold(): number {
  if (typeof window === "undefined") return 0.38;
  try {
    const raw = window.localStorage.getItem("jarvis_wake_threshold");
    if (raw === null) return 0.38;
    return clampWakeWordThreshold(Number(raw));
  } catch {
    return 0.38;
  }
}

function initialAudioDuckingEnabled(): boolean {
  if (typeof window === "undefined") return true;
  try {
    const val = window.localStorage.getItem("jarvis_audio_ducking");
    return val === null ? true : val === "1";
  } catch {
    return true;
  }
}

export const useJarvisStore = create<JarvisStoreState>((set, get) => ({
  sphereState: "idle",
  wsConnected: false,
  theme: initialTheme(),
  activeModel: "deepseek/deepseek-v4.1-flash-1017",
  thinkingMode: initialThinkingMode(),
  terminalMode:
    typeof window !== "undefined" && window.localStorage.getItem("jarvis_terminal_mode") === "ask"
      ? "ask"
      : "auto",
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
  workspace: initialWorkspace(),
  sensors: DEFAULT_SENSORS,
  voiceMode: "off",
  wakeWordEnabled: initialWakeWordEnabled(),
  wakeWordThreshold: initialWakeWordThreshold(),
  audioDuckingEnabled: initialAudioDuckingEnabled(),
  audio: { amplitude: 0, low: 0, mid: 0, high: 0 },
  micLevel: 0,
  lastError: null,
  lastInterruptedAt: null,
  sessionStartedAt: new Date().toISOString(),
  activeArtifact: null,
  artifactStepIndex: 0,
  artifactPlaying: false,
  artifactMinimized: false,
  artifactViewMode: "3d",

  setTheme: (theme) => {
    if (typeof document !== "undefined") {
      document.documentElement.setAttribute("data-theme", theme === "amber" ? "amber" : "jarvis-blue");
      try {
        window.localStorage.setItem("jarvis_accent", theme);
      } catch {
        // stockage indisponible
      }
    }
    set({ theme });
  },

  setActiveModel: (activeModel) => set({ activeModel }),
  setThinkingMode: (thinkingMode) => {
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem("jarvis_thinking_mode", thinkingMode);
      } catch {
        // stockage indisponible
      }
    }
    set({ thinkingMode });
  },

  setTerminalMode: (terminalMode) => {
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem("jarvis_terminal_mode", terminalMode);
      } catch {
        // stockage indisponible
      }
    }
    set({ terminalMode });
  },

  toggleTerminalMode: () => {
    const next: TerminalMode = get().terminalMode === "auto" ? "ask" : "auto";
    get().setTerminalMode(next);
    return next;
  },

  setWsConnected: (wsConnected) =>
    set((prev) => ({
      wsConnected,
      sphereState: wsConnected ? (prev.sphereState === "offline" ? "idle" : prev.sphereState) : "offline",
    })),

  setSphereState: (sphereState) => set({ sphereState }),
  setVoiceMode: (voiceMode) => set({ voiceMode }),
  setWakeWordEnabled: (wakeWordEnabled) => {
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem("jarvis_wake_word", wakeWordEnabled ? "1" : "0");
      } catch {
        // stockage indisponible
      }
    }
    set({ wakeWordEnabled });
  },
  setWakeWordThreshold: (threshold) => {
    const wakeWordThreshold = clampWakeWordThreshold(threshold);
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem("jarvis_wake_threshold", String(wakeWordThreshold));
      } catch {
        // stockage indisponible
      }
    }
    set({ wakeWordThreshold });
  },
  setAudioDuckingEnabled: (audioDuckingEnabled) => {
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem("jarvis_audio_ducking", audioDuckingEnabled ? "1" : "0");
      } catch {
        // stockage indisponible
      }
    }
    set({ audioDuckingEnabled });
  },
  setAudioMetrics: (audio) => set({ audio }),
  setMicLevel: (micLevel) => set({ micLevel }),
  setMessages: (messages) => set({ messages }),
  setApprovals: (approvals) => set({ approvals }),
  // Fusionne la liste serveur avec les entrees arrivees en WebSocket : un remplacement sec
  // faisait disparaitre toute approbation recue entre deux sondages (5 s).
  mergeApprovals: (incoming) =>
    set((prev) => {
      const serverIds = new Set(incoming.map((a) => a.id));
      const onlyLocal = prev.approvals.filter(
        (a) => !serverIds.has(a.id) && a.status === "pending"
      );
      return { approvals: [...onlyLocal, ...incoming] };
    }),
  setAlerts: (alerts) => set({ alerts }),
  mergeAlerts: (incoming) =>
    set((prev) => {
      const serverIds = new Set(incoming.map((a) => a.id));
      const onlyLocal = prev.alerts.filter((a) => !serverIds.has(a.id));
      return { alerts: [...onlyLocal, ...incoming] };
    }),
  setGoals: (goalList) =>
    set(() => {
      const next: Record<string, GoalRecord> = {};
      for (const g of goalList) {
        next[g.id] = g;
      }
      return { goals: next };
    }),
  upsertGoal: (goal) =>
    set((prev) => ({
      goals: {
        ...prev.goals,
        [goal.id]: goal,
      },
    })),
  setGoalMode: (goalMode) => set({ goalMode }),
  setWorkspace: (workspace) => {
    const clean = workspace?.trim() || null;
    if (typeof window !== "undefined") {
      try {
        if (clean) window.localStorage.setItem("jarvis_workspace", clean);
        else window.localStorage.removeItem("jarvis_workspace");
      } catch {
        // stockage indisponible
      }
    }
    set({ workspace: clean });
  },
  appendSystemMessage: (content) =>
    set((prev) => ({
      messages: [
        ...prev.messages,
        {
          id: `sys-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          role: "assistant",
          content,
          created_at: new Date().toISOString(),
          usage: null,
          tools: [],
        },
      ],
    })),
  clearMessagesLocal: () =>
    set({
      messages: [],
      streamingText: "",
      streamingMessageId: null,
      streamingStepBoundary: false,
      streamingStepCount: 0,
      sessionStartedAt: new Date().toISOString(),
    }),
  setSensors: (sensors) => set({ sensors }),

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
      streamingStepBoundary: false,
      streamingStepCount: 0,
      partialTranscript: "",
      finalTranscript: "",
      lastInterruptedAt: Date.now(),
      audio: { amplitude: 0, low: 0, mid: 0, high: 0 },
      artifactPlaying: false,
    })),

  clearTranscripts: () =>
    set({
      partialTranscript: "",
      finalTranscript: "",
    }),

  setError: (lastError) => set({ lastError }),

  setActiveArtifact: (artifact) =>
    set({
      activeArtifact: artifact,
      artifactStepIndex: 0,
      artifactPlaying: Boolean(artifact?.auto_play),
      artifactMinimized: false,
      artifactViewMode: artifact?.mode ?? "3d",
    }),

  setArtifactStepIndex: (index) =>
    set((prev) => {
      const total = prev.activeArtifact?.steps.length ?? 0;
      const clamped = total > 0 ? Math.max(0, Math.min(total - 1, index)) : 0;
      return { artifactStepIndex: clamped };
    }),

  setArtifactPlaying: (artifactPlaying) => set({ artifactPlaying }),
  setArtifactMinimized: (artifactMinimized) => set({ artifactMinimized }),
  setArtifactViewMode: (artifactViewMode) => set({ artifactViewMode }),
  closeArtifact: () =>
    set({
      activeArtifact: null,
      artifactStepIndex: 0,
      artifactPlaying: false,
      artifactMinimized: false,
    }),

  handleServerEvent: (event) => {
    switch (event.type) {
      case "state": {
        const value = event.payload.value;
        set((prev) => ({
          sphereState: value,
          ...(value === "idle" || (value === "thinking" && !prev.streamingMessageId)
            ? { streamingStepBoundary: false, streamingStepCount: 0 }
            : {}),
        }));
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
        set((prev) => {
          const isNewMessage = prev.streamingMessageId !== message_id;
          const isNewStep = isNewMessage || prev.streamingStepBoundary;
          return {
            streamingMessageId: message_id,
            streamingText: isNewStep ? text : prev.streamingText + text,
            streamingStepBoundary: false,
            streamingStepCount: isNewMessage
              ? 1
              : isNewStep
                ? prev.streamingStepCount + 1
                : prev.streamingStepCount,
            partialTranscript: "",
            finalTranscript: "",
          };
        });
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
          streamingStepBoundary: false,
          streamingStepCount: 0,
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
          streamingStepBoundary: true,
        }));
        break;
      }
      case "tool.result": {
        const p = event.payload as ToolResultPayload;
        set((prev) => {
          const existing = prev.pendingTools[p.id];
          if (!existing) {
            return { streamingStepBoundary: true };
          }
          return {
            streamingStepBoundary: true,
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
      case "goal.updated": {
        const p = event.payload;
        const now = new Date().toISOString();
        set((prev) => {
          const existing = prev.goals[p.goal_id];
          const merged: GoalRecord = existing
            ? {
                ...existing,
                title: p.title || existing.title,
                status: p.status as GoalRecord["status"],
                progress_pct: p.progress_pct,
                current_phase: p.current_phase,
                cost_usd: p.cost_usd,
                max_cost_usd: p.max_cost_usd,
                orchestrator_model: p.orchestrator_model || existing.orchestrator_model,
                pause_reason: p.pause_reason,
                final_summary: p.final_summary || existing.final_summary,
                updated_at: now,
              }
            : {
                id: p.goal_id,
                title: p.title,
                goal: p.title,
                status: p.status as GoalRecord["status"],
                success_criteria: [],
                verified_criteria: [],
                orchestrator_model: p.orchestrator_model,
                allowed_models: [],
                tokens_used: 0,
                cost_usd: p.cost_usd,
                max_cost_usd: p.max_cost_usd,
                progress_pct: p.progress_pct,
                current_phase: p.current_phase,
                final_summary: p.final_summary,
                pause_reason: p.pause_reason,
                notify_on_finish: true,
                subtasks: [],
                events: [],
                created_at: now,
                updated_at: now,
                finished_at: null,
              };
          return {
            goals: { ...prev.goals, [p.goal_id]: merged },
          };
        });
        break;
      }
      case "goal.subagent": {
        const p = event.payload;
        set((prev) => ({
          liveSubagents: {
            ...prev.liveSubagents,
            [p.run_id]: p,
          },
        }));
        break;
      }
      case "goal.intervention": {
        const p = event.payload;
        set((prev) => {
          const existing = prev.goals[p.goal_id];
          if (!existing) return prev;
          const ev = {
            id: `ev_${Date.now()}`,
            goal_id: p.goal_id,
            ts: p.ts,
            kind: (p.kind as GoalRecord["events"][number]["kind"]) || "watchdog",
            subtask_id: p.subtask_id,
            run_id: p.run_id,
            message: p.message,
            metadata: {},
          };
          return {
            goals: {
              ...prev.goals,
              [p.goal_id]: {
                ...existing,
                events: [ev, ...existing.events].slice(0, 80),
              },
            },
          };
        });
        break;
      }
      case "visual.artifact": {
        const p = event.payload as VisualArtifactPayload;
        set({
          activeArtifact: p,
          artifactStepIndex: 0,
          artifactPlaying: Boolean(p.auto_play),
          artifactMinimized: false,
          artifactViewMode: p.mode ?? "3d",
        });
        break;
      }
      default: {
        const raw = event as { type: string; payload?: { id?: string; approved?: boolean } };
        if (raw.type === "approval.resolved" && raw.payload?.id) {
          const approved = Boolean(raw.payload.approved);
          set((prev) => ({
            approvals: prev.approvals.map((a) =>
              a.id === raw.payload?.id
                ? { ...a, status: approved ? "approved" : "denied" }
                : a
            ),
          }));
        }
        break;
      }
    }
  },
}));
