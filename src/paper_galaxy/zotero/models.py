"""Dataclasses for normalized Zotero records."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ZoteroLibrary:
    """A Zotero library exposed by the local desktop API."""

    id: str
    type: str = "user"
    name: str = "Zotero Local Library"


@dataclass(frozen=True)
class ZoteroCollection:
    """A normalized Zotero collection."""

    key: str
    name: str
    parent_key: str | None = None
    version: int | None = None
    path: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ZoteroCreator:
    """A normalized Zotero creator row."""

    creator_type: str
    first_name: str | None = None
    last_name: str | None = None
    name: str | None = None

    @property
    def display_name(self) -> str:
        """Return a compact display name for metadata text."""

        if self.name:
            return self.name
        return " ".join(part for part in (self.first_name, self.last_name) if part)


@dataclass(frozen=True)
class ZoteroTag:
    """A normalized Zotero tag."""

    tag: str
    type: int | None = None


@dataclass(frozen=True)
class ZoteroAttachment:
    """A child attachment item."""

    key: str
    version: int | None
    title: str
    filename: str | None
    content_type: str | None
    path: str | None
    link_mode: str | None
    parent_key: str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ZoteroNote:
    """A child note item."""

    key: str
    version: int | None
    text: str
    parent_key: str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ZoteroItem:
    """A normalized top-level Zotero item."""

    key: str
    version: int | None
    library: ZoteroLibrary
    item_type: str
    title: str
    creators: tuple[ZoteroCreator, ...] = ()
    abstract_note: str | None = None
    date: str | None = None
    year: str | None = None
    publication_title: str | None = None
    doi: str | None = None
    url: str | None = None
    archive: str | None = None
    tags: tuple[ZoteroTag, ...] = ()
    collections: tuple[str, ...] = ()
    date_added: str | None = None
    date_modified: str | None = None
    extra: str | None = None
    relations: dict[str, Any] = field(default_factory=dict)
    parent_key: str | None = None
    attachments: tuple[ZoteroAttachment, ...] = ()
    notes: tuple[ZoteroNote, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AttachmentResolution:
    """Resolved local path status for a Zotero attachment."""

    status: str
    resolved_path: Path | None = None
    message: str | None = None


@dataclass(frozen=True)
class ZoteroDetection:
    """Best-effort local Zotero environment detection."""

    api_url: str
    api_reachable: bool
    api_error: str | None
    data_dir: Path | None
    database_exists: bool
    storage_exists: bool
    sqlite_diagnostics: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ZoteroImportRunSummary:
    """Summary returned by one Zotero import run."""

    run_id: str
    source_id: str
    project_dir: Path
    database_path: Path
    dry_run: bool
    items_seen: int = 0
    items_imported: int = 0
    items_updated: int = 0
    items_unchanged: int = 0
    attachments_seen: int = 0
    attachments_resolved: int = 0
    pdfs_extracted: int = 0
    notes_imported: int = 0
    metadata_only_documents: int = 0
    skipped: int = 0
    warnings: tuple[str, ...] = ()
    reading_status_counts: dict[str, int] = field(default_factory=dict)
    map_run_id: str | None = None


@dataclass(frozen=True)
class ZoteroReadingGraphSummary:
    """Summary for a Zotero reading graph payload or saved map run."""

    name: str
    document_count: int
    cluster_count: int
    map_run_id: str | None = None
    warnings: tuple[str, ...] = ()
