"""Adaptateur OS Windows (sans injection SQL ni KeyError USERPROFILE)."""

from __future__ import annotations

import os
from pathlib import Path


def search_files_windows(
    query: str, root_dir: str | None = None, limit: int = 50, timeout: float = 5.0
) -> dict:
    """Recherche de fichiers securisee sous Windows (requete SQL parametree ou os.walk)."""
    cleaned = query.strip()
    if not cleaned:
        return {"matches": [], "method": "empty"}

    matches: list[str] = []
    try:
        import pyodbc  # type: ignore[import-not-found]

        conn_str = "Driver={Search.CollatorDSO};Extended Properties='Application=Windows';"
        with pyodbc.connect(conn_str, autocommit=True, timeout=int(timeout)) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT TOP 50 System.ItemPathDisplay FROM SystemIndex WHERE System.FileName LIKE ?",
                    (f"%{cleaned}%",),
                )
                for row in cursor.fetchall():
                    matches.append(str(row[0]))
                    if len(matches) >= limit:
                        break
        if matches:
            return {"matches": matches, "method": "windows_index"}
    except Exception:
        pass

    home = os.environ.get("USERPROFILE") or str(Path.home())
    roots = (
        [root_dir]
        if root_dir
        else [os.path.join(home, folder) for folder in ("Documents", "Desktop", "Downloads")]
    )
    q_lower = cleaned.lower()
    for start in roots:
        if not os.path.exists(start):
            continue
        for root, dirs, files in os.walk(start):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if q_lower in fname.lower():
                    matches.append(os.path.join(root, fname))
                    if len(matches) >= limit:
                        return {"matches": matches, "method": "walk"}
    return {"matches": matches, "method": "walk"}


def send_notification_windows(title: str, message: str, timeout: float = 5.0) -> dict:
    """Envoie une notification Toast Windows."""
    try:
        from win10toast_persist import ToastNotifier  # type: ignore[import-not-found]

        toaster = ToastNotifier()
        toaster.show_toast(title, message, duration=int(timeout), threaded=True)
        return {"ok": True, "method": "toast"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def media_control_windows(action: str) -> dict:
    """Envoie une touche media Windows via user32 (ou fallback erreur propre)."""
    vk_map = {
        "play": 0xB3,
        "pause": 0xB3,
        "play-pause": 0xB3,
        "toggle": 0xB3,
        "next": 0xB0,
        "previous": 0xB1,
        "stop": 0xB2,
    }
    vk = vk_map.get(action.lower())
    if vk is None:
        return {"ok": False, "error": f"Action media inconnue : {action}"}
    try:
        import ctypes

        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)  # type: ignore[attr-defined]
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)  # type: ignore[attr-defined]
        return {"ok": True, "action": action}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def get_clipboard_windows() -> dict:
    """Lit le presse-papier Windows."""
    try:
        import pyperclip  # type: ignore[import-not-found]

        return {"content": pyperclip.paste(), "method": "pyperclip"}
    except Exception as exc:
        return {"error": str(exc)}
