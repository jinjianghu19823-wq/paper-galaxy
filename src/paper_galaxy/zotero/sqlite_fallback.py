"""Read-only Zotero SQLite diagnostics.

This module intentionally does not implement a Zotero import path. The local
API is the primary connector; direct SQLite access is only a fallback for
diagnostics and path confidence.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from paper_galaxy.errors import DatabaseError
from paper_galaxy.storage.sqlite import connect_external_read_only


def inspect_zotero_sqlite(database_path: Path) -> dict[str, object]:
    """Open zotero.sqlite read-only and return conservative diagnostics."""

    resolved = database_path.expanduser().resolve()
    if not resolved.exists():
        return {"exists": False, "valid": False}
    try:
        connection = connect_external_read_only(resolved)
        try:
            rows = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                ORDER BY name
                """
            ).fetchall()
        finally:
            connection.close()
    except DatabaseError as exc:
        return {
            "exists": True,
            "valid": False,
            "error": exc.safe_message,
            "error_code": exc.code,
        }
    except sqlite3.Error:
        return {
            "exists": True,
            "valid": False,
            "error": "The Zotero SQLite database could not be inspected safely.",
            "error_code": "database_unreadable",
        }
    names = {str(row[0]) for row in rows}
    return {
        "exists": True,
        "valid": bool(names),
        "table_count": len(names),
        "has_items_table": "items" in names,
        "has_attachments_table": "itemAttachments" in names,
    }
