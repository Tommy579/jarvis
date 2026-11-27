"""Service d'onboarding interactif (10 modules, reprise, wiki et question du jour)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from jarvis_shared.rest_models import (
    FactCreate,
    OnboardingModuleStatus,
    OnboardingQuestion,
    OnboardingStatus,
)

from ..memory.schema import MemoryDatabase

DEFAULT_MODULES_PATH = Path(__file__).with_name("modules.yaml")


class OnboardingService:
    def __init__(
        self,
        db: MemoryDatabase,
        wiki_dir: Path | None = None,
        modules_path: Path = DEFAULT_MODULES_PATH,
    ) -> None:
        self.db = db
        self.wiki_dir = wiki_dir
        self.modules_path = modules_path
        self._raw_modules = self._load_modules()

    def _load_modules(self) -> list[dict[str, Any]]:
        data = yaml.safe_load(self.modules_path.read_text(encoding="utf-8")) or {}
        return list(data.get("modules", []))

    def _known_fields_index(self) -> dict[str, str]:
        """Collecte les champs deja connus dans les faits et les reponses sauvegardees."""
        known: dict[str, str] = {}
        for fact in self.db.list_facts(limit=1000):
            known[fact.predicate.lower()] = fact.object
        for module_answers in self.db.get_onboarding_answers().values():
            for key, val in module_answers.items():
                if val.strip():
                    known[key.lower()] = val
        # Correspondance avec le profil legacy importe
        legacy_aliases = {
            "se_nomme": "preferred_name",
            "current_projects": "active_projects",
            "communication_style": "tone_preference",
            "music_genres": "music_cinema",
        }
        for fact_pred, field_key in legacy_aliases.items():
            if fact_pred in known and field_key not in known:
                known[field_key] = known[fact_pred]
        return known

    def get_status(self) -> OnboardingStatus:
        known = self._known_fields_index()
        saved_answers = self.db.get_onboarding_answers()
        modules_status: list[OnboardingModuleStatus] = []
        completed_modules = 0
        daily_question: OnboardingQuestion | None = None

        for mod in self._raw_modules:
            mod_id = str(mod["id"])
            mod_saved = saved_answers.get(mod_id, {})
            questions: list[OnboardingQuestion] = []
            answered_count = 0
            skipped_count = 0

            for q in mod.get("questions", []):
                field_key = str(q["field_key"])
                saved_val = mod_saved.get(field_key)
                known_val = saved_val or known.get(field_key.lower())
                already_known = bool(known_val and str(known_val).strip())
                if saved_val and saved_val.strip():
                    answered_count += 1
                elif already_known:
                    skipped_count += 1
                    answered_count += 1

                q_obj = OnboardingQuestion(
                    id=str(q["id"]),
                    prompt=str(q["prompt"]),
                    field_key=field_key,
                    category=str(q["category"]),
                    sensitivity=q.get("sensitivity", "normal"),
                    placeholder=str(q.get("placeholder", "")),
                    already_known=already_known,
                    known_value=str(known_val) if already_known else None,
                )
                questions.append(q_obj)
                if not already_known and daily_question is None:
                    daily_question = q_obj

            total = len(questions)
            done = total > 0 and answered_count >= total
            if done:
                completed_modules += 1
            modules_status.append(
                OnboardingModuleStatus(
                    id=mod_id,
                    title=str(mod["title"]),
                    description=str(mod["description"]),
                    completed=done,
                    answered_count=answered_count,
                    total_count=total,
                    skipped_count=skipped_count,
                    questions=questions,
                )
            )

        return OnboardingStatus(
            completed_modules=completed_modules,
            total_modules=len(modules_status),
            daily_question=daily_question,
            quarterly_check_due=self._is_quarterly_check_due(),
            modules=modules_status,
        )

    def _is_quarterly_check_due(self) -> bool:
        facts = self.db.list_facts(query="controle_trimestriel", limit=1)
        if not facts:
            return False
        try:
            last = datetime.fromisoformat(facts[0].updated_at)
            delta = datetime.now(timezone.utc) - last.astimezone(timezone.utc)
            return delta.days >= 90
        except Exception:
            return False

    def submit_module_answers(self, module_id: str, answers: dict[str, str]) -> OnboardingStatus:
        target = next((m for m in self._raw_modules if m["id"] == module_id), None)
        if not target:
            raise ValueError(f"Module d'onboarding inconnu : {module_id}")

        question_by_key = {str(q["field_key"]): q for q in target.get("questions", [])}
        question_by_id = {str(q["id"]): q for q in target.get("questions", [])}

        for key, raw_val in answers.items():
            val = str(raw_val).strip()
            if not val:
                continue
            q = question_by_key.get(key) or question_by_id.get(key)
            if not q:
                continue
            field_key = str(q["field_key"])
            self.db.save_onboarding_answer(module_id, str(q["id"]), field_key, val)
            sensitivity = str(q.get("sensitivity", "normal"))
            self.db.create_fact(
                FactCreate(
                    subject="Tom",
                    predicate=field_key,
                    object=val,
                    text=f"{target['title']} ({field_key}) : {val}",
                    category=str(q.get("category", module_id)),
                    confidence=0.98,
                    source="onboarding",
                    sensitivity=sensitivity,  # type: ignore[arg-type]
                )
            )
            # Ne jamais copier les donnees tres privees (sante, finances) dans le wiki en clair
            if (
                self.wiki_dir
                and sensitivity not in {"très privé", "tres_prive"}
                and module_id not in {"sante", "finances"}
            ):
                self._append_to_wiki(module_id, str(target["title"]), field_key, val)

        return self.get_status()

    def _append_to_wiki(self, module_id: str, title: str, field_key: str, value: str) -> None:
        if not self.wiki_dir:
            return
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        slug_map = {
            "identite": "moi",
            "entourage": "famille",
            "travail_etudes": "projets",
            "regles_jarvis": "rules",
            "gouts": "preferences",
        }
        slug = slug_map.get(module_id, module_id)
        md_path = self.wiki_dir / f"{slug}.md"
        line = f"- {field_key} : {value}\n"
        if md_path.exists():
            existing = md_path.read_text(encoding="utf-8")
            if line not in existing:
                md_path.write_text(existing.rstrip() + "\n" + line, encoding="utf-8")
        else:
            md_path.write_text(f"# {title}\n\n{line}", encoding="utf-8")
