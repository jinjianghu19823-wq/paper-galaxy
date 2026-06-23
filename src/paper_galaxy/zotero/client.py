"""Client protocols for read-only Zotero connectors."""

from __future__ import annotations

from typing import Any, Protocol


class ZoteroClient(Protocol):
    """Small read-only Zotero client interface used by import tests."""

    def root(self) -> dict[str, Any]:
        """Return local API root metadata."""

    def collections(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Return Zotero collections."""

    def tags(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Return Zotero tags."""

    def top_items(
        self,
        *,
        limit: int | None = None,
        start: int = 0,
        since: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return top-level Zotero items."""

    def items(
        self,
        *,
        limit: int | None = None,
        start: int = 0,
        since: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return Zotero items."""

    def item_children(self, item_key: str) -> list[dict[str, Any]]:
        """Return children for one Zotero item."""

    def collection_items(
        self, collection_key: str, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Return items in one Zotero collection."""
