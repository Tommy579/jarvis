import { getAuthToken } from "../lib/authToken";
import type { ThinkingMode } from "../store/useJarvisStore";
import type {
  AlertRecord,
  ApprovalItem,
  AttentionRules,
  AutonomousTaskCreate,
  AutonomousTaskRecord,
  AutonomyPolicyConfig,
  AutonomyPolicyUpdate,
  BlacklistConfig,
  ChatMessage,
  DailyUsageItem,
  EvalSuiteResult,
  FactCreate,
  FactRecord,
  FactUpdate,
  GoalCreate,
  GoalRecord,
  GoalSteerRequest,
  IntegrationInfo,
  MCPServerStatus,
  MemoryExport,
  OnboardingStatus,
  PersonRecord,
  RoleModelConfig,
  SystemAutostartConfig,
  VoiceProfilesConfig,
  VoiceVerifyResult,
  WikiPage,
} from "../types/generated";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

/** Erreur HTTP typée : permet a l'interface de distinguer un 401 (jeton) d'une autre panne. */
export class HttpError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "HttpError";
    this.status = status;
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getAuthToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const resp = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!resp.ok) {
    const errBody = await resp.text();
    let message = errBody || `Erreur HTTP ${resp.status}`;
    try {
      const parsed = JSON.parse(errBody) as { detail?: string };
      if (parsed && typeof parsed.detail === "string" && parsed.detail) {
        message = parsed.detail;
      }
    } catch {
      // Garde errBody tel quel si ce n'est pas du JSON.
    }
    throw new HttpError(message, resp.status);
  }
  return (await resp.json()) as T;
}

export const apiClient = {
  getHealth: () => requestJson<{ status: string; service: string }>("/api/health"),
  resolveWorkspace: (path: string) =>
    requestJson<{ found: boolean; path: string | null; display: string | null }>(
      "/api/workspace/resolve",
      {
        method: "POST",
        body: JSON.stringify({ path }),
      }
    ),
  getMessages: () => requestJson<ChatMessage[]>("/api/messages"),
  clearMessages: () =>
    requestJson<{ cleared: number }>("/api/messages/clear", {
      method: "POST",
    }),
  sendMessage: (text: string) =>
    requestJson<ChatMessage>("/api/messages/send", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  transcribeVoice: (audio_base64: string, mime_type = "audio/wav") =>
    requestJson<{ text: string }>("/api/voice/transcribe", {
      method: "POST",
      body: JSON.stringify({ audio_base64, mime_type }),
    }),
  wakeCheckVoice: (audio_base64: string, mime_type = "audio/wav", wake_threshold?: number) =>
    requestJson<{ text: string }>("/api/voice/wake-check", {
      method: "POST",
      body: JSON.stringify({
        audio_base64,
        mime_type,
        ...(wake_threshold !== undefined ? { wake_threshold } : {}),
      }),
    }),
  speakVoice: (text: string) =>
    requestJson<{ audio_base64: string; sample_rate: number }>("/api/voice/speak", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  setAudioDucking: (active: boolean, ratio = 0.22) =>
    requestJson<{ active: boolean }>("/api/voice/ducking", {
      method: "POST",
      body: JSON.stringify({ active, ratio }),
    }),
  getVoiceProfiles: () => requestJson<VoiceProfilesConfig>("/api/voice/profiles"),
  updateVoiceProfileSettings: (payload: { enabled?: boolean; threshold?: number }) =>
    requestJson<VoiceProfilesConfig>("/api/voice/profiles/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  enrollVoiceProfile: (payload: {
    name: string;
    role: string;
    audio_base64: string;
    mime_type?: string;
  }) =>
    requestJson<VoiceProfilesConfig>("/api/voice/profiles", {
      method: "POST",
      body: JSON.stringify({ mime_type: "audio/wav", ...payload }),
    }),
  addVoiceProfileSample: (profileId: string, audio_base64: string, mime_type = "audio/wav") =>
    requestJson<VoiceProfilesConfig>(`/api/voice/profiles/${profileId}/samples`, {
      method: "POST",
      body: JSON.stringify({ audio_base64, mime_type }),
    }),
  deleteVoiceProfile: (profileId: string) =>
    requestJson<VoiceProfilesConfig>(`/api/voice/profiles/${profileId}`, {
      method: "DELETE",
    }),
  verifyVoiceSample: (audio_base64: string, mime_type = "audio/wav") =>
    requestJson<VoiceVerifyResult>("/api/voice/profiles/verify", {
      method: "POST",
      body: JSON.stringify({ audio_base64, mime_type }),
    }),
  getFacts: (params?: { q?: string; category?: string; sensitivity?: string }) => {
    const sp = new URLSearchParams();
    if (params?.q) sp.set("q", params.q);
    if (params?.category) sp.set("category", params.category);
    if (params?.sensitivity) sp.set("sensitivity", params.sensitivity);
    const qs = sp.toString();
    return requestJson<FactRecord[]>(`/api/facts${qs ? `?${qs}` : ""}`);
  },
  getWeeklyReviewFacts: () => requestJson<FactRecord[]>("/api/facts/review"),
  createFact: (body: FactCreate) =>
    requestJson<FactRecord>("/api/facts", { method: "POST", body: JSON.stringify(body) }),
  updateFact: (id: string, body: Partial<FactUpdate>) =>
    requestJson<FactRecord>(`/api/facts/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteFact: (id: string) =>
    requestJson<{ deleted: boolean }>(`/api/facts/${id}`, { method: "DELETE" }),
  listWiki: () => requestJson<WikiPage[]>("/api/wiki"),
  getWiki: (slug: string) => requestJson<WikiPage>(`/api/wiki/${slug}`),
  putWiki: (slug: string, content: string) =>
    requestJson<WikiPage>(`/api/wiki/${slug}`, { method: "PUT", body: JSON.stringify({ content }) }),
  getPeople: () => requestJson<PersonRecord[]>("/api/people"),
  createPerson: (payload: {
    name: string;
    relation: string;
    birthday?: string | null;
    contact_ref?: string | null;
    notes?: string;
  }) =>
    requestJson<PersonRecord>("/api/people", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deletePerson: (id: string) =>
    requestJson<{ deleted: boolean; id: string }>(`/api/people/${id}`, { method: "DELETE" }),
  exportMemory: () => requestJson<MemoryExport>("/api/memory/export"),
  getOnboardingStatus: () => requestJson<OnboardingStatus>("/api/onboarding/status"),
  answerOnboarding: (moduleId: string, answers: Record<string, string>) =>
    requestJson<OnboardingStatus>(`/api/onboarding/${moduleId}/answer`, {
      method: "POST",
      body: JSON.stringify({ answers }),
    }),
  getModels: () => requestJson<RoleModelConfig[]>("/api/models"),
  getThinkingMode: () => requestJson<{ mode: string }>("/api/models/thinking"),
  updateThinkingMode: (mode: string) =>
    requestJson<{ mode: string }>("/api/models/thinking", {
      method: "PUT",
      body: JSON.stringify({ mode }),
    }),
  updateRoleModel: (
    role: string,
    model: string,
    fallback?: string[],
    thinking_mode?: ThinkingMode
  ) =>
    requestJson<RoleModelConfig>("/api/models/role", {
      method: "PUT",
      body: JSON.stringify({ role, model, fallback, thinking_mode }),
    }),
  getDailyUsage: () => requestJson<DailyUsageItem[]>("/api/usage/daily"),
  getDailyBudget: () => requestJson<{ daily_budget_usd: number }>("/api/usage/budget"),
  updateDailyBudget: (daily_budget_usd: number) =>
    requestJson<{ daily_budget_usd: number }>("/api/usage/budget", {
      method: "PUT",
      body: JSON.stringify({ daily_budget_usd }),
    }),
  getLatestEvals: () => requestJson<EvalSuiteResult>("/api/evals/latest"),
  runEvals: () => requestJson<EvalSuiteResult>("/api/evals/run", { method: "POST" }),
  getAlerts: () => requestJson<AlertRecord[]>("/api/alerts"),
  ackAlert: (id: string) => requestJson<AlertRecord>(`/api/alerts/${id}/ack`, { method: "POST" }),
  markAlertTooNoisy: (id: string) =>
    requestJson<AlertRecord>(`/api/alerts/${id}/too-noisy`, { method: "POST" }),
  getRules: () => requestJson<AttentionRules>("/api/rules"),
  updateRules: (rules: AttentionRules) =>
    requestJson<AttentionRules>("/api/rules", { method: "PUT", body: JSON.stringify(rules) }),
  getApprovals: () => requestJson<ApprovalItem[]>("/api/approvals"),
  decideApproval: (id: string, approved: boolean) =>
    requestJson<ApprovalItem>(`/api/approvals/${id}`, {
      method: "POST",
      body: JSON.stringify({ approved }),
    }),
  getIntegrations: () => requestJson<IntegrationInfo[]>("/api/integrations"),
  toggleIntegration: (id: string) =>
    requestJson<IntegrationInfo>(`/api/integrations/${id}/toggle`, { method: "POST" }),
  syncIntegration: (id: string) =>
    requestJson<IntegrationInfo>(`/api/integrations/${id}/sync`, { method: "POST" }),
  configureIntegration: (
    id: string,
    payload: {
      token?: string;
      url?: string;
      csv_text?: string;
      client_id?: string;
      client_secret?: string;
    }
  ) =>
    requestJson<IntegrationInfo>(`/api/integrations/${id}/configure`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getIntegrationOAuthUrl: (id: string) =>
    requestJson<{ url: string; mode: string; redirect_uri?: string }>(
      `/api/integrations/${id}/oauth/url`
    ),
  getSensorsBlacklist: () => requestJson<BlacklistConfig>("/api/sensors/blacklist"),
  toggleSensor: (sensor: string, paused: boolean) =>
    requestJson<BlacklistConfig>(`/api/sensors/${sensor}/toggle`, {
      method: "POST",
      body: JSON.stringify({ paused }),
    }),
  updateSensorsBlacklist: (cfg: BlacklistConfig) =>
    requestJson<BlacklistConfig>("/api/sensors/blacklist", {
      method: "PUT",
      body: JSON.stringify(cfg),
    }),
  getNotificationPolicy: () =>
    requestJson<{
      enabled: boolean;
      quiet_start: string;
      quiet_end: string;
      quiet_allow_urgency: number;
      allow_calls: boolean;
      allow_emergency_calls: boolean;
      call_min_urgency: number;
      call_require_pin: boolean;
      max_calls_per_day: number;
      min_seconds_between_calls: number;
      channel_ladder: string[];
      escalation_seconds: Record<string, number>;
      approval_ttl_seconds: number;
      snooze_until: string | null;
    }>("/api/notifications/policy"),
  updateNotificationPolicy: (payload: Record<string, unknown>) =>
    requestJson<Record<string, unknown>>("/api/notifications/policy", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  getNotificationOrders: (activeOnly = true) =>
    requestJson<
      Array<{
        id: string;
        created_at: string;
        source_glob: string;
        event_type_glob: string;
        text_match: string | null;
        min_urgency: number;
        expires_at: string | null;
        max_fires: number | null;
        fired_count: number;
        active: boolean;
        note: string;
      }>
    >(`/api/notifications/orders?active_only=${activeOnly}`),
  createNotificationOrder: (order: Record<string, unknown>) =>
    requestJson<Record<string, unknown>>("/api/notifications/orders", {
      method: "POST",
      body: JSON.stringify(order),
    }),
  deleteNotificationOrder: (orderId: string) =>
    requestJson<{ ok: boolean; revoked: string }>(`/api/notifications/orders/${orderId}`, {
      method: "DELETE",
    }),
  getNotificationAttempts: (limit = 50) =>
    requestJson<
      Array<{
        id: string;
        notification_id: string;
        approval_id: string | null;
        channel: string;
        target: string;
        ts: string;
        status: string;
        error: string | null;
        external_id: string | null;
      }>
    >(`/api/notifications/attempts?limit=${limit}`),
  getAutostart: () => requestJson<SystemAutostartConfig>("/api/system/autostart"),
  updateAutostart: (enabled: boolean) =>
    requestJson<SystemAutostartConfig>("/api/system/autostart", {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),
  getAutonomyPolicy: () => requestJson<AutonomyPolicyConfig>("/api/autonomy/policy"),
  updateAutonomyPolicy: (patch: Partial<AutonomyPolicyUpdate>) =>
    requestJson<AutonomyPolicyConfig>("/api/autonomy/policy", {
      method: "PUT",
      body: JSON.stringify(patch),
    }),
  emergencyStop: () =>
    requestJson<{ ok: boolean; emergency_stop: boolean; cancelled_tasks: string[] }>(
      "/api/autonomy/emergency-stop",
      { method: "POST" }
    ),
  getAutonomousTasks: (limit = 20) =>
    requestJson<AutonomousTaskRecord[]>(`/api/autonomy/tasks?limit=${limit}`),
  createAutonomousTask: (payload: AutonomousTaskCreate) =>
    requestJson<AutonomousTaskRecord>("/api/autonomy/tasks", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  cancelAutonomousTask: (taskId: string) =>
    requestJson<AutonomousTaskRecord>(`/api/autonomy/tasks/${taskId}/cancel`, {
      method: "POST",
    }),
  getMcpServers: () => requestJson<MCPServerStatus[]>("/api/mcp/servers"),
  getGoalModels: () =>
    requestJson<
      Array<{
        id: string;
        provider: string;
        label: string;
        tier: string;
        strengths: string;
        in_cost_per_m: number;
        out_cost_per_m: number;
        supports_vision: boolean;
        best_for?: string[];
      }>
    >("/api/goals/models"),
  getGoals: (limit = 30) => requestJson<GoalRecord[]>(`/api/goals?limit=${limit}`),
  createGoal: (payload: Partial<GoalCreate> & { goal: string }) =>
    requestJson<GoalRecord>("/api/goals", {
      method: "POST",
      body: JSON.stringify({
        max_cost_usd: 1.5,
        orchestrator_model: null,
        allowed_models: null,
        notify_on_finish: true,
        ...payload,
      }),
    }),
  getGoal: (goalId: string) => requestJson<GoalRecord>(`/api/goals/${goalId}`),
  steerGoal: (goalId: string, payload: Partial<GoalSteerRequest> & { message: string }) =>
    requestJson<GoalRecord>(`/api/goals/${goalId}/steer`, {
      method: "POST",
      body: JSON.stringify({
        resume: true,
        extra_budget_usd: null,
        ...payload,
      }),
    }),
  pauseGoal: (goalId: string) =>
    requestJson<GoalRecord>(`/api/goals/${goalId}/pause`, {
      method: "POST",
    }),
  resumeGoal: (goalId: string) =>
    requestJson<GoalRecord>(`/api/goals/${goalId}/resume`, {
      method: "POST",
    }),
  cancelGoal: (goalId: string) =>
    requestJson<GoalRecord>(`/api/goals/${goalId}/cancel`, {
      method: "POST",
    }),
  triggerEmergencyStop: () =>
    requestJson<{ ok: boolean; emergency_stop: boolean; cancelled_tasks: string[] }>(
      "/api/autonomy/emergency-stop",
      { method: "POST" }
    ),
};
