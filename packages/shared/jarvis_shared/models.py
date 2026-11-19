"""Modeles de donnees internes neutres (Pydantic v2) pour jarvis-core, jarvis-desktop et jarvis-web."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Sensitivity(str, Enum):
    NORMAL = "normal"
    PRIVATE = "prive"
    HIGHLY_PRIVATE = "tres_prive"


class RiskLevel(IntEnum):
    READ_ONLY = 0
    REVERSIBLE_WRITE = 1
    SENSITIVE = 2
    DANGEROUS = 3


class UrgencyLevel(IntEnum):
    INFO = 0
    NORMAL = 1
    IMPORTANT = 2
    URGENT = 3
    CRITICAL = 4


class AgentState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ALERT = "alert"
    OFFLINE = "offline"


class ImageAttachment(BaseModel):
    mime_type: str = "image/png"
    base64_data: str
    description: str | None = None


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: f"call_{uuid.uuid4().hex[:12]}")
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Usage(BaseModel):
    model: str = ""
    provider: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0


class ToolResult(BaseModel):
    tool_call_id: str
    name: str
    output: Any
    is_error: bool = False
    is_untrusted: bool = False
    source: str | None = None
    # Un outil qui consomme des tokens hors du flux LLM (analyse media, sous-agent...)
    # doit le declarer ici pour entrer dans le budget max_cost_usd.
    usage: Usage | None = None


class Message(BaseModel):
    id: str = Field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    role: Role
    content: str = ""
    images: list[ImageAttachment] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    contains_untrusted: bool = False
    created_at: str = Field(default_factory=utc_now_iso)


class StreamDelta(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage | None = None
    finish_reason: str | None = None


class ModelCapabilities(BaseModel):
    model_id: str
    provider: str
    supports_tools: bool = True
    supports_vision: bool = False
    supports_audio: bool = False
    context_length: int = 128000
    input_cost_per_1m: float = 0.0
    output_cost_per_1m: float = 0.0


class Fact(BaseModel):
    id: str = Field(default_factory=lambda: f"fact_{uuid.uuid4().hex[:12]}")
    subject: str = "Tom"
    predicate: str
    object: str
    text: str
    category: str = "general"
    confidence: float = 0.9
    source: str = "conversation"
    sensitivity: Sensitivity = Sensitivity.NORMAL
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    last_used_at: str | None = None
    superseded_by: str | None = None


class Episode(BaseModel):
    id: str = Field(default_factory=lambda: f"ep_{uuid.uuid4().hex[:12]}")
    ts: str = Field(default_factory=utc_now_iso)
    kind: str = "conversation"
    channel: str = "web"
    summary: str
    raw_ref: str | None = None


class Person(BaseModel):
    id: str = Field(default_factory=lambda: f"pers_{uuid.uuid4().hex[:12]}")
    name: str
    relation: str
    birthday: str | None = None
    contact_ref: str | None = None
    is_vip: bool = False
    notes: str = ""


class SystemEvent(BaseModel):
    id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    ts: str = Field(default_factory=utc_now_iso)
    source: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    dedup_key: str | None = None
    urgency: UrgencyLevel | None = None
    handled: bool = False


class NotificationRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"notif_{uuid.uuid4().hex[:12]}")
    ts: str = Field(default_factory=utc_now_iso)
    channel: str
    urgency: UrgencyLevel
    message: str
    reason: str = ""
    dedup_key: str | None = None
    status: Literal["sent", "acked", "escalated", "suppressed", "too_noisy"] = "sent"
    acked_at: str | None = None


class ApprovalRequest(BaseModel):
    id: str = Field(default_factory=lambda: f"appr_{uuid.uuid4().hex[:12]}")
    tool_name: str
    arguments: dict[str, Any]
    description: str
    exact_command: str | None = None
    risk: RiskLevel
    created_at: str = Field(default_factory=utc_now_iso)
    expires_at: str
    status: Literal["pending", "approved", "denied", "expired"] = "pending"
