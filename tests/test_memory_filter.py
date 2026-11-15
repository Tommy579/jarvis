from __future__ import annotations

from omi_core.memory.engine import MemoryEngine


def test_should_remember_turn_filters_small_prompts() -> None:
    assert MemoryEngine.should_remember_turn("bonjour", "Bonjour !") is False
    assert MemoryEngine.should_remember_turn("ok merci", "De rien.") is False
    assert MemoryEngine.should_remember_turn("quelle heure est-il ?", "Il est 8h.") is False
    assert MemoryEngine.should_remember_turn("bonjour jarvis comment ça va", "Très bien !") is False


def test_should_remember_turn_keeps_substantive_or_memory_prompts() -> None:
    assert MemoryEngine.should_remember_turn("retiens mon code wifi", "Noté.") is True
    assert MemoryEngine.should_remember_turn("je préfère le café noir", "Compris.") is True
    assert (
        MemoryEngine.should_remember_turn(
            "Peux-tu préparer le plan de déploiement du serveur pour demain matin ?",
            "Voici le plan.",
        )
        is True
    )
