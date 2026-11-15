/* eslint-disable */
// Fichier genere automatiquement depuis packages/shared/omi_shared/. Ne pas modifier a la main.

export type SphereState = "idle" | "listening" | "thinking" | "speaking" | "alert" | "offline";
export type SensorName = "screen" | "mic" | "camera";
export type RiskLevel = 0 | 1 | 2 | 3;
export type HighRiskLevel = 2 | 3;
export type UrgencyLevel = 0 | 1 | 2 | 3 | 4;
export type SensitivityLevel = "normal" | "privé" | "très privé";

export interface UserTextPayload {
  text: string;
}

export interface UserTextEvent {
  type: "user.text";
  payload: UserTextPayload;
}

export interface UserAudioPayload {
  chunk: string;
}

export interface UserAudioEvent {
  type: "user.audio";
  payload: UserAudioPayload;
}

export interface ActionApprovePayload {
  id: string;
  approved: boolean;
}

export interface ActionApproveEvent {
  type: "action.approve";
  payload: ActionApprovePayload;
}

export interface AlertAckPayload {
  id: string;
}

export interface AlertAckEvent {
  type: "alert.ack";
  payload: AlertAckPayload;
}

export interface SettingsModelPayload {
  role: string;
  model: string;
}

export interface SettingsModelEvent {
  type: "settings.model";
  payload: SettingsModelPayload;
}

export interface SensorsTogglePayload {
  sensor: "screen" | "mic" | "camera";
  paused: boolean;
}

export interface SensorsToggleEvent {
  type: "sensors.toggle";
  payload: SensorsTogglePayload;
}

export interface UserInterruptPayload {
  reason: string;
}

export interface UserInterruptEvent {
  type: "user.interrupt";
  payload: UserInterruptPayload;
}

export interface StatePayload {
  value: "idle" | "listening" | "thinking" | "speaking" | "alert" | "offline";
}

export interface StateEvent {
  type: "state";
  payload: StatePayload;
}

export interface TranscriptPayload {
  text: string;
  speaker: "user";
}

export interface TranscriptPartialEvent {
  type: "transcript.partial";
  payload: TranscriptPayload;
}

export interface TranscriptFinalEvent {
  type: "transcript.final";
  payload: TranscriptPayload;
}

export interface AssistantDeltaPayload {
  text: string;
  message_id: string;
}

export interface AssistantDeltaEvent {
  type: "assistant.delta";
  payload: AssistantDeltaPayload;
}

export interface UsageStats {
  model: string;
  tokens_in: number;
  tokens_out: number;
  cost: number;
}

export interface AssistantDonePayload {
  message_id: string;
  usage: UsageStats;
}

export interface AssistantDoneEvent {
  type: "assistant.done";
  payload: AssistantDonePayload;
}

export interface ToolCallPayload {
  id: string;
  name: string;
  args: Record<string, unknown>;
  risk: 0 | 1 | 2 | 3;
}

export interface ToolCallEvent {
  type: "tool.call";
  payload: ToolCallPayload;
}

export interface ToolResultPayload {
  id: string;
  ok: boolean;
  summary: string;
}

export interface ToolResultEvent {
  type: "tool.result";
  payload: ToolResultPayload;
}

export interface TtsChunkPayload {
  audio: string;
  seq: number;
  is_last: boolean;
}

export interface TtsChunkEvent {
  type: "tts.chunk";
  payload: TtsChunkPayload;
}

export interface MicLevelPayload {
  value: number;
}

export interface MicLevelEvent {
  type: "mic.level";
  payload: MicLevelPayload;
}

export interface ApprovalRequestPayload {
  id: string;
  description: string;
  risk: 2 | 3;
  expires_at: string;
}

export interface ApprovalRequestEvent {
  type: "approval.request";
  payload: ApprovalRequestPayload;
}

export interface AlertPayload {
  id: string;
  urgency: 0 | 1 | 2 | 3 | 4;
  message: string;
  reason: string;
}

export interface AlertEvent {
  type: "alert";
  payload: AlertPayload;
}

export interface ToolCallRecord {
  id: string;
  name: string;
  args: Record<string, unknown>;
  risk: 0 | 1 | 2 | 3;
  ok: boolean;
  summary: string;
  ts: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  usage: UsageStats | null;
  tools: ToolCallRecord[];
}

export interface FactRecord {
  id: string;
  subject: string;
  predicate: string;
  object: string;
  text: string;
  category: string;
  confidence: number;
  source: string;
  sensitivity: "normal" | "privé" | "très privé";
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
  superseded_by: string | null;
}

export interface FactCreate {
  subject: string;
  predicate: string;
  object: string;
  text: string;
  category: string;
  confidence: number;
  source: string;
  sensitivity: "normal" | "privé" | "très privé";
}

export interface FactUpdate {
  text: string | null;
  category: string | null;
  confidence: number | null;
  sensitivity: "normal" | "privé" | "très privé" | null;
  superseded_by: string | null;
}

export interface WikiPage {
  slug: string;
  title: string;
  content: string;
  updated_at: string;
}

export interface WikiUpdate {
  content: string;
  title: string | null;
}

export interface PersonRecord {
  id: string;
  name: string;
  relation: string;
  birthday: string | null;
  contact_ref: string | null;
  notes: string;
}

export interface MemoryExport {
  exported_at: string;
  facts: FactRecord[];
  people: PersonRecord[];
  wiki_pages: WikiPage[];
  markdown_bundle: string;
}

export interface OnboardingQuestion {
  id: string;
  prompt: string;
  field_key: string;
  category: string;
  sensitivity: "normal" | "privé" | "très privé";
  placeholder: string;
  already_known: boolean;
  known_value: string | null;
}

export interface OnboardingModuleStatus {
  id: string;
  title: string;
  description: string;
  completed: boolean;
  answered_count: number;
  total_count: number;
  skipped_count: number;
  questions: OnboardingQuestion[];
}

export interface OnboardingStatus {
  completed_modules: number;
  total_modules: number;
  daily_question: OnboardingQuestion | null;
  quarterly_check_due: boolean;
  modules: OnboardingModuleStatus[];
}

export interface OnboardingAnswerRequest {
  answers: Record<string, string>;
}

export interface RoleModelConfig {
  role: string;
  model: string;
  provider: string;
  fallback: string[];
  supports_vision: boolean;
  supports_tools: boolean;
}

export interface UpdateRoleModelRequest {
  role: string;
  model: string;
  provider: string | null;
  fallback: string[] | null;
}

export interface DailyUsageItem {
  date: string;
  model: string;
  calls: number;
  tokens_in: number;
  tokens_out: number;
  cost: number;
}

export interface EvalScenarioResult {
  id: string;
  title: string;
  model: string;
  passed: boolean;
  latency_ms: number;
  score: number;
  notes: string;
}

export interface EvalSuiteResult {
  run_at: string;
  total: number;
  passed: number;
  scenarios: EvalScenarioResult[];
}

export interface AlertRecord {
  id: string;
  ts: string;
  channel: string;
  urgency: 0 | 1 | 2 | 3 | 4;
  message: string;
  reason: string;
  status: "sent" | "acked" | "too_noisy";
  acked_at: string | null;
}

export interface VipContact {
  name: string;
  identifiers: string[];
  default_urgency: 0 | 1 | 2 | 3 | 4;
}

export interface AttentionRules {
  quiet_start: string;
  quiet_end: string;
  timezone: string;
  max_calls_per_day: number;
  vip_contacts: VipContact[];
  min_urgency_for_call: 0 | 1 | 2 | 3 | 4;
}

export interface ApprovalItem {
  id: string;
  tool_name: string;
  description: string;
  args: Record<string, unknown>;
  risk: 2 | 3;
  created_at: string;
  expires_at: string;
  status: "pending" | "approved" | "denied" | "expired";
}

export interface ApprovalDecisionRequest {
  approved: boolean;
}

export interface IntegrationInfo {
  id: string;
  name: string;
  priority: 1 | 2 | 3;
  enabled: boolean;
  connected: boolean;
  last_sync: string | null;
  description: string;
  tools: string[];
  auth_type: "oauth" | "api_key" | "url" | "csv";
  oauth_provider: string | null;
  authorize_url: string | null;
  oauth_client_configured: boolean;
}

export interface SensorState {
  sensor: "screen" | "mic" | "camera";
  active: boolean;
  paused: boolean;
  detail: string;
}

export interface BlacklistConfig {
  apps: string[];
  global_paused: boolean;
  sensors: SensorState[];
}

export interface SystemAutostartConfig {
  enabled: boolean;
  supported: boolean;
  desktop_file: string;
  detail: string;
}

export interface SystemAutostartUpdate {
  enabled: boolean;
}

export type ClientWsEvent = UserTextEvent | UserAudioEvent | ActionApproveEvent | AlertAckEvent | SettingsModelEvent | SensorsToggleEvent | UserInterruptEvent;
export type ServerWsEvent = StateEvent | TranscriptPartialEvent | TranscriptFinalEvent | AssistantDeltaEvent | AssistantDoneEvent | ToolCallEvent | ToolResultEvent | TtsChunkEvent | MicLevelEvent | ApprovalRequestEvent | AlertEvent;
