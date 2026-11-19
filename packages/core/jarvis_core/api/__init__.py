"""Serveur HTTP REST et WebSocket de jarvis-core."""

from typing import Any

from .server import JarvisCoreState, create_app

__all__ = ["JarvisCoreState", "app", "create_app"]


def __getattr__(name: str) -> Any:
    if name == "app":
        from . import server

        return server.app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
