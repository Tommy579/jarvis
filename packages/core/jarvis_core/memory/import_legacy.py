"""Script d'import du profil legacy jarvis_profile.json vers SQLite (facts + wiki Markdown)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jarvis_shared.rest_models import FactCreate

from .schema import MemoryDatabase


def _extract_profile_facts(profile: dict) -> list[FactCreate]:
    facts: list[FactCreate] = []

    identity = profile.get("identity", {})
    if identity.get("name"):
        facts.append(
            FactCreate(
                subject="Tom",
                predicate="se_nomme",
                object=str(identity["name"]),
                text=f"Nom d'utilisateur : {identity['name']}",
                category="identite",
                confidence=0.95,
                source="legacy_profile",
            )
        )
    if identity.get("language"):
        facts.append(
            FactCreate(
                subject="Tom",
                predicate="langue_principale",
                object=str(identity["language"]),
                text=f"Langue principale : {identity['language']}",
                category="identite",
                confidence=0.95,
                source="legacy_profile",
            )
        )
    if identity.get("timezone"):
        facts.append(
            FactCreate(
                subject="Tom",
                predicate="fuseau_horaire",
                object=str(identity["timezone"]),
                text=f"Fuseau horaire : {identity['timezone']}",
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
        section_data = profile.get(section_key, {})
        if not isinstance(section_data, dict):
            continue
        for key, value in section_data.items():
            if value is None or value == [] or value == "":
                continue
            if isinstance(value, list):
                rendered = ", ".join(str(v) for v in value if v)
            else:
                rendered = str(value)
            if not rendered:
                continue
            facts.append(
                FactCreate(
                    subject="Tom",
                    predicate=key,
                    object=rendered,
                    text=f"{section_key}.{key} : {rendered}",
                    category=category,
                    confidence=0.85,
                    source="legacy_profile",
                )
            )

    for idx, note in enumerate(profile.get("notes", [])):
        if isinstance(note, dict) and note.get("text"):
            date_label = note.get("date", "archive")
            text = str(note["text"]).strip()
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


def _bootstrap_wiki(wiki_dir: Path, profile: dict) -> None:
    wiki_dir.mkdir(parents=True, exist_ok=True)
    identity = profile.get("identity", {})
    work = profile.get("work", {})
    prefs = profile.get("preferences", {})

    moi_file = wiki_dir / "moi.md"
    if not moi_file.exists():
        name = identity.get("name") or "Tom"
        lang = identity.get("language") or "fr"
        tz = identity.get("timezone") or "Europe/Paris"
        moi_file.write_text(
            f"# Profil personnel ({name})\n\n- Langue : {lang}\n- Fuseau horaire : {tz}\n",
            encoding="utf-8",
        )

    proj_file = wiki_dir / "projets.md"
    if not proj_file.exists():
        projects = work.get("current_projects") or ["JARVIS 2.0"]
        stack = work.get("tech_stack") or ["Python", "TypeScript", "React"]
        proj_file.write_text(
            "# Projets en cours\n\n"
            f"- Projets : {', '.join(map(str, projects))}\n"
            f"- Stack : {', '.join(map(str, stack))}\n",
            encoding="utf-8",
        )

    pref_file = wiki_dir / "preferences.md"
    if not pref_file.exists():
        style = prefs.get("communication_style") or "concis et pince-sans-rire"
        pref_file.write_text(
            f"# Preferences\n\n- Ton : {style}\n",
            encoding="utf-8",
        )

    rules_file = wiki_dir / "regles.md"
    if not rules_file.exists():
        rules_file.write_text(
            "# Regles pour Jarvis\n\n"
            "- Toujours demander confirmation pour les actions sensibles de niveau 2 et 3.\n"
            "- Respecter les heures calmes entre 23:00 et 08:00 sauf alerte critique de niveau 4.\n",
            encoding="utf-8",
        )


def import_legacy_profile(
    profile_path: Path, db: MemoryDatabase, wiki_dir: Path | None = None
) -> int:
    """Importe les entrees de jarvis_profile.json dans SQLite sans creer de doublons."""
    if not profile_path.exists():
        if wiki_dir:
            _bootstrap_wiki(wiki_dir, {})
        return 0

    data = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return 0

    existing_texts = {f.text for f in db.list_facts(include_superseded=True, limit=5000)}
    candidates = _extract_profile_facts(data)
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
