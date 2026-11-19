"""Adaptateurs OS multiplateformes pour jarvis-desktop."""

from __future__ import annotations

import platform

from . import linux, windows


def current_platform_adapter():
    return windows if platform.system() == "Windows" else linux


__all__ = ["linux", "windows", "current_platform_adapter"]
