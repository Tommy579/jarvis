"""Schemas Pydantic v2 des endpoints REST de omi-core (section 1.2)."""

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


class RoleModelConfig(BaseModel):
    role: str
    model: str
    provider: str
    fallback: list[str] = Field(default_factory=list)
    supports_vision: bool = False
    supports_tools: bool = True


class UpdateRoleModelRequest(BaseModel):
    role: str
    model: str
    provider: str | None = None
    fallback: list[str] | None = None


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
