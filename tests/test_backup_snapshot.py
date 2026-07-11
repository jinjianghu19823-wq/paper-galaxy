"""Focused safety tests for online SQLite backup snapshots."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest

from paper_galaxy.backup.snapshot import (
    create_database_snapshot,
    validate_database_snapshot,
)
from paper_galaxy.errors import DatabaseCorruptError, UnsupportedSchemaError
from paper_galaxy.storage.migrations import CURRENT_SCHEMA_VERSION
from paper_galaxy.storage.sqlite import ensure_database_ready


def _current_database(tmp_path: Path) -> Path:
    return ensure_database_ready(tmp_path / "project")


def test_snapshot_includes_committed_wal_and_is_self_contained(
    tmp_path: Path,
) -> None:
    source = _current_database(tmp_path)
    writer = sqlite3.connect(source)
    try:
        mode = writer.execute("PRAGMA journal_mode = WAL").fetchone()
        assert mode == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute("CREATE TABLE snapshot_probe(value TEXT NOT NULL)")
        writer.execute("INSERT INTO snapshot_probe(value) VALUES ('committed-in-wal')")
        writer.commit()
        assert Path(f"{source}-wal").stat().st_size > 0

        destination = tmp_path / "backups" / "snapshot.sqlite3"
        result = create_database_snapshot(
            source,
            destination,
            expected_schema_version=CURRENT_SCHEMA_VERSION,
            pages_per_step=1,
        )

        assert result == destination
        assert validate_database_snapshot(destination) == CURRENT_SCHEMA_VERSION
        with sqlite3.connect(destination) as snapshot:
            assert snapshot.execute("PRAGMA journal_mode").fetchone() == ("delete",)
            assert snapshot.execute("PRAGMA quick_check").fetchone() == ("ok",)
            assert snapshot.execute("PRAGMA foreign_key_check").fetchall() == []
            assert snapshot.execute("SELECT value FROM snapshot_probe").fetchone() == (
                "committed-in-wal",
            )
        assert not Path(f"{destination}-wal").exists()
        assert not Path(f"{destination}-shm").exists()
        assert not Path(f"{destination}-journal").exists()
        if os.name == "posix":
            assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    finally:
        writer.close()


def test_snapshot_validation_failure_removes_partial_destination(
    tmp_path: Path,
) -> None:
    source = _current_database(tmp_path)
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            INSERT INTO chunks(id, document_id, chunk_index, text, char_count)
            VALUES ('orphan', 'missing-document', 0, 'invalid', 7)
            """
        )
    destination = tmp_path / "failed.sqlite3"

    with pytest.raises(DatabaseCorruptError, match="foreign_key_check"):
        create_database_snapshot(source, destination)

    assert not destination.exists()
    assert not list(tmp_path.glob("failed.sqlite3-*"))


def test_expected_schema_mismatch_removes_partial_destination(
    tmp_path: Path,
) -> None:
    source = _current_database(tmp_path)
    destination = tmp_path / "wrong-version.sqlite3"

    with pytest.raises(UnsupportedSchemaError, match="expected"):
        create_database_snapshot(
            source,
            destination,
            expected_schema_version=CURRENT_SCHEMA_VERSION + 1,
        )

    assert not destination.exists()


@pytest.mark.parametrize("suffix", ["", "-journal", "-wal", "-shm"])
def test_snapshot_refuses_source_and_sidecar_aliases(
    tmp_path: Path,
    suffix: str,
) -> None:
    source = _current_database(tmp_path)
    before = source.read_bytes()

    with pytest.raises(ValueError, match="aliases"):
        create_database_snapshot(source, Path(f"{source}{suffix}"))

    assert source.read_bytes() == before


def test_snapshot_refuses_destination_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    source = _current_database(tmp_path)
    target = tmp_path / "user-data.sqlite3"
    sentinel = b"must-not-be-replaced"
    target.write_bytes(sentinel)
    destination = tmp_path / "snapshot.sqlite3"
    destination.symlink_to(target)

    with pytest.raises(ValueError, match="symbolic"):
        create_database_snapshot(source, destination)

    assert destination.is_symlink()
    assert target.read_bytes() == sentinel


def test_snapshot_refuses_symlinked_destination_parent(tmp_path: Path) -> None:
    source = _current_database(tmp_path)
    real_directory = tmp_path / "user-directory"
    real_directory.mkdir()
    sentinel = real_directory / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    alias = tmp_path / "alias"
    alias.symlink_to(real_directory, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic directory"):
        create_database_snapshot(source, alias / "snapshot.sqlite3")

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (real_directory / "snapshot.sqlite3").exists()


def test_snapshot_refuses_to_replace_existing_file(tmp_path: Path) -> None:
    source = _current_database(tmp_path)
    destination = tmp_path / "existing.sqlite3"
    original = b"existing-user-data"
    destination.write_bytes(original)

    with pytest.raises(FileExistsError, match="existing"):
        create_database_snapshot(source, destination)

    assert destination.read_bytes() == original


def test_snapshot_refuses_existing_destination_sidecar(tmp_path: Path) -> None:
    source = _current_database(tmp_path)
    destination = tmp_path / "snapshot.sqlite3"
    sidecar = Path(f"{destination}-wal")
    original = b"unrelated-user-data"
    sidecar.write_bytes(original)

    with pytest.raises(FileExistsError, match="existing sidecar"):
        create_database_snapshot(source, destination)

    assert not destination.exists()
    assert sidecar.read_bytes() == original


def test_clean_wal_source_does_not_gain_sidecars(tmp_path: Path) -> None:
    source = _current_database(tmp_path)
    connection = sqlite3.connect(source)
    try:
        assert connection.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
        connection.execute("CREATE TABLE clean_wal_probe(value TEXT NOT NULL)")
        connection.execute("INSERT INTO clean_wal_probe VALUES ('preserved')")
        connection.commit()
    finally:
        connection.close()
    assert not Path(f"{source}-wal").exists()
    assert not Path(f"{source}-shm").exists()

    destination = tmp_path / "snapshot.sqlite3"
    create_database_snapshot(source, destination)

    assert not Path(f"{source}-wal").exists()
    assert not Path(f"{source}-shm").exists()
    with sqlite3.connect(destination) as snapshot:
        assert snapshot.execute("SELECT value FROM clean_wal_probe").fetchone() == (
            "preserved",
        )


def test_standalone_validation_is_read_only(tmp_path: Path) -> None:
    source = _current_database(tmp_path)
    destination = tmp_path / "snapshot.sqlite3"
    create_database_snapshot(source, destination)
    before = destination.read_bytes()

    assert (
        validate_database_snapshot(
            destination,
            expected_schema_version=str(CURRENT_SCHEMA_VERSION),
        )
        == CURRENT_SCHEMA_VERSION
    )

    assert destination.read_bytes() == before
    assert not Path(f"{destination}-wal").exists()
    assert not Path(f"{destination}-shm").exists()


def test_invalid_page_batch_does_not_create_destination(tmp_path: Path) -> None:
    source = _current_database(tmp_path)
    destination = tmp_path / "snapshot.sqlite3"

    with pytest.raises(ValueError, match="greater than zero"):
        create_database_snapshot(source, destination, pages_per_step=0)

    assert not destination.exists()
