from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from paper_galaxy.storage import run_recovery
from paper_galaxy.storage import sqlite as sqlite_storage

NOW = "2026-07-11T00:00:00+00:00"
DEAD_PID = 424_242
PERMISSION_PID = 434_343


def _prepare_project(project_dir: Path) -> Path:
    return sqlite_storage.ensure_database_ready(project_dir)


def _insert_run_fixtures(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO corpora(id, root_path, created_at, updated_at)
            VALUES ('corpus', '/synthetic/corpus', ?, ?)
            """,
            (NOW, NOW),
        )
        connection.executemany(
            """
            INSERT INTO scan_runs(
              id, corpus_id, corpus_path, started_at, status, owner_pid
            ) VALUES (?, 'corpus', '/synthetic/corpus', ?, 'running', ?)
            """,
            (
                ("scan_null", NOW, None),
                ("scan_invalid", NOW, 0),
                ("scan_permission", NOW, PERMISSION_PID),
            ),
        )
        connection.execute(
            """
            INSERT INTO embedding_models(
              id, name, provider, dimension, distance, config_json, created_at
            ) VALUES ('model', 'synthetic', 'test', 2, 'cosine', '{}', ?)
            """,
            (NOW,),
        )
        connection.execute(
            """
            INSERT INTO embedding_runs(
              id, model_id, started_at, status, owner_pid
            ) VALUES ('embedding_dead', 'model', ?, 'running', ?)
            """,
            (NOW, DEAD_PID),
        )
        connection.execute(
            """
            INSERT INTO zotero_sources(
              id, source_type, name, created_at, updated_at
            ) VALUES ('source', 'local_api', 'Synthetic', ?, ?)
            """,
            (NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO zotero_import_runs(
              id, source_id, started_at, status, owner_pid
            ) VALUES ('zotero_current', 'source', ?, 'running', ?)
            """,
            (NOW, os.getpid()),
        )
        connection.commit()
    finally:
        connection.close()


def _run_state(database_path: Path, table_name: str, run_id: str) -> tuple[object, ...]:
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            f"""
            SELECT status, finished_at, error_code, error_message, owner_pid
            FROM {table_name}
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        assert row is not None
        return tuple(row)
    finally:
        connection.close()


def test_writer_readiness_recovers_orphaned_runs_but_preserves_live_owners(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = _prepare_project(tmp_path)
    _insert_run_fixtures(database_path)
    probes: list[int] = []

    def probe(pid: int) -> bool:
        probes.append(pid)
        if pid == DEAD_PID:
            return False
        assert pid in {PERMISSION_PID, os.getpid()}
        return True

    monkeypatch.setattr(run_recovery, "process_is_alive", probe)

    sqlite_storage.ensure_database_ready(tmp_path)

    for table_name, run_id in (
        ("scan_runs", "scan_null"),
        ("scan_runs", "scan_invalid"),
        ("embedding_runs", "embedding_dead"),
    ):
        status, finished_at, error_code, error_message, _owner_pid = _run_state(
            database_path, table_name, run_id
        )
        assert status == "interrupted"
        assert isinstance(finished_at, str) and finished_at
        assert error_code == "ProcessInterrupted"
        assert error_message == (
            "The process that started this run is no longer active."
        )
        assert str(tmp_path) not in error_message

    assert _run_state(database_path, "scan_runs", "scan_permission") == (
        "running",
        None,
        None,
        None,
        PERMISSION_PID,
    )
    assert _run_state(database_path, "zotero_import_runs", "zotero_current") == (
        "running",
        None,
        None,
        None,
        os.getpid(),
    )
    assert probes == [PERMISSION_PID, DEAD_PID, os.getpid()]


def test_read_only_connection_does_not_recover_dead_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = _prepare_project(tmp_path)
    _insert_run_fixtures(database_path)
    before_hash = hashlib.sha256(database_path.read_bytes()).hexdigest()
    before_mtime = database_path.stat().st_mtime_ns

    def forbidden_probe(pid: int) -> bool:
        del pid
        raise AssertionError("read-only access must not inspect or recover run owners")

    monkeypatch.setattr(run_recovery, "process_is_alive", forbidden_probe)

    connection = sqlite_storage.connect_read_only(tmp_path)
    connection.close()

    assert _run_state(database_path, "embedding_runs", "embedding_dead")[0] == (
        "running"
    )
    assert hashlib.sha256(database_path.read_bytes()).hexdigest() == before_hash
    assert database_path.stat().st_mtime_ns == before_mtime


def test_run_recovery_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = _prepare_project(tmp_path)
    _insert_run_fixtures(database_path)

    def probe(pid: int) -> bool:
        return pid != DEAD_PID

    monkeypatch.setattr(run_recovery, "process_is_alive", probe)

    sqlite_storage.ensure_database_ready(tmp_path)
    first = _run_state(database_path, "embedding_runs", "embedding_dead")
    sqlite_storage.ensure_database_ready(tmp_path)
    second = _run_state(database_path, "embedding_runs", "embedding_dead")

    assert first == second
    assert first[0] == "interrupted"


def test_recovery_ignores_missing_tables_and_pre_owner_pid_schemas() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE scan_runs (
              id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              finished_at TEXT,
              error_code TEXT,
              error_message TEXT
            )
            """
        )

        assert run_recovery.recover_interrupted_runs(connection) == 0
    finally:
        connection.close()
