"""Agent de bureau jarvis-desktop (capteurs KDE Wayland et pipeline vocal temps reel)."""

from .voice_pipeline import GeminiLiveBridge, RealtimeVoicePipeline, VoiceLatencyMetrics
from .wayland_sensors import ActiveWindowInfo, WaylandSensors

__all__ = [
    "ActiveWindowInfo",
    "GeminiLiveBridge",
    "RealtimeVoicePipeline",
    "VoiceLatencyMetrics",
    "WaylandSensors",
]
