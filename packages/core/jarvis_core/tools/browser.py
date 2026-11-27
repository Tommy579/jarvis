"""Outils navigateur via Chrome DevTools Protocol (CDP) et arbre d'accessibilite sans screenshots."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urljoin, urlparse

import httpx
import websockets
from jarvis_shared.models import RiskLevel
from pydantic import BaseModel, Field

from .autonomy import AutonomyPolicy, extract_domain, validate_http_target_host
from .registry import TOOL_USAGE_KEY
from .registry import ToolSpec as RegistryToolSpec

INTERACTIVE_AX_ROLES: frozenset[str] = frozenset(
    {
        "button",
        "link",
        "textbox",
        "searchbox",
        "radio",
        "checkbox",
        "combobox",
        "listbox",
        "option",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "tab",
        "switch",
        "slider",
        "spinbutton",
    }
)

CONTENT_AX_ROLES: frozenset[str] = frozenset(
    {
        "heading",
        "paragraph",
        "label",
        "StaticText",
        "text",
        "listitem",
        "cell",
        "columnheader",
        "rowheader",
        "status",
        "alert",
        "dialog",
        "region",
        "main",
        "navigation",
        "form",
        "radiogroup",
        "group",
        "table",
    }
)


def _ax_val(prop: Any) -> Any:
    if isinstance(prop, dict):
        return prop.get("value")
    return prop


_NEUTRAL_ROLES: frozenset[str] = frozenset(
    {"", "generic", "GenericContainer", "StaticText", "text", "group", "section"}
)

# Repertoires et motifs jamais envoyes vers une page web : un upload est un puits
# controle par le contenu de la page, donc un vecteur d'exfiltration direct.
UPLOAD_FORBIDDEN_DIRS: tuple[str, ...] = (
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".gcloud",
    ".kube",
    ".docker",
    ".password-store",
    ".local/share/keyrings",
    ".config",
    ".mozilla",
    "jarvis-data",
    "jarvis-browser",
)
UPLOAD_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "/proc",
    "/sys",
    "/dev",
    "/etc",
    "/root",
)
UPLOAD_FORBIDDEN_SUFFIXES: tuple[str, ...] = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".kdbx",
    ".gpg",
    ".asc",
    ".ovpn",
)
UPLOAD_FORBIDDEN_NAMES: tuple[str, ...] = (
    "credentials",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "netrc",
    ".netrc",
    ".git-credentials",
    ".bash_history",
    ".zsh_history",
    ".npmrc",
    ".pypirc",
    "secrets.enc",
    "jarvis.db",
    "shadow",
    "passwd",
)


def validate_upload_path(raw_path: str) -> tuple[Path | None, str | None]:
    """Valide un fichier local avant envoi vers une page : (chemin, erreur)."""
    if not raw_path.strip():
        return None, "Chemin vide."
    try:
        path = Path(raw_path).expanduser().resolve()
    except Exception as exc:
        return None, f"Chemin invalide ({exc})."
    if not path.exists():
        return None, f"Fichier introuvable : {path}"
    if not path.is_file():
        return None, f"Pas un fichier regulier : {path}"
    if path.stat().st_size > 100 * 1024 * 1024:
        return None, f"Fichier trop volumineux (> 100 Mo) : {path}"
    if os.name != "nt":
        for prefix in UPLOAD_FORBIDDEN_PREFIXES:
            p = Path(prefix)
            if path == p or path.is_relative_to(p):
                return None, f"Repertoire systeme interdit a l'upload : {prefix}"
    # On raisonne sur les segments du chemin absolu (les symlinks sont deja resolus par
    # Path.resolve), donc ~/.ssh/id_rsa comme /etc/ssh/ssh_host_rsa_key sont couverts.
    parts = {part.lower() for part in path.parts}
    forbidden_segments = {d.rsplit("/", 1)[-1].lower() for d in UPLOAD_FORBIDDEN_DIRS}
    hit = parts & forbidden_segments
    if hit:
        return None, f"Repertoire sensible interdit a l'upload : {sorted(hit)[0]}"
    if path.suffix.lower() in UPLOAD_FORBIDDEN_SUFFIXES:
        return None, f"Extension sensible interdite a l'upload : {path.suffix}"
    name = path.name.lower()
    if name.startswith(".env") or name in UPLOAD_FORBIDDEN_NAMES or name.endswith(".env"):
        return None, f"Fichier de secrets interdit a l'upload : {path.name}"
    return path, None


# Plafond de construction du texte complet : au-dela on tronque l'arbre lui-meme
# (une page de 400k caracteres d'arbre n'est plus exploitable par un modele).
MAX_SNAPSHOT_BUILD_CHARS = 400_000


def format_ax_tree(
    nodes: list[dict[str, Any]],
    max_chars: int = 12000,
    generation: int | None = None,
    offset: int = 0,
) -> tuple[str, dict[str, int], int]:
    """Convertit un arbre Accessibility CDP en snapshot texte compact avec references [eN@G].

    `generation` (>= 1) versionne les references : les cles retournees deviennent "e3@2"
    et le texte affiche "[e3@2]". Une reference d'une generation anterieure est refusee
    par _lookup_backend_id, ce qui evite de cliquer silencieusement le mauvais element
    apres une nouvelle lecture de la page.

    `offset` pagine le snapshot par lignes entieres (le scroll ne change pas cet arbre) :
    on renvoie (texte, refs visibles, offset_suivant), offset_suivant valant 0 a la fin.
    """
    if not nodes:
        return ("(arbre d'accessibilite vide)", {}, 0)

    by_id: dict[str, dict[str, Any]] = {}
    children_map: dict[str, list[str]] = {}
    has_parent: set[str] = set()

    for node in nodes:
        nid = str(node.get("nodeId", ""))
        if not nid:
            continue
        by_id[nid] = node
        cids = [str(c) for c in (node.get("childIds") or [])]
        children_map[nid] = cids
        for c in cids:
            has_parent.add(c)

    roots = [nid for nid in by_id if nid not in has_parent]
    if not roots:
        roots = [str(nodes[0].get("nodeId", ""))]

    ref_map: dict[str, int] = {}
    lines: list[str] = []
    line_refs: list[str | None] = []
    visited: set[str] = set()
    ref_counter = 1
    total_chars = 0

    def _visit(nid: str, depth: int) -> None:
        nonlocal ref_counter, total_chars
        if total_chars >= MAX_SNAPSHOT_BUILD_CHARS or nid in visited:
            return
        visited.add(nid)
        node = by_id.get(nid)
        if not node:
            return

        ignored = bool(node.get("ignored", False))
        role = str(_ax_val(node.get("role")) or "")
        name = str(_ax_val(node.get("name")) or "").strip()
        value = _ax_val(node.get("value"))
        backend_id = node.get("backendDOMNodeId")

        props: dict[str, Any] = {}
        for p in node.get("properties") or []:
            if isinstance(p, dict) and p.get("name"):
                props[str(p["name"])] = _ax_val(p.get("value"))

        is_focusable = bool(props.get("focusable", False))
        is_editable = bool(props.get("editable", False))
        is_interactive = (
            not ignored
            and backend_id is not None
            and (
                role in INTERACTIVE_AX_ROLES
                or is_editable
                or (role == "Iframe" and bool(name))
                or (is_focusable and role not in {"RootWebArea", "WebArea"})
            )
        )

        should_emit = False
        if not ignored:
            if is_interactive:
                should_emit = True
            elif (
                role in {"heading", "RootWebArea", "WebArea", "dialog", "alert", "status"} and name
            ):
                should_emit = True
            elif role in CONTENT_AX_ROLES and name:
                # Eviter le bruit de StaticText redondant deja inclus dans le nom d'un bouton/lien
                should_emit = role != "StaticText" or depth <= 6

        next_depth = depth
        if should_emit:
            indent = "  " * min(depth, 8)
            prefix = ""
            emitted_ref: str | None = None
            if is_interactive:
                base_key = f"e{ref_counter}"
                ref_counter += 1
                ref_key = f"{base_key}@{generation}" if generation else base_key
                ref_map[ref_key] = int(backend_id)
                emitted_ref = ref_key
                prefix = f"[{ref_key}] "

            label_part = f' "{name[:160]}"' if name else ""
            state_bits: list[str] = []
            if value not in (None, ""):
                state_bits.append(f'value="{str(value)[:80]}"')
            for flag in ("checked", "selected", "disabled", "expanded", "required", "focused"):
                if flag in props and props[flag] not in (None, False, "false"):
                    state_bits.append(f"{flag}={props[flag]}")
            if is_editable:
                state_bits.append("editable=true")
            state_str = f" ({', '.join(state_bits)})" if state_bits else ""
            # Une zone contenteditable remonte en role "generic" : sans libelle dedie le
            # modele ne peut pas deviner que c'est la zone de saisie.
            if is_editable and role in _NEUTRAL_ROLES:
                role_label = "editable_text"
            else:
                role_label = role or "element"
            line = f"{indent}{prefix}{role_label}{label_part}{state_str}"

            if total_chars >= MAX_SNAPSHOT_BUILD_CHARS:
                return
            lines.append(line)
            line_refs.append(emitted_ref)
            total_chars += len(line) + 1
            next_depth = depth + 1

        # Si un element interactif porte deja un nom (ex: button "Valider") ou est une zone
        # editable, ne pas re-lister son StaticText interne (bruit + refs en double).
        skip_static_text = is_interactive and (bool(name) or is_editable)
        for cid in children_map.get(nid, []):
            child = by_id.get(cid)
            if skip_static_text and child:
                c_role = str(_ax_val(child.get("role")) or "")
                c_name = str(_ax_val(child.get("name")) or "").strip()
                if c_role in {"StaticText", "InlineTextBox"} and (c_name == name or is_editable):
                    continue
                # Chrome expose un noeud editable interne sans nom sous <input> : il
                # produisait une ref en double qui faisait remplir le mauvais element.
                if is_interactive and name and not c_name and c_role in {"textbox", "generic"}:
                    continue
            _visit(cid, next_depth)

    for r in roots:
        _visit(r, 0)

    if not lines:
        return ("(aucun element accessible detecte sur cette page)", {}, 0)

    # Pagination par lignes entieres : offsets[i] = position du debut de la ligne i.
    offsets: list[int] = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    total_chars = pos

    start_index = 0
    if offset > 0:
        start_index = len(lines)
        for i, line_offset in enumerate(offsets):
            if line_offset >= offset:
                start_index = i
                break

    page: list[str] = []
    page_chars = 0
    end_index = start_index
    while end_index < len(lines):
        candidate = lines[end_index]
        if page and page_chars + len(candidate) + 1 > max_chars:
            break
        page.append(candidate)
        page_chars += len(candidate) + 1
        end_index += 1

    next_offset = offsets[end_index] if end_index < len(lines) else 0
    text_out = "\n".join(page)
    if start_index > 0:
        text_out = f"... [suite du snapshot a partir du caractere {offset}]\n{text_out}"
    if next_offset:
        remaining = total_chars - next_offset
        text_out += (
            f"\n... [snapshot tronque : {remaining} caracteres restants, rappelle "
            f"browser_snapshot avec offset={next_offset} pour la suite]"
        )
    visible_refs = {
        key: ref_map[key]
        for key in line_refs[start_index:end_index]
        if key is not None and key in ref_map
    }
    return (text_out, visible_refs, next_offset)


class _CDPSession:
    """Session WebSocket Chrome DevTools Protocol legere."""

    def __init__(
        self,
        ws_url: str,
        timeout_seconds: float = 10.0,
        *,
        auto_dismiss_beforeunload: bool = True,
    ) -> None:
        self.ws_url = ws_url
        self.timeout_seconds = timeout_seconds
        self.auto_dismiss_beforeunload = auto_dismiss_beforeunload
        self.dismissed_beforeunload = False
        self._ws: Any = None
        self._next_id = 1
        self._page_enabled = False

    async def __aenter__(self) -> _CDPSession:
        self._ws = await websockets.connect(
            self.ws_url,
            max_size=16 * 1024 * 1024,
            open_timeout=self.timeout_seconds,
        )
        try:
            if self.auto_dismiss_beforeunload:
                try:
                    await self._send_raw("Page.enable", {}, wait_timeout=3.0)
                    self._page_enabled = True
                except Exception:
                    pass
        except BaseException:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
            raise
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def _send_raw(
        self,
        method: str,
        params: dict[str, Any],
        *,
        wait_timeout: float,
    ) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("Session CDP non ouverte.")
        req_id = self._next_id
        self._next_id += 1
        payload = {"id": req_id, "method": method, "params": params}
        await self._ws.send(json.dumps(payload))

        async def _wait_reply() -> dict[str, Any]:
            while True:
                raw = await self._ws.recv()
                msg = json.loads(raw)
                if (
                    self.auto_dismiss_beforeunload
                    and msg.get("method") == "Page.javascriptDialogOpening"
                ):
                    dlg_params = msg.get("params") or {}
                    if dlg_params.get("type") == "beforeunload":
                        dismiss_id = self._next_id
                        self._next_id += 1
                        self.dismissed_beforeunload = True
                        await self._ws.send(
                            json.dumps(
                                {
                                    "id": dismiss_id,
                                    "method": "Page.handleJavaScriptDialog",
                                    "params": {"accept": False},
                                }
                            )
                        )
                    continue
                if msg.get("id") == req_id:
                    if "error" in msg and msg["error"] is not None:
                        err = msg["error"]
                        raise RuntimeError(
                            err.get("message", str(err)) if isinstance(err, dict) else str(err)
                        )
                    return msg.get("result") or {}

        return await asyncio.wait_for(_wait_reply(), timeout=wait_timeout)

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        wait_timeout: float | None = None,
    ) -> dict[str, Any]:
        if method == "Page.enable" and self._page_enabled:
            return {}
        effective_timeout = wait_timeout if wait_timeout is not None else self.timeout_seconds
        try:
            return await self._send_raw(method, params or {}, wait_timeout=effective_timeout)
        except (TimeoutError, asyncio.TimeoutError):
            # Si un dialogue beforeunload deja ouvert avant la connexion bloque le renderer, on le rejette et retente une fois
            if self.auto_dismiss_beforeunload and method != "Page.handleJavaScriptDialog":
                try:
                    await self._send_raw(
                        "Page.handleJavaScriptDialog",
                        {"accept": False},
                        wait_timeout=2.0,
                    )
                    self.dismissed_beforeunload = True
                    return await self._send_raw(
                        method, params or {}, wait_timeout=effective_timeout
                    )
                except Exception:
                    pass
            raise


class BrowserStatusArgs(BaseModel):
    pass


class BrowserLaunchArgs(BaseModel):
    url: str | None = Field(default=None, description="URL optionnelle a ouvrir au lancement.")
    app: str | None = Field(
        default=None,
        description="Nom d'une application web (raccourci .desktop sur le PC, ex: 'Apple Music', 'Marges', 'Gemini') a lancer dans sa fenetre dediee.",
    )
    app_mode: bool = Field(
        default=False,
        description="Ouvrir l'URL en fenetre d'application web (--app=URL) sans barre d'onglets.",
    )
    port: int = Field(default=9222, ge=1024, le=65535)
    headless: bool = Field(default=False, description="Lancer sans fenetre visible.")


class BrowserListTabsArgs(BaseModel):
    pass


class BrowserOpenTabArgs(BaseModel):
    url: str = Field(min_length=1, description="URL http(s) a ouvrir dans un nouvel onglet.")


class BrowserNavigateArgs(BaseModel):
    url: str = Field(min_length=1, description="URL cible http(s).")
    tab_id: str | None = Field(default=None, description="ID de l'onglet (defaut: onglet actif).")


class BrowserSnapshotArgs(BaseModel):
    tab_id: str | None = Field(
        default=None, description="ID de l'onglet cible (defaut: onglet actif)."
    )
    activate: bool = Field(
        default=False,
        description="Mettre l'onglet au premier plan avant lecture (vole le focus de l'utilisateur).",
    )
    max_chars: int = Field(default=12000, ge=1000, le=30000)
    offset: int = Field(
        default=0,
        ge=0,
        description=(
            "Pagination du snapshot : reprendre a partir du next_offset renvoye par "
            "l'appel precedent (0 = premiere page)."
        ),
    )


class BrowserClickArgs(BaseModel):
    ref: str = Field(
        min_length=1,
        description="Reference versionnee retournee par browser_snapshot (ex: e3@2, ou e3 pour la generation courante).",
    )
    tab_id: str | None = Field(
        default=None, description="ID de l'onglet cible (defaut: dernier onglet lu)."
    )


class BrowserCloseTabArgs(BaseModel):
    tab_id: str | None = Field(
        default=None, description="ID de l'onglet a fermer (defaut: dernier onglet lu)."
    )


class BrowserScreenshotArgs(BaseModel):
    ref: str | None = Field(
        default=None,
        description="Reference versionnee de l'element (ex: e4@1) pour capturer uniquement cette zone/canvas/element. Si omis, capture la vue.",
    )
    full_page: bool = Field(
        default=False,
        description="Capture la page entiere avec defilement (ignore si ref est specifie).",
    )
    save_path: str | None = Field(
        default=None,
        description="Chemin local absolu ou relatif ou enregistrer l'image PNG. Si omis, un fichier temporaire est cree.",
    )
    tab_id: str | None = Field(default=None, description="ID de l'onglet cible.")


class BrowserHistoryArgs(BaseModel):
    direction: Literal["back", "forward"] = Field(
        description="Sens de navigation dans l'historique."
    )
    tab_id: str | None = Field(default=None, description="ID de l'onglet cible.")


class BrowserUploadFileArgs(BaseModel):
    ref: str = Field(
        min_length=1,
        description="Reference versionnee de l'element <input type=file> (ex: e7@1).",
    )
    file_paths: list[str] = Field(
        min_length=1,
        max_length=5,
        description="Chemins absolus des fichiers locaux a deposer dans le champ.",
    )
    tab_id: str | None = Field(default=None, description="ID de l'onglet cible.")


class BrowserFillArgs(BaseModel):
    ref: str = Field(min_length=1, description="Reference du champ texte (ex: e4).")
    value: str = Field(description="Texte a saisir dans le champ.")
    clear_first: bool = Field(default=True, description="Effacer le contenu existant avant saisie.")
    tab_id: str | None = Field(default=None, description="ID de l'onglet cible.")


class BrowserSelectArgs(BaseModel):
    ref: str = Field(min_length=1, description="Reference du menu deroulant <select> (ex: e5).")
    value: str = Field(min_length=1, description="Valeur ou texte de l'option a selectionner.")
    tab_id: str | None = Field(default=None, description="ID de l'onglet cible.")


class BrowserPressKeyArgs(BaseModel):
    key: str = Field(
        min_length=1, description="Touche a envoyer (Enter, Tab, Escape, Space, ArrowDown...)."
    )
    tab_id: str | None = Field(default=None, description="ID de l'onglet cible.")


class BrowserScrollArgs(BaseModel):
    direction: Literal["down", "up", "top", "bottom"] = "down"
    pixels: int = Field(default=600, ge=50, le=5000)
    ref: str | None = Field(
        default=None, description="Reference optionnelle a faire defiler dans la vue."
    )
    tab_id: str | None = Field(default=None, description="ID de l'onglet cible.")


class BrowserDragArgs(BaseModel):
    source_ref: str = Field(
        min_length=1, description="Reference de l'element source a deplacer (ex: e3)."
    )
    target_ref: str = Field(
        min_length=1, description="Reference de la zone cible ou deposer (ex: e12)."
    )
    steps: int = Field(
        default=10, ge=2, le=40, description="Nombre d'etapes intermediaires du glisser."
    )
    tab_id: str | None = Field(default=None, description="ID de l'onglet cible.")


class BrowserHandleDialogArgs(BaseModel):
    accept: bool = Field(default=True, description="True pour accepter/OK, False pour annuler.")
    prompt_text: str | None = Field(
        default=None, description="Texte optionnel pour une boite prompt()."
    )
    tab_id: str | None = Field(default=None, description="ID de l'onglet cible.")


class BrowserAnalyzeMediaArgs(BaseModel):
    question: str = Field(
        default="Transcris ou resume le contenu de ce media et reponds aux questions liees.",
        min_length=1,
        max_length=2000,
        description="Question ou consigne pour analyser la video/audio de la page.",
    )
    media_index: int = Field(
        default=0, ge=0, le=20, description="Index de la balise <video>/<audio> sur la page."
    )
    audio_only: bool = Field(
        default=True,
        description="Extraire uniquement la piste audio (MP3 32kbps) pour consommer ~10x moins de tokens.",
    )
    tab_id: str | None = Field(default=None, description="ID de l'onglet cible.")


_DESKTOP_FIELD_CODES: frozenset[str] = frozenset({"%u", "%U", "%f", "%F", "%i", "%c", "%k"})
_DESKTOP_CHROME_DOMAIN_RE = re.compile(r"^chrome-(?P<domain>[a-zA-Z0-9.-]+?)__")


@dataclass(slots=True)
class _WebAppShortcut:
    desktop_path: Path
    desktop_id: str
    name: str
    exec_cmd: list[str]
    app_url: str | None
    app_id: str | None
    domain: str
    wm_class: str | None


def _sync_default_profile_session(dst_root: Path, src_root: Path | None = None) -> None:
    """Copie la session Chromium Default (cookies, preferences, storage) vers dst_root."""
    source_root = src_root or (Path.home() / ".config" / "chromium")
    src_def = source_root / "Default"
    if not src_def.is_dir():
        return
    dst_def = dst_root / "Default"
    dst_def.mkdir(parents=True, exist_ok=True)
    for fname in ("Local State",):
        src_file = source_root / fname
        if src_file.is_file():
            try:
                shutil.copy2(src_file, dst_root / fname)
            except OSError:
                pass
    for fname in ("Cookies", "Preferences", "Secure Preferences"):
        src_file = src_def / fname
        if src_file.is_file():
            try:
                shutil.copy2(src_file, dst_def / fname)
            except OSError:
                pass
    for dname in ("Local Storage", "Session Storage"):
        src_dir = src_def / dname
        if src_dir.is_dir():
            try:
                shutil.copytree(
                    src_dir,
                    dst_def / dname,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("LOCK", "*.lock"),
                )
            except OSError:
                pass


class BrowserToolkit:
    """Controle autonome du navigateur Chrome/Chromium via CDP et arbre d'accessibilite."""

    def __init__(
        self,
        cdp_url: str | None = None,
        autonomy_policy: AutonomyPolicy | None = None,
        timeout_seconds: float = 10.0,
        apps_dir: Path | None = None,
    ) -> None:
        self.cdp_url = (
            cdp_url or os.environ.get("JARVIS_CDP_URL", "http://127.0.0.1:9222")
        ).rstrip("/")
        self.autonomy_policy = autonomy_policy
        self.timeout_seconds = timeout_seconds
        self.apps_dir: Path = apps_dir or (Path.home() / ".local" / "share" / "applications")
        self._ref_maps: dict[str, dict[str, int]] = {}
        self._ref_gens: dict[str, int] = {}
        self._active_tab_id: str | None = None
        self._launched_proc: subprocess.Popen[bytes] | None = None
        self._tab_locks: dict[str, asyncio.Lock] = {}

    def _tab_lock(self, tab_id: str) -> asyncio.Lock:
        lock = self._tab_locks.get(tab_id)
        if lock is None:
            lock = asyncio.Lock()
            self._tab_locks[tab_id] = lock
        return lock

    @staticmethod
    def _clean_ref(ref: str) -> str:
        return ref.strip().lstrip("[@").rstrip("]")

    async def _fetch_tabs(self) -> list[dict[str, Any]]:
        candidate_urls = [self.cdp_url]
        if self.cdp_url in {"http://127.0.0.1:9222", "http://localhost:9222"}:
            for extra_port in (9223, 9224):
                url = f"http://127.0.0.1:{extra_port}"
                if url not in candidate_urls:
                    candidate_urls.append(url)

        last_exc: Exception | None = None
        for base_url in candidate_urls:
            try:
                async with httpx.AsyncClient(timeout=min(self.timeout_seconds, 3.0)) as client:
                    resp = await client.get(f"{base_url}/json/list")
                    resp.raise_for_status()
                    items = resp.json()
                self.cdp_url = base_url
                return [
                    t
                    for t in items
                    if isinstance(t, dict)
                    and t.get("type") == "page"
                    and t.get("webSocketDebuggerUrl")
                    and not str(t.get("url", "")).startswith("devtools://")
                ]
            except Exception as exc:
                last_exc = exc
        if last_exc:
            raise last_exc
        return []

    async def _resolve_tab(self, tab_id: str | None = None) -> dict[str, Any]:
        tabs = await self._fetch_tabs()
        if not tabs:
            raise RuntimeError(
                f"Aucun onglet Chrome/Chromium detecte sur {self.cdp_url}. "
                "Lancez Chromium avec --remote-debugging-port=9222 ou utilisez browser_launch."
            )
        target_id = tab_id or self._active_tab_id
        if target_id:
            for t in tabs:
                if t.get("id") == target_id:
                    return t
            if tab_id:
                # Ne jamais retomber silencieusement sur un autre onglet : le modele agirait
                # sur une page differente de celle qu'il croit cibler.
                known = ", ".join(str(t.get("id")) for t in tabs[:5])
                raise RuntimeError(
                    f"Onglet '{tab_id}' introuvable (onglets ouverts : {known}). "
                    "Appelez browser_list_tabs pour rafraichir les identifiants."
                )
        self._active_tab_id = str(tabs[0]["id"])
        return tabs[0]

    async def _wait_dom_stable(
        self,
        cdp: _CDPSession,
        *,
        timeout: float = 8.0,
        quiet: float = 0.25,
    ) -> bool:
        """Attend que readyState soit complete et que le DOM arrete de bouger.

        Naviguer puis lire immediatement l'arbre donne l'etat d'avant l'action sur une SPA
        ou un site lent ; ce polling evite au modele de conclure sur du vide.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        last_sig: str | None = None
        stable_since = 0.0
        js = (
            "(() => ({ready: document.readyState, "
            "count: document.getElementsByTagName('*').length, "
            "size: (document.body ? document.body.innerHTML.length : 0)}))()"
        )
        while loop.time() < deadline:
            res = await cdp.call("Runtime.evaluate", {"expression": js, "returnByValue": True})
            info = (res.get("result") or {}).get("value") or {}
            sig = f"{info.get('ready')}|{info.get('count')}|{info.get('size')}"
            ready = str(info.get("ready")) == "complete"
            if sig == last_sig and ready and (loop.time() - stable_since) >= quiet:
                return True
            if sig != last_sig:
                last_sig = sig
                stable_since = loop.time()
            await asyncio.sleep(0.1)
        return False

    async def _safe_wait_dom_stable(
        self, cdp: _CDPSession, *, timeout: float = 8.0, quiet: float = 0.25
    ) -> bool:
        """Comme _wait_dom_stable mais tolerant aux navigations qui tuent la session CDP."""
        try:
            return await self._wait_dom_stable(cdp, timeout=timeout, quiet=quiet)
        except Exception:
            return False

    def _check_tab_domain(self, tab_url: str) -> str | None:
        if self.autonomy_policy is None:
            return None
        ok, reason = self.autonomy_policy.check_url_allowed(tab_url)
        return None if ok else (reason or "Domaine bloque.")

    @staticmethod
    def _validate_http_url(url: str) -> str | None:
        cleaned = (url or "").strip()
        parsed = urlparse(cleaned)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return "Seules les URLs http:// ou https:// valides sont autorisees."
        return None

    def close(self) -> None:
        """Termine proprement le processus Chromium eventuellement lance par ce toolkit."""
        proc = self._launched_proc
        self._launched_proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    async def browser_status(self, args: BrowserStatusArgs) -> dict[str, Any]:
        _ = args
        try:
            tabs = await self._fetch_tabs()
            return {
                "ok": True,
                "connected": True,
                "cdp_url": self.cdp_url,
                "tabs_count": len(tabs),
                "active_tab_id": self._active_tab_id or (tabs[0]["id"] if tabs else None),
            }
        except Exception as exc:
            return {
                "ok": False,
                "connected": False,
                "cdp_url": self.cdp_url,
                "error": str(exc),
                "hint": "Lancez chromium --remote-debugging-port=9222 ou appelez browser_launch.",
            }

    @staticmethod
    def _find_browser_binary() -> str | None:
        return (
            shutil.which("chromium")
            or shutil.which("google-chrome")
            or shutil.which("google-chrome-stable")
            or shutil.which("google-chrome-beta")
            or shutil.which("google-chrome-unstable")
            or shutil.which("chromium-browser")
            or shutil.which("brave")
            or shutil.which("brave-browser")
            or shutil.which("microsoft-edge")
            or shutil.which("microsoft-edge-stable")
            or shutil.which("edge")
        )

    def _discover_webapp_shortcuts(self) -> list[_WebAppShortcut]:
        if not self.apps_dir.is_dir():
            return []
        shortcuts: list[_WebAppShortcut] = []
        for path in sorted(self.apps_dir.glob("*.desktop")):
            if path.name in {"jarvis.desktop", "moa.desktop"}:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            name = ""
            exec_raw = ""
            wm_class: str | None = None
            in_desktop_entry = False
            for raw_line in content.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("["):
                    in_desktop_entry = line == "[Desktop Entry]"
                    continue
                if not in_desktop_entry or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if key == "Name" and not name:
                    name = val
                elif key == "Exec" and not exec_raw:
                    exec_raw = val
                elif key == "StartupWMClass" and not wm_class:
                    wm_class = val
            if not exec_raw:
                continue
            try:
                tokens = [tok for tok in shlex.split(exec_raw) if tok not in _DESKTOP_FIELD_CODES]
            except ValueError:
                continue
            if not tokens:
                continue
            app_url: str | None = None
            app_id: str | None = None
            for tok in tokens:
                if tok.startswith("--app="):
                    app_url = tok.split("=", 1)[1].strip()
                elif tok.startswith("--app-id="):
                    app_id = tok.split("=", 1)[1].strip()
            if not app_url and not app_id:
                continue
            domain = extract_domain(app_url) if app_url else ""
            if not domain:
                m = _DESKTOP_CHROME_DOMAIN_RE.match(path.stem)
                if m:
                    domain = extract_domain(m.group("domain"))
            if domain in {"localhost", "127.0.0.1"}:
                continue
            shortcuts.append(
                _WebAppShortcut(
                    desktop_path=path,
                    desktop_id=path.name,
                    name=name or path.stem,
                    exec_cmd=tokens,
                    app_url=app_url,
                    app_id=app_id,
                    domain=domain,
                    wm_class=wm_class,
                )
            )
        return shortcuts

    def _find_webapp_shortcut(
        self, *, url: str | None = None, app: str | None = None
    ) -> _WebAppShortcut | None:
        shortcuts = self._discover_webapp_shortcuts()
        if not shortcuts:
            return None
        if app:
            needle = app.strip().lower()
            for sc in shortcuts:
                if sc.name.lower() == needle or sc.desktop_id.lower() == needle:
                    return sc
            for sc in shortcuts:
                if needle in sc.name.lower() or (sc.domain and needle in sc.domain.lower()):
                    return sc
        if url:
            target_domain = extract_domain(url)
            if target_domain:
                for sc in shortcuts:
                    if not sc.domain:
                        continue
                    if (
                        target_domain == sc.domain
                        or target_domain.endswith(f".{sc.domain}")
                        or sc.domain.endswith(f".{target_domain}")
                    ):
                        return sc
        return None

    @staticmethod
    def _tab_matches_domain(tab_url: str, domain: str) -> bool:
        if not domain:
            return False
        tab_dom = extract_domain(tab_url)
        return bool(
            tab_dom
            and (
                tab_dom == domain
                or tab_dom.endswith(f".{domain}")
                or domain.endswith(f".{tab_dom}")
            )
        )

    async def _wait_tab_stable(self, tab: dict[str, Any]) -> bool:
        ws_url = tab.get("webSocketDebuggerUrl")
        if not ws_url:
            return False
        for _ in range(3):
            try:
                async with _CDPSession(str(ws_url), self.timeout_seconds) as cdp:
                    return await self._safe_wait_dom_stable(cdp, timeout=8.0)
            except Exception:
                await asyncio.sleep(0.3)
        return False

    async def _open_or_activate_webapp(
        self,
        shortcut: _WebAppShortcut,
        *,
        requested_url: str | None = None,
        port: int = 9222,
    ) -> dict[str, Any]:
        target_url = (requested_url or shortcut.app_url or "").strip()
        if target_url:
            scheme_err = self._validate_http_url(target_url)
            if scheme_err:
                return {"ok": False, "error": scheme_err}
            if self.autonomy_policy:
                ok, reason = self.autonomy_policy.check_url_allowed(target_url)
                if not ok:
                    return {"ok": False, "error": reason}

        before_ids: set[str] = set()
        try:
            existing_tabs = await self._fetch_tabs()
            before_ids = {str(t.get("id", "")) for t in existing_tabs}
            for tab in existing_tabs:
                if self._tab_matches_domain(str(tab.get("url", "")), shortcut.domain):
                    tid = str(tab.get("id", ""))
                    self._active_tab_id = tid
                    try:
                        async with httpx.AsyncClient(timeout=3.0) as client:
                            await client.get(f"{self.cdp_url}/json/activate/{tid}")
                    except Exception:
                        pass
                    return {
                        "ok": True,
                        "already_open": True,
                        "tab_id": tid,
                        "url": tab.get("url", ""),
                        "title": tab.get("title", ""),
                        "web_app": shortcut.name,
                        "desktop_file": shortcut.desktop_id,
                    }
        except Exception:
            existing_tabs = []

        # Lancer d'abord via la commande du raccourci .desktop
        cmd = list(shortcut.exec_cmd)
        if not any(tok.startswith("--remote-debugging-port") for tok in cmd):
            cmd.insert(1, f"--remote-debugging-port={port}")
        if not any(tok.startswith("--window-size") for tok in cmd):
            cmd.append("--window-size=1280,820")

        self._launched_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if not before_ids:
            self.cdp_url = f"http://127.0.0.1:{port}"

        matched_tab: dict[str, Any] | None = None
        for _ in range(14):
            await asyncio.sleep(0.25)
            try:
                tabs = await self._fetch_tabs()
            except Exception:
                continue
            for tab in tabs:
                if self._tab_matches_domain(str(tab.get("url", "")), shortcut.domain):
                    matched_tab = tab
                    break
            if matched_tab is None and before_ids:
                for tab in tabs:
                    if str(tab.get("id", "")) not in before_ids:
                        matched_tab = tab
                        break
            if matched_tab is not None:
                break

        # Repli si ~/.config/chromium est deja verrouille par un Chromium sans CDP
        if matched_tab is None and (shortcut.app_url or target_url):
            browser_bin = self._find_browser_binary()
            if browser_bin:
                app_url = shortcut.app_url or target_url
                profile_dir = Path.home() / ".local" / "share" / "jarvis-browser"
                profile_dir.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(_sync_default_profile_session, profile_dir)
                fallback_port = port if not before_ids else (port + 1 if port < 65535 else port - 1)
                fallback_cmd = [
                    browser_bin,
                    f"--remote-debugging-port={fallback_port}",
                    f"--user-data-dir={profile_dir}",
                    "--profile-directory=Default",
                    "--window-size=1280,820",
                    "--no-first-run",
                    "--no-default-browser-check",
                ]
                if shortcut.wm_class:
                    fallback_cmd.append(f"--class={shortcut.wm_class}")
                fallback_cmd.append(f"--app={app_url}")
                self._launched_proc = subprocess.Popen(
                    fallback_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self.cdp_url = f"http://127.0.0.1:{fallback_port}"
                for _ in range(24):
                    await asyncio.sleep(0.25)
                    try:
                        tabs = await self._fetch_tabs()
                    except Exception:
                        continue
                    for tab in tabs:
                        if self._tab_matches_domain(str(tab.get("url", "")), shortcut.domain):
                            matched_tab = tab
                            break
                    if matched_tab is None and tabs:
                        matched_tab = tabs[0]
                    if matched_tab is not None:
                        break

        if matched_tab is None:
            return {
                "ok": False,
                "error": (
                    f"Application web '{shortcut.name}' lancee mais inaccessible via CDP sur {self.cdp_url}."
                ),
                "web_app": shortcut.name,
                "desktop_file": shortcut.desktop_id,
            }

        tid = str(matched_tab.get("id", ""))
        self._active_tab_id = tid
        load_stable = await self._wait_tab_stable(matched_tab)
        return {
            "ok": True,
            "launched": True,
            "tab_id": tid,
            "url": matched_tab.get("url", target_url),
            "title": matched_tab.get("title", ""),
            "web_app": shortcut.name,
            "desktop_file": shortcut.desktop_id,
            "load_stable": load_stable,
        }

    async def browser_launch(self, args: BrowserLaunchArgs) -> dict[str, Any]:
        shortcut = self._find_webapp_shortcut(url=args.url, app=args.app)
        if shortcut is not None:
            return await self._open_or_activate_webapp(
                shortcut, requested_url=args.url, port=args.port
            )

        try:
            tabs = await self._fetch_tabs()
            return {
                "ok": True,
                "already_running": True,
                "cdp_url": self.cdp_url,
                "tabs_count": len(tabs),
            }
        except Exception:
            pass

        if args.url:
            scheme_err = self._validate_http_url(args.url)
            if scheme_err:
                return {"ok": False, "error": scheme_err}
            if self.autonomy_policy:
                ok, reason = self.autonomy_policy.check_url_allowed(args.url)
                if not ok:
                    return {"ok": False, "error": reason}

        browser_bin = self._find_browser_binary()
        if not browser_bin:
            return {"ok": False, "error": "Aucun binaire Chromium/Chrome trouve dans PATH."}

        self.close()
        profile_dir = Path.home() / ".local" / "share" / "jarvis-browser"
        profile_dir.mkdir(parents=True, exist_ok=True)
        if not args.headless:
            await asyncio.to_thread(_sync_default_profile_session, profile_dir)
        cmd = [
            browser_bin,
            f"--remote-debugging-port={args.port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if args.headless:
            cmd.append("--headless=new")
        if args.url:
            if args.app_mode:
                cmd.append(f"--app={args.url.strip()}")
            else:
                cmd.extend(["--", args.url.strip()])

        self._launched_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.cdp_url = f"http://127.0.0.1:{args.port}"
        for _ in range(20):
            await asyncio.sleep(0.25)
            try:
                tabs = await self._fetch_tabs()
                return {
                    "ok": True,
                    "launched": True,
                    "binary": browser_bin,
                    "cdp_url": self.cdp_url,
                    "tabs_count": len(tabs),
                }
            except Exception:
                continue
        self.close()
        return {
            "ok": False,
            "error": f"Navigateur lance mais port CDP {args.port} inaccessible apres 5s.",
        }

    async def browser_list_tabs(self, args: BrowserListTabsArgs) -> dict[str, Any]:
        _ = args
        tabs = await self._fetch_tabs()
        items = [
            {
                "tab_id": t.get("id"),
                "title": t.get("title", ""),
                "url": t.get("url", ""),
                "active": t.get("id") == self._active_tab_id,
                # Signale les onglets que la politique de domaines interdit : inutile
                # d'essayer de les lire, browser_snapshot les refusera.
                "allowed": self._check_tab_domain(str(t.get("url", ""))) is None,
            }
            for t in tabs
        ]
        return {"ok": True, "count": len(items), "tabs": items}

    async def browser_open_tab(self, args: BrowserOpenTabArgs) -> dict[str, Any]:
        scheme_err = self._validate_http_url(args.url)
        if scheme_err:
            return {"ok": False, "error": scheme_err}
        if self.autonomy_policy:
            ok, reason = self.autonomy_policy.check_url_allowed(args.url)
            if not ok:
                return {"ok": False, "error": reason}
        shortcut = self._find_webapp_shortcut(url=args.url)
        if shortcut is not None:
            return await self._open_or_activate_webapp(shortcut, requested_url=args.url)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.put(f"{self.cdp_url}/json/new?{quote(args.url, safe=':/?&=')}")
            resp.raise_for_status()
            tab = resp.json()
        self._active_tab_id = str(tab.get("id", ""))
        # /json/new rend la main avant le chargement : sans cette attente le premier
        # browser_snapshot lit un arbre vide et le modele croit la page inutilisable.
        load_stable = await self._wait_tab_stable(tab)
        return {
            "ok": True,
            "tab_id": self._active_tab_id,
            "url": tab.get("url", args.url),
            "title": tab.get("title", ""),
            "load_stable": load_stable,
        }

    async def browser_close_tab(self, args: BrowserCloseTabArgs) -> dict[str, Any]:
        tab = await self._resolve_tab(args.tab_id)
        tid = str(tab["id"])
        async with self._tab_lock(tid):
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(f"{self.cdp_url}/json/close/{tid}")
                resp.raise_for_status()
            self._ref_maps.pop(tid, None)
            self._ref_gens.pop(tid, None)
            if self._active_tab_id == tid:
                self._active_tab_id = None
        self._tab_locks.pop(tid, None)
        return {"ok": True, "closed_tab_id": tid}

    async def browser_history(self, args: BrowserHistoryArgs) -> dict[str, Any]:
        tab = await self._resolve_tab(args.tab_id)
        tid = str(tab["id"])
        blocked = self._check_tab_domain(str(tab.get("url", "")))
        if blocked:
            return {"ok": False, "error": blocked, "blocked_by": "domain_policy"}
        async with self._tab_lock(tid):
            async with _CDPSession(tab["webSocketDebuggerUrl"], self.timeout_seconds) as cdp:
                await cdp.call("Page.enable")
                await cdp.call(
                    "Runtime.evaluate",
                    {"expression": f"history.{args.direction}()", "returnByValue": True},
                )
                stable = await self._safe_wait_dom_stable(cdp, timeout=10.0)
            self._ref_maps.pop(tid, None)
            self._ref_gens.pop(tid, None)
        return {
            "ok": True,
            "tab_id": tid,
            "direction": args.direction,
            "load_stable": stable,
        }

    async def browser_upload_file(self, args: BrowserUploadFileArgs) -> dict[str, Any]:
        paths: list[Path] = []
        for raw in args.file_paths:
            path, error = validate_upload_path(raw)
            if path is None:
                return {"ok": False, "error": error, "refused_path": raw}
            paths.append(path)
        tab = await self._resolve_tab(args.tab_id)
        tid = str(tab["id"])
        blocked = self._check_tab_domain(str(tab.get("url", "")))
        if blocked:
            return {"ok": False, "error": blocked, "blocked_by": "domain_policy"}

        async with self._tab_lock(tid):
            backend_id = self._lookup_backend_id(tid, args.ref)
            async with _CDPSession(tab["webSocketDebuggerUrl"], self.timeout_seconds) as cdp:
                await cdp.call("DOM.enable")
                await cdp.call(
                    "DOM.setFileInputFiles",
                    {"files": [str(p) for p in paths], "backendNodeId": backend_id},
                )
                # Relecture : confirme que le champ contient bien les fichiers vises.
                attached: list[str] = []
                try:
                    resolved = await cdp.call("DOM.resolveNode", {"backendNodeId": backend_id})
                    obj_id = (resolved.get("object") or {}).get("objectId")
                    if obj_id:
                        read_res = await cdp.call(
                            "Runtime.callFunctionOn",
                            {
                                "objectId": obj_id,
                                "returnByValue": True,
                                "functionDeclaration": (
                                    "function() { "
                                    "const f = this.files || []; "
                                    "return Array.from(f).map(x => x.name); "
                                    "}"
                                ),
                            },
                        )
                        names = (read_res.get("result") or {}).get("value")
                        if isinstance(names, list):
                            attached = [str(n) for n in names]
                except Exception:
                    attached = []
        return {
            "ok": True,
            "tab_id": tid,
            "uploaded_ref": self._clean_ref(args.ref),
            "files": attached or [p.name for p in paths],
            "verified": bool(attached) and len(attached) == len(paths),
            "note": "Champ fichier rempli : soumets le formulaire avec browser_click.",
        }

    async def browser_navigate(self, args: BrowserNavigateArgs) -> dict[str, Any]:
        scheme_err = self._validate_http_url(args.url)
        if scheme_err:
            return {"ok": False, "error": scheme_err}
        if self.autonomy_policy:
            ok, reason = self.autonomy_policy.check_url_allowed(args.url)
            if not ok:
                return {"ok": False, "error": reason}
        tab = await self._resolve_tab(args.tab_id)
        tid = str(tab["id"])
        self._active_tab_id = tid
        async with self._tab_lock(tid):
            async with _CDPSession(tab["webSocketDebuggerUrl"], self.timeout_seconds) as cdp:
                await cdp.call("Page.enable")
                res = await cdp.call("Page.navigate", {"url": args.url})
                stable = await self._safe_wait_dom_stable(cdp, timeout=10.0)
            self._ref_maps.pop(tid, None)
            self._ref_gens.pop(tid, None)
        return {
            "ok": True,
            "tab_id": tid,
            "url": args.url,
            "frame_id": res.get("frameId"),
            "load_stable": stable,
        }

    async def browser_snapshot(self, args: BrowserSnapshotArgs) -> dict[str, Any]:
        tab = await self._resolve_tab(args.tab_id)
        tid = str(tab["id"])
        # La liste blanche/noire doit aussi proteger la LECTURE : sinon il suffit d'ouvrir
        # l'onglet a la main pour exfiltrer le contenu d'un domaine interdit.
        blocked = self._check_tab_domain(str(tab.get("url", "")))
        if blocked:
            return {"ok": False, "error": blocked, "blocked_by": "domain_policy"}
        self._active_tab_id = tid
        if args.activate:
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    await client.get(f"{self.cdp_url}/json/activate/{tid}")
            except Exception:
                pass

        async with self._tab_lock(tid):
            async with _CDPSession(tab["webSocketDebuggerUrl"], self.timeout_seconds) as cdp:
                await cdp.call("Accessibility.enable")
                ax_res = await cdp.call("Accessibility.getFullAXTree")
                nodes = list(ax_res.get("nodes") or [])
                try:
                    frame_tree_res = await cdp.call("Page.getFrameTree")
                    child_frame_ids: list[str] = []

                    def _collect_frames(ft: dict[str, Any], is_root: bool = True) -> None:
                        frame = ft.get("frame") or {}
                        fid = frame.get("id")
                        if not is_root and fid:
                            child_frame_ids.append(str(fid))
                        for ch in ft.get("childFrames") or []:
                            if isinstance(ch, dict):
                                _collect_frames(ch, is_root=False)

                    _collect_frames(frame_tree_res.get("frameTree") or {}, is_root=True)
                    for fid in child_frame_ids[:8]:
                        try:
                            sub_ax = await cdp.call("Accessibility.getFullAXTree", {"frameId": fid})
                            for n in sub_ax.get("nodes") or []:
                                cloned = dict(n)
                                if cloned.get("nodeId") is not None:
                                    cloned["nodeId"] = f"{fid}:{cloned['nodeId']}"
                                if cloned.get("childIds"):
                                    cloned["childIds"] = [f"{fid}:{c}" for c in cloned["childIds"]]
                                nodes.append(cloned)
                        except Exception:
                            continue
                except Exception:
                    pass
            # offset=0 : nouvelle lecture -> nouvelle generation, table remise a zero.
            # offset>0 : page suivante de la MEME generation, refs accumulees (sinon les
            # references de la page precedente deviendraient perimees en pleine lecture).
            continuing = args.offset > 0 and self._ref_gens.get(tid, 0) > 0
            if continuing:
                generation = self._ref_gens[tid]
            else:
                generation = self._ref_gens.get(tid, 0) + 1
                self._ref_gens[tid] = generation

            snapshot_text, page_refs, next_offset = format_ax_tree(
                nodes,
                max_chars=args.max_chars,
                generation=generation,
                offset=args.offset,
            )
            if continuing:
                self._ref_maps.setdefault(tid, {}).update(page_refs)
            else:
                self._ref_maps[tid] = dict(page_refs)

        lowered = snapshot_text.lower()
        title_lowered = str(tab.get("title", "")).lower()
        is_challenge = any(
            marker in lowered or marker in title_lowered
            for marker in (
                "cf-chl",
                "cloudflare",
                "turnstile",
                "recaptcha",
                "hcaptcha",
                "verify you are human",
                "attention required! | cloudflare",
                "vérification que vous êtes un humain",
            )
        )

        return {
            "ok": True,
            "tab_id": tid,
            "title": tab.get("title", ""),
            "url": tab.get("url", ""),
            "generation": generation,
            "offset": args.offset,
            "next_offset": next_offset,
            "has_more": bool(next_offset),
            "refs_count": len(page_refs),
            "challenge_detected": is_challenge,
            "snapshot": snapshot_text,
        }

    @staticmethod
    def _split_ref(ref: str) -> tuple[str, int | None]:
        """Decoupe 'e4@2' en ('e4', 2). Sans suffixe, la generation est implicite."""
        key = BrowserToolkit._clean_ref(ref)
        base, _, gen_raw = key.partition("@")
        if not gen_raw:
            return base, None
        try:
            return base, int(gen_raw)
        except ValueError:
            raise RuntimeError(f"Reference '{ref}' invalide : format attendu eN ou eN@G.") from None

    def _lookup_backend_id(self, tab_id: str, ref: str) -> int:
        key, gen = self._split_ref(ref)
        current = self._ref_gens.get(tab_id, 0)
        ref_map = self._ref_maps.get(tab_id) or {}
        if not ref_map:
            raise RuntimeError(
                f"Aucune reference connue pour l'onglet {tab_id}. Appelez browser_snapshot d'abord."
            )
        if gen is None and current > 1:
            raise RuntimeError(
                f"Reference '{ref}' ambigue : cet onglet a ete relu ({current} snapshots). "
                "Reprends la reference versionnee eN@G du dernier browser_snapshot."
            )
        if gen is not None and gen != current:
            raise RuntimeError(
                f"Reference '{ref}' perimee : le dernier snapshot de cet onglet est la "
                f"generation @{current}. Refais browser_snapshot et utilise ses references."
            )
        lookup_key = f"{key}@{gen if gen is not None else current}" if current else key
        if lookup_key not in ref_map:
            raise RuntimeError(
                f"Reference '{key}' introuvable pour l'onglet {tab_id}. "
                "Appelez browser_snapshot d'abord."
            )
        return ref_map[lookup_key]

    async def browser_click(self, args: BrowserClickArgs) -> dict[str, Any]:
        tab = await self._resolve_tab(args.tab_id)
        tid = str(tab["id"])
        blocked = self._check_tab_domain(str(tab.get("url", "")))
        if blocked:
            return {"ok": False, "error": blocked}

        async with self._tab_lock(tid):
            backend_id = self._lookup_backend_id(tid, args.ref)
            async with _CDPSession(tab["webSocketDebuggerUrl"], self.timeout_seconds) as cdp:
                await cdp.call("DOM.enable")
                try:
                    await cdp.call("DOM.scrollIntoViewIfNeeded", {"backendNodeId": backend_id})
                except Exception:
                    pass

                clicked_via_mouse = False
                try:
                    box = await cdp.call("DOM.getBoxModel", {"backendNodeId": backend_id})
                    quad = (box.get("model") or {}).get("content") or []
                    if len(quad) >= 8:
                        cx = (quad[0] + quad[2] + quad[4] + quad[6]) / 4.0
                        cy = (quad[1] + quad[3] + quad[5] + quad[7]) / 4.0
                        await cdp.call(
                            "Input.dispatchMouseEvent",
                            {
                                "type": "mousePressed",
                                "x": cx,
                                "y": cy,
                                "button": "left",
                                "clickCount": 1,
                            },
                        )
                        await cdp.call(
                            "Input.dispatchMouseEvent",
                            {
                                "type": "mouseReleased",
                                "x": cx,
                                "y": cy,
                                "button": "left",
                                "clickCount": 1,
                            },
                        )
                        clicked_via_mouse = True
                except Exception:
                    clicked_via_mouse = False

                if not clicked_via_mouse:
                    resolved = await cdp.call("DOM.resolveNode", {"backendNodeId": backend_id})
                    obj_id = (resolved.get("object") or {}).get("objectId")
                    if not obj_id:
                        return {
                            "ok": False,
                            "error": f"Impossible de resoudre l'element {args.ref}.",
                        }
                    await cdp.call(
                        "Runtime.callFunctionOn",
                        {
                            "objectId": obj_id,
                            "functionDeclaration": "function() { this.scrollIntoView({block:'center'}); this.click(); }",
                        },
                    )
                navigated_soon = await self._safe_wait_dom_stable(cdp, timeout=2.5, quiet=0.2)
        return {
            "ok": True,
            "tab_id": tid,
            "clicked_ref": self._clean_ref(args.ref),
            "method": "mouse" if clicked_via_mouse else "dom_click",
            "dom_settled": navigated_soon,
            "note": "Relis avec browser_snapshot : les references changent de generation.",
        }

    async def browser_fill(self, args: BrowserFillArgs) -> dict[str, Any]:
        tab = await self._resolve_tab(args.tab_id)
        tid = str(tab["id"])
        blocked = self._check_tab_domain(str(tab.get("url", "")))
        if blocked:
            return {"ok": False, "error": blocked}

        async with self._tab_lock(tid):
            backend_id = self._lookup_backend_id(tid, args.ref)
            async with _CDPSession(tab["webSocketDebuggerUrl"], self.timeout_seconds) as cdp:
                await cdp.call("DOM.enable")
                try:
                    await cdp.call("DOM.focus", {"backendNodeId": backend_id})
                except Exception:
                    pass
                resolved = await cdp.call("DOM.resolveNode", {"backendNodeId": backend_id})
                obj_id = (resolved.get("object") or {}).get("objectId")
                set_directly = False
                if obj_id:
                    res = await cdp.call(
                        "Runtime.callFunctionOn",
                        {
                            "objectId": obj_id,
                            "arguments": [{"value": args.value}, {"value": bool(args.clear_first)}],
                            "returnByValue": True,
                            "functionDeclaration": (
                                "function(val, clearFirst) { "
                                "const win = (this.ownerDocument && this.ownerDocument.defaultView) || window; "
                                "const tmce = win.tinymce || (win.parent && win.parent.tinymce) || null; "
                                "const edId = (this.dataset && this.dataset.id) || (this.id ? this.id.replace(/_ifr$/, '') : ''); "
                                "if (tmce && typeof tmce.get === 'function' && edId) { "
                                "  const ed = tmce.get(edId); "
                                "  if (ed) { "
                                "    const prev = clearFirst ? '' : (ed.getContent({format: 'text'}) || ''); "
                                "    const nextText = prev ? (prev + '\\n' + val) : val; "
                                "    ed.setContent(nextText); "
                                "    ed.fire('input'); ed.fire('change'); ed.save(); "
                                "    return true; "
                                "  } "
                                "} "
                                "if (this.tagName === 'IFRAME' && this.contentDocument && this.contentDocument.body) { "
                                "  const b = this.contentDocument.body; "
                                "  const bid = (b.dataset && b.dataset.id) || edId; "
                                "  if (tmce && typeof tmce.get === 'function' && bid && tmce.get(bid)) { "
                                "    const ed = tmce.get(bid); "
                                "    const prev = clearFirst ? '' : (ed.getContent({format: 'text'}) || ''); "
                                "    ed.setContent(prev ? (prev + '\\n' + val) : val); "
                                "    ed.fire('input'); ed.fire('change'); ed.save(); "
                                "    return true; "
                                "  } "
                                "  b.focus(); "
                                "  b.innerText = clearFirst ? val : ((b.innerText || '') + val); "
                                "  b.dispatchEvent(new Event('input', {bubbles: true})); "
                                "  b.dispatchEvent(new Event('change', {bubbles: true})); "
                                "  return true; "
                                "} "
                                "if ('value' in this) { "
                                "  const nextVal = clearFirst ? val : ((this.value || '') + val); "
                                "  const proto = Object.getOwnPropertyDescriptor(this.constructor.prototype, 'value'); "
                                "  if (proto && proto.set) proto.set.call(this, nextVal); else this.value = nextVal; "
                                "  this.dispatchEvent(new Event('input', {bubbles: true})); "
                                "  this.dispatchEvent(new Event('change', {bubbles: true})); "
                                "  return true; "
                                "} "
                                "const cmWrap = this.CodeMirror ? this : (this.closest ? this.closest('.CodeMirror') : null); "
                                "if (cmWrap && cmWrap.CodeMirror && typeof cmWrap.CodeMirror.setValue === 'function') { "
                                "  const nextVal = clearFirst ? val : ((cmWrap.CodeMirror.getValue() || '') + val); "
                                "  cmWrap.CodeMirror.setValue(nextVal); "
                                "  return true; "
                                "} "
                                "const ce = this.isContentEditable ? this : (this.closest ? this.closest('[contenteditable=\"true\"]') : null); "
                                "if (ce) { "
                                "  ce.focus(); "
                                "  const nextVal = clearFirst ? val : ((ce.innerText || '') + val); "
                                "  ce.innerText = nextVal; "
                                "  ce.dispatchEvent(new Event('input', {bubbles: true})); "
                                "  ce.dispatchEvent(new Event('change', {bubbles: true})); "
                                "  return true; "
                                "} "
                                "return false; "
                                "}"
                            ),
                        },
                    )
                    set_directly = bool((res.get("result") or {}).get("value"))
                if not set_directly:
                    await cdp.call("Input.insertText", {"text": args.value})
                    if obj_id:
                        await cdp.call(
                            "Runtime.callFunctionOn",
                            {
                                "objectId": obj_id,
                                "functionDeclaration": (
                                    "function() { "
                                    "this.dispatchEvent(new Event('input', {bubbles: true})); "
                                    "this.dispatchEvent(new Event('change', {bubbles: true})); "
                                    "}"
                                ),
                            },
                        )

                # Relecture reelle : renvoyer l'entree en la baptisant "verified" trompait le
                # modele (et masquait les champs qui refusent la valeur).
                actual_value: str | None = None
                if obj_id:
                    try:
                        read_res = await cdp.call(
                            "Runtime.callFunctionOn",
                            {
                                "objectId": obj_id,
                                "returnByValue": True,
                                "functionDeclaration": (
                                    "function() { "
                                    "const win = (this.ownerDocument && this.ownerDocument.defaultView) || window; "
                                    "const tmce = win.tinymce || (win.parent && win.parent.tinymce) || null; "
                                    "const edId = (this.dataset && this.dataset.id) || (this.id ? this.id.replace(/_ifr$/, '') : ''); "
                                    "if (tmce && typeof tmce.get === 'function' && edId && tmce.get(edId)) "
                                    "  return String(tmce.get(edId).getContent({format: 'text'}) || ''); "
                                    "if (this.tagName === 'IFRAME' && this.contentDocument && this.contentDocument.body) "
                                    "  return String(this.contentDocument.body.innerText || ''); "
                                    "if ('value' in this && typeof this.value === 'string') "
                                    "  return this.value; "
                                    "const cmWrap = this.CodeMirror ? this : (this.closest ? this.closest('.CodeMirror') : null); "
                                    "if (cmWrap && cmWrap.CodeMirror && typeof cmWrap.CodeMirror.getValue === 'function') "
                                    "  return String(cmWrap.CodeMirror.getValue() || ''); "
                                    "const ce = this.isContentEditable ? this : "
                                    "  (this.closest ? this.closest('[contenteditable=\"true\"]') : null); "
                                    "if (ce) return String(ce.innerText || ''); "
                                    "return null; "
                                    "}"
                                ),
                            },
                        )
                        got = (read_res.get("result") or {}).get("value")
                        actual_value = got if isinstance(got, str) else None
                    except Exception:
                        actual_value = None

        if actual_value is None:
            verified: bool | None = None
        elif args.clear_first:
            verified = actual_value == args.value
        else:
            verified = actual_value.endswith(args.value)
        return {
            "ok": True,
            "tab_id": tid,
            "filled_ref": self._clean_ref(args.ref),
            "length": len(args.value),
            "verified": verified,
            "actual_value": (actual_value or "")[:200],
            "note": (
                "Valeur relue et conforme : ne le remplis pas a nouveau, passe au champ suivant."
                if verified
                else "Valeur non relue ou differente : verifie le champ avec browser_snapshot avant de soumettre."
            ),
        }

    async def browser_select(self, args: BrowserSelectArgs) -> dict[str, Any]:
        tab = await self._resolve_tab(args.tab_id)
        tid = str(tab["id"])
        blocked = self._check_tab_domain(str(tab.get("url", "")))
        if blocked:
            return {"ok": False, "error": blocked}

        async with self._tab_lock(tid):
            backend_id = self._lookup_backend_id(tid, args.ref)
            async with _CDPSession(tab["webSocketDebuggerUrl"], self.timeout_seconds) as cdp:
                await cdp.call("DOM.enable")
                resolved = await cdp.call("DOM.resolveNode", {"backendNodeId": backend_id})
                obj_id = (resolved.get("object") or {}).get("objectId")
                if not obj_id:
                    return {"ok": False, "error": f"Element {args.ref} introuvable."}
                res = await cdp.call(
                    "Runtime.callFunctionOn",
                    {
                        "objectId": obj_id,
                        "arguments": [{"value": args.value}],
                        "returnByValue": True,
                        "functionDeclaration": (
                            "function(target) { "
                            "if (!this.options) return {ok: false, error: 'Pas un element <select>'}; "
                            "for (const opt of this.options) { "
                            "  if (opt.value === target || opt.text.trim() === target.trim()) { "
                            "    this.value = opt.value; opt.selected = true; "
                            "    this.dispatchEvent(new Event('input', {bubbles: true})); "
                            "    this.dispatchEvent(new Event('change', {bubbles: true})); "
                            "    return {ok: true, selected: opt.value, label: opt.text.trim()}; "
                            "  } "
                            "} "
                            "return {ok: false, error: 'Option introuvable: ' + target}; "
                            "}"
                        ),
                    },
                )
                val = (res.get("result") or {}).get("value") or {"ok": False}
                return {"tab_id": tid, **val}

    async def browser_press_key(self, args: BrowserPressKeyArgs) -> dict[str, Any]:
        tab = await self._resolve_tab(args.tab_id)
        tid = str(tab["id"])
        blocked = self._check_tab_domain(str(tab.get("url", "")))
        if blocked:
            return {"ok": False, "error": blocked}
        key_map: dict[str, tuple[str, str, int]] = {
            "space": (" ", "Space", 32),
            " ": (" ", "Space", 32),
            "enter": ("Enter", "Enter", 13),
            "tab": ("Tab", "Tab", 9),
            "escape": ("Escape", "Escape", 27),
            "esc": ("Escape", "Escape", 27),
            "backspace": ("Backspace", "Backspace", 8),
            "delete": ("Delete", "Delete", 46),
            "arrowdown": ("ArrowDown", "ArrowDown", 40),
            "arrowup": ("ArrowUp", "ArrowUp", 38),
            "arrowleft": ("ArrowLeft", "ArrowLeft", 37),
            "arrowright": ("ArrowRight", "ArrowRight", 39),
        }
        mapped = key_map.get(args.key.strip().lower())
        if mapped:
            key_val, code_val, vk = mapped
        else:
            key_val, code_val, vk = args.key, args.key, 0

        async with self._tab_lock(tid):
            async with _CDPSession(tab["webSocketDebuggerUrl"], self.timeout_seconds) as cdp:
                base: dict[str, Any] = {"key": key_val, "code": code_val}
                if vk:
                    base["windowsVirtualKeyCode"] = vk
                    base["nativeVirtualKeyCode"] = vk
                await cdp.call("Input.dispatchKeyEvent", {"type": "rawKeyDown", **base})
                if len(key_val) == 1 or key_val == "Enter":
                    txt = "\r" if key_val == "Enter" else key_val
                    await cdp.call("Input.dispatchKeyEvent", {"type": "char", "text": txt, **base})
                await cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", **base})
        return {"ok": True, "tab_id": tid, "key": key_val}

    async def browser_scroll(self, args: BrowserScrollArgs) -> dict[str, Any]:
        tab = await self._resolve_tab(args.tab_id)
        tid = str(tab["id"])
        blocked = self._check_tab_domain(str(tab.get("url", "")))
        if blocked:
            return {"ok": False, "error": blocked, "blocked_by": "domain_policy"}
        async with self._tab_lock(tid):
            async with _CDPSession(tab["webSocketDebuggerUrl"], self.timeout_seconds) as cdp:
                if args.ref:
                    backend_id = self._lookup_backend_id(tid, args.ref)
                    await cdp.call("DOM.enable")
                    await cdp.call("DOM.scrollIntoViewIfNeeded", {"backendNodeId": backend_id})
                    return {"ok": True, "tab_id": tid, "scrolled_to": self._clean_ref(args.ref)}

                js_map = {
                    "down": f"window.scrollBy(0, {args.pixels})",
                    "up": f"window.scrollBy(0, -{args.pixels})",
                    "top": "window.scrollTo(0, 0)",
                    "bottom": "window.scrollTo(0, document.body.scrollHeight)",
                }
                eval_res = await cdp.call(
                    "Runtime.evaluate",
                    {
                        "expression": (
                            f"(() => {{ const before = window.scrollY; {js_map[args.direction]}; "
                            "const after = window.scrollY; "
                            "const maxY = Math.max(0, document.documentElement.scrollHeight - window.innerHeight); "
                            "return {before, after, maxY, at_bottom: after >= maxY - 2}; })()"
                        ),
                        "returnByValue": True,
                    },
                )
                info = (eval_res.get("result") or {}).get("value") or {}
        out: dict[str, Any] = {
            "ok": True,
            "tab_id": tid,
            "direction": args.direction,
            "at_bottom": bool(info.get("at_bottom", False)),
            "moved": info.get("before") != info.get("after"),
        }
        if out["at_bottom"] or not out["moved"]:
            out["note"] = (
                "Bas de page ou limite atteinte : inutile de scroller davantage, browser_snapshot lit déjà tout le DOM."
            )
        return out

    async def browser_drag(self, args: BrowserDragArgs) -> dict[str, Any]:
        tab = await self._resolve_tab(args.tab_id)
        tid = str(tab["id"])
        blocked = self._check_tab_domain(str(tab.get("url", "")))
        if blocked:
            return {"ok": False, "error": blocked}

        async with self._tab_lock(tid):
            src_backend_id = self._lookup_backend_id(tid, args.source_ref)
            dst_backend_id = self._lookup_backend_id(tid, args.target_ref)
            async with _CDPSession(tab["webSocketDebuggerUrl"], self.timeout_seconds) as cdp:
                await cdp.call("DOM.enable")
                src_node = await cdp.call("DOM.resolveNode", {"backendNodeId": src_backend_id})
                dst_node = await cdp.call("DOM.resolveNode", {"backendNodeId": dst_backend_id})
                src_oid = (src_node.get("object") or {}).get("objectId")
                dst_oid = (dst_node.get("object") or {}).get("objectId")

                # Moodle ddwtos : si la ref pointe vers .dragplaceholder, resoudre vers la vraie .draghome
                if src_oid:
                    resolved_src = await cdp.call(
                        "Runtime.callFunctionOn",
                        {
                            "objectId": src_oid,
                            "functionDeclaration": (
                                "function() { "
                                "  if (!this.classList || !this.classList.contains('dragplaceholder')) return this; "
                                "  const form = this.closest('.formulation') || document; "
                                "  const choiceCls = Array.from(this.classList).find(c => /^choice\\d+$/.test(c)); "
                                "  if (!choiceCls) return this; "
                                "  const real = form.querySelector('.draghome.' + choiceCls + ':not(.dragplaceholder)'); "
                                "  return real || this; "
                                "}"
                            ),
                        },
                    )
                    new_oid = (resolved_src.get("result") or {}).get("objectId")
                    if new_oid:
                        src_oid = new_oid

                async def _center_coords(
                    oid: str | None, bid: int, *, scroll: bool = True
                ) -> tuple[float, float] | None:
                    if oid:
                        res = await cdp.call(
                            "Runtime.callFunctionOn",
                            {
                                "objectId": oid,
                                "returnByValue": True,
                                "functionDeclaration": (
                                    "function(doScroll) { "
                                    "  if (doScroll) this.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'}); "
                                    "  const r = this.getBoundingClientRect(); "
                                    "  if (!r || (r.width === 0 && r.height === 0)) return null; "
                                    "  return {x: r.left + r.width / 2, y: r.top + r.height / 2, "
                                    "    inView: r.top >= 0 && r.bottom <= window.innerHeight && r.left >= 0 && r.right <= window.innerWidth}; "
                                    "}"
                                ),
                                "arguments": [{"value": scroll}],
                            },
                        )
                        val = (res.get("result") or {}).get("value")
                        if isinstance(val, dict) and "x" in val and "y" in val:
                            return float(val["x"]), float(val["y"])
                    try:
                        if scroll:
                            await cdp.call("DOM.scrollIntoViewIfNeeded", {"backendNodeId": bid})
                        box = await cdp.call("DOM.getBoxModel", {"backendNodeId": bid})
                        q = (box.get("model") or {}).get("content") or []
                        if len(q) >= 8:
                            return (q[0] + q[2] + q[4] + q[6]) / 4.0, (
                                q[1] + q[3] + q[5] + q[7]
                            ) / 4.0
                    except Exception:
                        pass
                    return None

                # Si source et cible tiennent dans le meme viewport, les cadrer ensemble avant mousePressed
                framed_together = False
                if src_oid and dst_oid:
                    fr = await cdp.call(
                        "Runtime.callFunctionOn",
                        {
                            "objectId": src_oid,
                            "arguments": [{"objectId": dst_oid}],
                            "returnByValue": True,
                            "functionDeclaration": (
                                "function(target) { "
                                "  const sr = this.getBoundingClientRect(); "
                                "  const tr = target.getBoundingClientRect(); "
                                "  const top = Math.min(sr.top, tr.top) + window.scrollY; "
                                "  const bottom = Math.max(sr.bottom, tr.bottom) + window.scrollY; "
                                "  if ((bottom - top) <= window.innerHeight - 40) { "
                                "    window.scrollTo({top: Math.max(0, (top + bottom - window.innerHeight) / 2), behavior: 'instant'}); "
                                "    return true; "
                                "  } "
                                "  return false; "
                                "}"
                            ),
                        },
                    )
                    framed_together = bool((fr.get("result") or {}).get("value"))

                dragged_via_mouse = False
                src_xy = await _center_coords(src_oid, src_backend_id, scroll=not framed_together)
                if src_xy is not None:
                    sx, sy = src_xy
                    await cdp.call(
                        "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": sx, "y": sy}
                    )
                    await cdp.call(
                        "Input.dispatchMouseEvent",
                        {
                            "type": "mousePressed",
                            "x": sx,
                            "y": sy,
                            "button": "left",
                            "buttons": 1,
                            "clickCount": 1,
                        },
                    )
                    await asyncio.sleep(0.05)
                    await cdp.call(
                        "Input.dispatchMouseEvent",
                        {
                            "type": "mouseMoved",
                            "x": sx + 4.0,
                            "y": sy + 4.0,
                            "button": "left",
                            "buttons": 1,
                        },
                    )
                    await asyncio.sleep(0.03)

                    # Ne scroller vers la cible que si elle est hors viewport
                    dst_xy = await _center_coords(dst_oid, dst_backend_id, scroll=False)
                    if dst_oid:
                        in_view_res = await cdp.call(
                            "Runtime.callFunctionOn",
                            {
                                "objectId": dst_oid,
                                "returnByValue": True,
                                "functionDeclaration": (
                                    "function() { const r = this.getBoundingClientRect(); "
                                    "return r.top >= 0 && r.bottom <= window.innerHeight; }"
                                ),
                            },
                        )
                        if not (in_view_res.get("result") or {}).get("value"):
                            dst_xy = await _center_coords(dst_oid, dst_backend_id, scroll=True)

                    if dst_xy is not None:
                        tx, ty = dst_xy
                        for step in range(1, args.steps + 1):
                            ratio = step / float(args.steps)
                            mx = sx + (tx - sx) * ratio
                            my = sy + (ty - sy) * ratio
                            await cdp.call(
                                "Input.dispatchMouseEvent",
                                {
                                    "type": "mouseMoved",
                                    "x": mx,
                                    "y": my,
                                    "button": "left",
                                    "buttons": 1,
                                },
                            )
                            await asyncio.sleep(0.02)
                        await asyncio.sleep(0.05)
                        await cdp.call(
                            "Input.dispatchMouseEvent",
                            {
                                "type": "mouseReleased",
                                "x": tx,
                                "y": ty,
                                "button": "left",
                                "buttons": 0,
                                "clickCount": 1,
                            },
                        )
                        dragged_via_mouse = True
                    else:
                        await cdp.call(
                            "Input.dispatchMouseEvent",
                            {
                                "type": "mouseReleased",
                                "x": sx,
                                "y": sy,
                                "button": "left",
                                "buttons": 0,
                                "clickCount": 1,
                            },
                        )

                # Complement HTML5 DragEvent uniquement si la souris n'a pas pu deposer
                if src_oid and dst_oid and not dragged_via_mouse:
                    await cdp.call(
                        "Runtime.callFunctionOn",
                        {
                            "objectId": src_oid,
                            "arguments": [{"objectId": dst_oid}],
                            "functionDeclaration": (
                                "function(target) { "
                                "try { "
                                "  const dt = new DataTransfer(); "
                                "  this.dispatchEvent(new DragEvent('dragstart', {bubbles: true, cancelable: true, dataTransfer: dt})); "
                                "  target.dispatchEvent(new DragEvent('dragenter', {bubbles: true, cancelable: true, dataTransfer: dt})); "
                                "  target.dispatchEvent(new DragEvent('dragover', {bubbles: true, cancelable: true, dataTransfer: dt})); "
                                "  target.dispatchEvent(new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: dt})); "
                                "  this.dispatchEvent(new DragEvent('dragend', {bubbles: true, cancelable: true, dataTransfer: dt})); "
                                "} catch (e) {} "
                                "}"
                            ),
                        },
                    )

                # Verification Moodle ddwtos (placeinput mis a jour ?) + repli clavier accessible Moodle
                moodle_value: str | None = None
                moodle_method_used = False
                if dst_oid and src_oid:
                    meta_res = await cdp.call(
                        "Runtime.callFunctionOn",
                        {
                            "objectId": dst_oid,
                            "arguments": [{"objectId": src_oid}],
                            "returnByValue": True,
                            "functionDeclaration": (
                                "function(src) { "
                                "  const form = this.closest('.formulation'); "
                                "  const placeCls = Array.from(this.classList || []).find(c => /^place\\d+$/.test(c)); "
                                "  const choiceCls = Array.from(src.classList || []).find(c => /^choice(\\d+)$/.test(c)); "
                                "  if (!form || !placeCls) return null; "
                                "  const inp = form.querySelector('input.placeinput.' + placeCls); "
                                "  if (!inp) return null; "
                                "  return { "
                                "    current: String(inp.value || ''), "
                                "    wanted: choiceCls ? choiceCls.replace('choice', '') : '' "
                                "  }; "
                                "}"
                            ),
                        },
                    )
                    meta = (meta_res.get("result") or {}).get("value")
                    if isinstance(meta, dict):
                        moodle_value = str(meta.get("current") or "")
                        wanted = str(meta.get("wanted") or "")
                        if wanted and moodle_value != wanted:
                            # Repli officiel accessibilite Moodle ddwtos : focus sur .drop + Space/ArrowDown jusqu'a wanted
                            await cdp.call(
                                "Runtime.callFunctionOn",
                                {
                                    "objectId": dst_oid,
                                    "functionDeclaration": "function() { this.focus(); }",
                                },
                            )
                            for key_name, key_code, vk in (
                                (" ", "Space", 32),
                                ("ArrowDown", "ArrowDown", 40),
                            ):
                                if moodle_value == wanted:
                                    break
                                for _ in range(16):
                                    await cdp.call(
                                        "Input.dispatchKeyEvent",
                                        {
                                            "type": "keyDown",
                                            "key": key_name,
                                            "code": key_code,
                                            "windowsVirtualKeyCode": vk,
                                        },
                                    )
                                    await cdp.call(
                                        "Input.dispatchKeyEvent",
                                        {
                                            "type": "keyUp",
                                            "key": key_name,
                                            "code": key_code,
                                            "windowsVirtualKeyCode": vk,
                                        },
                                    )
                                    await asyncio.sleep(0.03)
                                    cur_res = await cdp.call(
                                        "Runtime.callFunctionOn",
                                        {
                                            "objectId": dst_oid,
                                            "returnByValue": True,
                                            "functionDeclaration": (
                                                "function() { "
                                                "  const form = this.closest('.formulation'); "
                                                "  const placeCls = Array.from(this.classList || []).find(c => /^place\\d+$/.test(c)); "
                                                "  const inp = form && placeCls ? form.querySelector('input.placeinput.' + placeCls) : null; "
                                                "  return inp ? String(inp.value || '') : ''; "
                                                "}"
                                            ),
                                        },
                                    )
                                    new_val = str((cur_res.get("result") or {}).get("value") or "")
                                    changed = new_val != moodle_value
                                    moodle_value = new_val
                                    if moodle_value == wanted:
                                        moodle_method_used = True
                                        break
                                    if not changed:
                                        break

                await asyncio.sleep(0.2)
        out: dict[str, Any] = {
            "ok": True,
            "tab_id": tid,
            "source_ref": self._clean_ref(args.source_ref),
            "target_ref": self._clean_ref(args.target_ref),
            "method": (
                "moodle_keyboard"
                if moodle_method_used
                else ("mouse" if dragged_via_mouse else "html5")
            ),
        }
        if moodle_value is not None:
            out["moodle_place_value"] = moodle_value
            out["moodle_placed"] = bool(moodle_value and moodle_value != "0")
        return out

    async def browser_handle_dialog(self, args: BrowserHandleDialogArgs) -> dict[str, Any]:
        tab = await self._resolve_tab(args.tab_id)
        tid = str(tab["id"])
        blocked = self._check_tab_domain(str(tab.get("url", "")))
        if blocked:
            return {"ok": False, "error": blocked}
        async with self._tab_lock(tid):
            async with _CDPSession(
                tab["webSocketDebuggerUrl"],
                self.timeout_seconds,
                auto_dismiss_beforeunload=False,
            ) as cdp:
                await cdp.call("Page.enable")
                params: dict[str, Any] = {"accept": bool(args.accept)}
                if args.prompt_text is not None:
                    params["promptText"] = args.prompt_text
                await cdp.call("Page.handleJavaScriptDialog", params)
        return {"ok": True, "tab_id": tid, "accepted": args.accept}

    @staticmethod
    def _same_origin(url_a: str, url_b: str) -> bool:
        pa, pb = urlparse(url_a), urlparse(url_b)
        if not pa.scheme or not pa.hostname or not pb.scheme or not pb.hostname:
            return False
        port_a = pa.port or (443 if pa.scheme.lower() == "https" else 80)
        port_b = pb.port or (443 if pb.scheme.lower() == "https" else 80)
        return (
            pa.scheme.lower() == pb.scheme.lower()
            and pa.hostname.lower() == pb.hostname.lower()
            and port_a == port_b
        )

    async def browser_analyze_media(self, args: BrowserAnalyzeMediaArgs) -> dict[str, Any]:
        tab = await self._resolve_tab(args.tab_id)
        tid = str(tab["id"])
        tab_url = str(tab.get("url", ""))
        blocked = self._check_tab_domain(tab_url)
        if blocked:
            return {"ok": False, "error": blocked}

        js_inspect = f"""
        (async () => {{
          const els = Array.from(document.querySelectorAll('video, audio'));
          const idx = {int(args.media_index)};
          const el = els[idx];
          if (!el) return {{ok: false, error: 'Aucun element <video>/<audio> trouve (count=' + els.length + ')'}};
          const tracks = Array.from(el.querySelectorAll('track[kind="subtitles"], track[kind="captions"]'));
          for (const tr of tracks) {{
            if (tr.src) {{
              try {{
                const trackUrl = new URL(tr.src, location.href);
                if (trackUrl.origin !== location.origin) continue;
                const r = await fetch(trackUrl.href, {{credentials: 'include'}});
                if (r.ok) {{
                  const txt = await r.text();
                  if (txt && txt.trim().length > 20) {{
                    return {{ok: true, mode: 'subtitles', url: trackUrl.href, text: txt.slice(0, 24000)}};
                  }}
                }}
              }} catch (e) {{}}
            }}
          }}
          let src = el.currentSrc || el.src || '';
          if (!src) {{
            const s = el.querySelector('source[src]');
            if (s) src = s.src;
          }}
          if (!src) return {{ok: false, error: 'Element media trouve mais aucune URL source.'}};
          if (src.startsWith('blob:')) {{
            const resp = await fetch(src);
            const buf = await resp.arrayBuffer();
            if (buf.byteLength > 40 * 1024 * 1024) {{
              return {{ok: false, error: 'Flux blob trop volumineux (>40 Mo).'}};
            }}
            const bytes = new Uint8Array(buf);
            let bin = '';
            const chunk = 0x8000;
            for (let i = 0; i < bytes.length; i += chunk) {{
              bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
            }}
            return {{ok: true, mode: el.tagName.toLowerCase(), url: src, b64: btoa(bin), mime: resp.headers.get('content-type') || ''}};
          }}
          return {{ok: true, mode: el.tagName.toLowerCase(), url: src, ua: navigator.userAgent}};
        }})()
        """

        async with self._tab_lock(tid):
            async with _CDPSession(
                tab["webSocketDebuggerUrl"], max(self.timeout_seconds, 20.0)
            ) as cdp:
                eval_res = await cdp.call(
                    "Runtime.evaluate",
                    {"expression": js_inspect, "returnByValue": True, "awaitPromise": True},
                )
                info = ((eval_res.get("result") or {}).get("value")) or {}
                if not info.get("ok"):
                    return {
                        "ok": False,
                        "tab_id": tid,
                        "error": info.get("error") or "Echec inspection media.",
                    }

                if info.get("mode") == "subtitles":
                    return {
                        "ok": True,
                        "tab_id": tid,
                        "mode": "subtitles",
                        "analysis": info["text"],
                    }

                media_url = str(info.get("url") or "").strip()
                cookies_res: dict[str, Any] = {}
                if not info.get("b64"):
                    # Verifie l'URL media avant toute lecture de cookies ou requete reseau
                    scheme_err = self._validate_http_url(media_url)
                    if scheme_err:
                        return {"ok": False, "tab_id": tid, "error": scheme_err}
                    blocked_media = self._check_tab_domain(media_url)
                    if blocked_media:
                        return {
                            "ok": False,
                            "tab_id": tid,
                            "error": blocked_media,
                            "blocked_by": "domain_policy",
                        }
                    ok_host, host_err = await asyncio.to_thread(
                        validate_http_target_host, media_url, allow_private=True
                    )
                    if not ok_host:
                        return {
                            "ok": False,
                            "tab_id": tid,
                            "error": host_err or "URL media locale interdite.",
                        }
                    # N'attache les cookies CDP que si le media est de la meme origine que l'onglet
                    if self._same_origin(media_url, tab_url):
                        cookies_res = await cdp.call("Network.getCookies", {"urls": [media_url]})

        from jarvis_core.llm.gemini import GeminiProvider

        raw_path: Path | None = None
        audio_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tmp:
                raw_path = Path(tmp.name)

            mime_type = str(info.get("mime") or "")
            if info.get("b64"):
                decoded = base64.b64decode(info["b64"])
                await asyncio.to_thread(raw_path.write_bytes, decoded)
            else:
                cookie_header = "; ".join(
                    f"{c['name']}={c['value']}"
                    for c in (cookies_res.get("cookies") or [])
                    if c.get("name")
                )
                current_url = str(info["url"]).strip()
                async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
                    downloaded = False
                    for _hop in range(6):
                        scheme_err = self._validate_http_url(current_url)
                        if scheme_err:
                            return {"ok": False, "tab_id": tid, "error": scheme_err}
                        blocked_media = self._check_tab_domain(current_url)
                        if blocked_media:
                            return {
                                "ok": False,
                                "tab_id": tid,
                                "error": blocked_media,
                                "blocked_by": "domain_policy",
                            }
                        ok_host, host_err = await asyncio.to_thread(
                            validate_http_target_host, current_url, allow_private=True
                        )
                        if not ok_host:
                            return {
                                "ok": False,
                                "tab_id": tid,
                                "error": host_err or "Redirection media interdite.",
                            }
                        headers = {"User-Agent": str(info.get("ua") or "Mozilla/5.0")}
                        if cookie_header and self._same_origin(current_url, tab_url):
                            headers["Cookie"] = cookie_header
                        async with client.stream("GET", current_url, headers=headers) as resp:
                            if resp.status_code in {301, 302, 303, 307, 308}:
                                loc = resp.headers.get("location")
                                if not loc:
                                    return {
                                        "ok": False,
                                        "tab_id": tid,
                                        "error": "Redirection media sans Location.",
                                    }
                                current_url = urljoin(str(resp.url), loc)
                                continue
                            if resp.status_code >= 400:
                                return {
                                    "ok": False,
                                    "tab_id": tid,
                                    "error": f"HTTP {resp.status_code} lors du telechargement media.",
                                }
                            mime_type = (
                                mime_type
                                or resp.headers.get("content-type", "").split(";")[0].strip()
                            )
                            total = 0
                            fh = raw_path.open("wb")
                            try:
                                async for chunk in resp.aiter_bytes(chunk_size=65536):
                                    total += len(chunk)
                                    if total > 150 * 1024 * 1024:
                                        return {
                                            "ok": False,
                                            "tab_id": tid,
                                            "error": "Media > 150 Mo.",
                                        }
                                    await asyncio.to_thread(fh.write, chunk)
                            finally:
                                fh.close()
                            downloaded = True
                            break
                    if not downloaded:
                        return {
                            "ok": False,
                            "tab_id": tid,
                            "error": "Trop de redirections media (> 5).",
                        }

            target_path = raw_path
            target_mime = mime_type or ("audio/mp3" if info.get("mode") == "audio" else "video/mp4")
            used_mode = info.get("mode", "video")

            ffmpeg_bin = shutil.which("ffmpeg")
            if args.audio_only and ffmpeg_bin and not target_mime.startswith("audio/"):
                audio_path = raw_path.with_suffix(".mp3")
                proc = await asyncio.create_subprocess_exec(
                    ffmpeg_bin,
                    "-y",
                    "-i",
                    str(raw_path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-b:a",
                    "32k",
                    str(audio_path),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    await asyncio.wait_for(proc.wait(), timeout=120.0)
                except (TimeoutError, asyncio.TimeoutError):
                    proc.kill()
                    await proc.wait()
                except asyncio.CancelledError:
                    proc.kill()
                    await proc.wait()
                    raise
                if proc.returncode == 0 and audio_path.exists() and audio_path.stat().st_size > 0:
                    target_path = audio_path
                    target_mime = "audio/mp3"
                    used_mode = "audio_extracted"

            media_model = os.environ.get("JARVIS_MEDIA_MODEL", "gemini-4-carbon").strip()
            try:
                gemini = GeminiProvider()
                analysis, media_usage = await gemini.analyze_media(
                    target_path,
                    prompt=args.question,
                    mime_type=target_mime,
                    model=media_model,
                    with_usage=True,
                )
            except Exception as exc:
                # Sans cle Gemini l'outil doit degrader proprement, pas faire echouer le tour.
                return {
                    "ok": False,
                    "tab_id": tid,
                    "error": (
                        f"Analyse media indisponible ({type(exc).__name__}: {exc}). "
                        "Verifie la cle Gemini ou JARVIS_MEDIA_MODEL."
                    ),
                    "hint": "Reessaie sans audio_only, ou traite le media autrement.",
                }
            return {
                "ok": True,
                "tab_id": tid,
                "mode": used_mode,
                "model": media_model,
                "analysis": analysis,
                # Facture l'appel Gemini dans le budget max_cost_usd du tour.
                TOOL_USAGE_KEY: media_usage.model_dump(),
            }
        finally:
            for p in (raw_path, audio_path):
                if p is not None:
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass

    async def browser_screenshot(self, args: BrowserScreenshotArgs) -> dict[str, Any]:
        """Capture d'ecran ciblee d'un element (ex: canvas, svg, widget) via eN@G ou de la page en repli visuel."""
        tab = await self._resolve_tab(args.tab_id)
        tid = str(tab["id"])
        async with self._tab_lock(tid):
            blocked = self._check_tab_domain(str(tab.get("url", "")))
            if blocked:
                return {"ok": False, "tab_id": tid, "error": blocked, "blocked_by": "domain_policy"}

            async with _CDPSession(tab["webSocketDebuggerUrl"], self.timeout_seconds) as cdp:
                await cdp.call("Page.enable")
                clip: dict[str, Any] | None = None
                if args.ref:
                    backend_id = self._lookup_backend_id(tid, args.ref)
                    try:
                        await cdp.call(
                            "DOM.scrollIntoViewIfNeeded",
                            {"backendNodeId": backend_id},
                            wait_timeout=3.0,
                        )
                    except Exception:
                        pass
                    box = await cdp.call("DOM.getBoxModel", {"backendNodeId": backend_id})
                    model = box.get("model") or {}
                    border = model.get("border") or model.get("content") or []
                    if len(border) >= 8:
                        xs = [border[0], border[2], border[4], border[6]]
                        ys = [border[1], border[3], border[5], border[7]]
                        min_x = max(0.0, float(min(xs)))
                        min_y = max(0.0, float(min(ys)))
                        width = max(1.0, float(max(xs) - min_x))
                        height = max(1.0, float(max(ys) - min_y))
                        clip = {
                            "x": min_x,
                            "y": min_y,
                            "width": width,
                            "height": height,
                            "scale": 1,
                        }

                params: dict[str, Any] = {"format": "png"}
                if clip:
                    params["clip"] = clip
                elif args.full_page:
                    params["captureBeyondViewport"] = True

                shot_res = await cdp.call("Page.captureScreenshot", params, wait_timeout=15.0)
                b64_data = str(shot_res.get("data") or "")
                if not b64_data:
                    return {
                        "ok": False,
                        "tab_id": tid,
                        "error": "Aucune donnee d'image retournee par CDP.",
                    }

                raw_bytes = base64.b64decode(b64_data)
                shots_dir = (Path(tempfile.gettempdir()) / "jarvis_screenshots").resolve()
                shots_dir.mkdir(parents=True, exist_ok=True)
                # Confinement strict : un outil READ_ONLY ne peut jamais ecraser un fichier arbitraire
                if args.save_path:
                    candidate = Path(args.save_path).expanduser()
                    dest = (
                        candidate.resolve()
                        if candidate.is_absolute()
                        else (shots_dir / candidate).resolve()
                    )
                    if not dest.is_relative_to(shots_dir):
                        return {
                            "ok": False,
                            "tab_id": tid,
                            "error": f"Chemin save_path hors dossier autorise ({shots_dir}).",
                        }
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(dest.write_bytes, raw_bytes)
                    saved_path = str(dest)
                else:

                    def _write_temp_shot() -> str:
                        existing = sorted(
                            shots_dir.glob("jarvis_browser_shot_*.png"),
                            key=lambda p: p.stat().st_mtime,
                        )
                        for stale in existing[:-50]:
                            stale.unlink(missing_ok=True)
                        with tempfile.NamedTemporaryFile(
                            dir=shots_dir,
                            prefix="jarvis_browser_shot_",
                            suffix=".png",
                            delete=False,
                        ) as tmp:
                            tmp.write(raw_bytes)
                            return tmp.name

                    saved_path = await asyncio.to_thread(_write_temp_shot)

                return {
                    "ok": True,
                    "tab_id": tid,
                    "ref": self._clean_ref(args.ref) if args.ref else None,
                    "full_page": bool(args.full_page and not args.ref),
                    "path": saved_path,
                    "bytes_count": len(raw_bytes),
                    "format": "png",
                }

    def register_all(self, registry: Any) -> None:
        tools: list[tuple[str, str, type[BaseModel], Any, RiskLevel, bool, bool]] = [
            (
                "browser_status",
                "Verifie si Chrome/Chromium est joignable via CDP (sans screenshot).",
                BrowserStatusArgs,
                self.browser_status,
                RiskLevel.READ_ONLY,
                False,
                False,
            ),
            (
                "browser_launch",
                "Lance Chromium avec debogage CDP actif sur le port 9222, ou ouvre/active une application web installee sur le PC (raccourci .desktop, ex: app='Apple Music').",
                BrowserLaunchArgs,
                self.browser_launch,
                RiskLevel.REVERSIBLE_WRITE,
                True,
                True,
            ),
            (
                "browser_list_tabs",
                "Liste tous les onglets ouverts dans le navigateur (titre, URL, tab_id).",
                BrowserListTabsArgs,
                self.browser_list_tabs,
                RiskLevel.READ_ONLY,
                True,
                False,
            ),
            (
                "browser_open_tab",
                "Ouvre une URL dans un nouvel onglet du navigateur.",
                BrowserOpenTabArgs,
                self.browser_open_tab,
                RiskLevel.REVERSIBLE_WRITE,
                True,
                True,
            ),
            (
                "browser_navigate",
                "Navigue vers une URL dans l'onglet actif ou specifie.",
                BrowserNavigateArgs,
                self.browser_navigate,
                RiskLevel.REVERSIBLE_WRITE,
                False,
                True,
            ),
            (
                "browser_snapshot",
                "Lit l'arbre d'accessibilite texte de la page avec des references versionnees [e1@1], [e2@1] pour cliquer/remplir sans screenshot.",
                BrowserSnapshotArgs,
                self.browser_snapshot,
                RiskLevel.READ_ONLY,
                True,
                False,
            ),
            (
                "browser_screenshot",
                "Capture d'ecran ciblee d'un element (ex: canvas, svg, widget) via eN@G ou de la page en repli visuel.",
                BrowserScreenshotArgs,
                self.browser_screenshot,
                RiskLevel.READ_ONLY,
                True,
                False,
            ),
            (
                "browser_click",
                "Clique sur un element identifie par sa reference versionnee eN@G issue du dernier browser_snapshot.",
                BrowserClickArgs,
                self.browser_click,
                RiskLevel.SENSITIVE,
                False,
                True,
            ),
            (
                "browser_fill",
                "Saisit du texte dans un champ (eN@G) et relit la valeur pour confirmer qu'elle a bien ete prise.",
                BrowserFillArgs,
                self.browser_fill,
                RiskLevel.SENSITIVE,
                True,
                True,
            ),
            (
                "browser_select",
                "Selectionne une option dans un menu deroulant <select> identifie par eN@G.",
                BrowserSelectArgs,
                self.browser_select,
                RiskLevel.SENSITIVE,
                True,
                True,
            ),
            (
                "browser_upload_file",
                "Depose un ou plusieurs fichiers locaux dans un <input type=file> identifie par eN@G (secrets systeme refuses).",
                BrowserUploadFileArgs,
                self.browser_upload_file,
                RiskLevel.SENSITIVE,
                True,
                False,
            ),
            (
                "browser_history",
                "Revient en arriere ou avance dans l'historique de l'onglet (back/forward).",
                BrowserHistoryArgs,
                self.browser_history,
                RiskLevel.REVERSIBLE_WRITE,
                False,
                True,
            ),
            (
                "browser_close_tab",
                "Ferme un onglet du navigateur (defaut: dernier onglet lu).",
                BrowserCloseTabArgs,
                self.browser_close_tab,
                RiskLevel.REVERSIBLE_WRITE,
                False,
                True,
            ),
            (
                "browser_press_key",
                "Envoie une touche clavier (Enter, Tab, Escape...) dans l'onglet actif.",
                BrowserPressKeyArgs,
                self.browser_press_key,
                RiskLevel.SENSITIVE,
                False,
                True,
            ),
            (
                "browser_scroll",
                "Fait defiler la page (down, up, top, bottom) ou vers une reference [eN].",
                BrowserScrollArgs,
                self.browser_scroll,
                RiskLevel.READ_ONLY,
                False,
                True,
            ),
            (
                "browser_drag",
                "Glisse et depose un element source eN@G sur une zone cible eM@G (souris + DragEvent HTML5).",
                BrowserDragArgs,
                self.browser_drag,
                RiskLevel.SENSITIVE,
                True,
                True,
            ),
            (
                "browser_handle_dialog",
                "Accepte ou annule une boite de dialogue JavaScript native (alert, confirm, prompt).",
                BrowserHandleDialogArgs,
                self.browser_handle_dialog,
                RiskLevel.SENSITIVE,
                False,
                True,
            ),
            (
                "browser_analyze_media",
                "Lit les sous-titres ou telecharge une video/audio (meme intranet authentifie), extrait l'audio pour economiser les tokens et l'analyse avec Gemini (cout compte dans le budget du tour).",
                BrowserAnalyzeMediaArgs,
                self.browser_analyze_media,
                RiskLevel.READ_ONLY,
                True,
                False,
            ),
        ]

        for name, desc, args_model, handler, risk, returns_untrusted, is_ui in tools:
            registry.register(
                RegistryToolSpec(
                    name=name,
                    description=desc,
                    handler=handler,
                    args_model=args_model,
                    risk=risk,
                    returns_untrusted=returns_untrusted,
                    untrusted_source=f"browser:{name}" if returns_untrusted else None,
                    is_interactive_ui=is_ui,
                    pass_model=True,
                )
            )
