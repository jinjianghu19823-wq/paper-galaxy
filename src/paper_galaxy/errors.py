"""Shared exceptions for Paper Galaxy."""

from __future__ import annotations

from pathlib import Path


class MissingDependencyError(RuntimeError):
    """Raised when an optional dependency group is required but unavailable."""

    def __init__(self, dependency: str) -> None:
        self.dependency = dependency
        super().__init__(dependency)


class DatabaseError(RuntimeError):
    """Base class for structured, locally actionable database failures."""

    code = "database_error"
    default_safe_message = "The Paper Galaxy database could not be opened."

    def __init__(
        self,
        database_path: str | Path,
        *,
        safe_message: str | None = None,
        detail_message: str | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.safe_message = safe_message or self.default_safe_message
        self.detail_message = detail_message or self.safe_message
        super().__init__(self.detail_message)


class DatabaseNotFoundError(DatabaseError):
    """Raised when a command needs an existing Paper Galaxy database."""

    code = "database_missing"
    default_safe_message = (
        "No Paper Galaxy database exists for this project. Initialize it first."
    )

    def __init__(self, database_path: str | Path) -> None:
        super().__init__(
            database_path,
            detail_message=f"No Paper Galaxy database found at {database_path}",
        )


class DatabaseLockedError(DatabaseError):
    """Raised when another local writer prevents a database operation."""

    code = "database_locked"
    default_safe_message = (
        "The Paper Galaxy database is busy. Wait for the other operation to finish "
        "and try again."
    )


class FutureSchemaError(DatabaseError):
    """Raised when a newer Paper Galaxy database is opened by older code."""

    code = "future_schema"
    default_safe_message = (
        "This project was created by a newer Paper Galaxy version. Upgrade Paper "
        "Galaxy before opening it."
    )

    def __init__(
        self,
        database_path: str | Path,
        *,
        found_version: int,
        current_version: int,
    ) -> None:
        self.found_version = found_version
        self.current_version = current_version
        super().__init__(
            database_path,
            detail_message=(
                f"Database at {database_path} has future schema version "
                f"{found_version}; this Paper Galaxy build supports "
                f"{current_version}."
            ),
        )


class DatabaseCorruptError(DatabaseError):
    """Raised when SQLite cannot read a database safely."""

    code = "database_corrupt"
    default_safe_message = (
        "The Paper Galaxy database is unreadable or corrupt. Restore a verified "
        "backup before making changes."
    )


class DatabaseNeedsMigrationError(DatabaseError):
    """Raised when an operational connection sees an older supported schema."""

    code = "database_needs_migration"
    default_safe_message = (
        "This Paper Galaxy project needs a database migration before it can be used."
    )

    def __init__(
        self,
        database_path: str | Path,
        *,
        found_version: int,
        current_version: int,
    ) -> None:
        self.found_version = found_version
        self.current_version = current_version
        super().__init__(
            database_path,
            detail_message=(
                f"Database at {database_path} uses schema version {found_version}; "
                f"migration to {current_version} is required."
            ),
        )


class DatabaseNeedsWriterNormalizationError(DatabaseError):
    """Raised when WAL state cannot be read without creating sidecar files."""

    code = "database_needs_writer_normalization"
    default_safe_message = (
        "This database needs a local writer maintenance pass before read-only "
        "access is safe. Open it with Paper Galaxy's project command and retry."
    )


class UnsupportedSchemaError(DatabaseError):
    """Raised when schema history is missing or too old to migrate safely."""

    code = "unsupported_schema"
    default_safe_message = (
        "This database schema cannot be migrated safely by this Paper Galaxy build."
    )


class FTSUnavailableError(RuntimeError):
    """Raised when the current SQLite build lacks FTS5 support."""
