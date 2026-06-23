"""Static registry for built-in Paper Galaxy plugins."""

from __future__ import annotations

from paper_galaxy.plugins.base import PluginInfo
from paper_galaxy.plugins.builtin import BUILTIN_PLUGINS


class PluginRegistry:
    """Read-only registry for built-in local plugins."""

    def __init__(self, plugins: tuple[PluginInfo, ...]) -> None:
        self._plugins = plugins

    def list_plugins(self) -> list[PluginInfo]:
        """Return all known built-in plugins."""

        return list(self._plugins)

    def list_payloads(self) -> list[dict[str, object]]:
        """Return JSON-safe plugin metadata."""

        return [plugin.payload() for plugin in self._plugins]

    def get(self, plugin_id: str) -> PluginInfo | None:
        """Return one plugin by id."""

        return next(
            (plugin for plugin in self._plugins if plugin.id == plugin_id), None
        )


def get_plugin_registry() -> PluginRegistry:
    """Return the static local registry; external loading is intentionally absent."""

    return PluginRegistry(BUILTIN_PLUGINS)
