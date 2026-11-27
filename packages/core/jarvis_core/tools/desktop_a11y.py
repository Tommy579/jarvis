"""Controle d'applications Linux natives via AT-SPI2 (arbre d'accessibilite sans screenshots)."""

from __future__ import annotations

import threading
from typing import Any

from jarvis_shared.models import RiskLevel
from pydantic import BaseModel, Field

from .registry import ToolSpec as RegistryToolSpec

SENSITIVE_APP_BLACKLIST: tuple[str, ...] = (
    "keepass",
    "keepassxc",
    "1password",
    "bitwarden",
    "protonpass",
    "lastpass",
    "dashlane",
    "kwallet",
    "seahorse",
    "gnome-keyring",
    "ksshaskpass",
    "ssh-askpass",
    "polkit",
    "gnupg",
    "pinentry",
)


def is_sensitive_app(app_name: str, window_title: str = "") -> bool:
    """Retourne True si l'application ou le titre de fenetre correspond a une app sensible."""
    combined = f"{app_name or ''} {window_title or ''}".lower()
    return any(blocked in combined for blocked in SENSITIVE_APP_BLACKLIST)


INTERACTIVE_ATSPI_ROLES: frozenset[str] = frozenset(
    {
        "push button",
        "toggle button",
        "check box",
        "radio button",
        "combo box",
        "text",
        "entry",
        "password text",
        "spin button",
        "slider",
        "menu item",
        "check menu item",
        "radio menu item",
        "page tab",
        "link",
        "list item",
        "switch",
    }
)


def _load_atspi() -> Any | None:
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi  # type: ignore[import-not-found]

        return Atspi
    except Exception:
        return None


def _is_blacklisted_app(app_name: str, win_title: str = "") -> bool:
    combined = f"{app_name} {win_title}".lower()
    return any(b in combined for b in SENSITIVE_APP_BLACKLIST)


class DesktopListWindowsArgs(BaseModel):
    pass


class DesktopSnapshotArgs(BaseModel):
    app_name: str = Field(
        min_length=1, description="Nom de l'application (ex: Konsole, Dolphin, Kate)."
    )
    max_depth: int = Field(default=6, ge=1, le=12)
    max_chars: int = Field(default=10000, ge=500, le=25000)


class DesktopActionArgs(BaseModel):
    ref: str = Field(min_length=1, description="Reference [aN] issue de desktop_snapshot.")
    text: str | None = Field(
        default=None,
        description="Texte a saisir si l'element est un champ editable (sinon action clic/activation).",
    )


class DesktopA11yToolkit:
    """Inspecte et pilote les fenetres Linux compatibles AT-SPI2 sans capture d'ecran."""

    def __init__(self) -> None:
        self._ref_nodes: dict[str, Any] = {}
        self._generation: int = 0
        self._lock = threading.Lock()

    @staticmethod
    def _clean_ref(ref: str) -> str:
        return ref.strip().lstrip("[@").rstrip("]")

    def desktop_list_windows(self, args: DesktopListWindowsArgs) -> dict[str, Any]:
        _ = args
        Atspi = _load_atspi()
        if Atspi is None:
            return {
                "ok": False,
                "error": "AT-SPI2 (gi.repository.Atspi) indisponible sur ce systeme.",
            }
        with self._lock:
            try:
                desktop = Atspi.get_desktop(0)
                windows: list[dict[str, Any]] = []
                count = desktop.get_child_count()
                for i in range(count):
                    app = desktop.get_child_at_index(i)
                    if app is None:
                        continue
                    app_name = str(app.get_name() or "").strip()
                    if not app_name or _is_blacklisted_app(app_name):
                        continue
                    win_titles: list[str] = []
                    for j in range(app.get_child_count()):
                        win = app.get_child_at_index(j)
                        if win is None:
                            continue
                        title = str(win.get_name() or "").strip()
                        if _is_blacklisted_app(app_name, title):
                            continue
                        win_titles.append(title or "(sans titre)")
                    if win_titles:
                        windows.append({"app": app_name, "windows": win_titles})
                return {"ok": True, "apps": windows, "count": len(windows)}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

    def desktop_snapshot(self, args: DesktopSnapshotArgs) -> dict[str, Any]:
        Atspi = _load_atspi()
        if Atspi is None:
            return {"ok": False, "error": "AT-SPI2 (gi.repository.Atspi) indisponible."}
        if _is_blacklisted_app(args.app_name):
            return {
                "ok": False,
                "error": f"Application '{args.app_name}' protegee par la liste noire.",
            }

        with self._lock:
            try:
                desktop = Atspi.get_desktop(0)
                target_app = None
                fallback_app = None
                needle = args.app_name.strip().lower()
                for i in range(desktop.get_child_count()):
                    app = desktop.get_child_at_index(i)
                    if app is None:
                        continue
                    aname = str(app.get_name() or "").strip()
                    low = aname.lower()
                    if low == needle:
                        target_app = app
                        break
                    if fallback_app is None and needle in low:
                        fallback_app = app
                target_app = target_app or fallback_app
                if target_app is None:
                    return {
                        "ok": False,
                        "error": f"Application '{args.app_name}' introuvable via AT-SPI.",
                    }

                app_name = str(target_app.get_name() or args.app_name)
                if _is_blacklisted_app(app_name):
                    return {"ok": False, "error": f"Application '{app_name}' bloquee."}

                self._generation += 1
                gen = self._generation
                self._ref_nodes.clear()
                lines: list[str] = []
                counter = 1
                total_chars = 0
                truncated = False

                def _walk(node: Any, depth: int) -> None:
                    nonlocal counter, total_chars, truncated
                    if node is None or depth > args.max_depth or truncated:
                        return
                    try:
                        role = str(node.get_role_name() or "")
                        name = str(node.get_name() or "").strip()
                    except Exception:
                        return
                    if role == "password text":
                        return
                    # Bloquer toute fenetre/dialogue dont le titre correspond a un gestionnaire de secrets
                    if role in {"frame", "dialog", "window"} and _is_blacklisted_app(
                        app_name, name
                    ):
                        return

                    is_interactive = role in INTERACTIVE_ATSPI_ROLES
                    if is_interactive or (
                        name
                        and role in {"frame", "dialog", "label", "heading", "panel", "page tab"}
                    ):
                        prefix = ""
                        if is_interactive:
                            ref = f"a{counter}@{gen}"
                            counter += 1
                            self._ref_nodes[ref] = node
                            prefix = f"[{ref}] "
                        indent = "  " * min(depth, 6)
                        label = f' "{name[:140]}"' if name else ""
                        line = f"{indent}{prefix}{role}{label}"
                        if total_chars + len(line) + 1 > args.max_chars:
                            truncated = True
                            return
                        lines.append(line)
                        total_chars += len(line) + 1

                    try:
                        n_children = min(node.get_child_count(), 60)
                    except Exception:
                        n_children = 0
                    for idx in range(n_children):
                        _walk(node.get_child_at_index(idx), depth + 1)

                _walk(target_app, 0)
                if truncated:
                    lines.append("... [snapshot tronque]")
                return {
                    "ok": True,
                    "app": app_name,
                    "generation": gen,
                    "refs_count": len(self._ref_nodes),
                    "snapshot": "\n".join(lines) if lines else "(arbre AT-SPI vide)",
                }
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

    def desktop_action(self, args: DesktopActionArgs) -> dict[str, Any]:
        Atspi = _load_atspi()
        if Atspi is None:
            return {"ok": False, "error": "AT-SPI2 indisponible."}
        with self._lock:
            key = self._clean_ref(args.ref)
            if "@" not in key and self._generation > 0:
                key = f"{key}@{self._generation}"
            node = self._ref_nodes.get(key)
            if node is None:
                return {
                    "ok": False,
                    "error": f"Reference '{args.ref}' introuvable ou perimee. Appelez desktop_snapshot d'abord.",
                }
            try:
                role = str(node.get_role_name() or "")
                if role == "password text":
                    return {"ok": False, "error": "Interaction refusee sur un champ mot de passe."}
                if args.text is not None:
                    ed = node.get_editable_text_iface()
                    if ed is None:
                        return {
                            "ok": False,
                            "error": f"L'element [{key}] ({role}) n'est pas editable.",
                        }
                    ok = bool(ed.set_text_contents(args.text))
                    return {"ok": ok, "ref": key, "action": "set_text"}

                act = node.get_action_iface()
                if act is None or act.get_n_actions() <= 0:
                    return {
                        "ok": False,
                        "error": f"Aucune action AT-SPI disponible sur [{key}] ({role}).",
                    }
                ok = bool(act.do_action(0))
                return {"ok": ok, "ref": key, "action": act.get_action_name(0) or "activate"}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

    def register_all(self, registry: Any) -> None:
        tools: list[tuple[str, str, type[BaseModel], Any, RiskLevel, bool, bool]] = [
            (
                "desktop_list_windows",
                "Liste les applications et fenetres Linux ouvertes via AT-SPI2 (sans screenshot).",
                DesktopListWindowsArgs,
                self.desktop_list_windows,
                RiskLevel.READ_ONLY,
                True,
                False,
            ),
            (
                "desktop_snapshot",
                "Lit l'arbre d'accessibilite d'une application desktop Linux avec references [aN@G].",
                DesktopSnapshotArgs,
                self.desktop_snapshot,
                RiskLevel.READ_ONLY,
                True,
                False,
            ),
            (
                "desktop_action",
                "Declenche un clic/action semantique ou saisit du texte sur une reference [aN@G] AT-SPI2.",
                DesktopActionArgs,
                self.desktop_action,
                RiskLevel.SENSITIVE,
                False,
                True,
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
                    platforms={"linux"},
                    returns_untrusted=returns_untrusted,
                    untrusted_source=f"desktop:{name}" if returns_untrusted else None,
                    is_interactive_ui=is_ui,
                    pass_model=True,
                )
            )
