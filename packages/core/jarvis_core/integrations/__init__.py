"""Connecteurs d'integrations externes JARVIS 2.0."""

from .apple_calendar import AppleCalendarIntegration
from .base import BaseIntegration, IntegrationRegistry
from .github_probes import GitHubProbesIntegration
from .gmail import GmailIntegration
from .google_calendar import GoogleCalendarIntegration
from .google_contacts import GoogleContactsIntegration
from .home_assistant import HomeAssistantIntegration
from .lifestyle import FinanceIntegration, HealthIntegration, SpotifyIntegration
from .notion import NotionIntegration
from .outlook import OutlookCalendarIntegration, OutlookMailIntegration
from .weather_transport import WeatherTransportIntegration

__all__ = [
    "AppleCalendarIntegration",
    "BaseIntegration",
    "FinanceIntegration",
    "GitHubProbesIntegration",
    "GmailIntegration",
    "GoogleCalendarIntegration",
    "GoogleContactsIntegration",
    "HealthIntegration",
    "HomeAssistantIntegration",
    "IntegrationRegistry",
    "NotionIntegration",
    "OutlookCalendarIntegration",
    "OutlookMailIntegration",
    "SpotifyIntegration",
    "WeatherTransportIntegration",
]
