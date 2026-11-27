"""Outils OS standards (niveaux 0 et 1) avec validation Pydantic v2 et timeouts explicites."""

from __future__ import annotations

import asyncio
import html
import os
import re
import signal
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import urljoin, urlparse

import httpx
import psutil
from jarvis_desktop.platform import current_platform_adapter
from jarvis_shared.rest_models import FactCreate, SensitivityLevel
from pydantic import BaseModel, Field

from ..memory.schema import MemoryDatabase
from .autonomy import AutonomyPolicy, validate_http_target_host


def extract_clean_html_text(raw_html: str, max_chars: int = 8000) -> str:
    """Extrait le texte lisible d'une page HTML en supprimant scripts et styles."""
    cleaned = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw_html)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"[ \t\r\f\v]+", " ", cleaned)
    cleaned = re.sub(r"\n\s*\n+", "\n", cleaned).strip()
    return cleaned[:max_chars]


def default_workdir() -> Path:
    raw = os.environ.get("JARVIS_WORKDIR", "/").strip() or "/"
    return Path(raw).expanduser().resolve()


def resolve_workdir_path(raw: str) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = default_workdir() / p
    return p.resolve()


_SENSITIVE_LINUX_PREFIXES = (
    "/etc",
    "/boot",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/sys",
    "/proc",
    "/dev",
    "/root",
    "/var/lib",
    "/var/log",
)

_SENSITIVE_READ_PREFIXES = (
    "/proc",
    "/sys",
    "/dev",
    "/root",
)

_SENSITIVE_USER_DIR_NAMES = frozenset(
    {
        ".ssh",
        ".gnupg",
        ".aws",
        ".azure",
        ".gcloud",
        ".kube",
        ".docker",
        ".password-store",
        "keyrings",
        "jarvis-data",
        "jarvis-browser",
    }
)

_SENSITIVE_USER_FILE_NAMES = frozenset(
    {
        ".netrc",
        "netrc",
        ".git-credentials",
        ".npmrc",
        ".pypirc",
        ".bash_history",
        ".zsh_history",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "secrets.enc",
        "shadow",
        "credentials",
    }
)

_SENSITIVE_WRITE_USER_FILES = frozenset(
    {
        ".bashrc",
        ".zshrc",
        ".profile",
        ".bash_profile",
        ".zprofile",
        ".xinitrc",
        "authorized_keys",
    }
)

_SENSITIVE_SECRET_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".kdbx", ".gpg", ".ovpn"})


def _jarvis_private_roots() -> list[Path]:
    """Dossiers propres a Jarvis interdits aux outils fichiers, quel que soit leur nom.

    - JARVIS_DATA_DIR : base SQLite, wiki, sauvegardes (memoire personnelle complete) ;
    - etat du lanceur (~/.local/state/jarvis) : journaux contenant le jeton d'API ;
    - profil Chromium de la fenetre Jarvis : jeton en sessionStorage, cookies.
    """
    roots: list[Path] = []
    raw_data = os.environ.get("JARVIS_DATA_DIR", "").strip()
    if raw_data:
        roots.append(Path(raw_data).expanduser())
    state_home = os.environ.get("XDG_STATE_HOME", "").strip() or str(
        Path.home() / ".local" / "state"
    )
    config_home = os.environ.get("XDG_CONFIG_HOME", "").strip() or str(Path.home() / ".config")
    roots.append(Path(state_home).expanduser() / "jarvis")
    roots.append(Path(config_home).expanduser() / "chromium-jarvis")
    resolved: list[Path] = []
    for root in roots:
        try:
            resolved.append(root.resolve())
        except OSError:
            continue
    return resolved


def _is_jarvis_private_path(resolved: Path) -> bool:
    return any(
        resolved == root or resolved.is_relative_to(root) for root in _jarvis_private_roots()
    )


_DANGEROUS_COMMAND_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\brm\b[^\n;|&]*--no-preserve-root"),
        "suppression recursive avec --no-preserve-root",
    ),
    (
        re.compile(
            r"\brm\b[^\n;|&]*(?:\s-(?:[a-zA-Z]*[rR][a-zA-Z]*)|\s--recursive)(?:[^\n;|&]*)\s+"
            r"(?:/\*?|/etc(?:/\S*)?|/usr(?:/\S*)?|/boot(?:/\S*)?|/bin(?:/\S*)?|/sbin(?:/\S*)?|"
            r"/lib\d*(?:/\S*)?|/sys(?:/\S*)?|/proc(?:/\S*)?|/dev(?:/\S*)?|/root(?:/\S*)?|"
            r"/home(?:/[^/\s;|&]+)?/?)(?:\s|$|;|&|\|)"
        ),
        "suppression recursive sur un dossier systeme, racine ou home",
    ),
    (
        re.compile(r"\bfind\b[^\n;|&]*\s+/(?:\s|$)[^\n;|&]*\s-delete\b"),
        "suppression recursive via find -delete sur /",
    ),
    (
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
        "fork bomb detectee",
    ),
    (
        re.compile(r"\b(?:mkfs(?:\.[a-z0-9]+)?|wipefs|fdisk|gdisk|parted|shred)\b"),
        "outil de formatage ou destruction de disque",
    ),
    (
        re.compile(
            r"\bdd\b[^\n;|&]*\bof=/dev/(?:sd[a-z]|nvme\d+n\d+|vd[a-z]|hd[a-z]|mmcblk\d+|loop\d+|disk/)"
        ),
        "ecriture directe sur peripherique bloc via dd",
    ),
    (
        re.compile(r">\s*/dev/(?:sd[a-z]|nvme\d+n\d+|vd[a-z]|hd[a-z]|mmcblk\d+)"),
        "redirection directe vers un peripherique bloc",
    ),
    (
        re.compile(
            r"\b(?:shutdown|poweroff|halt|reboot|init\s+[06]|systemctl\s+(?:poweroff|halt|reboot))\b"
        ),
        "arret ou redemarrage systeme",
    ),
    (
        re.compile(
            r"\b(?:chmod|chown)\b[^\n;|&]*(?:\s-[a-zA-Z]*R|\s--recursive)[^\n;|&]*\s+/(?:\s|$)"
        ),
        "modification recursive des permissions sur /",
    ),
]


def _has_sensitive_secret_name(resolved: Path) -> bool:
    name = resolved.name.lower()
    if name.startswith(".env") or name.endswith(".env") or name in _SENSITIVE_USER_FILE_NAMES:
        return True
    if resolved.suffix.lower() in _SENSITIVE_SECRET_SUFFIXES:
        return True
    parts = {p.lower() for p in resolved.parts}
    if parts & _SENSITIVE_USER_DIR_NAMES:
        return True
    return _is_jarvis_private_path(resolved)


def is_sensitive_read_path(path: Path) -> bool:
    """Retourne True si le chemin expose des secrets locaux ou fichiers noyau sensibles."""
    resolved = path.resolve()
    if os.name != "nt":
        for prefix in _SENSITIVE_READ_PREFIXES:
            p = Path(prefix)
            if resolved == p or resolved.is_relative_to(p):
                return True
        if resolved == Path("/etc/shadow"):
            return True
    return _has_sensitive_secret_name(resolved)


def is_sensitive_system_path(path: Path) -> bool:
    """Retourne True si le chemin cible un fichier ou dossier systeme ou de persistance sensible."""
    resolved = path.resolve()
    if os.name == "nt":
        norm = str(resolved).lower()
        if any(
            norm == prefix or norm.startswith(prefix + "\\")
            for prefix in (
                "c:\\windows",
                "c:\\program files",
                "c:\\program files (x86)",
                "c:\\programdata",
            )
        ):
            return True
    else:
        if str(resolved) == "/":
            return True
        for prefix in _SENSITIVE_LINUX_PREFIXES:
            p = Path(prefix)
            if resolved == p or resolved.is_relative_to(p):
                return True
    if _has_sensitive_secret_name(resolved):
        return True
    name = resolved.name.lower()
    if name in _SENSITIVE_WRITE_USER_FILES:
        return True
    parts_lower = [p.lower() for p in resolved.parts]
    if ".config" in parts_lower and ("autostart" in parts_lower or "systemd" in parts_lower):
        return True
    return False


def is_dangerous_command(command: str) -> str | None:
    """Retourne la raison du blocage si la commande est trop dangereuse, sinon None."""
    for pattern, reason in _DANGEROUS_COMMAND_PATTERNS:
        if pattern.search(command):
            return reason
    return None


class ReadFileArgs(BaseModel):
    file_path: str
    max_bytes: int = Field(default=500000, ge=1, le=2000000)


class WriteFileArgs(BaseModel):
    file_path: str
    content: str
    append: bool = False
    create_dirs: bool = True


class ListDirectoryArgs(BaseModel):
    path: str = "/"


class SearchFilesArgs(BaseModel):
    query: str = Field(min_length=1)
    root_dir: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class RunCommandArgs(BaseModel):
    command: str = Field(min_length=1, max_length=2000, description="Commande shell a executer.")
    cwd: str | None = Field(default=None, description="Repertoire de travail (defaut : /).")
    timeout_seconds: float = Field(default=15.0, ge=1.0, le=60.0)


class SystemStatsArgs(BaseModel):
    pass


class ListProcessesArgs(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    sort_by: Literal["cpu_percent", "memory_percent"] = "cpu_percent"


class ClipboardArgs(BaseModel):
    pass


class SendNotificationArgs(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=500)


class MediaControlArgs(BaseModel):
    action: Literal["play", "pause", "play-pause", "toggle", "next", "previous", "stop"]


class OpenUrlArgs(BaseModel):
    url: str = Field(min_length=1)


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=5, ge=1, le=10)


class WebFetchArgs(BaseModel):
    url: str = Field(min_length=8)
    max_chars: int = Field(default=8000, ge=200, le=30000)


class MemorySearchArgs(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


class MemorySaveArgs(BaseModel):
    text: str = Field(min_length=1)
    category: str = "general"
    subject: str = "Tom"
    predicate: str = "note"
    object: str | None = None
    sensitivity: SensitivityLevel = "normal"


class MemoryForgetArgs(BaseModel):
    fact_id: str | None = None
    query: str | None = None


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    risk: Literal[0, 1, 2, 3]
    args_model: type[BaseModel]
    handler: Callable[[Any], Awaitable[dict[str, Any]]]


class StandardToolkit:
    """Ensemble des outils OS, Web et Memoire exposes a la boucle agent."""

    def __init__(
        self,
        db: MemoryDatabase | None = None,
        timeout_seconds: float = 10.0,
        database: MemoryDatabase | None = None,
        autonomy_policy: AutonomyPolicy | None = None,
    ) -> None:
        self.db = database or db
        self.timeout_seconds = timeout_seconds
        self.autonomy_policy = autonomy_policy

    def register_all(self, registry: Any) -> None:
        from jarvis_shared.models import RiskLevel

        from .registry import ToolSpec as RegistryToolSpec

        if self.autonomy_policy is None and hasattr(registry, "autonomy_policy"):
            self.autonomy_policy = registry.autonomy_policy

        for spec in self.specs().values():
            is_external = spec.name in {"web_search", "web_fetch"}
            registry.register(
                RegistryToolSpec(
                    name=spec.name,
                    description=spec.description,
                    handler=spec.handler,
                    args_model=spec.args_model,
                    risk=RiskLevel(spec.risk),
                    returns_untrusted=is_external,
                    untrusted_source=spec.name if is_external else None,
                    pass_model=True,
                )
            )

    def specs(self) -> dict[str, ToolSpec]:
        items = [
            ToolSpec(
                "read_file", "Lit le contenu textuel d'un fichier.", 0, ReadFileArgs, self.read_file
            ),
            ToolSpec(
                "write_file",
                "Ecrit ou modifie un fichier sur le PC (sauf fichiers systeme sensibles).",
                1,
                WriteFileArgs,
                self.write_file,
            ),
            ToolSpec(
                "list_directory",
                "Liste les fichiers et dossiers d'un chemin.",
                0,
                ListDirectoryArgs,
                self.list_directory,
            ),
            ToolSpec(
                "search_files",
                "Recherche des fichiers par nom.",
                0,
                SearchFilesArgs,
                self.search_files,
            ),
            ToolSpec(
                "run_command",
                "Execute une commande terminal (repertoire de travail par defaut : /).",
                2,
                RunCommandArgs,
                self.run_command,
            ),
            ToolSpec(
                "get_system_stats",
                "Recupere CPU, RAM, disque et batterie.",
                0,
                SystemStatsArgs,
                self.get_system_stats,
            ),
            ToolSpec(
                "list_processes",
                "Liste les processus actifs tries par usage.",
                0,
                ListProcessesArgs,
                self.list_processes,
            ),
            ToolSpec(
                "get_clipboard",
                "Lit le presse-papier courant.",
                0,
                ClipboardArgs,
                self.get_clipboard,
            ),
            ToolSpec(
                "send_notification",
                "Affiche une notification systeme.",
                1,
                SendNotificationArgs,
                self.send_notification,
            ),
            ToolSpec(
                "media_control",
                "Controle la lecture media locale.",
                1,
                MediaControlArgs,
                self.media_control,
            ),
            ToolSpec(
                "open_url",
                "Ouvre une URL http/https dans le navigateur.",
                1,
                OpenUrlArgs,
                self.open_url,
            ),
            ToolSpec(
                "web_search",
                "Recherche web via Brave Search ou Tavily.",
                0,
                WebSearchArgs,
                self.web_search,
            ),
            ToolSpec(
                "web_fetch",
                "Recupere et extrait le texte d'une page web.",
                0,
                WebFetchArgs,
                self.web_fetch,
            ),
            ToolSpec(
                "memory_search",
                "Recherche hybride dans les faits memorises.",
                0,
                MemorySearchArgs,
                self.memory_search,
            ),
            ToolSpec(
                "memory_save",
                "Enregistre un fait durable en memoire.",
                1,
                MemorySaveArgs,
                self.memory_save,
            ),
            ToolSpec(
                "memory_forget",
                "Supprime ou marque obsolete un fait en memoire.",
                1,
                MemoryForgetArgs,
                self.memory_forget,
            ),
        ]
        return {spec.name: spec for spec in items}

    async def execute(self, name: str, raw_args: dict[str, Any]) -> dict[str, Any]:
        spec = self.specs().get(name)
        if not spec:
            return {"ok": False, "error": f"Outil inconnu : {name}"}
        parsed = spec.args_model.model_validate(raw_args)
        return await spec.handler(parsed)

    async def read_file(self, args: ReadFileArgs) -> dict[str, Any]:
        def _sync_read() -> dict[str, Any]:
            path = resolve_workdir_path(args.file_path)
            if is_sensitive_read_path(path):
                return {
                    "ok": False,
                    "path": str(path),
                    "error": f"Lecture refusee : '{path}' est un fichier sensible ou de secrets.",
                }
            if not path.is_file():
                return {"ok": False, "error": f"Fichier introuvable : {path}"}
            if path.stat().st_size > args.max_bytes:
                return {"ok": False, "error": "Fichier trop volumineux."}
            with path.open("rb") as fh:
                raw = fh.read(args.max_bytes + 1)
            if len(raw) > args.max_bytes:
                return {"ok": False, "error": "Fichier trop volumineux."}
            return {
                "ok": True,
                "path": str(path),
                "content": raw.decode("utf-8", errors="replace"),
            }

        try:
            return await asyncio.to_thread(_sync_read)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def write_file(self, args: WriteFileArgs) -> dict[str, Any]:
        def _sync_write() -> dict[str, Any]:
            path = resolve_workdir_path(args.file_path)
            if is_sensitive_system_path(path):
                return {
                    "ok": False,
                    "path": str(path),
                    "error": f"Ecriture refusee : '{path}' est un fichier ou dossier systeme sensible.",
                }
            if args.create_dirs:
                path.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if args.append else "w"
            with path.open(mode, encoding="utf-8") as fh:
                fh.write(args.content)
            return {
                "ok": True,
                "path": str(path),
                "bytes_written": len(args.content.encode("utf-8")),
            }

        try:
            return await asyncio.to_thread(_sync_write)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def list_directory(self, args: ListDirectoryArgs) -> dict[str, Any]:
        def _sync_list() -> dict[str, Any]:
            path = resolve_workdir_path(args.path)
            if not path.is_dir():
                return {"ok": False, "error": f"Dossier introuvable : {path}"}
            entries = sorted(
                (
                    f"{'[DIR]' if item.is_dir() else '[FILE]'} {item.name}"
                    for item in path.iterdir()
                ),
                key=str.lower,
            )
            return {"ok": True, "path": str(path), "items": entries}

        try:
            return await asyncio.to_thread(_sync_list)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def search_files(self, args: SearchFilesArgs) -> dict[str, Any]:
        root_dir = str(resolve_workdir_path(args.root_dir)) if args.root_dir else None
        adapter = current_platform_adapter()
        timeout = min(self.timeout_seconds, 5.0)
        if hasattr(adapter, "search_files_linux"):
            res = await asyncio.to_thread(
                adapter.search_files_linux, args.query, root_dir, args.limit, timeout
            )
        else:
            res = await asyncio.to_thread(
                adapter.search_files_windows,
                args.query,
                root_dir or str(default_workdir()),
                args.limit,
                timeout,
            )
        return {"ok": "error" not in res, **res}

    @staticmethod
    async def _read_capped(stream: asyncio.StreamReader | None, limit: int = 262_144) -> bytes:
        if stream is None:
            return b""
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await stream.read(32768)
            if not chunk:
                break
            if total < limit:
                take = min(len(chunk), limit - total)
                chunks.append(chunk[:take])
                total += take
        return b"".join(chunks)

    @staticmethod
    def _kill_proc_group(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            if os.name != "nt" and proc.pid:
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    async def run_command(self, args: RunCommandArgs) -> dict[str, Any]:
        work_dir = resolve_workdir_path(args.cwd) if args.cwd else default_workdir()
        if not work_dir.is_dir():
            return {"ok": False, "error": f"Repertoire introuvable : {work_dir}"}

        cmd_to_run = args.command
        uses_sudo = bool(re.search(r"(?:^|[;&|]\s*|\s)sudo\b", cmd_to_run))
        if uses_sudo and not re.search(r"\bsudo\s+-n\b", cmd_to_run):
            check = await asyncio.create_subprocess_exec(
                "sudo",
                "-n",
                "true",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if await check.wait() != 0:
                return {
                    "ok": False,
                    "needs_elevation": True,
                    "cwd": str(work_dir),
                    "error": (
                        "Elevation sudo requise : ne jamais transmettre de mot de passe root dans le chat. "
                        "Execute cette commande dans un terminal local ou via pkexec."
                    ),
                }

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd_to_run,
                cwd=str(work_dir),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=(os.name != "nt"),
            )
            try:
                stdout_b, stderr_b, _ = await asyncio.wait_for(
                    asyncio.gather(
                        self._read_capped(proc.stdout),
                        self._read_capped(proc.stderr),
                        proc.wait(),
                    ),
                    timeout=args.timeout_seconds,
                )
            except (TimeoutError, asyncio.TimeoutError):
                self._kill_proc_group(proc)
                await proc.wait()
                return {
                    "ok": False,
                    "cwd": str(work_dir),
                    "error": f"Delai depasse ({args.timeout_seconds}s).",
                }
            except asyncio.CancelledError:
                self._kill_proc_group(proc)
                await proc.wait()
                raise
            return {
                "ok": proc.returncode == 0,
                "exit_code": proc.returncode,
                "cwd": str(work_dir),
                "stdout": stdout_b.decode("utf-8", errors="replace")[:10000],
                "stderr": stderr_b.decode("utf-8", errors="replace")[:10000],
            }
        except Exception as exc:
            return {"ok": False, "cwd": str(work_dir), "error": str(exc)}

    async def get_system_stats(self, args: SystemStatsArgs) -> dict[str, Any]:
        _ = args

        def _sync_stats() -> dict[str, Any]:
            cpu_pct = psutil.cpu_percent(interval=0.05)
            ram = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            battery = psutil.sensors_battery()
            return {
                "ok": True,
                "cpu_usage_pct": cpu_pct,
                "ram_used_gb": round(ram.used / (1024**3), 2),
                "ram_total_gb": round(ram.total / (1024**3), 2),
                "ram_usage_pct": ram.percent,
                "disk_free_gb": round(disk.free / (1024**3), 2),
                "disk_usage_pct": disk.percent,
                "battery_pct": battery.percent if battery else None,
                "battery_plugged": battery.power_plugged if battery else None,
            }

        return await asyncio.to_thread(_sync_stats)

    async def list_processes(self, args: ListProcessesArgs) -> dict[str, Any]:
        def _sync_procs() -> dict[str, Any]:
            procs: list[dict[str, Any]] = []
            for p in psutil.process_iter(
                ["pid", "name", "username", "cpu_percent", "memory_percent"]
            ):
                try:
                    procs.append(dict(p.info))
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            procs.sort(key=lambda x: x.get(args.sort_by) or 0.0, reverse=True)
            return {"ok": True, "processes": procs[: args.limit]}

        return await asyncio.to_thread(_sync_procs)

    async def get_clipboard(self, args: ClipboardArgs) -> dict[str, Any]:
        _ = args
        adapter = current_platform_adapter()
        if hasattr(adapter, "get_clipboard_linux"):
            res = await asyncio.to_thread(
                adapter.get_clipboard_linux, min(self.timeout_seconds, 2.0)
            )
        else:
            res = await asyncio.to_thread(adapter.get_clipboard_windows)
        return {"ok": "error" not in res, **res}

    async def send_notification(self, args: SendNotificationArgs) -> dict[str, Any]:
        adapter = current_platform_adapter()
        if hasattr(adapter, "send_notification_linux"):
            return await asyncio.to_thread(
                adapter.send_notification_linux,
                args.title,
                args.message,
                min(self.timeout_seconds, 3.0),
            )
        return await asyncio.to_thread(
            adapter.send_notification_windows,
            args.title,
            args.message,
            min(self.timeout_seconds, 5.0),
        )

    async def media_control(self, args: MediaControlArgs) -> dict[str, Any]:
        adapter = current_platform_adapter()
        if hasattr(adapter, "media_control_linux"):
            return await asyncio.to_thread(
                adapter.media_control_linux, args.action, min(self.timeout_seconds, 3.0)
            )
        return await asyncio.to_thread(adapter.media_control_windows, args.action)

    async def open_url(self, args: OpenUrlArgs) -> dict[str, Any]:
        parsed = urlparse(args.url.strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return {"ok": False, "error": "Seules les URLs http:// ou https:// sont autorisees."}
        opened = await asyncio.to_thread(webbrowser.open, args.url.strip())
        return {"ok": bool(opened), "url": args.url.strip()}

    async def web_search(self, args: WebSearchArgs) -> dict[str, Any]:
        brave_key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
        tavily_key = os.environ.get("TAVILY_API_KEY", "").strip()

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            if brave_key:
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    headers={"Accept": "application/json", "X-Subscription-Token": brave_key},
                    params={"q": args.query, "count": args.limit},
                )
                resp.raise_for_status()
                data = resp.json()
                results = [
                    {
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("description", ""),
                    }
                    for item in data.get("web", {}).get("results", [])[: args.limit]
                ]
                return {"ok": True, "provider": "brave", "results": results}

            if tavily_key:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": tavily_key, "query": args.query, "max_results": args.limit},
                )
                resp.raise_for_status()
                data = resp.json()
                results = [
                    {
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("content", ""),
                    }
                    for item in data.get("results", [])[: args.limit]
                ]
                return {"ok": True, "provider": "tavily", "results": results}

        return {
            "ok": False,
            "error": "Aucune cle BRAVE_SEARCH_API_KEY ou TAVILY_API_KEY configuree.",
            "results": [],
        }

    async def web_fetch(self, args: WebFetchArgs) -> dict[str, Any]:
        current_url = args.url.strip()
        max_bytes = 2 * 1024 * 1024
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, follow_redirects=False
            ) as client:
                for _hop in range(6):
                    ok_host, host_reason = await asyncio.to_thread(
                        validate_http_target_host, current_url
                    )
                    if not ok_host:
                        return {"ok": False, "error": host_reason or "URL interdite (SSRF)."}
                    if self.autonomy_policy is not None:
                        ok_dom, dom_reason = self.autonomy_policy.check_url_allowed(current_url)
                        if not ok_dom:
                            return {"ok": False, "error": dom_reason or "Domaine interdit."}

                    async with client.stream(
                        "GET", current_url, headers={"User-Agent": "JARVIS-Core/2.0"}
                    ) as resp:
                        if resp.status_code in {301, 302, 303, 307, 308}:
                            loc = resp.headers.get("location")
                            if not loc:
                                return {"ok": False, "error": "Redirection HTTP sans Location."}
                            current_url = urljoin(str(resp.url), loc)
                            continue
                        resp.raise_for_status()
                        chunks: list[bytes] = []
                        total = 0
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            total += len(chunk)
                            if total > max_bytes:
                                break
                            chunks.append(chunk)
                        raw_html = b"".join(chunks).decode(
                            resp.encoding or "utf-8", errors="replace"
                        )
                        text = extract_clean_html_text(raw_html, max_chars=args.max_chars)
                        return {
                            "ok": True,
                            "url": str(resp.url),
                            "untrusted": True,
                            "content": text,
                        }
                return {"ok": False, "error": "Trop de redirections HTTP (> 5)."}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def memory_search(self, args: MemorySearchArgs) -> dict[str, Any]:
        if not self.db:
            return {"ok": False, "error": "Base memoire non initialisee."}

        def _sync_search() -> list[dict[str, Any]]:
            facts = self.db.search_facts_hybrid(args.query, limit=args.limit * 2)  # type: ignore[union-attr]
            safe = [
                f.model_dump()
                for f in facts
                if str(getattr(f, "sensitivity", "")).lower() not in {"tres_prive", "très privé"}
            ]
            return safe[: args.limit]

        return {"ok": True, "facts": await asyncio.to_thread(_sync_search)}

    async def memory_save(self, args: MemorySaveArgs) -> dict[str, Any]:
        if not self.db:
            return {"ok": False, "error": "Base memoire non initialisee."}
        fact = await asyncio.to_thread(
            self.db.create_fact,
            FactCreate(
                subject=args.subject,
                predicate=args.predicate,
                object=args.object or args.text[:80],
                text=args.text,
                category=args.category,
                confidence=0.95,
                source="tool_memory_save",
                sensitivity=args.sensitivity,
            ),
        )
        return {"ok": True, "fact": fact.model_dump()}

    async def memory_forget(self, args: MemoryForgetArgs) -> dict[str, Any]:
        if not self.db:
            return {"ok": False, "error": "Base memoire non initialisee."}
        if args.fact_id:
            deleted = await asyncio.to_thread(self.db.delete_fact, args.fact_id)
            return {"ok": deleted, "deleted_count": 1 if deleted else 0}
        if args.query:

            def _sync_forget_query() -> int:
                matches = self.db.list_facts(query=args.query, limit=50)  # type: ignore[union-attr]
                count = 0
                for item in matches:
                    if self.db.delete_fact(item.id):  # type: ignore[union-attr]
                        count += 1
                return count

            count = await asyncio.to_thread(_sync_forget_query)
            return {"ok": True, "deleted_count": count}
        return {"ok": False, "error": "Fournir fact_id ou query."}
