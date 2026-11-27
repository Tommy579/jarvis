"""Version applicative unique, lue depuis les metadonnees du paquet installe."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version

FALLBACK_VERSION = "2.0.0-dev"


def app_version() -> str:
    """Retourne la version de jarvis-core installee (source unique : pyproject.toml)."""
    try:
        return _package_version("jarvis-core")
    except PackageNotFoundError:
        return FALLBACK_VERSION
