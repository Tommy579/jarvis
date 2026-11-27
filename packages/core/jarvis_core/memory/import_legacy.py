"""Script d'import du profil legacy jarvis_profile.json vers SQLite (facts + wiki Markdown)."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

from jarvis_shared.rest_models import FactCreate

from .schema import MemoryDatabase

logger = logging.getLogger(__name__)

# Bornes appliquees a un profil potentiellement hostile.
MAX_PROFILE_BYTES = 1_048_576
MAX_IMPORTED_FACTS = 500
MAX_KEY_CHARS = 64
MAX_VALUE_CHARS = 2_000


def _clip(value: Any, limit: int = MAX_VALUE_CHARS) -> str:
    return str(value).strip()[:limit]


def _section(profile: dict, key: str) -> dict:
    """Retourne une section du profil uniquement si c'est un objet JSON."""
    value = profile.get(key)
    return value if isinstance(value, dict) else {}


def _extract_profile_facts(profile: dict) -> list[FactCreate]:
    facts: list[FactCreate] = []

    identity = _section(profile, "identity")
    for field, predicate, label in (
        ("name", "se_nomme", "Nom d'utilisateur"),
        ("language", "langue_principale", "Langue principale"),
        ("timezone", "fuseau_horaire", "Fuseau horaire"),
    ):
        raw = identity.get(field)
        if raw and isinstance(raw, (str, int, float)):
            value = _clip(raw, 200)
            facts.append(
                FactCreate(
                    subject="Tom",
                    predicate=predicate,
                    object=value,
                    text=f"{label} : {value}",
                    category="identite",
                    confidence=0.95,
                    source="legacy_profile",
                )
            )

    section_map = {
        "work": "travail",
        "habits": "habitudes",
        "preferences": "preferences",
        "schedule": "rythme",
    }
    for section_key, category in section_map.items():
        for key, value in _section(profile, section_key).items():
            if value is None or value == [] or value == "":
                continue
            if isinstance(value, list):
                rendered = ", ".join(str(v) for v in value[:50] if v)
            elif isinstance(value, dict):
                # Objets imbriques : pas de rendu recursif d'une structure arbitraire.
                continue
            else:
                rendered = str(value)
            rendered = _clip(rendered)
            safe_key = _clip(key, MAX_KEY_CHARS)
            if not rendered or not safe_key:
                continue
            facts.append(
                FactCreate(
                    subject="Tom",
                    predicate=safe_key,
                    object=rendered,
                    text=f"{section_key}.{safe_key} : {rendered}",
                    category=category,
                    confidence=0.85,
                    source="legacy_profile",
                )
            )

    notes = profile.get("notes")
    if not isinstance(notes, list):
        notes = []
    for idx, note in enumerate(notes[:MAX_IMPORTED_FACTS]):
        if isinstance(note, dict) and note.get("text"):
            date_label = _clip(note.get("date", "archive"), 40)
            text = _clip(note["text"])
            facts.append(
                FactCreate(
                    subject="Tom",
                    predicate=f"note_{idx}",
                    object=text[:120],
                    text=f"[{date_label}] {text}",
                    category="notes",
                    confidence=0.8,
                    source="legacy_profile",
                )
            )
    return facts


def _write_private(path: Path, content: str) -> None:
    """Ecrit une page du wiki en 0600 (donnees personnelles), sans suivre de lien."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)


def _as_list(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, list) and value:
        return [_clip(v, 200) for v in value[:50]]
    return default


def _bootstrap_wiki(wiki_dir: Path, profile: dict) -> None:
    wiki_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    identity = _section(profile, "identity")
    work = _section(profile, "work")
    prefs = _section(profile, "preferences")

    name = _clip(identity.get("name") or "Tom", 200)
    lang = _clip(identity.get("language") or "fr", 40)
    tz = _clip(identity.get("timezone") or "Europe/Paris", 64)
    _write_private(
        wiki_dir / "moi.md",
        f"# Profil personnel ({name})\n\n- Langue : {lang}\n- Fuseau horaire : {tz}\n",
    )

    projects = _as_list(work.get("current_projects"), ["JARVIS 2.0"])
    stack = _as_list(work.get("tech_stack"), ["Python", "TypeScript", "React"])
    _write_private(
        wiki_dir / "projets.md",
        f"# Projets en cours\n\n- Projets : {', '.join(projects)}\n- Stack : {', '.join(stack)}\n",
    )

    style = _clip(prefs.get("communication_style") or "concis et pince-sans-rire", 500)
    _write_private(wiki_dir / "preferences.md", f"# Preferences\n\n- Ton : {style}\n")

    _write_private(
        wiki_dir / "rules.md",
        "# Regles pour Jarvis\n\n"
        "- Toujours demander confirmation pour les actions sensibles de niveau 2 et 3.\n"
        "- Respecter les heures calmes entre 23:00 et 08:00 sauf alerte critique de niveau 4.\n",
    )


def import_legacy_profile(
    profile_path: Path, db: MemoryDatabase, wiki_dir: Path | None = None
) -> int:
    """Importe les entrees de jarvis_profile.json dans SQLite sans creer de doublons.

    Le fichier est traite comme non fiable : taille, types, nombre et longueur des
    entrees sont bornes pour qu'un profil hostile ne bloque pas le demarrage.
    """
    if not profile_path.exists():
        if wiki_dir:
            _bootstrap_wiki(wiki_dir, {})
        return 0

    if profile_path.is_symlink() or not profile_path.is_file():
        logger.warning("Profil legacy ignore (pas un fichier ordinaire) : %s", profile_path)
        return 0
    if profile_path.stat().st_size > MAX_PROFILE_BYTES:
        logger.warning("Profil legacy ignore (plus de %d octets).", MAX_PROFILE_BYTES)
        return 0
    try:
        data = json.loads(profile_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        logger.warning("Profil legacy illisible, import ignore : %s", exc)
        return 0
    if not isinstance(data, dict):
        return 0

    existing_texts = {f.text for f in db.list_facts(include_superseded=True, limit=5000)}
    candidates = _extract_profile_facts(data)[:MAX_IMPORTED_FACTS]
    inserted = 0
    for fact in candidates:
        if fact.text not in existing_texts:
            db.create_fact(fact)
            existing_texts.add(fact.text)
            inserted += 1

    if wiki_dir:
        _bootstrap_wiki(wiki_dir, data)
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importe jarvis_profile.json vers SQLite JARVIS 2.0."
    )
    parser.add_argument(
        "--profile", default="jarvis_profile.json", help="Chemin du profil JSON legacy."
    )
    parser.add_argument(
        "--db", default="jarvis-data/jarvis.db", help="Chemin de la base SQLite cible."
    )
    parser.add_argument("--wiki", default="jarvis-data/wiki", help="Dossier du wiki Markdown.")
    args = parser.parse_args()

    db = MemoryDatabase(Path(args.db))
    count = import_legacy_profile(Path(args.profile), db, Path(args.wiki))
    print(f"Import legacy termine : {count} faits ajoutes.")


if __name__ == "__main__":
    main()
