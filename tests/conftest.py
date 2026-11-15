"""Configuration pytest partagee pour resoudre les paquets du monorepo."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
for candidate in (
    ROOT_DIR,
    ROOT_DIR / "packages" / "shared",
    ROOT_DIR / "packages" / "core",
    ROOT_DIR / "packages" / "desktop",
):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
