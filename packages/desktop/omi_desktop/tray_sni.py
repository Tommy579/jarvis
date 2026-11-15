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
        return f"OMI 2.0 : capteurs actifs ({', '.join(active) if active else 'tous en pause'})"


@dataclass
class KdeStatusNotifierTray:
    """Controleur StatusNotifierItem KDE ouvrant l'application web locale."""

    web_url: str = field(
        default_factory=lambda: os.environ.get("OMI_WEB_URL", "http://127.0.0.1:8765")
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
            "Id": "omi-desktop",
            "Title": "OMI 2.0",
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

        img = Image.new("RGBA", (64, 64), (17, 19, 22, 255))
        draw = ImageDraw.Draw(img)
        draw.ellipse((10, 10, 54, 54), fill=(217, 119, 6, 255))

        menu = pystray.Menu(
            pystray.MenuItem(
                "Ouvrir l'interface OMI", lambda _icon, _item: self.open_web_ui(), default=True
            ),
            pystray.MenuItem("Quitter", lambda icon, _item: icon.stop()),
        )
        icon = pystray.Icon("omi-desktop", img, self.sensors.tooltip(), menu)
        icon.run()


def check_kde_plasma_session() -> bool:
    return "kde" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower() or bool(
        subprocess.run(["which", "kwin_wayland"], capture_output=True).returncode == 0
    )


if __name__ == "__main__":
    KdeStatusNotifierTray().run()
