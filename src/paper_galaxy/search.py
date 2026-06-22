"""Search and database stats wrappers."""

from __future__ import annotations

from pathlib import Path

from paper_galaxy.errors import DatabaseNotFoundError
from paper_galaxy.records import DatabaseStats, SearchResult
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path


def search_index(
    query: str,
    *,
    project_dir: Path,
    limit: int = 10,
    include_missing: bool = False,
) -> list[SearchResult]:
    """Search the local Paper Galaxy SQLite index."""

    database_path = resolve_database_path(project_dir)
    if not database_path.exists():
        raise DatabaseNotFoundError(database_path)
    connection = connect_database(project_dir)
    try:
        repository = Repository(connection, database_path)
        return repository.search_documents(
            query,
            limit=limit,
            include_missing=include_missing,
        )
    finally:
        connection.close()


def get_database_stats(*, project_dir: Path) -> DatabaseStats:
    """Read local Paper Galaxy database statistics."""

    database_path = resolve_database_path(project_dir)
    if not database_path.exists():
        raise DatabaseNotFoundError(database_path)
    connection = connect_database(project_dir)
    try:
        initialize_database(connection)
        repository = Repository(connection, database_path)
        return repository.get_stats()
    finally:
        connection.close()
