"""Icone barre systeme KDE via StatusNotifierItem (ou repli pystray) ouvrant l'UI web."""

from __future__ import annotations

import os
import subprocess
import webbrowser
from dataclasses import dataclass, field


@dataclass(slots=True)
class TraySensorStatus:
    screen_paused: bool = False
    mic_paused: bool = False
    camera_paused: bool = True

    def tooltip(self) -> str:
        active = []
        if not self.mic_paused:
            active.append("micro")
        if not self.screen_paused:
            active.append("ecran")
        if not self.camera_paused:
            active.append("camera")
        return f"JARVIS 2.0 : capteurs actifs ({', '.join(active) if active else 'tous en pause'})"


@dataclass
class KdeStatusNotifierTray:
    """Controleur StatusNotifierItem KDE ouvrant l'application web locale."""

    web_url: str = field(
        default_factory=lambda: os.environ.get("JARVIS_WEB_URL", "http://127.0.0.1:8765")
    )
    sensors: TraySensorStatus = field(default_factory=TraySensorStatus)

    def open_web_ui(self) -> bool:
        try:
            return bool(webbrowser.open(self.web_url))
        except Exception:
            return False

    def register_sni_dbus_metadata(self) -> dict[str, str]:
        return {
            "Category": "ApplicationStatus",
            "Id": "jarvis-desktop",
            "Title": "JARVIS 2.0",
            "Status": "Active",
            "IconName": "audio-input-microphone",
            "ToolTip": self.sensors.tooltip(),
        }

    def run(self) -> None:
        """Lance l'icone systeme si un serveur graphique est present."""
        try:
            import pystray  # type: ignore[import-not-found]
            from PIL import Image, ImageDraw
        except ImportError:
            self.open_web_ui()
            return

        from pathlib import Path

        icon_path = Path(__file__).resolve().parents[3] / "jarvis_icon.png"
        if icon_path.is_file():
            img = Image.open(icon_path).convert("RGBA").resize((64, 64))
        else:
            img = Image.new("RGBA", (64, 64), (11, 18, 29, 255))
            draw = ImageDraw.Draw(img)
            draw.arc((10, 10, 54, 54), start=45, end=315, fill=(56, 189, 248, 255), width=6)
            draw.ellipse((26, 26, 38, 38), fill=(224, 242, 254, 255))

        menu = pystray.Menu(
            pystray.MenuItem(
                "Ouvrir l'interface JARVIS", lambda _icon, _item: self.open_web_ui(), default=True
            ),
            pystray.MenuItem("Quitter", lambda icon, _item: icon.stop()),
        )
        icon = pystray.Icon("jarvis-desktop", img, self.sensors.tooltip(), menu)
        icon.run()


def check_kde_plasma_session() -> bool:
    return "kde" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower() or bool(
        subprocess.run(["which", "kwin_wayland"], capture_output=True).returncode == 0
    )


if __name__ == "__main__":
    KdeStatusNotifierTray().run()
