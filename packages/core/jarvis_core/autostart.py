"""Gestion du lancement automatique au demarrage du systeme (XDG autostart Linux)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from jarvis_shared.rest_models import SystemAutostartConfig


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_autostart_dir() -> Path:
    env_dir = os.environ.get("JARVIS_AUTOSTART_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    xdg_config = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg_config) if xdg_config else Path.home() / ".config"
    return base / "autostart"


def _parse_desktop_enabled(content: str) -> bool:
    hidden = False
    gnome_enabled = True
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        val = value.strip().lower()
        if key == "Hidden":
            hidden = val == "true"
        elif key == "X-GNOME-Autostart-enabled":
            gnome_enabled = val != "false"
    return (not hidden) and gnome_enabled


def _render_desktop_entry(repo_root: Path, enabled: bool) -> str:
    launch_script = repo_root / "scripts" / "launch_jarvis_v2.sh"
    icon_path = repo_root / "jarvis_icon.png"
    hidden_str = "false" if enabled else "true"
    gnome_str = "true" if enabled else "false"
    return (
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        "Name=JARVIS\n"
        "GenericName=Assistant IA personnel\n"
        "Comment=Interface web JARVIS 2.0\n"
        f"Exec={launch_script}\n"
        f"Path={repo_root}\n"
        f"Icon={icon_path}\n"
        "Terminal=false\n"
        "Categories=Utility;Application;\n"
        "StartupNotify=true\n"
        "StartupWMClass=chrome-localhost__-Default\n"
        f"Hidden={hidden_str}\n"
        "NoDisplay=false\n"
        f"X-GNOME-Autostart-enabled={gnome_str}\n"
    )


def get_autostart_status(
    autostart_dir: Path | None = None,
    repo_root: Path | None = None,
) -> SystemAutostartConfig:
    if not sys.platform.startswith("linux") and autostart_dir is None:
        return SystemAutostartConfig(
            enabled=False,
            supported=False,
            desktop_file="",
            detail="Démarrage automatique géré uniquement sous Linux (XDG).",
        )
    target_dir = autostart_dir or _default_autostart_dir()
    desktop_file = target_dir / "JarvisAssistant.desktop"
    if not desktop_file.is_file():
        return SystemAutostartConfig(
            enabled=False,
            supported=True,
            desktop_file=str(desktop_file),
            detail="Entrée de démarrage automatique absente.",
        )
    content = desktop_file.read_text(encoding="utf-8", errors="replace")
    enabled = _parse_desktop_enabled(content)
    return SystemAutostartConfig(
        enabled=enabled,
        supported=True,
        desktop_file=str(desktop_file),
        detail=(
            "Activé au démarrage de la session."
            if enabled
            else "Désactivé au démarrage de la session."
        ),
    )


def set_autostart_enabled(
    enabled: bool,
    autostart_dir: Path | None = None,
    repo_root: Path | None = None,
) -> SystemAutostartConfig:
    if not sys.platform.startswith("linux") and autostart_dir is None:
        return SystemAutostartConfig(
            enabled=False,
            supported=False,
            desktop_file="",
            detail="Démarrage automatique géré uniquement sous Linux (XDG).",
        )
    target_dir = autostart_dir or _default_autostart_dir()
    root = repo_root or _default_repo_root()
    target_dir.mkdir(parents=True, exist_ok=True)
    desktop_file = target_dir / "JarvisAssistant.desktop"
    desktop_file.write_text(_render_desktop_entry(root, enabled=enabled), encoding="utf-8")
    try:
        desktop_file.chmod(0o755)
    except OSError:
        pass
    return SystemAutostartConfig(
        enabled=enabled,
        supported=True,
        desktop_file=str(desktop_file),
        detail=(
            "Activé au démarrage de la session."
            if enabled
            else "Désactivé au démarrage de la session."
        ),
    )
