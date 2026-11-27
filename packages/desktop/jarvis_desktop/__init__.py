"""Agent de bureau jarvis-desktop (adaptateurs Linux/Windows et icone systeme)."""

from .platform import current_platform_adapter

__all__ = ["current_platform_adapter"]
