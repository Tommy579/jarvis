"""Serveur HTTP REST et WebSocket de omi-core."""

from typing import Any

from .server import OmiCoreState, create_app

__all__ = ["OmiCoreState", "app", "create_app"]


def __getattr__(name: str) -> Any:
    if name == "app":
        from . import server

        return server.app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
