from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from paper_galaxy.zotero.sqlite_fallback import inspect_zotero_sqlite


def test_zotero_sqlite_reader_handles_uri_special_characters(tmp_path: Path) -> None:
    database_path = tmp_path / "Zotero ? #" / "zotero.sqlite"
    database_path.parent.mkdir()
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("CREATE TABLE items (itemID INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()

    before = sorted(path.name for path in database_path.parent.iterdir())
    result = inspect_zotero_sqlite(database_path)

    assert result["valid"] is True
    assert result["has_items_table"] is True
    assert sorted(path.name for path in database_path.parent.iterdir()) == before


def test_zotero_sqlite_reader_never_creates_missing_wal_shm(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    writer = sqlite3.connect(source)
    try:
        assert writer.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute("CREATE TABLE items (itemID INTEGER PRIMARY KEY)")
        writer.commit()
        source_wal = Path(f"{source}-wal")
        assert source_wal.stat().st_size > 0

        snapshot = tmp_path / "snapshot" / "zotero.sqlite"
        snapshot.parent.mkdir()
        shutil.copy2(source, snapshot)
        shutil.copy2(source_wal, Path(f"{snapshot}-wal"))
        names_before = sorted(path.name for path in snapshot.parent.iterdir())

        result = inspect_zotero_sqlite(snapshot)

        assert result["valid"] is False
        assert result["error_code"] == "database_locked"
        assert sorted(path.name for path in snapshot.parent.iterdir()) == names_before
        assert not Path(f"{snapshot}-shm").exists()
    finally:
        writer.close()
