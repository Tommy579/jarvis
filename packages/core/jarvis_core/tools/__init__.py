"""Registre d'outils, matrice de permissions 0..3 et defense anti-injection."""

from .approvals import ApprovalManager
from .integrations import IntegrationToolkit
from .registry import (
    ToolRegistry,
    ToolSpec,
    contains_untrusted_tags,
    wrap_untrusted_content,
)

__all__ = [
    "ApprovalManager",
    "IntegrationToolkit",
    "ToolRegistry",
    "ToolSpec",
    "contains_untrusted_tags",
    "wrap_untrusted_content",
]
