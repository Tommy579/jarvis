"""Gestionnaire de la politique de notification persistee dans SQLite."""

from __future__ import annotations

import logging
from pathlib import Path

from jarvis_shared.notifications import NotificationPolicy
from pydantic import ValidationError

from ..memory.schema import MemoryDatabase

logger = logging.getLogger(__name__)


class PolicyStore:
    """Accès unique, sûr et persistant à la politique de notification."""

    def __init__(self, db: MemoryDatabase, wiki_dir: Path | None = None) -> None:
        self.db = db
        self.wiki_dir = wiki_dir
        self._cache: NotificationPolicy | None = None

    def get(self) -> NotificationPolicy:
        if self._cache is not None:
            return self._cache
        raw = self.db.get_policy_json()
        if raw:
            try:
                self._cache = NotificationPolicy.model_validate_json(raw)
                return self._cache
            except ValidationError:
                logger.warning("Politique de notification illisible, retour aux défauts.")
        self._cache = NotificationPolicy()
        return self._cache

    def update(self, **changes: object) -> NotificationPolicy:
        current = self.get()
        # Filtre les valeurs None pour ne pas écraser les paramètres non mentionnés
        filtered = {k: v for k, v in changes.items() if v is not None}
        updated = current.model_copy(update=filtered)
        self.db.set_policy_json(updated.model_dump_json())
        self._cache = updated
        self._mirror_to_wiki(updated)
        return updated

    def _mirror_to_wiki(self, policy: NotificationPolicy) -> None:
        if not self.wiki_dir or not self.wiki_dir.exists():
            return
        target = self.wiki_dir / "politique-notifications.md"
        content = (
            "# Politique de notification effective pour Jarvis\n\n"
            f"- Statut global : {'Actif' if policy.enabled else 'Désactivé'}\n"
            f"- Fuseau : `{policy.timezone}`\n"
            f"- Heures calmes : `{policy.quiet_start}` à `{policy.quiet_end}` (urgence autorisée la nuit : niveau {policy.quiet_allow_urgency})\n"
            f"- Appels autorisés : {'Oui' if policy.allow_calls else 'Non'}\n"
            f"- Appels en extrême urgence (niveau 4) : {'Oui' if policy.allow_emergency_calls else 'Non'}\n"
            f"- Urgence minimale d'appel : niveau {policy.call_min_urgency}\n"
            f"- PIN vocal requis : {'Oui' if policy.call_require_pin else 'Non'}\n"
            f"- Plafond d'appels : {policy.max_calls_per_day} appels/jour\n"
            f"- Échelle d'escalade : `{' -> '.join(policy.channel_ladder)}`\n"
            f"- Expiration des approbations : {policy.approval_ttl_seconds} secondes\n"
            f"- Pause temporaire (snooze) : `{policy.snooze_until or 'Aucune'}`\n"
        )
        try:
            target.write_text(content, encoding="utf-8")
        except OSError:
            logger.debug("Impossible d'écrire le miroir wiki de la politique.")
