import type {
  AlertRecord,
  ApprovalItem,
  AttentionRules,
  BlacklistConfig,
  ChatMessage,
  DailyUsageItem,
  EvalSuiteResult,
  FactCreate,
  FactRecord,
  FactUpdate,
  IntegrationInfo,
  MemoryExport,
  OnboardingStatus,
  PersonRecord,
  RoleModelConfig,
  SystemAutostartConfig,
  WikiPage,
} from "../types/generated";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const token =
    typeof window !== "undefined" ? window.localStorage.getItem("omi_api_token") ?? "" : "";
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
    throw new Error(message);
  }
  return (await resp.json()) as T;
}

export const apiClient = {
  getMessages: () => requestJson<ChatMessage[]>("/api/messages"),
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
  speakVoice: (text: string) =>
    requestJson<{ audio_base64: string; sample_rate: number }>("/api/voice/speak", {
      method: "POST",
      body: JSON.stringify({ text }),
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
  updateRoleModel: (role: string, model: string, fallback?: string[]) =>
    requestJson<RoleModelConfig>("/api/models/role", {
      method: "PUT",
      body: JSON.stringify({ role, model, fallback }),
    }),
  getDailyUsage: () => requestJson<DailyUsageItem[]>("/api/usage/daily"),
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
};
