"""Plugin value objects for built-in local extensions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PluginInfo:
    """Reader-safe plugin metadata for CLI and API boundaries."""

    id: str
    name: str
    kind: str
    enabled_by_default: bool
    local_only: bool
    description: str
    file_extensions: tuple[str, ...] = ()
    optional_dependencies: tuple[str, ...] = ()

    def payload(self) -> dict[str, object]:
        """Return JSON-safe plugin metadata."""

        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "enabled_by_default": self.enabled_by_default,
            "local_only": self.local_only,
            "description": self.description,
            "file_extensions": list(self.file_extensions),
            "optional_dependencies": list(self.optional_dependencies),
        }
