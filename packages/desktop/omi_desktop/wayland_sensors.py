"""Capteurs KDE Wayland : D-Bus KWin/KIdleTime/ScreenSaver, ScreenCast portal avec restore_token, OCR local et liste noire."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_BLACKLIST_KEYWORDS: tuple[str, ...] = (
    "bitwarden",
    "keepass",
    "keepassxc",
    "1password",
    "kwallet",
    "banque",
    "bank",
    "boursorama",
    "revolut",
    "paypal",
    "navigation privée",
    "navigation privee",
    "private browsing",
    "incognito",
    "signal",
    "telegram",
    "whatsapp",
)


@dataclass
class ActiveWindowInfo:
    app_name: str
    title: str
    is_blacklisted: bool = False


class WaylandSensors:
    """Capteurs passifs et capture a la demande sous Arch Linux / KDE Plasma Wayland."""

    def __init__(
        self,
        restore_token_path: Path | None = None,
        blacklist_keywords: list[str] | None = None,
    ):
        self.restore_token_path = restore_token_path or (
            Path.home() / "omi-data" / "portal_restore_token.txt"
        )
        self.blacklist_keywords = [
            kw.lower() for kw in (blacklist_keywords or list(DEFAULT_BLACKLIST_KEYWORDS))
        ]
        self.activity_transitions: list[dict[str, Any]] = []

    def is_window_blacklisted(self, app_name: str, title: str) -> bool:
        """Verifie si la fenetre active appartient a la liste noire (mots de passe, banque, prive, messagerie)."""
        haystack = f"{app_name} {title}".lower()
        return any(kw in haystack for kw in self.blacklist_keywords)

    def load_portal_restore_token(self) -> str | None:
        if self.restore_token_path.is_file():
            tok = self.restore_token_path.read_text(encoding="utf-8").strip()
            return tok or None
        return None

    def save_portal_restore_token(self, token: str) -> None:
        self.restore_token_path.parent.mkdir(parents=True, exist_ok=True)
        self.restore_token_path.write_text(token.strip(), encoding="utf-8")

    def get_active_window(self) -> ActiveWindowInfo:
        """Interroge kdotool / D-Bus KWin / xdotool pour obtenir l'application et le titre actifs."""
        app_name = "Unknown"
        title = ""

        # 1. kdotool (natif KDE Wayland)
        if shutil.which("kdotool"):
            try:
                win_id = (
                    subprocess.check_output(
                        ["kdotool", "getactivewindow"],
                        stderr=subprocess.DEVNULL,
                        timeout=1,
                    )
                    .decode("utf-8", errors="ignore")
                    .strip()
                )
                if win_id:
                    title = (
                        subprocess.check_output(
                            ["kdotool", "getwindowname", win_id],
                            stderr=subprocess.DEVNULL,
                            timeout=1,
                        )
                        .decode("utf-8", errors="ignore")
                        .strip()
                    )
                    app_name = (
                        subprocess.check_output(
                            ["kdotool", "getwindowclassname", win_id],
                            stderr=subprocess.DEVNULL,
                            timeout=1,
                        )
                        .decode("utf-8", errors="ignore")
                        .strip()
                        or "Unknown"
                    )
            except Exception:
                pass

        # 2. Repli qdbus / gdbus org.kde.KWin
        if not title and shutil.which("qdbus6"):
            try:
                out = (
                    subprocess.check_output(
                        ["qdbus6", "org.kde.KWin", "/KWin", "org.kde.KWin.activeWindow"],
                        stderr=subprocess.DEVNULL,
                        timeout=1,
                    )
                    .decode("utf-8", errors="ignore")
                    .strip()
                )
                if out:
                    title = out
            except Exception:
                pass

        # 3. Repli XWayland / X11 xdotool
        if not title and shutil.which("xdotool"):
            try:
                title = (
                    subprocess.check_output(
                        ["xdotool", "getactivewindow", "getwindowname"],
                        stderr=subprocess.DEVNULL,
                        timeout=1,
                    )
                    .decode("utf-8", errors="ignore")
                    .strip()
                )
            except Exception:
                pass

        blacklisted = self.is_window_blacklisted(app_name, title)
        return ActiveWindowInfo(app_name=app_name, title=title, is_blacklisted=blacklisted)

    def record_window_transition(self, win: ActiveWindowInfo | None = None) -> ActiveWindowInfo:
        info = win or self.get_active_window()
        safe_title = "[MASQUE - LISTE NOIRE]" if info.is_blacklisted else info.title
        self.activity_transitions.append(
            {
                "ts": time.time(),
                "iso": datetime.now(timezone.utc).isoformat(),
                "app": info.app_name,
                "title": safe_title,
                "blacklisted": info.is_blacklisted,
            }
        )
        self.activity_transitions = self.activity_transitions[-1000:]
        return info

    def get_idle_and_lock_state(self) -> dict[str, Any]:
        """Lit l'inactivite et le verrouillage via D-Bus org.freedesktop.ScreenSaver / KIdleTime."""
        locked = False
        idle_seconds = 0.0
        for qdbus_bin in ("qdbus6", "qdbus"):
            if not shutil.which(qdbus_bin):
                continue
            try:
                lock_out = (
                    subprocess.check_output(
                        [
                            qdbus_bin,
                            "org.freedesktop.ScreenSaver",
                            "/ScreenSaver",
                            "org.freedesktop.ScreenSaver.GetActive",
                        ],
                        stderr=subprocess.DEVNULL,
                        timeout=1,
                    )
                    .decode("utf-8", errors="ignore")
                    .strip()
                    .lower()
                )
                locked = lock_out == "true"

                idle_out = (
                    subprocess.check_output(
                        [
                            qdbus_bin,
                            "org.freedesktop.ScreenSaver",
                            "/ScreenSaver",
                            "org.freedesktop.ScreenSaver.GetSessionIdleTime",
                        ],
                        stderr=subprocess.DEVNULL,
                        timeout=1,
                    )
                    .decode("utf-8", errors="ignore")
                    .strip()
                )
                if idle_out.isdigit():
                    idle_seconds = float(idle_out)
                break
            except Exception:
                continue
        return {"session_locked": locked, "idle_seconds": idle_seconds}

    def capture_screen_on_demand(
        self, active_window: ActiveWindowInfo | None = None
    ) -> dict[str, Any]:
        """Capture l'ecran a la demande (Wayland spectacle / portal) et extrait le texte OCR localement."""
        win = active_window or self.get_active_window()
        if win.is_blacklisted:
            return {
                "ok": False,
                "blocked_by_blacklist": True,
                "error": f"Capture refusee : la fenetre active ({win.app_name}) figure dans la liste noire.",
            }

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        captured = False
        # 1. Spectacle CLI en arriere-plan sous KDE Wayland
        if shutil.which("spectacle"):
            try:
                res = subprocess.run(
                    ["spectacle", "-b", "-n", "-o", str(tmp_path)],
                    capture_output=True,
                    timeout=5,
                )
                captured = res.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 0
            except Exception:
                captured = False

        # 2. Repli grim (wlroots) ou mss
        if not captured and shutil.which("grim"):
            try:
                res = subprocess.run(["grim", str(tmp_path)], capture_output=True, timeout=5)
                captured = res.returncode == 0 and tmp_path.exists()
            except Exception:
                captured = False

        if not captured:
            tmp_path.unlink(missing_ok=True)
            return {
                "ok": False,
                "error": "Aucun outil de capture Wayland (spectacle/xdg-desktop-portal) disponible.",
                "restore_token_present": self.load_portal_restore_token() is not None,
            }

        ocr_text = self.extract_local_ocr(tmp_path)
        tmp_path.unlink(missing_ok=True)
        return {
            "ok": True,
            "window": {"app": win.app_name, "title": win.title},
            "ocr_text": ocr_text,
            "restore_token_present": self.load_portal_restore_token() is not None,
        }

    @staticmethod
    def extract_local_ocr(image_path: Path) -> str:
        """Extrait le texte d'une capture via RapidOCR ou Tesseract avant tout envoi au LLM."""
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore

            engine = RapidOCR()
            result, _ = engine(str(image_path))
            if result:
                return "\n".join(str(line[1]) for line in result if len(line) > 1)
        except Exception:
            pass

        if shutil.which("tesseract"):
            try:
                out = subprocess.check_output(
                    ["tesseract", str(image_path), "stdout", "-l", "fra+eng"],
                    stderr=subprocess.DEVNULL,
                    timeout=8,
                ).decode("utf-8", errors="ignore")
                return out.strip()
            except Exception:
                pass
        return ""

    @staticmethod
    def detect_face_locally(frame_rgb_or_gray: Any = None) -> bool:
        """Verifie localement la presence d'un visage via OpenCV sans jamais envoyer d'image dans le cloud."""
        if frame_rgb_or_gray is None:
            return False
        try:
            import cv2

            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            detector = cv2.CascadeClassifier(cascade_path)
            faces = detector.detectMultiScale(frame_rgb_or_gray, scaleFactor=1.2, minNeighbors=4)
            return len(faces) > 0
        except Exception:
            return False

    def build_hourly_activity_summary(self) -> str:
        """Synthetise la derniere heure de transitions de fenetres pour la couche episodique."""
        if not self.activity_transitions:
            return "Aucune activite bureau enregistree sur la derniere heure."
        counts: Counter[str] = Counter()
        for item in self.activity_transitions:
            app = item.get("app") or "Unknown"
            counts[app] += 1
        top_parts = [f"{app} ({cnt} transitions)" for app, cnt in counts.most_common(5)]
        return "Activite horaire bureau : " + ", ".join(top_parts)
