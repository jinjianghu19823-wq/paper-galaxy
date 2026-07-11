from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path
from typing import Any

import pytest

from paper_galaxy import errors
from paper_galaxy.backup import bundle as backup_bundle
from paper_galaxy.backup import export_project, import_project, inspect_backup
from paper_galaxy.backup import publish as backup_publish
from paper_galaxy.backup import staging as backup_staging
from paper_galaxy.config import load_project_config
from paper_galaxy.indexer import index_corpus
from paper_galaxy.storage.locking import (
    PROJECT_LOCK_MARKER,
    PROJECT_LOCK_RELATIVE_PATH,
    exclusive_project_maintenance_lock,
)
from paper_galaxy.storage.sqlite import (
    connect_read_only,
    connect_read_write,
    ensure_database_ready,
    resolve_database_path,
)
from tests.test_indexer import copy_tiny_corpus


def _write_project_config(project_dir: Path, *, database_path: str) -> Path:
    config_path = project_dir / ".paper-galaxy" / "project.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                'project_name = "Backup test"',
                f'database_path = "{database_path}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _indexed_project(root: Path, *, database_path: str | None = None) -> Path:
    project_dir = root / "project"
    project_dir.mkdir(parents=True)
    if database_path is not None:
        _write_project_config(project_dir, database_path=database_path)
    corpus = copy_tiny_corpus(project_dir)
    index_corpus(corpus, project_dir=project_dir, min_chars=40)
    return project_dir


def _relative_file_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _raise_during_zip_write(
    _archive: zipfile.ZipFile,
    _name: str,
    _data: bytes,
    *args: object,
    **kwargs: object,
) -> None:
    raise OSError("synthetic archive write failure")


def test_active_wal_export_contains_committed_wal_rows(tmp_path: Path) -> None:
    project_dir = _indexed_project(tmp_path / "source")
    database_path = resolve_database_path(project_dir)
    writer = sqlite3.connect(database_path)
    try:
        writer.execute("CREATE TABLE backup_probe(value TEXT NOT NULL)")
        writer.commit()
        journal_mode = writer.execute("PRAGMA journal_mode = WAL").fetchone()
        assert journal_mode is not None and journal_mode[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute("INSERT INTO backup_probe(value) VALUES ('committed-in-wal')")
        writer.commit()
        wal_path = Path(f"{database_path}-wal")
        assert wal_path.stat().st_size > 0

        output_path = tmp_path / "backup.zip"
        export_project(
            project_dir=project_dir,
            output_path=output_path,
            yes=True,
        )

        snapshot_path = tmp_path / "snapshot.sqlite3"
        with zipfile.ZipFile(output_path) as archive:
            snapshot_path.write_bytes(archive.read("database.sqlite3"))
        with sqlite3.connect(snapshot_path) as snapshot:
            row = snapshot.execute("SELECT value FROM backup_probe").fetchone()
        assert row == ("committed-in-wal",)
    finally:
        writer.close()


def test_export_holds_project_read_lock_through_database_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = _indexed_project(tmp_path / "source")
    output_path = tmp_path / "backup.zip"
    original_snapshot = backup_bundle.create_database_snapshot
    maintenance_was_blocked = False

    def snapshot_while_checking_lock(source: Path, destination: Path) -> None:
        nonlocal maintenance_was_blocked
        with pytest.raises(errors.DatabaseLockedError, match="maintenance"):
            with exclusive_project_maintenance_lock(project_dir):
                pytest.fail("backup snapshot must hold the project read lock")
        maintenance_was_blocked = True
        original_snapshot(source, destination)

    monkeypatch.setattr(
        backup_bundle,
        "create_database_snapshot",
        snapshot_while_checking_lock,
    )

    export_project(project_dir=project_dir, output_path=output_path, yes=True)

    assert maintenance_was_blocked is True
    assert output_path.is_file()


def test_failed_export_preserves_existing_destination_and_cleans_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = _indexed_project(tmp_path / "source")
    output_dir = tmp_path / "backups"
    output_dir.mkdir()
    output_path = output_dir / "backup.zip"
    export_project(project_dir=project_dir, output_path=output_path, yes=True)
    original = output_path.read_bytes()
    names_before = {path.name for path in output_dir.iterdir()}
    monkeypatch.setattr(zipfile.ZipFile, "writestr", _raise_during_zip_write)

    with pytest.raises(OSError, match="synthetic archive write failure"):
        export_project(
            project_dir=project_dir,
            output_path=output_path,
            yes=True,
        )

    assert output_path.read_bytes() == original
    assert {path.name for path in output_dir.iterdir()} == names_before


def test_failed_first_export_leaves_no_output_or_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = _indexed_project(tmp_path / "source")
    output_dir = tmp_path / "backups"
    output_dir.mkdir()
    output_path = output_dir / "backup.zip"
    monkeypatch.setattr(zipfile.ZipFile, "writestr", _raise_during_zip_write)

    with pytest.raises(OSError, match="synthetic archive write failure"):
        export_project(
            project_dir=project_dir,
            output_path=output_path,
            yes=True,
        )

    assert not output_path.exists()
    assert list(output_dir.iterdir()) == []


@pytest.mark.parametrize("descendant", [False, True])
def test_export_cannot_claim_project_lock_marker_as_its_output(
    tmp_path: Path,
    descendant: bool,
) -> None:
    project_dir = _indexed_project(tmp_path / "source")
    lock_path = project_dir / PROJECT_LOCK_RELATIVE_PATH
    lock_path.unlink()
    output_path = lock_path / "backup.zip" if descendant else lock_path

    with pytest.raises(ValueError, match=r"(?i)output|project data"):
        export_project(
            project_dir=project_dir,
            output_path=output_path,
            yes=True,
        )

    assert not lock_path.exists()
    connection = connect_read_only(project_dir)
    connection.close()


def test_export_cleans_only_dead_owned_staging_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = _indexed_project(tmp_path / "source")
    output_path = tmp_path / "backup.zip"
    prefix = f".{output_path.name}.paper-galaxy-export-"
    orphan = backup_staging.create_owned_staging(
        parent=tmp_path,
        prefix=prefix,
        operation="backup-export",
        target=output_path,
    )
    (orphan / "private-snapshot.sqlite3").write_bytes(b"sensitive-staging-data")
    unowned = tmp_path / f"{prefix}user-directory"
    unowned.mkdir()
    sentinel = unowned / "keep.txt"
    sentinel.write_bytes(b"never-delete-unowned-staging-lookalike")
    monkeypatch.setattr(backup_staging, "_process_is_alive", lambda _pid: False)

    export_project(project_dir=project_dir, output_path=output_path, yes=True)

    assert not orphan.exists()
    assert sentinel.read_bytes() == b"never-delete-unowned-staging-lookalike"


def test_failed_force_restore_preserves_existing_project_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _indexed_project(tmp_path / "source")
    _write_project_config(
        source,
        database_path=".paper-galaxy/paper_galaxy.sqlite3",
    )
    archive_path = tmp_path / "backup.zip"
    export_project(project_dir=source, output_path=archive_path, yes=True)

    target = _indexed_project(tmp_path / "existing-target")
    config_path = _write_project_config(
        target,
        database_path=".paper-galaxy/paper_galaxy.sqlite3",
    )
    config_path.write_text(
        "\n".join(
            [
                'project_name = "Existing project"',
                'database_path = ".paper-galaxy/paper_galaxy.sqlite3"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    database_path = resolve_database_path(target)
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE keep_me(value TEXT NOT NULL)")
        connection.execute("INSERT INTO keep_me(value) VALUES ('original')")
    index_path = target / ".paper-galaxy" / "vector_indexes" / "keep.index"
    index_path.parent.mkdir(parents=True)
    index_path.write_bytes(b"original-vector-index")
    target_before = _relative_file_bytes(target)
    siblings_before = {path.name for path in tmp_path.iterdir()}
    original_replace = backup_publish.os.replace
    injected = False

    def fail_after_database_publish(source_path: Path, target_path: Path) -> None:
        nonlocal injected
        if not injected and Path(target_path) == config_path:
            injected = True
            raise OSError("synthetic restore config publish failure")
        original_replace(source_path, target_path)

    monkeypatch.setattr(backup_publish.os, "replace", fail_after_database_publish)

    with pytest.raises(OSError, match="synthetic restore config publish failure"):
        import_project(
            backup_path=archive_path,
            project_dir=target,
            force=True,
        )

    assert _relative_file_bytes(target) == target_before
    assert {path.name for path in tmp_path.iterdir()} == siblings_before


def test_force_restore_refuses_live_project_connection_before_publication(
    tmp_path: Path,
) -> None:
    source = _indexed_project(tmp_path / "source")
    archive_path = tmp_path / "backup.zip"
    export_project(project_dir=source, output_path=archive_path, yes=True)
    target = _indexed_project(tmp_path / "target")
    before = _relative_file_bytes(target)
    reader = connect_read_only(target)

    try:
        with pytest.raises(errors.DatabaseLockedError, match="maintenance"):
            import_project(
                backup_path=archive_path,
                project_dir=target,
                force=True,
            )
        assert _relative_file_bytes(target) == before
    finally:
        reader.close()

    import_project(backup_path=archive_path, project_dir=target, force=True)


def test_restore_dry_run_does_not_claim_legacy_project_lock(tmp_path: Path) -> None:
    source = _indexed_project(tmp_path / "source")
    archive_path = tmp_path / "backup.zip"
    export_project(project_dir=source, output_path=archive_path, yes=True)
    target = _indexed_project(tmp_path / "target")
    lock_path = target / PROJECT_LOCK_RELATIVE_PATH
    lock_path.unlink()
    before = _relative_file_bytes(target)

    result = import_project(
        backup_path=archive_path,
        project_dir=target,
        force=True,
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert not lock_path.exists()
    assert _relative_file_bytes(target) == before


def test_new_target_restore_blocks_concurrent_project_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _indexed_project(tmp_path / "source")
    archive_path = tmp_path / "backup.zip"
    export_project(project_dir=source, output_path=archive_path, yes=True)
    target = tmp_path / "new-target"
    original_restore = backup_bundle._restore_validated_project
    initializer_was_blocked = False

    def restore_after_concurrent_attempt(**kwargs: Any) -> None:
        nonlocal initializer_was_blocked
        with pytest.raises(errors.DatabaseLockedError, match="maintenance"):
            ensure_database_ready(target)
        initializer_was_blocked = True
        original_restore(**kwargs)

    monkeypatch.setattr(
        backup_bundle,
        "_restore_validated_project",
        restore_after_concurrent_attempt,
    )

    import_project(backup_path=archive_path, project_dir=target)

    assert initializer_was_blocked is True
    connection = connect_read_only(target)
    connection.close()


def test_failed_new_target_restore_removes_its_lock_skeleton(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _indexed_project(tmp_path / "source")
    archive_path = tmp_path / "backup.zip"
    export_project(project_dir=source, output_path=archive_path, yes=True)
    target = tmp_path / "new-target"

    def fail_before_staging(**_kwargs: Any) -> None:
        raise OSError("synthetic pre-staging restore failure")

    monkeypatch.setattr(
        backup_bundle,
        "_restore_validated_project",
        fail_before_staging,
    )

    with pytest.raises(OSError, match="synthetic pre-staging restore failure"):
        import_project(backup_path=archive_path, project_dir=target)

    assert not target.exists()


def test_custom_relative_database_path_round_trips_inside_restored_project(
    tmp_path: Path,
) -> None:
    configured_path = "state/sql/custom.sqlite3"
    source = _indexed_project(
        tmp_path / "source",
        database_path=configured_path,
    )
    source_database = resolve_database_path(source)
    assert source_database == (source / configured_path).resolve()
    archive_path = tmp_path / "backup.zip"
    export_project(project_dir=source, output_path=archive_path, yes=True)

    target = tmp_path / "restored"
    import_project(backup_path=archive_path, project_dir=target)

    restored_config = load_project_config(target)
    assert restored_config is not None
    assert restored_config.database_path == configured_path
    restored_database = resolve_database_path(target)
    assert restored_database == (target / configured_path).resolve()
    assert not (target / ".paper-galaxy" / "paper_galaxy.sqlite3").exists()
    with sqlite3.connect(restored_database) as connection:
        document_count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()
    assert document_count is not None and document_count[0] > 0


def test_config_only_backup_preserves_safe_custom_database_path(
    tmp_path: Path,
) -> None:
    configured_path = "state/sql/custom.sqlite3"
    source = _indexed_project(
        tmp_path / "source",
        database_path=configured_path,
    )
    archive_path = tmp_path / "config-only.zip"
    exported = export_project(
        project_dir=source,
        output_path=archive_path,
        include_db=False,
    )

    assert exported["manifest"]["contains_database"] is False
    assert exported["manifest"]["configured_database_path"] == configured_path
    target = tmp_path / "restored"
    import_project(backup_path=archive_path, project_dir=target)

    restored_config = load_project_config(target)
    assert restored_config is not None
    assert restored_config.database_path == configured_path
    assert resolve_database_path(target) == (target / configured_path).resolve()
    assert not resolve_database_path(target).exists()


def test_external_database_config_is_rejected_or_safely_mapped_on_restore(
    tmp_path: Path,
) -> None:
    external_database = (tmp_path / "external" / "library.sqlite3").resolve()
    source = _indexed_project(
        tmp_path / "source",
        database_path=str(external_database),
    )
    external_before = external_database.read_bytes()
    archive_path = tmp_path / "backup.zip"

    try:
        export_project(project_dir=source, output_path=archive_path, yes=True)
    except (PermissionError, ValueError) as exc:
        message = str(exc).lower()
        assert "outside" in message or "external" in message or "absolute" in message
        assert external_database.read_bytes() == external_before
        assert not archive_path.exists()
        return

    target = tmp_path / "restored"
    with zipfile.ZipFile(archive_path) as archive:
        portable_config = archive.read("project.toml").decode("utf-8")
    assert str(external_database) not in portable_config
    try:
        import_project(backup_path=archive_path, project_dir=target)
    except (PermissionError, ValueError) as exc:
        message = str(exc).lower()
        assert "outside" in message or "external" in message or "absolute" in message
    else:
        restored_database = resolve_database_path(target)
        restored_database.relative_to(target.resolve())
        assert restored_database.is_file()
    assert external_database.read_bytes() == external_before


def test_duplicate_vector_index_basenames_preserve_logical_paths(
    tmp_path: Path,
) -> None:
    source = _indexed_project(tmp_path / "source")
    first_relative = Path(".paper-galaxy/vector_indexes/model-a/shared.index")
    second_relative = Path(".paper-galaxy/vector_indexes/model-b/shared.index")
    first_path = source / first_relative
    second_path = source / second_relative
    first_path.parent.mkdir(parents=True)
    second_path.parent.mkdir(parents=True)
    first_path.write_bytes(b"model-a-index")
    second_path.write_bytes(b"model-b-index")
    database_path = resolve_database_path(source)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO embedding_models(
              id, name, provider, dimension, distance, config_json, created_at
            ) VALUES ('backup-model', 'synthetic', 'local', 2, 'cosine', '{}', ?)
            """,
            ("2026-07-11T00:00:00+00:00",),
        )
        connection.executemany(
            """
            INSERT INTO vector_indexes(
              id, model_id, object_type, index_path, vector_count,
              created_at, metadata_json
            ) VALUES (?, 'backup-model', 'document', ?, 1, ?, '{}')
            """,
            [
                (
                    "index-a",
                    first_relative.as_posix(),
                    "2026-07-11T00:00:00+00:00",
                ),
                (
                    "index-b",
                    second_relative.as_posix(),
                    "2026-07-11T00:00:00+00:00",
                ),
            ],
        )

    archive_path = tmp_path / "backup.zip"
    export_project(
        project_dir=source,
        output_path=archive_path,
        include_vector_indexes=True,
        yes=True,
    )
    inspection = inspect_backup(archive_path)
    archived_indexes = [
        name for name in inspection["files"] if name.startswith("vector_indexes/")
    ]
    assert len(archived_indexes) == 2
    assert len(set(archived_indexes)) == 2

    target = tmp_path / "restored"
    import_project(backup_path=archive_path, project_dir=target)

    assert (target / first_relative).read_bytes() == b"model-a-index"
    assert (target / second_relative).read_bytes() == b"model-b-index"


def test_non_build_owned_file_is_never_archived_as_a_vector_index(
    tmp_path: Path,
) -> None:
    source = _indexed_project(tmp_path / "source")
    user_file = source / "papers" / "user-notes.txt"
    user_file.parent.mkdir()
    secret = b"PRIVATE-NOTES-MUST-NOT-ENTER-BACKUP-97d7"
    user_file.write_bytes(secret)
    database_path = resolve_database_path(source)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO embedding_models(
              id, name, provider, dimension, distance, config_json, created_at
            ) VALUES ('leak-model', 'synthetic', 'local', 2, 'cosine', '{}', ?)
            """,
            ("2026-07-11T00:00:00+00:00",),
        )
        connection.execute(
            """
            INSERT INTO vector_indexes(
              id, model_id, object_type, index_path, vector_count,
              created_at, metadata_json
            ) VALUES ('leak-index', 'leak-model', 'document', ?, 1, ?, '{}')
            """,
            ("papers/user-notes.txt", "2026-07-11T00:00:00+00:00"),
        )

    archive_path = tmp_path / "backup.zip"
    result = export_project(
        project_dir=source,
        output_path=archive_path,
        include_vector_indexes=True,
        yes=True,
    )

    assert result["manifest"]["vector_indexes_included"] is False
    assert any(
        "non-build-owned" in warning for warning in result["manifest"]["warnings"]
    )
    with zipfile.ZipFile(archive_path) as archive:
        assert not any(
            name.startswith("vector_indexes/") for name in archive.namelist()
        )
        assert all(secret not in archive.read(name) for name in archive.namelist())
        snapshot_path = tmp_path / "snapshot-without-index.sqlite3"
        snapshot_path.write_bytes(archive.read("database.sqlite3"))
    with sqlite3.connect(snapshot_path) as snapshot:
        count = snapshot.execute("SELECT COUNT(*) FROM vector_indexes").fetchone()
    assert count == (0,)


def test_symbolic_vector_index_cannot_leak_an_internal_user_file(
    tmp_path: Path,
) -> None:
    source = _indexed_project(tmp_path / "source")
    user_file = source / "papers" / "private-note.txt"
    user_file.parent.mkdir()
    secret = b"SYMLINKED-PRIVATE-NOTE-MUST-NOT-ENTER-BACKUP"
    user_file.write_bytes(secret)
    index_path = source / ".paper-galaxy" / "vector_indexes" / "leak.index"
    index_path.parent.mkdir(parents=True)
    try:
        index_path.symlink_to(user_file)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")
    database_path = resolve_database_path(source)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO embedding_models(
              id, name, provider, dimension, distance, config_json, created_at
            ) VALUES ('symlink-model', 'synthetic', 'local', 2, 'cosine', '{}', ?)
            """,
            ("2026-07-11T00:00:00+00:00",),
        )
        connection.execute(
            """
            INSERT INTO vector_indexes(
              id, model_id, object_type, index_path, vector_count,
              created_at, metadata_json
            ) VALUES ('symlink-index', 'symlink-model', 'document', ?, 1, ?, '{}')
            """,
            (
                ".paper-galaxy/vector_indexes/leak.index",
                "2026-07-11T00:00:00+00:00",
            ),
        )

    archive_path = tmp_path / "backup.zip"
    result = export_project(
        project_dir=source,
        output_path=archive_path,
        include_vector_indexes=True,
        yes=True,
    )

    assert result["manifest"]["vector_indexes_included"] is False
    with zipfile.ZipFile(archive_path) as archive:
        assert all(secret not in archive.read(name) for name in archive.namelist())


def test_interrupted_restore_journal_recovers_original_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    original_database = target / "state" / "database.sqlite3"
    original_config = target / ".paper-galaxy" / "project.toml"
    original_database.parent.mkdir(parents=True)
    original_config.parent.mkdir(parents=True)
    original_database.write_bytes(b"ORIGINAL-DATABASE")
    original_config.write_bytes(b"ORIGINAL-CONFIG")

    staged = tmp_path / "staged"
    staged_database = staged / "state" / "database.sqlite3"
    staged_config = staged / ".paper-galaxy" / "project.toml"
    staged_database.parent.mkdir(parents=True)
    staged_config.parent.mkdir(parents=True)
    staged_database.write_bytes(b"NEW-DATABASE")
    staged_config.write_bytes(b"NEW-CONFIG")
    relative_files = (
        Path("state/database.sqlite3"),
        Path(".paper-galaxy/project.toml"),
    )
    entries = backup_publish._transaction_entries(
        target,
        staged,
        relative_files,
    )
    transaction = backup_publish._transaction_root(target)
    transaction.mkdir(mode=0o700)
    backup_publish._write_transaction_journal(
        transaction,
        target=target,
        state="prepared",
        entries=entries,
    )
    backup_database = transaction / "originals" / "state" / "database.sqlite3"
    backup_database.parent.mkdir(parents=True)
    backup_publish.os.replace(original_database, backup_database)
    backup_publish.os.replace(staged_database, original_database)

    with pytest.raises(FileExistsError, match=r"(?i)requires recovery"):
        backup_publish.recover_interrupted_project_restore(target, dry_run=True)
    assert original_database.read_bytes() == b"NEW-DATABASE"
    assert original_config.read_bytes() == b"ORIGINAL-CONFIG"

    monkeypatch.setattr(backup_publish, "_process_is_alive", lambda _pid: False)
    assert backup_publish.recover_interrupted_project_restore(target, dry_run=False)

    assert original_database.read_bytes() == b"ORIGINAL-DATABASE"
    assert original_config.read_bytes() == b"ORIGINAL-CONFIG"
    assert not transaction.exists()


def test_new_project_import_recovers_pending_transaction_without_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _indexed_project(tmp_path / "source")
    archive_path = tmp_path / "backup.zip"
    export_project(project_dir=source, output_path=archive_path, yes=True)

    target = tmp_path / "target"
    marker = target / PROJECT_LOCK_RELATIVE_PATH
    marker.parent.mkdir(parents=True)
    marker.write_bytes(PROJECT_LOCK_MARKER)
    marker.chmod(0o600)
    staged = tmp_path / "interrupted-staging"
    staged_database = staged / ".paper-galaxy/paper_galaxy.sqlite3"
    staged_config = staged / ".paper-galaxy/project.toml"
    staged_database.parent.mkdir(parents=True)
    staged_database.write_bytes(resolve_database_path(source).read_bytes())
    staged_config.write_text(
        'project_name = "Interrupted restore"\n'
        'database_path = ".paper-galaxy/paper_galaxy.sqlite3"\n',
        encoding="utf-8",
    )
    relative_files = (
        Path(".paper-galaxy/paper_galaxy.sqlite3"),
        Path(".paper-galaxy/project.toml"),
    )
    entries = backup_publish._transaction_entries(target, staged, relative_files)
    transaction = backup_publish._transaction_root(target)
    transaction.mkdir(mode=0o700)
    backup_publish._write_transaction_journal(
        transaction,
        target=target,
        state="prepared",
        entries=entries,
    )
    target_database = target / relative_files[0]
    backup_publish.os.replace(staged_database, target_database)

    for connect in (connect_read_only, connect_read_write):
        with pytest.raises(
            errors.DatabaseLockedError,
            match=r"(?i)interrupted|recovery",
        ):
            connect(target)
    with pytest.raises(
        errors.DatabaseLockedError,
        match=r"(?i)interrupted|recovery",
    ):
        ensure_database_ready(target)

    monkeypatch.setattr(backup_publish, "_process_is_alive", lambda _pid: False)

    import_project(backup_path=archive_path, project_dir=target)

    assert not transaction.exists()
    connection = connect_read_only(target)
    connection.close()
