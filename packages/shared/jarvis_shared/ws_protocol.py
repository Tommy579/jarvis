"""Contrats WebSocket client <-> serveur pour JARVIS 2.0 (section 1.1)."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

SphereState = Literal["idle", "listening", "thinking", "speaking", "alert", "offline"]
SensorName = Literal["screen", "mic", "camera"]
RiskLevelLiteral = Literal[0, 1, 2, 3]
HighRiskLiteral = Literal[2, 3]
UrgencyLiteral = Literal[0, 1, 2, 3, 4]


# === Client -> Serveur ===


class UserTextPayload(BaseModel):
    text: str


class UserTextEvent(BaseModel):
    type: Literal["user.text"] = "user.text"
    payload: UserTextPayload


class UserAudioPayload(BaseModel):
    chunk: str


class UserAudioEvent(BaseModel):
    type: Literal["user.audio"] = "user.audio"
    payload: UserAudioPayload


class ActionApprovePayload(BaseModel):
    id: str
    approved: bool


class ActionApproveEvent(BaseModel):
    type: Literal["action.approve"] = "action.approve"
    payload: ActionApprovePayload


class AlertAckPayload(BaseModel):
    id: str


class AlertAckEvent(BaseModel):
    type: Literal["alert.ack"] = "alert.ack"
    payload: AlertAckPayload


class SettingsModelPayload(BaseModel):
    role: str
    model: str
    thinking_mode: Literal["auto", "off", "low", "medium", "high"] | None = None


class SettingsModelEvent(BaseModel):
    type: Literal["settings.model"] = "settings.model"
    payload: SettingsModelPayload


class SensorsTogglePayload(BaseModel):
    sensor: SensorName
    paused: bool


class SensorsToggleEvent(BaseModel):
    type: Literal["sensors.toggle"] = "sensors.toggle"
    payload: SensorsTogglePayload


class UserInterruptPayload(BaseModel):
    reason: str = "user_cancel"


class UserInterruptEvent(BaseModel):
    type: Literal["user.interrupt"] = "user.interrupt"
    payload: UserInterruptPayload = Field(default_factory=UserInterruptPayload)


ClientWsEvent = Annotated[
    Union[
        UserTextEvent,
        UserAudioEvent,
        ActionApproveEvent,
        AlertAckEvent,
        SettingsModelEvent,
        SensorsToggleEvent,
        UserInterruptEvent,
    ],
    Field(discriminator="type"),
]


# === Serveur -> Client ===


class StatePayload(BaseModel):
    value: SphereState


class StateEvent(BaseModel):
    type: Literal["state"] = "state"
    payload: StatePayload


class TranscriptPayload(BaseModel):
    text: str
    speaker: Literal["user"] = "user"


class TranscriptPartialEvent(BaseModel):
    type: Literal["transcript.partial"] = "transcript.partial"
    payload: TranscriptPayload


class TranscriptFinalEvent(BaseModel):
    type: Literal["transcript.final"] = "transcript.final"
    payload: TranscriptPayload


class AssistantDeltaPayload(BaseModel):
    text: str
    message_id: str


class AssistantDeltaEvent(BaseModel):
    type: Literal["assistant.delta"] = "assistant.delta"
    payload: AssistantDeltaPayload


class UsageStats(BaseModel):
    model: str
    tokens_in: int
    tokens_out: int
    cost: float


class AssistantDonePayload(BaseModel):
    message_id: str
    usage: UsageStats


class AssistantDoneEvent(BaseModel):
    type: Literal["assistant.done"] = "assistant.done"
    payload: AssistantDonePayload


class ToolCallPayload(BaseModel):
    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevelLiteral


class ToolCallEvent(BaseModel):
    type: Literal["tool.call"] = "tool.call"
    payload: ToolCallPayload


class ToolResultPayload(BaseModel):
    id: str
    ok: bool
    summary: str


class ToolResultEvent(BaseModel):
    type: Literal["tool.result"] = "tool.result"
    payload: ToolResultPayload


class TtsChunkPayload(BaseModel):
    audio: str
    seq: int
    is_last: bool


class TtsChunkEvent(BaseModel):
    type: Literal["tts.chunk"] = "tts.chunk"
    payload: TtsChunkPayload


class MicLevelPayload(BaseModel):
    value: float


class MicLevelEvent(BaseModel):
    type: Literal["mic.level"] = "mic.level"
    payload: MicLevelPayload


class ApprovalRequestPayload(BaseModel):
    id: str
    description: str
    risk: HighRiskLiteral
    expires_at: str


class ApprovalRequestEvent(BaseModel):
    type: Literal["approval.request"] = "approval.request"
    payload: ApprovalRequestPayload


class AlertPayload(BaseModel):
    id: str
    urgency: UrgencyLiteral
    message: str
    reason: str


class AlertEvent(BaseModel):
    type: Literal["alert"] = "alert"
    payload: AlertPayload


class GoalUpdatedPayload(BaseModel):
    goal_id: str
    title: str
    status: str
    progress_pct: int = 0
    current_phase: str = ""
    cost_usd: float = 0.0
    max_cost_usd: float = 1.5
    orchestrator_model: str = ""
    pause_reason: str | None = None
    final_summary: str = ""


class GoalUpdatedEvent(BaseModel):
    type: Literal["goal.updated"] = "goal.updated"
    payload: GoalUpdatedPayload


class SubagentStatusPayload(BaseModel):
    goal_id: str
    subtask_id: str
    run_id: str
    title: str = ""
    model: str
    role_profile: str = "general"
    status: str
    iterations: int = 0
    max_iterations: int = 0
    cost_usd: float = 0.0
    last_tool: str = ""
    error: str | None = None


class SubagentStatusEvent(BaseModel):
    type: Literal["goal.subagent"] = "goal.subagent"
    payload: SubagentStatusPayload


class SupervisorInterventionPayload(BaseModel):
    goal_id: str
    subtask_id: str | None = None
    run_id: str | None = None
    kind: str
    message: str
    ts: str


class SupervisorInterventionEvent(BaseModel):
    type: Literal["goal.intervention"] = "goal.intervention"
    payload: SupervisorInterventionPayload


VisualElementKind = Literal[
    "board",
    "chip",
    "led",
    "resistor",
    "sensor",
    "button",
    "wire",
    "node",
    "box",
    "cylinder",
    "sphere",
    "arrow",
    "label",
    "svg_path",
]


class VisualElementPayload(BaseModel):
    id: str
    kind: VisualElementKind = "node"
    label: str
    sublabel: str | None = None
    color: str = "#38bdf8"
    x: float = 50.0
    y: float = 50.0
    z: float = 0.0
    width: float = 14.0
    height: float = 10.0
    from_id: str | None = None
    to_id: str | None = None
    from_pin: str | None = None
    to_pin: str | None = None
    svg_path: str | None = None


class VisualStepPayload(BaseModel):
    index: int
    title: str
    spoken_text: str
    annotation: str = ""
    reveal_elements: list[str] = Field(default_factory=list)
    highlight_elements: list[str] = Field(default_factory=list)
    active_connections: list[str] = Field(default_factory=list)


class VisualArtifactPayload(BaseModel):
    id: str
    title: str
    subtitle: str = ""
    mode: Literal["2d", "3d"] = "3d"
    category: Literal["hardware", "architecture", "algorithm", "physics", "custom"] = "hardware"
    elements: list[VisualElementPayload] = Field(default_factory=list)
    steps: list[VisualStepPayload] = Field(default_factory=list)
    auto_play: bool = True


class VisualArtifactEvent(BaseModel):
    type: Literal["visual.artifact"] = "visual.artifact"
    payload: VisualArtifactPayload


ServerWsEvent = Annotated[
    Union[
        StateEvent,
        TranscriptPartialEvent,
        TranscriptFinalEvent,
        AssistantDeltaEvent,
        AssistantDoneEvent,
        ToolCallEvent,
        ToolResultEvent,
        TtsChunkEvent,
        MicLevelEvent,
        ApprovalRequestEvent,
        AlertEvent,
        GoalUpdatedEvent,
        SubagentStatusEvent,
        SupervisorInterventionEvent,
        VisualArtifactEvent,
    ],
    Field(discriminator="type"),
]
