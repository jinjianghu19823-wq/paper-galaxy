from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest

from paper_galaxy import errors
from paper_galaxy.storage import sqlite as sqlite_storage
from paper_galaxy.storage.locking import (
    PROJECT_LOCK_MARKER,
    PROJECT_LOCK_RELATIVE_PATH,
    acquire_shared_project_locks,
    exclusive_project_maintenance_lock,
)
from paper_galaxy.storage.migrations import initialize_database


def _bootstrap_legacy_database(project_dir: Path) -> Path:
    """Create a current database without going through the project connectors."""

    database_path = sqlite_storage.resolve_database_path(project_dir)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        initialize_database(connection)
        connection.commit()
    finally:
        connection.close()
    return database_path


def test_operational_read_only_holds_shared_lock_without_creating_marker(
    tmp_path: Path,
) -> None:
    _bootstrap_legacy_database(tmp_path)
    lock_path = tmp_path / PROJECT_LOCK_RELATIVE_PATH
    assert not lock_path.exists()

    connection = sqlite_storage.connect_read_only(tmp_path)
    try:
        assert not lock_path.exists()
    finally:
        connection.close()

    assert not lock_path.exists()


def test_maintenance_claims_legacy_project_before_refusing_idle_reader(
    tmp_path: Path,
) -> None:
    _bootstrap_legacy_database(tmp_path)
    lock_path = tmp_path / PROJECT_LOCK_RELATIVE_PATH
    connection = sqlite_storage.connect_read_only(tmp_path)
    assert not lock_path.exists()

    try:
        with pytest.raises(errors.DatabaseLockedError, match="maintenance"):
            with exclusive_project_maintenance_lock(tmp_path):
                pytest.fail("an idle project reader must block maintenance")
        assert lock_path.read_bytes() == PROJECT_LOCK_MARKER
    finally:
        connection.close()

    with exclusive_project_maintenance_lock(tmp_path):
        pass


def test_writer_claims_private_build_owned_project_lock(tmp_path: Path) -> None:
    _bootstrap_legacy_database(tmp_path)
    lock_path = tmp_path / PROJECT_LOCK_RELATIVE_PATH

    connection = sqlite_storage.connect_read_write(tmp_path)
    try:
        assert lock_path.read_bytes() == PROJECT_LOCK_MARKER
        if os.name != "nt":
            assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
        with pytest.raises(errors.DatabaseLockedError):
            with exclusive_project_maintenance_lock(tmp_path):
                pytest.fail("a writer must block maintenance")
    finally:
        connection.close()

    with exclusive_project_maintenance_lock(tmp_path):
        pass


def test_connection_close_releases_shared_project_lock(tmp_path: Path) -> None:
    sqlite_storage.ensure_database_ready(tmp_path)

    first = sqlite_storage.connect_read_only(tmp_path)
    second = sqlite_storage.connect_read_only(tmp_path)
    first.close()
    with pytest.raises(errors.DatabaseLockedError):
        with exclusive_project_maintenance_lock(tmp_path):
            pytest.fail("the second reader still owns a shared lock")

    second.close()
    second.close()  # Lock release and SQLite close are both idempotent.
    with exclusive_project_maintenance_lock(tmp_path):
        pass


def test_maintenance_marker_blocks_new_connection_after_database_replace(
    tmp_path: Path,
) -> None:
    database_path = _bootstrap_legacy_database(tmp_path)
    replacement = tmp_path / "replacement.sqlite3"
    replacement.write_bytes(database_path.read_bytes())

    with exclusive_project_maintenance_lock(tmp_path):
        os.replace(replacement, database_path)
        with pytest.raises(errors.DatabaseLockedError, match="maintenance"):
            sqlite_storage.connect_read_only(tmp_path)

    connection = sqlite_storage.connect_read_only(tmp_path)
    connection.close()


def test_migration_connection_holds_shared_lock_for_its_lifetime(
    tmp_path: Path,
) -> None:
    connection = sqlite_storage.connect_migration(tmp_path)
    try:
        initialize_database(connection)
        with pytest.raises(errors.DatabaseLockedError):
            with exclusive_project_maintenance_lock(tmp_path):
                pytest.fail("migration connection must block maintenance")
    finally:
        connection.close()

    with exclusive_project_maintenance_lock(tmp_path):
        pass


def test_project_lock_symlink_is_refused_without_touching_target(
    tmp_path: Path,
) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symbolic links are not available")
    _bootstrap_legacy_database(tmp_path)
    lock_path = tmp_path / PROJECT_LOCK_RELATIVE_PATH
    sentinel = tmp_path / "user-owned.txt"
    sentinel.write_bytes(b"keep me")
    lock_path.symlink_to(sentinel)

    with pytest.raises(errors.DatabaseLockedError, match="symbolic link"):
        sqlite_storage.connect_read_only(tmp_path)

    assert lock_path.is_symlink()
    assert sentinel.read_bytes() == b"keep me"


def test_metadata_directory_symlink_is_refused_without_following_it(
    tmp_path: Path,
) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symbolic links are not available")
    _bootstrap_legacy_database(tmp_path)
    metadata_dir = tmp_path / ".paper-galaxy"
    redirected = tmp_path / "user-owned-metadata"
    metadata_dir.rename(redirected)
    metadata_dir.symlink_to(redirected, target_is_directory=True)
    database_path = redirected / "paper_galaxy.sqlite3"
    before = database_path.read_bytes()

    with pytest.raises(errors.DatabaseLockedError, match="metadata directory"):
        sqlite_storage.connect_read_only(tmp_path)

    assert metadata_dir.is_symlink()
    assert database_path.read_bytes() == before


def test_unsupported_project_lock_marker_is_not_claimed_or_rewritten(
    tmp_path: Path,
) -> None:
    _bootstrap_legacy_database(tmp_path)
    lock_path = tmp_path / PROJECT_LOCK_RELATIVE_PATH
    unsupported = b"paper-galaxy-project-lock-v999\n"
    lock_path.write_bytes(unsupported)
    if os.name != "nt":
        lock_path.chmod(0o600)

    with pytest.raises(errors.DatabaseLockedError, match="supported Paper Galaxy"):
        sqlite_storage.connect_read_write(tmp_path)

    assert lock_path.read_bytes() == unsupported


def test_maintenance_locks_default_database_when_config_is_corrupt(
    tmp_path: Path,
) -> None:
    database_path = _bootstrap_legacy_database(tmp_path)
    config_path = tmp_path / ".paper-galaxy/project.toml"
    config_path.write_bytes(b"this = [is not valid TOML")
    legacy_lock = acquire_shared_project_locks(
        tmp_path,
        database_path,
        create_marker=False,
    )

    try:
        with pytest.raises(errors.DatabaseLockedError, match="maintenance"):
            with exclusive_project_maintenance_lock(tmp_path):
                pytest.fail("corrupt config must not bypass the legacy DB drain")
    finally:
        legacy_lock.close()

    with exclusive_project_maintenance_lock(tmp_path):
        pass


def test_failed_new_project_maintenance_retains_gate_over_partial_state(
    tmp_path: Path,
) -> None:
    target = tmp_path / "new-project"
    lock_path = target / PROJECT_LOCK_RELATIVE_PATH
    partial = target / ".paper-galaxy/partial.sqlite3"

    with pytest.raises(RuntimeError, match="synthetic maintenance failure"):
        with exclusive_project_maintenance_lock(target):
            partial.write_bytes(b"partial-state-must-remain-gated")
            raise RuntimeError("synthetic maintenance failure")

    assert lock_path.read_bytes() == PROJECT_LOCK_MARKER
    assert partial.read_bytes() == b"partial-state-must-remain-gated"
