"""Shared exceptions for Paper Galaxy."""


class MissingDependencyError(RuntimeError):
    """Raised when an optional dependency group is required but unavailable."""

    def __init__(self, dependency: str) -> None:
        self.dependency = dependency
        super().__init__(dependency)


class DatabaseNotFoundError(RuntimeError):
    """Raised when a command needs an existing Paper Galaxy database."""

    def __init__(self, database_path: object) -> None:
        self.database_path = database_path
        super().__init__(f"No Paper Galaxy database found at {database_path}")


class FTSUnavailableError(RuntimeError):
    """Raised when the current SQLite build lacks FTS5 support."""
