"""Client protocols for read-only Zotero connectors."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from paper_galaxy.zotero.models import ZoteroDeletedBatch, ZoteroSyncBatch


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
        self,
        collection_key: str,
        *,
        limit: int | None = None,
        since: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return items in one Zotero collection."""

    def sync_collections(
        self,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ZoteroSyncBatch:
        """Return all collections plus a stable library version."""

    def sync_items(
        self,
        *,
        since: int,
        limit: int | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ZoteroSyncBatch:
        """Return changed parent and child items plus a stable library version."""

    def items_by_keys(
        self,
        keys: tuple[str, ...],
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ZoteroSyncBatch:
        """Hydrate explicit item keys in bounded batches."""

    def deleted_since(
        self,
        *,
        since: int,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ZoteroDeletedBatch:
        """Return deletion keys plus the same stable library version."""
