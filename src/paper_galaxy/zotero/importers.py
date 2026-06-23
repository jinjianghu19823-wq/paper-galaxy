"""Import Zotero records into a local Paper Galaxy project."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from paper_galaxy.chunking import chunk_text
from paper_galaxy.extract.pdf import extract_pdf_file
from paper_galaxy.indexer import stable_chunk_id
from paper_galaxy.records import IndexedChunk, IndexedDocument
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path
from paper_galaxy.zotero.attachments import RESOLVED_STATUSES, resolve_attachment_path
from paper_galaxy.zotero.client import ZoteroClient
from paper_galaxy.zotero.local_api import DEFAULT_LOCAL_API_URL, LocalZoteroAPIClient
from paper_galaxy.zotero.models import (
    AttachmentResolution,
    ZoteroAttachment,
    ZoteroCollection,
    ZoteroImportRunSummary,
    ZoteroItem,
)
from paper_galaxy.zotero.normalize import (
    attach_children,
    normalize_child,
    normalize_collection,
    normalize_item,
)
from paper_galaxy.zotero.reading import (
    DEFAULT_READ_TAGS,
    DEFAULT_READING_TAGS,
    DEFAULT_TO_READ_TAGS,
    build_and_store_zotero_reading_map,
    infer_reading_status,
    reading_status_counts,
)


def import_from_zotero(
    *,
    project_dir: Path,
    api_url: str = DEFAULT_LOCAL_API_URL,
    data_dir: Path | None = None,
    client: ZoteroClient | None = None,
    collection: str | None = None,
    tags: tuple[str, ...] = (),
    item_types: tuple[str, ...] = (),
    include_pdfs: bool = True,
    include_notes: bool = True,
    include_attachments: bool = True,
    include_metadata_only: bool = True,
    read_tags: tuple[str, ...] = DEFAULT_READ_TAGS,
    reading_tags: tuple[str, ...] = DEFAULT_READING_TAGS,
    to_read_tags: tuple[str, ...] = DEFAULT_TO_READ_TAGS,
    include_status: str = "all",
    limit: int | None = None,
    since_version: int | None = None,
    force: bool = False,
    dry_run: bool = False,
    build_reading_map: bool = True,
    map_name: str = "Zotero Reading Graph",
    min_chars: int = 40,
    chunk_size: int = 2000,
    chunk_overlap: int = 200,
    verbose: bool = False,
) -> ZoteroImportRunSummary:
    """Import Zotero top-level items into local Paper Galaxy SQLite state."""

    del verbose
    resolved_project_dir = project_dir.expanduser().resolve()
    database_path = resolve_database_path(resolved_project_dir)
    zotero_client = client or LocalZoteroAPIClient(api_url)
    source_id = stable_zotero_source_id(api_url, "0")
    source_corpus_id = stable_zotero_corpus_id(source_id)
    run_id = f"zotero_import_{uuid4().hex[:16]}"
    now = _utc_now()
    warnings: list[str] = []

    collections = [normalize_collection(row) for row in zotero_client.collections()]
    collection_paths = _collection_paths(collections)
    collection_id_by_key = {
        collection.key: stable_zotero_collection_id(source_id, collection.key)
        for collection in collections
    }
    items = [
        normalize_item(row)
        for row in zotero_client.top_items(limit=limit, since=since_version)
    ]
    items = _filter_items(
        items,
        collections=collections,
        collection=collection,
        tags=tags,
        item_types=item_types,
    )
    if limit is not None:
        items = items[: max(0, limit)]
    if not items:
        warnings.append(
            "No Zotero parent items matched the selected filters. Try removing "
            "--collection/--tag filters or run paper-galaxy zotero items."
        )

    enriched_items: list[ZoteroItem] = []
    for item in items:
        children = [
            child
            for child in (
                normalize_child(row) for row in zotero_client.item_children(item.key)
            )
            if child is not None
        ]
        enriched_items.append(attach_children(item, children))

    statuses = [
        infer_reading_status(
            item,
            collection_names=[
                collection_paths.get(collection_key, collection_key)
                for collection_key in item.collections
            ],
            read_tags=read_tags,
            reading_tags=reading_tags,
            to_read_tags=to_read_tags,
        )
        for item in enriched_items
    ]
    selected = [
        (item, status)
        for item, status in zip(enriched_items, statuses, strict=False)
        if include_status == "all" or status == include_status
    ]

    if dry_run:
        attachment_count = sum(len(item.attachments) for item, _ in selected)
        note_count = sum(len(item.notes) for item, _ in selected)
        return ZoteroImportRunSummary(
            run_id=run_id,
            source_id=source_id,
            project_dir=resolved_project_dir,
            database_path=database_path,
            dry_run=True,
            items_seen=len(enriched_items),
            attachments_seen=attachment_count,
            notes_imported=note_count if include_notes else 0,
            warnings=tuple(warnings),
            reading_status_counts=reading_status_counts(
                [item for item, _ in selected],
                [status for _, status in selected],
            ),
        )

    connection = connect_database(resolved_project_dir)
    map_run_id: str | None = None
    counts = _ImportCounts(items_seen=len(enriched_items))
    try:
        initialize_database(connection)
        repository = Repository(connection, database_path)
        with connection:
            repository.upsert_corpus(
                source_corpus_id,
                f"zotero://sources/{source_id}",
                now,
            )
            repository.upsert_zotero_source(
                {
                    "id": source_id,
                    "source_type": "local_api",
                    "local_api_url": api_url,
                    "data_dir": (
                        str(data_dir.expanduser().resolve()) if data_dir else None
                    ),
                    "library_id": "0",
                    "library_type": "user",
                    "name": "Zotero Local Library",
                    "last_version": _max_version([item for item, _ in selected]),
                    "created_at": now,
                    "updated_at": now,
                }
            )
            repository.create_zotero_import_run(
                run_id,
                source_id,
                started_at=now,
                config={
                    "include_pdfs": include_pdfs,
                    "include_notes": include_notes,
                    "include_attachments": include_attachments,
                    "include_metadata_only": include_metadata_only,
                    "include_status": include_status,
                    "limit": limit,
                    "since_version": since_version,
                    "force": force,
                },
            )
            for collection_row in collections:
                collection_id = collection_id_by_key[collection_row.key]
                repository.upsert_zotero_collection(
                    {
                        "id": collection_id,
                        "source_id": source_id,
                        "zotero_key": collection_row.key,
                        "parent_key": collection_row.parent_key,
                        "name": collection_row.name,
                        "path": collection_paths.get(collection_row.key),
                        "version": collection_row.version,
                        "data": collection_row.raw,
                    }
                )
            for item, status in selected:
                _import_one_item(
                    repository=repository,
                    item=item,
                    source_id=source_id,
                    source_corpus_id=source_corpus_id,
                    status=status,
                    collection_paths=collection_paths,
                    collection_id_by_key=collection_id_by_key,
                    data_dir=data_dir,
                    include_pdfs=include_pdfs,
                    include_notes=include_notes,
                    include_attachments=include_attachments,
                    include_metadata_only=include_metadata_only,
                    min_chars=min_chars,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    force=force,
                    now=now,
                    counts=counts,
                    warnings=warnings,
                )
            repository.finish_zotero_import_run(
                run_id,
                finished_at=_utc_now(),
                status="completed",
                items_seen=counts.items_seen,
                items_imported=counts.items_imported,
                items_updated=counts.items_updated,
                items_unchanged=counts.items_unchanged,
                attachments_seen=counts.attachments_seen,
                attachments_resolved=counts.attachments_resolved,
                pdfs_extracted=counts.pdfs_extracted,
                notes_imported=counts.notes_imported,
                skipped=counts.skipped,
                warnings=warnings,
            )
        if build_reading_map and selected:
            saved = build_and_store_zotero_reading_map(
                project_dir=resolved_project_dir,
                name=map_name,
                status=include_status,
                collection=collection,
                tag=tags[0] if tags else None,
            )
            map_run = saved.get("map_run")
            if isinstance(map_run, dict):
                map_run_id = str(map_run.get("id"))
    finally:
        connection.close()

    return ZoteroImportRunSummary(
        run_id=run_id,
        source_id=source_id,
        project_dir=resolved_project_dir,
        database_path=database_path,
        dry_run=False,
        items_seen=counts.items_seen,
        items_imported=counts.items_imported,
        items_updated=counts.items_updated,
        items_unchanged=counts.items_unchanged,
        attachments_seen=counts.attachments_seen,
        attachments_resolved=counts.attachments_resolved,
        pdfs_extracted=counts.pdfs_extracted,
        notes_imported=counts.notes_imported,
        metadata_only_documents=counts.metadata_only_documents,
        skipped=counts.skipped,
        warnings=tuple(warnings),
        reading_status_counts=reading_status_counts(
            [item for item, _ in selected],
            [status for _, status in selected],
        ),
        map_run_id=map_run_id,
    )


def stable_zotero_source_id(api_url: str, library_id: str) -> str:
    digest = hashlib.sha256(f"{api_url}\0{library_id}".encode()).hexdigest()
    return f"zotero_source_{digest[:16]}"


def stable_zotero_corpus_id(source_id: str) -> str:
    digest = hashlib.sha256(source_id.encode()).hexdigest()
    return f"corpus_zotero_{digest[:16]}"


def stable_zotero_item_id(source_id: str, zotero_key: str) -> str:
    digest = hashlib.sha256(f"{source_id}\0{zotero_key}".encode()).hexdigest()
    return f"zotero_item_{digest[:16]}"


def stable_zotero_document_id(source_id: str, zotero_key: str) -> str:
    digest = hashlib.sha256(f"{source_id}\0{zotero_key}".encode()).hexdigest()
    return f"doc_zotero_{digest[:16]}"


def stable_zotero_attachment_id(source_id: str, zotero_key: str) -> str:
    digest = hashlib.sha256(f"{source_id}\0{zotero_key}".encode()).hexdigest()
    return f"zotero_attachment_{digest[:16]}"


def stable_zotero_collection_id(source_id: str, zotero_key: str) -> str:
    digest = hashlib.sha256(f"{source_id}\0{zotero_key}".encode()).hexdigest()
    return f"zotero_collection_{digest[:16]}"


class _ImportCounts:
    def __init__(self, *, items_seen: int) -> None:
        self.items_seen = items_seen
        self.items_imported = 0
        self.items_updated = 0
        self.items_unchanged = 0
        self.attachments_seen = 0
        self.attachments_resolved = 0
        self.pdfs_extracted = 0
        self.notes_imported = 0
        self.metadata_only_documents = 0
        self.skipped = 0


def _import_one_item(
    *,
    repository: Repository,
    item: ZoteroItem,
    source_id: str,
    source_corpus_id: str,
    status: str,
    collection_paths: dict[str, str],
    collection_id_by_key: dict[str, str],
    data_dir: Path | None,
    include_pdfs: bool,
    include_notes: bool,
    include_attachments: bool,
    include_metadata_only: bool,
    min_chars: int,
    chunk_size: int,
    chunk_overlap: int,
    force: bool,
    now: str,
    counts: _ImportCounts,
    warnings: list[str],
) -> None:
    zotero_item_id = stable_zotero_item_id(source_id, item.key)
    document_id = stable_zotero_document_id(source_id, item.key)
    existing_document = repository.get_document(document_id)
    collection_names = [
        collection_paths.get(collection_key, collection_key)
        for collection_key in item.collections
    ]
    attachments, primary_pdf, primary_resolution = _attachment_records(
        item,
        source_id=source_id,
        zotero_item_id=zotero_item_id,
        data_dir=data_dir,
        include_attachments=include_attachments,
        now=now,
    )
    counts.attachments_seen += len(attachments)
    counts.attachments_resolved += sum(
        1
        for attachment, _ in attachments
        if str(attachment["path_status"]) in RESOLVED_STATUSES
    )
    notes = [note.text for note in item.notes if note.text] if include_notes else []
    counts.notes_imported += len(notes)
    pdf_text = ""
    if (
        include_pdfs
        and primary_pdf is not None
        and primary_resolution is not None
        and primary_resolution.resolved_path is not None
    ):
        extracted, reason = extract_pdf_file(primary_resolution.resolved_path)
        if extracted is not None and reason is None:
            pdf_text = extracted.text
            counts.pdfs_extracted += 1
        else:
            warnings.append(
                f"PDF extraction failed for Zotero item {item.key}: "
                f"{reason or 'unknown'}"
            )
    text = build_zotero_document_text(
        item,
        collection_names=collection_names,
        notes=notes,
        pdf_text=pdf_text,
        primary_attachment=primary_pdf,
    )
    if len(text) < min_chars and not include_metadata_only:
        counts.skipped += 1
        warnings.append(
            f"Skipped Zotero item {item.key}: metadata text shorter than {min_chars}."
        )
        return
    if not primary_pdf:
        counts.metadata_only_documents += 1
    digest = hashlib.sha256(text.encode()).hexdigest()
    if existing_document is None:
        counts.items_imported += 1
    elif existing_document.sha256 == digest and not force:
        counts.items_unchanged += 1
    else:
        counts.items_updated += 1
    document = _document_record(
        item=item,
        source_corpus_id=source_corpus_id,
        document_id=document_id,
        digest=digest,
        text=text,
        primary_pdf=primary_pdf,
        primary_resolution=primary_resolution,
        now=now,
    )
    chunks = [
        IndexedChunk(
            id=stable_chunk_id(document_id, index),
            document_id=document_id,
            chunk_index=index,
            text=chunk,
            char_count=len(chunk),
        )
        for index, chunk in enumerate(
            chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        )
    ]
    repository.upsert_zotero_item(
        {
            "id": zotero_item_id,
            "source_id": source_id,
            "zotero_key": item.key,
            "version": item.version,
            "item_type": item.item_type,
            "title": item.title,
            "year": item.year,
            "date": item.date,
            "date_added": item.date_added,
            "date_modified": item.date_modified,
            "publication_title": item.publication_title,
            "doi": item.doi,
            "url": item.url,
            "abstract_note": item.abstract_note,
            "extra": item.extra,
            "reading_status": status,
            "data": item.raw,
            "created_at": now,
            "updated_at": now,
        }
    )
    repository.replace_zotero_creators(
        zotero_item_id,
        [
            {
                "creator_type": creator.creator_type,
                "first_name": creator.first_name,
                "last_name": creator.last_name,
                "name": creator.name,
            }
            for creator in item.creators
        ],
    )
    repository.replace_zotero_item_tags(
        zotero_item_id,
        [{"tag": tag.tag, "type": tag.type} for tag in item.tags],
    )
    repository.replace_zotero_item_collections(
        zotero_item_id,
        [
            collection_id_by_key[key]
            for key in item.collections
            if key in collection_id_by_key
        ],
    )
    for attachment, _ in attachments:
        repository.upsert_zotero_attachment(attachment)
    repository.upsert_document(document, text, chunks)
    repository.upsert_zotero_document_link(
        document_id=document_id,
        zotero_item_id=zotero_item_id,
        attachment_id=stable_zotero_attachment_id(source_id, primary_pdf.key)
        if primary_pdf
        else None,
        role="primary",
    )


def build_zotero_document_text(
    item: ZoteroItem,
    *,
    collection_names: list[str],
    notes: list[str],
    pdf_text: str,
    primary_attachment: ZoteroAttachment | None,
) -> str:
    """Build transparent weighted text for Zotero similarity."""

    creators = "; ".join(creator.display_name for creator in item.creators)
    tags = "; ".join(tag.tag for tag in item.tags if tag.tag)
    collections = "; ".join(collection_names)
    fields = [
        item.title,
        item.title,
        item.title,
        item.abstract_note or "",
        item.abstract_note or "",
        tags,
        tags,
        collections,
        collections,
        " ".join(notes),
        " ".join(notes),
        creators,
        item.publication_title or "",
        item.year or item.date or "",
        item.doi or "",
        item.url or "",
        primary_attachment.filename if primary_attachment else "",
        item.extra or "",
        pdf_text,
    ]
    return "\n".join(field for field in fields if field and field.strip()).strip()


def _document_record(
    *,
    item: ZoteroItem,
    source_corpus_id: str,
    document_id: str,
    digest: str,
    text: str,
    primary_pdf: ZoteroAttachment | None,
    primary_resolution: AttachmentResolution | None,
    now: str,
) -> IndexedDocument:
    path = f"zotero://items/{item.key}"
    file_type = "zotero"
    size_bytes = 0
    mtime_ns = 0
    if primary_pdf and primary_resolution and primary_resolution.resolved_path:
        path = str(primary_resolution.resolved_path)
        file_type = "pdf"
        if primary_resolution.resolved_path.exists():
            stat = primary_resolution.resolved_path.stat()
            size_bytes = stat.st_size
            mtime_ns = stat.st_mtime_ns
    return IndexedDocument(
        id=document_id,
        corpus_id=source_corpus_id,
        path=path,
        relative_path=f"zotero/{item.key}",
        file_type=file_type,
        title=item.title,
        sha256=digest,
        size_bytes=size_bytes,
        mtime_ns=mtime_ns,
        char_count=len(text),
        status="active",
        first_seen_at=now,
        last_seen_at=now,
        updated_at=now,
    )


def _attachment_records(
    item: ZoteroItem,
    *,
    source_id: str,
    zotero_item_id: str,
    data_dir: Path | None,
    include_attachments: bool,
    now: str,
) -> tuple[
    list[tuple[dict[str, object], AttachmentResolution]],
    ZoteroAttachment | None,
    AttachmentResolution | None,
]:
    rows: list[tuple[dict[str, object], AttachmentResolution]] = []
    primary_pdf: ZoteroAttachment | None = None
    primary_resolution: AttachmentResolution | None = None
    if not include_attachments:
        return rows, primary_pdf, primary_resolution
    for attachment in item.attachments:
        resolution = resolve_attachment_path(attachment, data_dir=data_dir)
        attachment_id = stable_zotero_attachment_id(source_id, attachment.key)
        rows.append(
            (
                {
                    "id": attachment_id,
                    "source_id": source_id,
                    "parent_zotero_item_id": zotero_item_id,
                    "zotero_key": attachment.key,
                    "title": attachment.title,
                    "filename": attachment.filename,
                    "content_type": attachment.content_type,
                    "link_mode": attachment.link_mode,
                    "zotero_path": attachment.path,
                    "resolved_path": str(resolution.resolved_path)
                    if resolution.resolved_path
                    else None,
                    "path_status": resolution.status,
                    "version": attachment.version,
                    "data": attachment.raw,
                    "created_at": now,
                    "updated_at": now,
                },
                resolution,
            )
        )
        if primary_pdf is None and _is_pdf_attachment(attachment, resolution):
            primary_pdf = attachment
            primary_resolution = resolution
    return rows, primary_pdf, primary_resolution


def _is_pdf_attachment(
    attachment: ZoteroAttachment, resolution: AttachmentResolution
) -> bool:
    if resolution.status not in RESOLVED_STATUSES:
        return False
    content_type = (attachment.content_type or "").lower()
    filename = (attachment.filename or attachment.path or "").lower()
    return content_type == "application/pdf" or filename.endswith(".pdf")


def _collection_paths(collections: list[ZoteroCollection]) -> dict[str, str]:
    by_key = {collection.key: collection for collection in collections}

    def path_for(key: str, seen: set[str] | None = None) -> str:
        seen = seen or set()
        if key in seen or key not in by_key:
            return key
        seen.add(key)
        collection = by_key[key]
        if collection.parent_key and collection.parent_key in by_key:
            return f"{path_for(collection.parent_key, seen)} / {collection.name}"
        return collection.name

    return {collection.key: path_for(collection.key) for collection in collections}


def _filter_items(
    items: list[ZoteroItem],
    *,
    collections: list[ZoteroCollection],
    collection: str | None,
    tags: tuple[str, ...],
    item_types: tuple[str, ...],
) -> list[ZoteroItem]:
    collection_lookup = {row.key: row for row in collections}
    selected: list[ZoteroItem] = []
    allowed_types = {
        item_type for part in item_types for item_type in part.split(",") if item_type
    }
    for item in items:
        if allowed_types and item.item_type not in allowed_types:
            continue
        item_tags = {tag_row.tag for tag_row in item.tags}
        if tags and any(tag not in item_tags for tag in tags):
            continue
        if collection:
            collection_names = {
                collection_lookup[key].name
                for key in item.collections
                if key in collection_lookup
            }
            if collection not in set(item.collections) | collection_names:
                continue
        selected.append(item)
    return selected


def _max_version(items: list[ZoteroItem]) -> int | None:
    versions = [item.version for item in items if item.version is not None]
    return max(versions) if versions else None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")
