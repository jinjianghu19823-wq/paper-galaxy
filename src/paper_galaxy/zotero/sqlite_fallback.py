"""Read-only Zotero SQLite diagnostics.

This module intentionally does not implement a Zotero import path. The local
API is the primary connector; direct SQLite access is only a fallback for
diagnostics and path confidence.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def inspect_zotero_sqlite(database_path: Path) -> dict[str, object]:
    """Open zotero.sqlite read-only and return conservative diagnostics."""

    resolved = database_path.expanduser().resolve()
    if not resolved.exists():
        return {"exists": False, "valid": False}
    uri = f"file:{resolved}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
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
    except sqlite3.Error as exc:
        return {"exists": True, "valid": False, "error": str(exc)}
    names = {str(row[0]) for row in rows}
    return {
        "exists": True,
        "valid": bool(names),
        "table_count": len(names),
        "has_items_table": "items" in names,
        "has_attachments_table": "itemAttachments" in names,
    }
