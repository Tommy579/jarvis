"""Configuration pytest partagee pour resoudre les paquets du monorepo."""

from __future__ import annotations

import os
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

# Les tests ne doivent pas dependre du .env du developpeur : sur une machine neuve (CI),
# create_app refuse de demarrer sans jeton. Les tests qui verifient cette garde
# suppriment la variable explicitement.
os.environ.setdefault("JARVIS_ALLOW_INSECURE", "1")
