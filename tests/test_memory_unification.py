from pathlib import Path

from jarvis_core.memory.schema import MemoryDatabase
from jarvis_core.memory.storage import MemoryStorage


def test_single_writer_on_same_file(tmp_path: Path) -> None:
    db_path = tmp_path / "jarvis.db"
    db = MemoryDatabase(db_path)
    storage = MemoryStorage(db_path, database=db)
    assert storage.get_today_spend_usd() == 0.0
    assert "settings" in db.table_names()
