"""Minimal schema initialization for Phase 2."""

from __future__ import annotations

import sqlite3
from importlib.resources import files

from paper_galaxy.errors import FTSUnavailableError

SCHEMA_VERSION = "3"


def initialize_database(connection: sqlite3.Connection) -> None:
    """Create the local project schema idempotently."""

    schema = (
        files("paper_galaxy.storage").joinpath("schema.sql").read_text(encoding="utf-8")
    )
    try:
        connection.executescript(schema)
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).lower():
            raise FTSUnavailableError(
                "SQLite FTS5 is not available in this Python build."
            ) from exc
        raise
    connection.execute(
        """
        INSERT INTO schema_meta(key, value)
        VALUES ('schema_version', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (SCHEMA_VERSION,),
    )
