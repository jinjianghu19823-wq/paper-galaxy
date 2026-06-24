"""Import Zotero records into a local Paper Galaxy project."""

from __future__ import annotations

import hashlib
import sqlite3
from collections import Counter
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
from paper_galaxy.zotero.filters import (
    CollectionSelection,
    ZoteroFilterError,
    collection_paths,
    filter_items,
    normalize_reading_status,
    resolve_collection,
    validate_non_empty_values,
)
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
    pdf_policy: str = "extract",
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
    if pdf_policy not in {"extract", "metadata", "skip-missing"}:
        raise ZoteroFilterError(
            "Invalid --pdf-policy value "
            f"{pdf_policy!r}. Expected one of: extract, metadata, skip-missing."
        )
    status_selection = normalize_reading_status(
        include_status,
        option_name="--include-status",
    )
    include_status = status_selection.value
    validate_non_empty_values(tags, option_name="--tag")
    validate_non_empty_values(item_types, option_name="--item-type")
    validate_non_empty_values(read_tags, option_name="--read-tag")
    validate_non_empty_values(reading_tags, option_name="--reading-tag")
    validate_non_empty_values(to_read_tags, option_name="--to-read-tag")
    resolved_project_dir = project_dir.expanduser().resolve()
    database_path = resolve_database_path(resolved_project_dir)
    zotero_client = client or LocalZoteroAPIClient(api_url)
    source_id = stable_zotero_source_id(api_url, "0")
    source_corpus_id = stable_zotero_corpus_id(source_id)
    run_id = f"zotero_import_{uuid4().hex[:16]}"
    now = _utc_now()
    warnings: list[str] = []
    if status_selection.warning:
        warnings.append(status_selection.warning)

    collections = [normalize_collection(row) for row in zotero_client.collections()]
    collection_paths_by_key = collection_paths(collections)
    selected_collection = _resolve_collection_filter(collections, collection)
    collection_id_by_key = {
        collection.key: stable_zotero_collection_id(source_id, collection.key)
        for collection in collections
    }
    fetched_rows = (
        zotero_client.collection_items(
            selected_collection.key,
            limit=limit,
            since=since_version,
        )
        if selected_collection
        else zotero_client.top_items(limit=limit, since=since_version)
    )
    items = [normalize_item(row) for row in fetched_rows]
    items_fetched = len(items)
    items = filter_items(
        items,
        collection_key=selected_collection.key if selected_collection else None,
        tags=tags,
        item_types=item_types,
    )
    if limit is not None:
        items = items[: max(0, limit)]
        if items_fetched >= limit:
            warnings.append(
                f"Import was capped by --limit {limit}. Increase --limit or remove "
                "it for a larger run."
            )
    if not items:
        if since_version is not None:
            warnings.append(
                f"No Zotero parent items changed since version {since_version}. "
                "This is a successful empty incremental result."
            )
        else:
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
                collection_paths_by_key.get(collection_key, collection_key)
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
    last_version_before = _last_source_version(resolved_project_dir, source_id)
    last_version_after = _max_version([item for item, _ in selected])
    if last_version_after is None:
        last_version_after = last_version_before
    filters = _filter_payload(
        tags=tags,
        item_types=item_types,
        include_status=include_status,
        since_version=since_version,
        pdf_policy=pdf_policy,
    )

    if dry_run:
        attachment_count = sum(len(item.attachments) for item, _ in selected)
        note_count = sum(len(item.notes) for item, _ in selected)
        annotation_count = sum(len(item.annotations) for item, _ in selected)
        return ZoteroImportRunSummary(
            run_id=run_id,
            source_id=source_id,
            project_dir=resolved_project_dir,
            database_path=database_path,
            dry_run=True,
            items_seen=len(enriched_items),
            items_fetched=items_fetched,
            items_selected=len(selected),
            items_filtered_out=max(0, items_fetched - len(selected)),
            attachments_seen=attachment_count,
            notes_imported=note_count if include_notes else 0,
            annotations_imported=annotation_count if include_notes else 0,
            filters=filters,
            selected_collection=_collection_payload(selected_collection),
            include_status=include_status,
            since_version=since_version,
            last_version_before=last_version_before,
            last_version_after=last_version_after,
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
                    "last_version": last_version_after,
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
                    "pdf_policy": pdf_policy,
                    "include_status": include_status,
                    "limit": limit,
                    "since_version": since_version,
                    "force": force,
                    "filters": filters,
                    "selected_collection": _collection_payload(selected_collection),
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
                        "path": collection_paths_by_key.get(collection_row.key),
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
                    collection_paths=collection_paths_by_key,
                    collection_id_by_key=collection_id_by_key,
                    data_dir=data_dir,
                    include_pdfs=include_pdfs,
                    include_notes=include_notes,
                    include_attachments=include_attachments,
                    include_metadata_only=include_metadata_only,
                    pdf_policy=pdf_policy,
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
        items_fetched=items_fetched,
        items_selected=len(selected),
        items_filtered_out=max(0, items_fetched - len(selected)),
        items_imported=counts.items_imported,
        items_updated=counts.items_updated,
        items_unchanged=counts.items_unchanged,
        attachments_seen=counts.attachments_seen,
        attachments_resolved=counts.attachments_resolved,
        attachment_status_counts=dict(counts.attachment_status_counts),
        stored_attachments=counts.stored_attachments,
        linked_attachments=counts.linked_attachments,
        pdfs_seen=counts.pdfs_seen,
        pdfs_extracted=counts.pdfs_extracted,
        pdfs_missing=counts.pdfs_missing,
        pdfs_extraction_failed=counts.pdfs_extraction_failed,
        notes_imported=counts.notes_imported,
        annotations_imported=counts.annotations_imported,
        metadata_only_documents=counts.metadata_only_documents,
        skipped=counts.skipped,
        filters=filters,
        selected_collection=_collection_payload(selected_collection),
        include_status=include_status,
        since_version=since_version,
        last_version_before=last_version_before,
        last_version_after=last_version_after,
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
        self.attachment_status_counts: Counter[str] = Counter()
        self.stored_attachments = 0
        self.linked_attachments = 0
        self.pdfs_seen = 0
        self.pdfs_extracted = 0
        self.pdfs_missing = 0
        self.pdfs_extraction_failed = 0
        self.notes_imported = 0
        self.annotations_imported = 0
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
    pdf_policy: str,
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
    counts.attachment_status_counts.update(
        str(attachment["path_status"]) for attachment, _ in attachments
    )
    counts.stored_attachments += sum(
        1
        for attachment, _ in attachments
        if str(attachment.get("zotero_path", "")).startswith("storage:")
    )
    counts.linked_attachments += sum(
        1
        for attachment, _ in attachments
        if attachment.get("zotero_path")
        and not str(attachment.get("zotero_path", "")).startswith("storage:")
    )
    notes = [note.text for note in item.notes if note.text] if include_notes else []
    annotation_texts = (
        [
            text
            for annotation in item.annotations
            for text in (annotation.text, annotation.comment)
            if text
        ]
        if include_notes
        else []
    )
    counts.notes_imported += len(notes)
    counts.annotations_imported += len(item.annotations) if include_notes else 0
    pdf_text = ""
    pdf_extract_attempted = False
    if primary_pdf is not None:
        counts.pdfs_seen += 1
        if primary_resolution and primary_resolution.status not in RESOLVED_STATUSES:
            counts.pdfs_missing += 1
    if (
        include_pdfs
        and pdf_policy != "metadata"
        and primary_pdf is not None
        and primary_resolution is not None
        and primary_resolution.resolved_path is not None
    ):
        pdf_extract_attempted = True
        extracted, reason = extract_pdf_file(primary_resolution.resolved_path)
        if extracted is not None and reason is None:
            pdf_text = extracted.text
            counts.pdfs_extracted += 1
        else:
            counts.pdfs_extraction_failed += 1
            warnings.append(
                f"PDF extraction failed for Zotero item {item.key}: "
                f"{reason or 'unknown'}"
            )
    if pdf_policy == "skip-missing" and primary_pdf is not None and not pdf_text:
        counts.skipped += 1
        warnings.append(
            f"Skipped Zotero item {item.key}: --pdf-policy skip-missing requires "
            "a resolvable, extractable PDF."
        )
        return
    text = build_zotero_document_text(
        item,
        collection_names=collection_names,
        notes=notes,
        annotations=annotation_texts,
        pdf_text=pdf_text,
        primary_attachment=primary_pdf,
    )
    if len(text) < min_chars and not include_metadata_only:
        counts.skipped += 1
        warnings.append(
            f"Skipped Zotero item {item.key}: metadata text shorter than {min_chars}."
        )
        return
    if not pdf_text:
        counts.metadata_only_documents += 1
        if primary_pdf is not None:
            reason = primary_resolution.status if primary_resolution else "unresolved"
            action = "created metadata-only document"
            if pdf_policy == "metadata":
                action = "PDF extraction disabled by --pdf-policy metadata"
            elif pdf_extract_attempted:
                action = "created metadata-only document after PDF extraction failed"
            warnings.append(
                f"Zotero item {item.key} ({item.title}) has no extracted PDF text "
                f"from attachment {primary_pdf.key} ({reason}); {action}."
            )
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
        pdf_text_extracted=bool(pdf_text),
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
    annotations: list[str],
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
        " ".join(annotations),
        " ".join(annotations),
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
    pdf_text_extracted: bool,
    now: str,
) -> IndexedDocument:
    path = f"zotero://items/{item.key}"
    file_type = "zotero"
    size_bytes = 0
    mtime_ns = 0
    if (
        pdf_text_extracted
        and primary_pdf
        and primary_resolution
        and primary_resolution.resolved_path
    ):
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
        if primary_pdf is None and _is_pdf_attachment(attachment):
            primary_pdf = attachment
            primary_resolution = resolution
    return rows, primary_pdf, primary_resolution


def _is_pdf_attachment(attachment: ZoteroAttachment) -> bool:
    content_type = (attachment.content_type or "").lower()
    filename = (attachment.filename or attachment.path or "").lower()
    return content_type == "application/pdf" or filename.endswith(".pdf")


def _max_version(items: list[ZoteroItem]) -> int | None:
    versions = [item.version for item in items if item.version is not None]
    return max(versions) if versions else None


def _resolve_collection_filter(
    collections: list[ZoteroCollection], collection: str | None
) -> CollectionSelection | None:
    if collection is None:
        return None
    return resolve_collection(collections, collection)


def _collection_payload(
    selection: CollectionSelection | None,
) -> dict[str, object] | None:
    if selection is None:
        return None
    return {
        "key": selection.key,
        "name": selection.name,
        "path": selection.path,
        "matched_by": selection.matched_by,
    }


def _filter_payload(
    *,
    tags: tuple[str, ...],
    item_types: tuple[str, ...],
    include_status: str,
    since_version: int | None,
    pdf_policy: str,
) -> dict[str, object]:
    return {
        "tags": list(tags),
        "item_types": [
            item_type.strip()
            for part in item_types
            for item_type in part.split(",")
            if item_type.strip()
        ],
        "include_status": include_status,
        "since_version": since_version,
        "pdf_policy": pdf_policy,
    }


def _last_source_version(project_dir: Path, source_id: str) -> int | None:
    database_path = resolve_database_path(project_dir)
    if not database_path.exists():
        return None
    uri = f"file:{database_path}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        try:
            row = connection.execute(
                """
                SELECT last_version
                FROM zotero_sources
                WHERE id = ?
                """,
                (source_id,),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    if row is None or row[0] is None:
        return None
    return int(row[0])


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")
