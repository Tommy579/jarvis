"""Adaptateur OS Linux (Arch Linux / KDE Wayland)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def search_files_linux(
    query: str, root_dir: str | None = None, limit: int = 50, timeout: float = 5.0
) -> dict:
    """Recherche des fichiers via fd, plocate ou os.walk borne."""
    cleaned = query.strip()
    if not cleaned:
        return {"matches": [], "method": "empty"}

    base = str(Path(root_dir).expanduser().resolve()) if root_dir else str(Path.home())

    if shutil.which("fd"):
        try:
            proc = subprocess.run(
                ["fd", "--max-results", str(limit), cleaned, base],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if proc.returncode == 0:
                matches = [line for line in proc.stdout.splitlines() if line.strip()][:limit]
                return {"matches": matches, "method": "fd"}
        except subprocess.TimeoutExpired:
            return {"error": "Delai depasse lors de la recherche avec fd."}

    if shutil.which("plocate") and root_dir is None:
        try:
            proc = subprocess.run(
                ["plocate", "-l", str(limit), "-i", cleaned],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if proc.returncode == 0:
                matches = [line for line in proc.stdout.splitlines() if line.strip()][:limit]
                return {"matches": matches, "method": "plocate"}
        except subprocess.TimeoutExpired:
            return {"error": "Delai depasse lors de la recherche avec plocate."}

    matches: list[str] = []
    q_lower = cleaned.lower()
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"node_modules", ".venv"}]
        for fname in files:
            if q_lower in fname.lower():
                matches.append(os.path.join(root, fname))
                if len(matches) >= limit:
                    return {"matches": matches, "method": "walk"}
    return {"matches": matches, "method": "walk"}


def send_notification_linux(title: str, message: str, timeout: float = 3.0) -> dict:
    """Envoie une notification bureau via notify-send ou gdbus (D-Bus)."""
    if shutil.which("notify-send"):
        try:
            subprocess.run(
                ["notify-send", "-a", "OMI", title, message],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True,
            )
            return {"ok": True, "method": "notify-send"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    if shutil.which("gdbus"):
        try:
            subprocess.run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest=org.freedesktop.Notifications",
                    "--object-path=/org/freedesktop/Notifications",
                    "--method=org.freedesktop.Notifications.Notify",
                    "OMI",
                    "0",
                    "",
                    title,
                    message,
                    "[]",
                    "{}",
                    "5000",
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True,
            )
            return {"ok": True, "method": "dbus"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    return {"ok": False, "error": "Aucun service de notification (notify-send/gdbus) disponible."}


def media_control_linux(action: str, timeout: float = 3.0) -> dict:
    """Controle la lecture media via playerctl (MPRIS)."""
    allowed = {
        "play": "play",
        "pause": "pause",
        "play-pause": "play-pause",
        "toggle": "play-pause",
        "next": "next",
        "previous": "previous",
        "stop": "stop",
    }
    cmd_arg = allowed.get(action.lower())
    if not cmd_arg:
        return {"ok": False, "error": f"Action media inconnue : {action}"}

    if not shutil.which("playerctl"):
        return {"ok": False, "error": "playerctl introuvable."}
    try:
        proc = subprocess.run(
            ["playerctl", cmd_arg],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            return {"ok": False, "error": proc.stderr.strip() or "Echec playerctl"}
        return {"ok": True, "action": cmd_arg}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def get_clipboard_linux(timeout: float = 2.0) -> dict:
    """Lit le presse-papier sous Wayland (wl-paste) puis X11/pyperclip."""
    if shutil.which("wl-paste"):
        try:
            proc = subprocess.run(
                ["wl-paste", "--no-newline"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if proc.returncode == 0:
                return {"content": proc.stdout, "method": "wl-paste"}
        except Exception:
            pass
    try:
        import pyperclip  # type: ignore[import-not-found]

        return {"content": pyperclip.paste(), "method": "pyperclip"}
    except Exception as exc:
        return {"error": str(exc)}
