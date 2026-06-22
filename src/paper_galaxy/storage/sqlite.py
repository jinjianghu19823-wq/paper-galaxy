"""SQLite connection and project database path helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from paper_galaxy.config import load_project_config

DEFAULT_DATABASE_PATH = ".paper-galaxy/paper_galaxy.sqlite3"


def resolve_database_path(project_dir: Path | str) -> Path:
    """Resolve the SQLite path from project config or the Phase 2 default."""

    resolved_project_dir = Path(project_dir).expanduser().resolve()
    config = load_project_config(resolved_project_dir)
    configured_path = (
        config.database_path if config is not None else DEFAULT_DATABASE_PATH
    )
    database_path = Path(configured_path).expanduser()
    if not database_path.is_absolute():
        database_path = resolved_project_dir / database_path
    return database_path.resolve()


def connect_database(project_dir: Path | str) -> sqlite3.Connection:
    """Open the local project database, creating its parent directory."""

    database_path = resolve_database_path(project_dir)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
