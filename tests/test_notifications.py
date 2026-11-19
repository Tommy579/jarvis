"""Tests des modèles de notifications, migration 3 et PolicyStore."""

from __future__ import annotations

import tempfile
from pathlib import Path

from jarvis_core.memory.schema import MemoryDatabase
from jarvis_core.notifications.policy import PolicyStore
from jarvis_shared.notifications import NotificationAttempt, NotificationOrder


def test_migration_3_is_idempotent():
    """Vérifie que l'ouverture répétée applique la migration 3 sans erreur."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_migration.db"
        # 1ère initialisation
        db1 = MemoryDatabase(db_path)
        assert db1.schema_version >= 3

        # 2ème initialisation sur le même fichier
        db2 = MemoryDatabase(db_path)
        assert db2.schema_version >= 3

        # Vérification des tables créées
        orders = db2.list_orders()
        assert orders == []
        attempts = db2.list_attempts()
        assert attempts == []


def test_policy_store_lifecycle_and_mirroring():
    """Vérifie les défauts, la persistence et le miroir wiki de PolicyStore."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_policy.db"
        wiki_dir = Path(tmpdir) / "wiki"
        wiki_dir.mkdir(parents=True, exist_ok=True)

        db = MemoryDatabase(db_path)
        store = PolicyStore(db, wiki_dir=wiki_dir)

        # Défauts
        p = store.get()
        assert p.enabled is True
        assert p.allow_calls is True
        assert p.max_calls_per_day == 3

        # Mise à jour
        updated = store.update(allow_calls=False, quiet_start="22:00")
        assert updated.allow_calls is False
        assert updated.quiet_start == "22:00"

        # Miroir wiki
        wiki_file = wiki_dir / "politique-notifications.md"
        assert wiki_file.is_file()
        content = wiki_file.read_text(encoding="utf-8")
        assert "Appels autorisés : Non" in content
        assert "22:00" in content

        # Redémarrage propre
        store2 = PolicyStore(db, wiki_dir=wiki_dir)
        p2 = store2.get()
        assert p2.allow_calls is False
        assert p2.quiet_start == "22:00"


def test_attempts_and_orders_db_methods():
    """Vérifie l'enregistrement et les requêtes sur les tentatives et ordres."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_orders_db.db"
        db = MemoryDatabase(db_path)

        # Ajout d'une notification parente pour la clé étrangère
        alert = db.add_notification(channel="ntfy", urgency=2, message="Test alert")

        # Insertion tentative
        att = NotificationAttempt(
            notification_id=alert.id,
            channel="ntfy",
            target="user_topic",
            status="sent",
            external_id="ext_123",
        )
        db.insert_attempt(att.model_dump())

        attempts = db.list_attempts(notification_id=alert.id)
        assert len(attempts) == 1
        assert attempts[0]["status"] == "sent"
        assert attempts[0]["external_id"] == "ext_123"

        # Mise à jour statut
        db.update_attempt_status(att.id, "delivered")
        attempts_updated = db.list_attempts(notification_id=alert.id)
        assert attempts_updated[0]["status"] == "delivered"

        # Ordres
        order = NotificationOrder(
            source_glob="github:*",
            event_type_glob="build.failed",
            min_urgency=3,
            note="Alerte build GitHub",
        )
        db.upsert_order(order.model_dump())

        orders = db.list_orders(active_only=True)
        assert len(orders) == 1
        assert orders[0]["source_glob"] == "github:*"

        # Révocation
        db.revoke_order(order.id)
        assert db.list_orders(active_only=True) == []
        assert len(db.list_orders(active_only=False)) == 1
