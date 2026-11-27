"""Gestionnaire du wiki Markdown local (~/jarvis-data/wiki/) versionne automatiquement avec git."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_WIKI_PAGES: dict[str, str] = {
    "moi.md": "# Profil de Tom\n\n- Langue : fr\n- Fuseau : Europe/Paris\n",
    "famille.md": "# Entourage et contacts proches\n\n",
    "preferences.md": "# Preferences et gouts\n\n",
    "rules.md": (
        "# Regles procedurales pour Jarvis\n\n"
        "- Ne jamais interrompre entre 23h00 et 08h00 sauf alerte critique (niveau 4) ou panne serveur non acquittee après 20 min.\n"
        "- Toujours demander confirmation avant toute action de niveau 2 ou 3.\n"
        "- Concision universelle : quel que soit le modèle ou le canal, répondre de façon directe et concise en 1 à 3 phrases maximum. Si la réponse complète est trop longue, donner l'essentiel et terminer impérativement par '... [suite]'.\n"
        "- Tâches complexes et sous-agents : pour les tâches complexes, le moteur principal (modèle choisi par l'utilisateur) orchestre et subdivise le travail en sous-agents via delegate_subagent et synthétise leurs résultats. Lorsqu'on délègue à `agy` ou `hermes` (`run_cli_subagent`), formuler un objectif global orienté résultat sans pré-explorer les fichiers ni imposer de micro-étapes ou commandes shell.\n"
        "- Fenêtre Motion / Artifacts Visuels 2D & 3D : utiliser `present_visual_artifact` uniquement avec parcimonie (électronique/hardware Raspberry Pi/Arduino, architecture technique, mécanisme physique ou demande explicite de schéma/animation/3D), en plaçant la narration étape par étape dans `steps[].spoken_text`.\n"
    ),
    "projets/jarvis.md": "# Projet JARVIS 2.0 (Jarvis IRL)\n\n- Stack : Python 3.12 (jarvis-core, jarvis-desktop) + React/Three.js (jarvis-web)\n",
}

_LEGACY_SLUG_ALIASES: dict[str, tuple[str, ...]] = {
    "preferences.md": ("préférences.md",),
    "rules.md": ("regles.md",),
    "sante.md": ("santé.md",),
}


class MarkdownWiki:
    """Gere les pages Markdown lisibles par l'humain et versionnees par git en local."""

    def __init__(self, root_dir: Path, auto_git: bool = True):
        self.root_dir = Path(root_dir)
        self.auto_git = auto_git
        self.ensure_initialized()

    def ensure_initialized(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        for rel_path, default_content in DEFAULT_WIKI_PAGES.items():
            full_path = self.root_dir / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            if not full_path.exists():
                full_path.write_text(default_content, encoding="utf-8")

        if self.auto_git and not (self.root_dir / ".git").exists():
            try:
                subprocess.run(
                    ["git", "init"],
                    cwd=self.root_dir,
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
                subprocess.run(
                    ["git", "config", "user.name", "JARVIS Wiki"],
                    cwd=self.root_dir,
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
                subprocess.run(
                    ["git", "config", "user.email", "jarvis@local"],
                    cwd=self.root_dir,
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
                self.commit_changes("Initialisation du wiki JARVIS")
            except Exception:
                pass

    def _resolve_safe(self, slug: str) -> Path:
        clean = slug.lstrip("/")
        if not clean.endswith(".md"):
            clean += ".md"
        root_resolved = self.root_dir.resolve()
        target = (root_resolved / clean).resolve()
        if not target.is_relative_to(root_resolved):
            raise ValueError(f"Chemin de wiki invalide : {slug}")
        return target

    def read_page(self, slug: str) -> str:
        target = self._resolve_safe(slug)
        if not target.exists():
            for alias in _LEGACY_SLUG_ALIASES.get(target.name, ()):
                alt = self._resolve_safe(alias)
                if alt.exists():
                    return alt.read_text(encoding="utf-8")
            return ""
        return target.read_text(encoding="utf-8")

    def write_page(self, slug: str, content: str, commit_message: str | None = None) -> None:
        target = self._resolve_safe(slug)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        if commit_message:
            self.commit_changes(commit_message)

    def commit_changes(self, message: str | None = None) -> bool:
        if not self.auto_git:
            return False
        msg = (
            message or f"Consolidation wiki {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"
        )
        try:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=self.root_dir,
                capture_output=True,
                check=False,
                timeout=5,
            )
            res = subprocess.run(
                ["git", "commit", "-m", msg],
                cwd=self.root_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            return res.returncode == 0
        except Exception:
            return False
