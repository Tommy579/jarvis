"""Point d'entree de l'application Windows empaquetee (PyInstaller).

Le binaire demarre jarvis-core sur la boucle locale, attend que /api/health reponde,
puis ouvre l'interface dans une fenetre Chrome/Edge en mode application. Aucune
installation de Python n'est requise sur la machine cible.
"""

from __future__ import annotations

import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


def app_root() -> Path:
    """Repertoire de travail de l'application (a cote de l'exe, ou dossier du bundle)."""
    override = os.environ.get("JARVIS_APP_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resource_root() -> Path:
    """Racine des ressources embarquees (web/dist, config) : bundle ou depot."""
    root = app_root()
    if (root / "web" / "dist" / "index.html").is_file():
        return root
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return root


def free_port(preferred: int = 8765) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def ensure_env_file(root: Path) -> Path:
    """Cree .env a partir du modele et garantit un JARVIS_API_TOKEN."""
    env_path = root / ".env"
    example = root / ".env.example"
    if not env_path.exists():
        if example.is_file():
            env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            env_path.write_text(
                "OPENROUTER_API_KEY=\nGEMINI_API_KEY=\nJARVIS_ALLOW_INSECURE=0\n", encoding="utf-8"
            )

    content = env_path.read_text(encoding="utf-8")
    current = ""
    for line in content.splitlines():
        if line.startswith("JARVIS_API_TOKEN="):
            current = line.split("=", 1)[1].strip().strip('"').strip("'")
            break
    if current and current not in {"changez_ce_jeton_secret", "change-me-local-token"}:
        return env_path

    token = secrets.token_urlsafe(32)
    lines = [
        f"JARVIS_API_TOKEN={token}" if line.startswith("JARVIS_API_TOKEN=") else line
        for line in content.splitlines()
    ]
    if not any(line.startswith("JARVIS_API_TOKEN=") for line in lines):
        lines.append(f"JARVIS_API_TOKEN={token}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return env_path


def read_token(env_path: Path) -> str:
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("JARVIS_API_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def wait_health(url: str, timeout_s: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.3)
    return False


def open_window(url: str) -> None:
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Microsoft/Edge/Application/msedge.exe",
    ]
    for browser in candidates:
        if browser.is_file():
            os.spawnl(
                os.P_NOWAIT, str(browser), str(browser), f"--app={url}", "--window-size=1307,824"
            )
            return
    webbrowser.open(url)


def main() -> int:
    root = app_root()
    resources = resource_root()
    root.mkdir(parents=True, exist_ok=True)

    env_path = ensure_env_file(root)
    token = read_token(env_path)

    port = int(os.environ.get("JARVIS_PORT") or free_port())
    base_url = f"http://127.0.0.1:{port}"

    os.environ.setdefault("JARVIS_APP_ROOT", str(root))
    os.environ.setdefault("JARVIS_DATA_DIR", str(root / "jarvis-data"))
    os.environ.setdefault("JARVIS_API_TOKEN", token)
    os.environ.setdefault("JARVIS_HOST", "127.0.0.1")
    os.environ.setdefault("JARVIS_PORT", str(port))
    os.environ["JARVIS_CONFIG_DIR"] = str(resources / "config")

    import uvicorn
    from jarvis_core.api.server import create_app

    app = create_app()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", access_log=False)
    )
    thread = threading.Thread(target=server.run, name="jarvis-core", daemon=True)
    thread.start()

    if not wait_health(base_url):
        print(
            "jarvis-core n'a pas demarre. Verifiez .env et le journal ci-dessus.", file=sys.stderr
        )
        return 1

    print(f"JARVIS 2.0 pret : {base_url}")
    open_window(f"{base_url}/?token={token}")

    try:
        while thread.is_alive():
            thread.join(timeout=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
