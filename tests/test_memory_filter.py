from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jarvis_core.memory.engine import MemoryEngine
from jarvis_core.memory.storage import MemoryStorage
from jarvis_core.memory.wiki import MarkdownWiki


def test_should_remember_turn_filters_small_prompts() -> None:
    assert MemoryEngine.should_remember_turn("bonjour", "Bonjour !") is False
    assert MemoryEngine.should_remember_turn("ok merci", "De rien.") is False
    assert MemoryEngine.should_remember_turn("quelle heure est-il ?", "Il est 8h.") is False
    assert MemoryEngine.should_remember_turn("bonjour jarvis comment ça va", "Très bien !") is False
    assert (
        MemoryEngine.should_remember_turn(
            "Quelle est la météo aujourd'hui à Paris ?", "Il fait 18 degrés."
        )
        is False
    )
    assert (
        MemoryEngine.should_remember_turn(
            "Est-ce que j'ai reçu des nouveaux mails ce matin ?", "Aucun nouveau mail."
        )
        is False
    )
    assert (
        MemoryEngine.should_remember_turn(
            "Peux-tu lancer ma playlist de concentration sur Spotify ?", "C'est lancé."
        )
        is False
    )


def test_should_remember_turn_keeps_substantive_or_memory_prompts() -> None:
    assert MemoryEngine.should_remember_turn("retiens mon code wifi", "Noté.") is True
    assert MemoryEngine.should_remember_turn("je préfère le café noir", "Compris.") is True
    assert MemoryEngine.should_remember_turn("ma copine s'appelle Léa", "Noté.") is True
    assert (
        MemoryEngine.should_remember_turn(
            "Peux-tu préparer le plan de déploiement du serveur pour demain matin ?",
            "Voici le plan.",
        )
        is True
    )


class MemoryFilterTests(unittest.TestCase):
    def test_should_remember_turn_filters(self) -> None:
        test_should_remember_turn_filters_small_prompts()
        test_should_remember_turn_keeps_substantive_or_memory_prompts()

    def test_apply_extracted_facts_filters_low_confidence_and_ephemeral_predicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = MemoryStorage(Path(tmp) / "jarvis.db")
            wiki = MarkdownWiki(Path(tmp) / "wiki")
            engine = MemoryEngine(storage, wiki)
            payload = {
                "facts": [
                    {
                        "subject": "Tom",
                        "predicate": "a_demandé",
                        "object": "la météo à Paris",
                        "category": "general",
                        "confidence": 0.95,
                    },
                    {
                        "subject": "Tom",
                        "predicate": "aime",
                        "object": "peut-être le jazz",
                        "category": "preferences",
                        "confidence": 0.5,
                    },
                    {
                        "subject": "Tom",
                        "predicate": "préfère",
                        "object": "le café noir sans sucre",
                        "category": "preferences",
                        "confidence": 0.92,
                    },
                ]
            }
            created = engine.apply_extracted_facts_json(json.dumps(payload))
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0].predicate, "préfère")
            self.assertEqual(created[0].object, "le café noir sans sucre")


if __name__ == "__main__":
    unittest.main()
