"""Shared exceptions for Paper Galaxy."""


class MissingDependencyError(RuntimeError):
    """Raised when an optional dependency group is required but unavailable."""

    def __init__(self, dependency: str) -> None:
        self.dependency = dependency
        super().__init__(dependency)
