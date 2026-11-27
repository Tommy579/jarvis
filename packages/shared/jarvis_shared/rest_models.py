"""Schemas Pydantic v2 des endpoints REST de jarvis-core (section 1.2)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .ws_protocol import HighRiskLiteral, RiskLevelLiteral, SensorName, UrgencyLiteral, UsageStats

SensitivityLevel = Literal["normal", "privé", "très privé"]


# === Conversation ===


class ToolCallRecord(BaseModel):
    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevelLiteral = 0
    ok: bool = True
    summary: str = ""
    ts: str


class ChatMessage(BaseModel):
    id: str
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: str
    usage: UsageStats | None = None
    tools: list[ToolCallRecord] = Field(default_factory=list)


# === Memoire ===


class FactRecord(BaseModel):
    id: str
    subject: str = "Tom"
    predicate: str
    object: str
    text: str
    category: str = "general"
    confidence: float = 0.9
    source: str = "conversation"
    sensitivity: SensitivityLevel = "normal"
    created_at: str
    updated_at: str
    last_used_at: str | None = None
    superseded_by: str | None = None


class FactCreate(BaseModel):
    subject: str = "Tom"
    predicate: str
    object: str
    text: str
    category: str = "general"
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    source: str = "manual"
    sensitivity: SensitivityLevel = "normal"


class FactUpdate(BaseModel):
    text: str | None = None
    category: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    sensitivity: SensitivityLevel | None = None
    superseded_by: str | None = None


class WikiPage(BaseModel):
    slug: str
    title: str
    content: str
    updated_at: str


class WikiUpdate(BaseModel):
    content: str
    title: str | None = None


class PersonRecord(BaseModel):
    id: str
    name: str
    relation: str
    birthday: str | None = None
    contact_ref: str | None = None
    notes: str = ""


class MemoryExport(BaseModel):
    exported_at: str
    facts: list[FactRecord]
    people: list[PersonRecord]
    wiki_pages: list[WikiPage]
    markdown_bundle: str


# === Onboarding ===


class OnboardingQuestion(BaseModel):
    id: str
    prompt: str
    field_key: str
    category: str
    sensitivity: SensitivityLevel = "normal"
    placeholder: str = ""
    already_known: bool = False
    known_value: str | None = None


class OnboardingModuleStatus(BaseModel):
    id: str
    title: str
    description: str
    completed: bool
    answered_count: int
    total_count: int
    skipped_count: int
    questions: list[OnboardingQuestion] = Field(default_factory=list)


class OnboardingStatus(BaseModel):
    completed_modules: int
    total_modules: int
    daily_question: OnboardingQuestion | None = None
    quarterly_check_due: bool = False
    modules: list[OnboardingModuleStatus]


class OnboardingAnswerRequest(BaseModel):
    answers: dict[str, str]


# === Modeles & Couts ===


ThinkingModeLiteral = Literal["auto", "off", "low", "medium", "high"]


class RoleModelConfig(BaseModel):
    role: str
    model: str
    provider: str
    fallback: list[str] = Field(default_factory=list)
    thinking_mode: ThinkingModeLiteral = "auto"
    supports_vision: bool = False
    supports_tools: bool = True


class UpdateRoleModelRequest(BaseModel):
    role: str
    model: str
    provider: str | None = None
    fallback: list[str] | None = None
    thinking_mode: ThinkingModeLiteral | None = None


class DailyUsageItem(BaseModel):
    date: str
    model: str
    calls: int
    tokens_in: int
    tokens_out: int
    cost: float


class EvalScenarioResult(BaseModel):
    id: str
    title: str
    model: str
    passed: bool
    latency_ms: float
    score: float
    notes: str = ""


class EvalSuiteResult(BaseModel):
    run_at: str
    total: int
    passed: int
    scenarios: list[EvalScenarioResult]


# === Attention & Alertes ===


class AlertRecord(BaseModel):
    id: str
    ts: str
    channel: str
    urgency: UrgencyLiteral
    message: str
    reason: str
    status: Literal["sent", "acked", "too_noisy"]
    acked_at: str | None = None


class VipContact(BaseModel):
    name: str
    identifiers: list[str] = Field(default_factory=list)
    default_urgency: UrgencyLiteral = 2


class AttentionRules(BaseModel):
    quiet_start: str = "23:00"
    quiet_end: str = "08:00"
    timezone: str = "Europe/Paris"
    max_calls_per_day: int = 3
    vip_contacts: list[VipContact] = Field(default_factory=list)
    min_urgency_for_call: UrgencyLiteral = 3


# === Approbations ===


class ApprovalItem(BaseModel):
    id: str
    tool_name: str
    description: str
    args: dict[str, Any] = Field(default_factory=dict)
    risk: HighRiskLiteral
    created_at: str
    expires_at: str
    status: Literal["pending", "approved", "denied", "expired"] = "pending"


class ApprovalDecisionRequest(BaseModel):
    approved: bool


# === Integrations & Capteurs ===


class IntegrationInfo(BaseModel):
    id: str
    name: str
    priority: Literal[1, 2, 3]
    enabled: bool
    connected: bool
    last_sync: str | None = None
    description: str
    tools: list[str] = Field(default_factory=list)
    auth_type: Literal["oauth", "api_key", "url", "csv"] = "api_key"
    oauth_provider: str | None = None
    authorize_url: str | None = None
    oauth_client_configured: bool = False


class SensorState(BaseModel):
    sensor: SensorName
    active: bool
    paused: bool
    detail: str = ""


class BlacklistConfig(BaseModel):
    apps: list[str] = Field(default_factory=list)
    global_paused: bool = False
    sensors: list[SensorState] = Field(default_factory=list)


# === Systeme ===


class SystemAutostartConfig(BaseModel):
    enabled: bool
    supported: bool = True
    desktop_file: str = ""
    detail: str = ""


class SystemAutostartUpdate(BaseModel):
    enabled: bool


# === Autonomie & Taches de fond ===


AutonomyModeLiteral = Literal["supervised", "autonomous"]
AutonomousTaskStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


class AutonomyPolicyConfig(BaseModel):
    mode: AutonomyModeLiteral = "supervised"
    max_auto_risk: RiskLevelLiteral = 2
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(
        default_factory=lambda: ["paypal.com", "stripe.com", "impots.gouv.fr"]
    )
    allow_browser_in_untrusted: bool = True
    block_shell_in_untrusted: bool = True
    max_steps: int = Field(default=0, ge=0, le=1000)
    max_cost_usd: float = Field(default=10.0, ge=0.01, le=100.0)
    emergency_stop: bool = False


class AutonomyPolicyUpdate(BaseModel):
    mode: AutonomyModeLiteral | None = None
    max_auto_risk: RiskLevelLiteral | None = None
    allowed_domains: list[str] | None = None
    blocked_domains: list[str] | None = None
    allow_browser_in_untrusted: bool | None = None
    block_shell_in_untrusted: bool | None = None
    max_steps: int | None = Field(default=None, ge=0, le=1000)
    max_cost_usd: float | None = Field(default=None, ge=0.01, le=100.0)
    emergency_stop: bool | None = None


class AutonomousTaskCreate(BaseModel):
    goal: str = Field(min_length=1, max_length=4000)
    max_steps: int | None = Field(default=None, ge=1, le=1000)
    max_cost_usd: float | None = Field(default=None, ge=0.01, le=100.0)
    notify_on_finish: bool = True
    role: str = "agent"


class AutonomousTaskRecord(BaseModel):
    id: str
    goal: str
    status: AutonomousTaskStatus = "queued"
    iterations: int = 0
    max_iterations: int = 0
    tokens_used: int = 0
    cost_usd: float = 0.0
    current_step: str = ""
    result_summary: str = ""
    error: str | None = None
    notify_on_finish: bool = True
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class AutonomyAuditEntry(BaseModel):
    id: str
    ts: str
    task_id: str | None = None
    tool_name: str
    risk: RiskLevelLiteral = 0
    status: str
    target: str = ""
    summary: str = ""
    error: str | None = None


class MCPServerStatus(BaseModel):
    name: str
    enabled: bool
    transport: str
    connected: bool
    tools_count: int = 0
    tools: list[str] = Field(default_factory=list)
    error: str | None = None


# === Orchestration par objectifs & sous-agents ===


GoalStatus = Literal[
    "planning",
    "running",
    "verifying",
    "waiting_user",
    "waiting_approval",
    "paused",
    "paused_budget",
    "completed",
    "failed",
    "cancelled",
]
SubtaskStatus = Literal[
    "pending",
    "running",
    "stalled",
    "retrying",
    "completed",
    "failed",
    "skipped",
]
GoalEventKind = Literal[
    "plan",
    "spawn",
    "watchdog",
    "steer",
    "switch_model",
    "split",
    "verify",
    "pause",
    "resume",
    "complete",
    "error",
]


class GoalCreate(BaseModel):
    goal: str = Field(min_length=1, max_length=8000)
    max_cost_usd: float = Field(default=0.0, ge=0.0, le=100.0)
    orchestrator_model: str | None = None
    allowed_models: list[str] | None = None
    notify_on_finish: bool = True


class GoalSteerRequest(BaseModel):
    message: str = Field(default="", max_length=4000)
    resume: bool = True
    extra_budget_usd: float | None = Field(default=None, ge=0.05, le=100.0)


class SubagentRunRecord(BaseModel):
    id: str
    goal_id: str
    subtask_id: str
    model: str
    provider: str = ""
    role_profile: str = "general"
    status: SubtaskStatus = "running"
    iterations: int = 0
    max_iterations: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    last_tool: str = ""
    last_heartbeat_at: str = ""
    error: str | None = None
    result_summary: str = ""
    started_at: str = ""
    finished_at: str | None = None


class SubtaskRecord(BaseModel):
    id: str
    goal_id: str
    title: str
    description: str = ""
    instructions: str = ""
    role_profile: str = "general"
    assigned_model: str = "deepseek/deepseek-v4.1-flash-1017"
    depends_on: list[str] = Field(default_factory=list)
    status: SubtaskStatus = "pending"
    attempt: int = 0
    max_attempts: int = 3
    result: str = ""
    error: str | None = None
    runs: list[SubagentRunRecord] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class GoalEventRecord(BaseModel):
    id: str
    goal_id: str
    ts: str
    kind: GoalEventKind
    subtask_id: str | None = None
    run_id: str | None = None
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class GoalRecord(BaseModel):
    id: str
    title: str
    goal: str
    status: GoalStatus = "planning"
    success_criteria: list[str] = Field(default_factory=list)
    verified_criteria: list[str] = Field(default_factory=list)
    orchestrator_model: str = "gemini-4-argon"
    allowed_models: list[str] = Field(default_factory=list)
    tokens_used: int = 0
    cost_usd: float = 0.0
    max_cost_usd: float = 1.5
    progress_pct: int = 0
    current_phase: str = "Planification"
    final_summary: str = ""
    pause_reason: str | None = None
    notify_on_finish: bool = True
    subtasks: list[SubtaskRecord] = Field(default_factory=list)
    events: list[GoalEventRecord] = Field(default_factory=list)
    created_at: str
    updated_at: str
    finished_at: str | None = None


# === Profils vocaux (verification du locuteur) ===


class VoiceProfileRecord(BaseModel):
    id: str
    name: str
    role: str = "ami"
    sample_count: int = 1
    duration_sec: float = 0.0
    created_at: str
    updated_at: str


class VoiceProfilesConfig(BaseModel):
    enabled: bool = False
    threshold: float = 0.48
    profiles: list[VoiceProfileRecord] = Field(default_factory=list)


class VoiceVerifyResult(BaseModel):
    accepted: bool
    matched_name: str | None = None
    matched_id: str | None = None
    score: float = 0.0
    threshold: float = 0.48
    reason: str = ""


# === Systeme, version et sauvegardes ===


class VersionInfo(BaseModel):
    version: str
    schema_version: int
    python: str
    platform: str
    data_dir: str
    started_at: str
    uptime_seconds: int


class UpdateStatus(BaseModel):
    current_version: str
    latest_version: str | None = None
    update_available: bool = False
    release_url: str | None = None
    published_at: str | None = None
    checked_at: str
    detail: str = ""


class BackupRecord(BaseModel):
    name: str
    size_bytes: int
    created_at: str


class BackupConfig(BaseModel):
    directory: str
    keep: int
    last_backup_at: str | None = None
    backups: list[BackupRecord] = Field(default_factory=list)
