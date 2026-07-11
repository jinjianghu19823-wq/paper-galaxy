"""Recover run audit rows whose owning process exited unexpectedly."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from paper_galaxy.processes import process_is_alive

_RUN_TABLES = (
    "scan_runs",
    "embedding_runs",
    "zotero_import_runs",
)
_REQUIRED_COLUMNS = {
    "id",
    "status",
    "finished_at",
    "owner_pid",
    "error_code",
    "error_message",
}
_INTERRUPTED_CODE = "ProcessInterrupted"
_INTERRUPTED_MESSAGE = "The process that started this run is no longer active."


def recover_interrupted_runs(
    connection: sqlite3.Connection,
    *,
    finished_at: str | None = None,
) -> int:
    """Mark orphaned ``running`` audit rows as interrupted.

    The caller must provide an idle writer connection. Recovery obtains a
    write lock before inspecting owners so that each supported table is
    checked and updated in one short transaction. Older or partial schemas are
    ignored: migration readiness is responsible for upgrading them first.
    """

    recovered = 0
    timestamp = finished_at or _utc_now()
    connection.execute("BEGIN IMMEDIATE")
    try:
        for table_name in _RUN_TABLES:
            if not _supports_recovery(connection, table_name):
                continue
            rows = connection.execute(
                f"""
                SELECT id, owner_pid, typeof(owner_pid)
                FROM {table_name}
                WHERE status = 'running'
                """
            ).fetchall()
            orphaned_ids = [
                str(row[0])
                for row in rows
                if not _owner_is_alive(row[1], owner_type=str(row[2]))
            ]
            if not orphaned_ids:
                continue
            cursor = connection.executemany(
                f"""
                UPDATE {table_name}
                SET status = 'interrupted',
                    finished_at = ?,
                    error_code = ?,
                    error_message = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    (
                        timestamp,
                        _INTERRUPTED_CODE,
                        _INTERRUPTED_MESSAGE,
                        run_id,
                    )
                    for run_id in orphaned_ids
                ),
            )
            if cursor.rowcount >= 0:
                recovered += cursor.rowcount
            else:
                recovered += len(orphaned_ids)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return recovered


def _supports_recovery(connection: sqlite3.Connection, table_name: str) -> bool:
    table = connection.execute(
        """
        SELECT 1
        FROM sqlite_schema
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    if table is None:
        return False
    columns = {
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    }
    return _REQUIRED_COLUMNS <= columns


def _owner_is_alive(owner_pid: object, *, owner_type: str) -> bool:
    if (
        owner_type != "integer"
        or not isinstance(owner_pid, int)
        or isinstance(owner_pid, bool)
        or owner_pid <= 0
    ):
        return False
    return process_is_alive(owner_pid)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
