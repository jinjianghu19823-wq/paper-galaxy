"""Import Zotero records into a local Paper Galaxy project."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from paper_galaxy.chunking import chunk_text
from paper_galaxy.extract.pdf import extract_pdf_file
from paper_galaxy.indexer import stable_chunk_id
from paper_galaxy.records import IndexedChunk, IndexedDocument
from paper_galaxy.storage.json import load_json_object
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import (
    connect_read_only,
    connect_read_write,
    ensure_database_ready,
    resolve_database_path,
)
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
from paper_galaxy.zotero.local_api import (
    DEFAULT_LOCAL_API_URL,
    MAX_SYNC_RESULT_LIMIT,
    LocalZoteroAPIClient,
    canonical_local_api_url,
)
from paper_galaxy.zotero.models import (
    AttachmentResolution,
    ZoteroAnnotation,
    ZoteroAttachment,
    ZoteroCollection,
    ZoteroImportRunSummary,
    ZoteroItem,
    ZoteroNote,
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

if TYPE_CHECKING:
    from paper_galaxy.services.sources import ZoteroProfileRegistration


class ZoteroImportCancelled(RuntimeError):
    """Raised between records when a durable Zotero sync is cancelled."""


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
    full: bool = False,
    registered_profile_id: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    build_reading_map: bool = True,
    map_name: str = "Zotero Reading Graph",
    min_chars: int = 40,
    chunk_size: int = 2000,
    chunk_overlap: int = 200,
    verbose: bool = False,
    cancel_requested: Callable[[], bool] | None = None,
    commit_guard: Callable[[], None] | None = None,
    final_commit_guard: Callable[[sqlite3.Connection], None] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> ZoteroImportRunSummary:
    """Import Zotero top-level items into local Paper Galaxy SQLite state."""

    del verbose
    _validate_incremental_request_options(limit=limit, since_version=since_version)
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
    api_url = canonical_local_api_url(api_url)
    resolved_project_dir = project_dir.expanduser().resolve()
    database_path = resolve_database_path(resolved_project_dir)
    _preflight_zotero_project_paths(
        project_dir=resolved_project_dir,
        database_path=database_path,
        data_dir=data_dir,
    )
    zotero_client = client or LocalZoteroAPIClient(api_url)
    source_id = stable_zotero_source_id(api_url, "0")
    source_corpus_id = stable_zotero_corpus_id(source_id)
    run_id = f"zotero_import_{uuid4().hex[:16]}"
    now = _utc_now()
    warnings: list[str] = []
    if status_selection.warning:
        warnings.append(status_selection.warning)
    filters = _filter_payload(
        tags=tags,
        item_types=item_types,
        include_status=include_status,
        since_version=since_version,
        pdf_policy=pdf_policy,
    )
    if full and since_version is not None:
        raise ZoteroFilterError("--full and --since-version cannot be used together.")
    if _supports_incremental_sync(zotero_client):
        from paper_galaxy.services.sources import prepare_zotero_profile_registration

        if not dry_run and database_path.is_file():
            ensure_database_ready(resolved_project_dir)
        materialization_signature = _zotero_materialization_signature(
            data_dir=data_dir,
            include_pdfs=include_pdfs,
            include_notes=include_notes,
            include_attachments=include_attachments,
            include_metadata_only=include_metadata_only,
            pdf_policy=pdf_policy,
            read_tags=read_tags,
            reading_tags=reading_tags,
            to_read_tags=to_read_tags,
            min_chars=min_chars,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        registry_filters = _registered_profile_filters(
            collection=collection,
            tags=tags,
            item_types=item_types,
            include_status=include_status,
            pdf_policy=pdf_policy,
        )
        requested_profile = prepare_zotero_profile_registration(
            resolved_project_dir,
            zotero_source_id=source_id,
            api_url=api_url,
            data_dir=(
                str(data_dir.expanduser().resolve()) if data_dir is not None else None
            ),
            library_id="0",
            library_type="user",
            filters=registry_filters,
        )
        requested_profile, preflight_profile = _read_incremental_profile_state(
            project_dir=resolved_project_dir,
            source_id=source_id,
            requested_profile=requested_profile,
            registered_profile_id=registered_profile_id,
        )
        materialization_state = _read_source_materialization_state(
            resolved_project_dir,
            source_id=source_id,
        )
        _validate_explicit_since_version(
            since_version,
            profile=preflight_profile,
        )
        _validate_materialization_change(
            materialization_signature,
            source_state=materialization_state,
            full=full,
        )
        return _import_incremental_from_zotero(
            project_dir=resolved_project_dir,
            database_path=database_path,
            api_url=api_url,
            data_dir=data_dir,
            client=zotero_client,
            source_id=source_id,
            source_corpus_id=source_corpus_id,
            run_id=run_id,
            started_at=now,
            collection=collection,
            tags=tags,
            item_types=item_types,
            include_pdfs=include_pdfs,
            include_notes=include_notes,
            include_attachments=include_attachments,
            include_metadata_only=include_metadata_only,
            pdf_policy=pdf_policy,
            read_tags=read_tags,
            reading_tags=reading_tags,
            to_read_tags=to_read_tags,
            include_status=include_status,
            limit=limit,
            since_version=since_version,
            full=full,
            force=force,
            dry_run=dry_run,
            build_reading_map=build_reading_map,
            map_name=map_name,
            min_chars=min_chars,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            cancel_requested=cancel_requested,
            commit_guard=commit_guard,
            final_commit_guard=final_commit_guard,
            progress_callback=progress_callback,
            warnings=warnings,
            filters=filters,
            requested_profile=requested_profile,
            preflight_profile=preflight_profile,
            materialization_signature=materialization_signature,
            rematerialize_source=(
                (
                    full
                    and materialization_state.signatures != {materialization_signature}
                )
                or (
                    not materialization_state.signatures
                    and materialization_state.has_materialized_items
                )
            ),
        )
    if not dry_run:
        ensure_database_ready(resolved_project_dir)
    last_version_before = _last_source_version(resolved_project_dir, source_id)
    connection: sqlite3.Connection | None = None
    repository: Repository | None = None
    counts = _ImportCounts(items_seen=0)
    run_config: dict[str, object] = {
        "include_pdfs": include_pdfs,
        "include_notes": include_notes,
        "include_attachments": include_attachments,
        "include_metadata_only": include_metadata_only,
        "pdf_policy": pdf_policy,
        "read_tags": list(read_tags),
        "reading_tags": list(reading_tags),
        "to_read_tags": list(to_read_tags),
        "include_status": include_status,
        "min_chars": min_chars,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "limit": limit,
        "since_version": since_version,
        "force": force,
        "filters": filters,
        "requested_collection": collection,
    }
    if not dry_run:
        _raise_if_zotero_cancelled(cancel_requested)
        connection = connect_read_write(resolved_project_dir)
        repository = Repository(connection, database_path)
        try:
            with connection:
                repository.upsert_corpus(
                    source_corpus_id,
                    f"zotero://sources/{source_id}",
                    now,
                )
                repository.upsert_zotero_source(
                    _zotero_source_payload(
                        source_id=source_id,
                        api_url=api_url,
                        data_dir=data_dir,
                        last_version=last_version_before,
                        created_at=now,
                        updated_at=now,
                    )
                )
                repository.create_zotero_import_run(
                    run_id,
                    source_id,
                    started_at=now,
                    config=run_config,
                )
        except BaseException:
            connection.close()
            raise

    try:
        _raise_if_zotero_cancelled(cancel_requested)
        prepared = _prepare_zotero_import(
            client=zotero_client,
            collection=collection,
            tags=tags,
            item_types=item_types,
            include_status=include_status,
            limit=limit,
            since_version=since_version,
            read_tags=read_tags,
            reading_tags=reading_tags,
            to_read_tags=to_read_tags,
            source_id=source_id,
            warnings=warnings,
        )
    except BaseException as exc:
        if connection is not None and repository is not None:
            try:
                _mark_zotero_run_failed(
                    connection=connection,
                    repository=repository,
                    run_id=run_id,
                    counts=counts,
                    warnings=warnings,
                    error=exc,
                )
            except BaseException:
                pass
            finally:
                connection.close()
        raise

    collections = prepared.collections
    collection_paths_by_key = prepared.collection_paths_by_key
    selected_collection = prepared.selected_collection
    collection_id_by_key = prepared.collection_id_by_key
    items_fetched = prepared.items_fetched
    enriched_items = prepared.enriched_items
    selected = prepared.selected
    counts.items_seen = len(enriched_items)
    if connection is not None and repository is not None:
        run_config["selected_collection"] = _collection_payload(selected_collection)
        try:
            _raise_if_zotero_cancelled(cancel_requested)
            with connection:
                repository.update_zotero_import_run_config(run_id, run_config)
        except BaseException as exc:
            try:
                _mark_zotero_run_failed(
                    connection=connection,
                    repository=repository,
                    run_id=run_id,
                    counts=counts,
                    warnings=warnings,
                    error=exc,
                )
            except BaseException:
                pass
            finally:
                connection.close()
            raise
    last_version_after = _max_version([item for item, _ in selected])
    if last_version_after is None:
        last_version_after = last_version_before
    elif last_version_before is not None:
        last_version_after = max(last_version_before, last_version_after)

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

    assert connection is not None and repository is not None
    map_run_id: str | None = None
    try:
        _raise_if_zotero_cancelled(cancel_requested)
        with connection:
            for collection_row in collections:
                collection_id = collection_id_by_key[collection_row.key]
                accepted = repository.upsert_zotero_collection(
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
                if not accepted:
                    raise ZoteroVersionConflictError("collection", collection_row.key)
        selected_count = len(selected)
        for item_index, (item, status) in enumerate(selected):
            _raise_if_zotero_cancelled(cancel_requested)
            if progress_callback is not None:
                progress_callback(
                    item_index,
                    selected_count,
                    f"Syncing local Zotero item {item_index + 1} of {selected_count}.",
                )
            prepared_external: _PreparedZoteroExternal | None = None
            if _zotero_item_requires_external_preparation(
                repository,
                item=item,
                source_id=source_id,
                force=force,
                rematerialize=False,
                allow_unknown_child_state=False,
                verified_deleted_child_keys=set(),
            ):
                prepared_external = _prepare_zotero_external(
                    item=item,
                    source_id=source_id,
                    source_corpus_id=source_corpus_id,
                    collection_names=[
                        collection_paths_by_key.get(key, key)
                        for key in item.collections
                    ],
                    data_dir=data_dir,
                    include_pdfs=include_pdfs,
                    include_notes=include_notes,
                    include_attachments=include_attachments,
                    include_metadata_only=include_metadata_only,
                    pdf_policy=pdf_policy,
                    min_chars=min_chars,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    now=now,
                )
            count_snapshot = counts.snapshot()
            try:
                with connection:
                    accepted = _import_one_item(
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
                        commit_guard=commit_guard,
                        prepared_external=prepared_external,
                        external_preparation_complete=True,
                    )
            except BaseException:
                counts.restore(count_snapshot)
                raise
            if not accepted:
                counts.restore(count_snapshot)
                counts.items_unchanged += 1
        _raise_if_zotero_cancelled(cancel_requested)
        with connection:
            repository.upsert_zotero_source(
                _zotero_source_payload(
                    source_id=source_id,
                    api_url=api_url,
                    data_dir=data_dir,
                    last_version=last_version_after,
                    created_at=now,
                    updated_at=_utc_now(),
                )
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
    except BaseException as exc:
        try:
            _mark_zotero_run_failed(
                connection=connection,
                repository=repository,
                run_id=run_id,
                counts=counts,
                warnings=warnings,
                error=exc,
            )
        except BaseException:
            pass
        raise
    finally:
        connection.close()

    if build_reading_map and selected:
        try:
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
        except Exception as exc:
            warnings.append(
                "Zotero sync completed, but the reading map build failed "
                f"({type(exc).__name__}). Run `paper-galaxy zotero graph` to retry."
            )

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


def _supports_incremental_sync(client: object) -> bool:
    return all(
        callable(getattr(client, name, None))
        for name in (
            "sync_collections",
            "sync_items",
            "items_by_keys",
            "deleted_since",
        )
    )


@dataclass(frozen=True)
class _StoredSyncProfile:
    profile_id: str
    profile_signature: str
    materialization_signature: str | None
    revision: int
    last_version: int | None
    requires_full_sync: bool


@dataclass(frozen=True)
class _SourceMaterializationState:
    signatures: frozenset[str]
    has_materialized_items: bool


def _validate_incremental_request_options(
    *,
    limit: int | None,
    since_version: int | None,
) -> None:
    if limit is not None and (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_SYNC_RESULT_LIMIT
    ):
        raise ZoteroFilterError(
            f"--limit must be between 1 and {MAX_SYNC_RESULT_LIMIT}."
        )
    if since_version is not None and (
        isinstance(since_version, bool)
        or not isinstance(since_version, int)
        or since_version < 0
    ):
        raise ZoteroFilterError("--since-version must be a non-negative integer.")


def _registered_profile_filters(
    *,
    collection: str | None,
    tags: tuple[str, ...],
    item_types: tuple[str, ...],
    include_status: str,
    pdf_policy: str,
) -> dict[str, object]:
    filters: dict[str, object] = {
        "include_status": include_status,
        "pdf_policy": pdf_policy,
    }
    if collection:
        filters["collections"] = [collection]
    if tags:
        filters["tags"] = list(tags)
    if item_types:
        filters["item_types"] = list(item_types)
    return filters


def _same_zotero_locator_config(
    stored: dict[str, object], requested: dict[str, object]
) -> bool:
    return all(
        stored.get(name) == requested.get(name)
        for name in ("local_api_url", "data_dir", "library_id", "library_type")
    )


def _zotero_source_has_persisted_evidence(
    connection: sqlite3.Connection,
    *,
    source_id: str,
) -> bool:
    """Return whether a cursorless source already owns durable local state."""

    corpus_id = stable_zotero_corpus_id(source_id)
    row = connection.execute(
        """
        SELECT 1 FROM zotero_sync_profiles WHERE source_id = ?
        UNION ALL SELECT 1 FROM zotero_items WHERE source_id = ?
        UNION ALL SELECT 1 FROM zotero_collections WHERE source_id = ?
        UNION ALL SELECT 1 FROM zotero_attachments WHERE source_id = ?
        UNION ALL SELECT 1 FROM zotero_child_items WHERE source_id = ?
        UNION ALL SELECT 1 FROM zotero_tombstones WHERE source_id = ?
        UNION ALL
          SELECT 1
          FROM jobs AS job
          JOIN registered_sources AS profile ON profile.id = job.source_id
          WHERE profile.zotero_source_id = ?
        UNION ALL SELECT 1 FROM documents WHERE corpus_id = ?
        UNION ALL SELECT 1 FROM scan_runs WHERE corpus_id = ?
        UNION ALL
          SELECT 1 FROM zotero_import_runs
          WHERE source_id = ? AND status = 'running'
        LIMIT 1
        """,
        (
            source_id,
            source_id,
            source_id,
            source_id,
            source_id,
            source_id,
            source_id,
            corpus_id,
            corpus_id,
            source_id,
        ),
    ).fetchone()
    return row is not None


def _transactional_zotero_locator_claim_is_unverified(
    connection: sqlite3.Connection,
    *,
    requested_source_id: str,
    requested_config: dict[str, object],
) -> bool:
    """Recheck the project-wide locator after acquiring the initial write lock."""

    sources = connection.execute(
        """
        SELECT id, source_type, local_api_url, data_dir, library_id,
               library_type, last_version
        FROM zotero_sources
        ORDER BY id
        """
    ).fetchall()
    profiles = connection.execute(
        """
        SELECT zotero_source_id, config_json
        FROM registered_sources
        WHERE kind = 'zotero_profile' AND removed_at IS NULL
        ORDER BY id
        """
    ).fetchall()
    active_by_source: dict[str, list[dict[str, object]]] = {}
    for profile in profiles:
        profile_source_id = str(profile["zotero_source_id"])
        stored = load_json_object(profile["config_json"])
        active_by_source.setdefault(profile_source_id, []).append(stored)
        if not _same_zotero_locator_config(stored, requested_config):
            raise ValueError(
                "The requested local Zotero locator changed while registration "
                "was starting; the established locator was preserved. Retry "
                "with that locator or use a separate project."
            )

    requested_source: sqlite3.Row | None = None
    requested_has_evidence = False
    for source in sources:
        source_id = str(source["id"])
        if source_id == requested_source_id:
            requested_source = source
        has_evidence = _zotero_source_has_persisted_evidence(
            connection,
            source_id=source_id,
        )
        if source_id == requested_source_id:
            requested_has_evidence = has_evidence
        established = (
            source["last_version"] is not None
            or bool(active_by_source.get(source_id))
            or has_evidence
        )
        if not established:
            continue
        if source_id != requested_source_id:
            raise ValueError(
                "The established local Zotero source identity differs from the "
                "requested locator. Refusing to split one project across source "
                "identities; migrate it explicitly or use a separate project."
            )
        source_locator = {
            name: source[name]
            for name in (
                "local_api_url",
                "data_dir",
                "library_id",
                "library_type",
            )
        }
        if source["source_type"] != "local_api" or not _same_zotero_locator_config(
            source_locator,
            requested_config,
        ):
            raise ValueError(
                "The requested local Zotero locator changed while registration "
                "was starting; the established locator was preserved. Retry "
                "with that locator or use a separate project."
            )

    return requested_source is None or (
        requested_source["last_version"] is None
        and not active_by_source.get(requested_source_id)
        and not requested_has_evidence
    )


def _profile_filter_semantics_equal(left: object, right: object) -> bool:
    return _canonical_profile_filter_semantics(
        left
    ) == _canonical_profile_filter_semantics(right)


def _canonical_profile_filter_semantics(value: object) -> tuple[object, ...]:
    if not isinstance(value, dict):
        raise RuntimeError("Stored Zotero profile filters are malformed.")
    extras = set(value) - {
        "collections",
        "tags",
        "item_types",
        "include_status",
        "pdf_policy",
    }
    if extras:
        raise RuntimeError("Stored Zotero profile filters are malformed.")

    def sequence(name: str) -> tuple[str, ...]:
        raw = value.get(name, [])
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise RuntimeError("Stored Zotero profile filters are malformed.")
        return tuple(sorted(set(raw), key=lambda item: (item.casefold(), item)))

    include_status = value.get("include_status", "all")
    pdf_policy = value.get("pdf_policy", "extract")
    if not isinstance(include_status, str) or not isinstance(pdf_policy, str):
        raise RuntimeError("Stored Zotero profile filters are malformed.")
    return (
        sequence("collections"),
        sequence("tags"),
        sequence("item_types"),
        include_status,
        pdf_policy,
    )


def _read_incremental_profile_state(
    *,
    project_dir: Path,
    source_id: str,
    requested_profile: ZoteroProfileRegistration,
    registered_profile_id: str | None,
) -> tuple[ZoteroProfileRegistration, _StoredSyncProfile | None]:
    """Read an existing profile/cursor without creating project state."""

    from paper_galaxy.services.sources import ZoteroProfileRegistration

    database_path = resolve_database_path(project_dir)
    if not database_path.is_file():
        if registered_profile_id is not None:
            raise RuntimeError("Queued Zotero job profile no longer exists.")
        return requested_profile, None
    connection = connect_read_only(project_dir)
    try:
        source_rows = connection.execute(
            """
            SELECT id, local_api_url, data_dir, library_id, library_type
            FROM zotero_sources AS source
            WHERE source.source_type = 'local_api'
              AND (
                source.last_version IS NOT NULL
                OR EXISTS (
                  SELECT 1 FROM registered_sources AS profile
                  WHERE profile.zotero_source_id = source.id
                    AND profile.kind = 'zotero_profile'
                    AND profile.removed_at IS NULL
                )
              )
            ORDER BY id
            """
        ).fetchall()
        requested_config = requested_profile.config
        requested_api = requested_config.get("local_api_url")
        requested_data_dir = requested_config.get("data_dir")
        matching_locator = [
            row
            for row in source_rows
            if str(row["id"]) == source_id
            and row["local_api_url"] == requested_api
            and row["data_dir"] == requested_data_dir
            and row["library_id"] == requested_config.get("library_id")
            and row["library_type"] == requested_config.get("library_type")
        ]
        if source_rows and not matching_locator:
            raise ValueError(
                "The requested local Zotero locator differs from the one already "
                "registered for this project. Keep the existing locator, or "
                "register the intended local profile in a separate explicit step."
            )
        rows = connection.execute(
            """
            SELECT id, display_name, zotero_source_id, profile_signature,
                   config_json
            FROM registered_sources
            WHERE kind = 'zotero_profile' AND zotero_source_id = ?
              AND removed_at IS NULL
            ORDER BY id
            """,
            (source_id,),
        ).fetchall()
        requested_filters = requested_profile.config.get("filters", {})
        candidates: list[sqlite3.Row] = []
        for row in rows:
            config = load_json_object(row["config_json"])
            if not _same_zotero_locator_config(config, requested_profile.config):
                continue
            if not _profile_filter_semantics_equal(
                config.get("filters", {}), requested_filters
            ):
                continue
            candidates.append(row)
        if registered_profile_id is not None:
            candidates = [
                row for row in candidates if str(row["id"]) == registered_profile_id
            ]
        else:
            exact = [
                row for row in candidates if str(row["id"]) == requested_profile.id
            ]
            if exact:
                candidates = exact
        if not candidates:
            if registered_profile_id is not None:
                raise RuntimeError(
                    "Queued Zotero job profile does not match the requested "
                    "locator and filter identity."
                )
            return requested_profile, None
        if len(candidates) > 1:
            raise RuntimeError(
                "More than one semantically equivalent Zotero profile matched; "
                "select the registered source id explicitly."
            )
        source = candidates[0]
        stored_config = load_json_object(source["config_json"])
        effective_profile = ZoteroProfileRegistration(
            id=str(source["id"]),
            profile_signature=str(source["profile_signature"]),
            zotero_source_id=source_id,
            display_name=str(source["display_name"]),
            config=stored_config,
        )
        profile_id = str(source["id"])
        profile_row = connection.execute(
            """
            SELECT materialization_signature, revision, last_version,
                   requires_full_sync
            FROM zotero_sync_profiles
            WHERE id = ? AND source_id = ? AND profile_signature = ?
            """,
            (profile_id, source_id, str(source["profile_signature"])),
        ).fetchone()
        if profile_row is None:
            return (
                effective_profile,
                _StoredSyncProfile(
                    profile_id=profile_id,
                    profile_signature=str(source["profile_signature"]),
                    materialization_signature=None,
                    revision=0,
                    last_version=None,
                    requires_full_sync=True,
                ),
            )
        return (
            effective_profile,
            _StoredSyncProfile(
                profile_id=profile_id,
                profile_signature=str(source["profile_signature"]),
                materialization_signature=(
                    str(profile_row["materialization_signature"])
                    if profile_row["materialization_signature"] is not None
                    else None
                ),
                revision=_profile_revision(profile_row["revision"]),
                last_version=_optional_profile_version(profile_row["last_version"]),
                requires_full_sync=bool(profile_row["requires_full_sync"]),
            ),
        )
    finally:
        connection.close()


def _read_source_materialization_state(
    project_dir: Path,
    *,
    source_id: str,
) -> _SourceMaterializationState:
    """Inspect source-global materialization state without creating SQLite state."""

    database_path = resolve_database_path(project_dir)
    if not database_path.is_file():
        return _SourceMaterializationState(frozenset(), False)
    connection = connect_read_only(project_dir)
    try:
        rows = connection.execute(
            """
            SELECT DISTINCT profile.materialization_signature
            FROM zotero_sync_profiles AS profile
            JOIN registered_sources AS source ON source.id = profile.id
            WHERE profile.source_id = ?
              AND profile.materialization_signature IS NOT NULL
              AND source.kind = 'zotero_profile'
              AND source.removed_at IS NULL
            ORDER BY materialization_signature
            """,
            (source_id,),
        ).fetchall()
        item_row = connection.execute(
            "SELECT 1 FROM zotero_items WHERE source_id = ? LIMIT 1",
            (source_id,),
        ).fetchone()
    finally:
        connection.close()
    return _SourceMaterializationState(
        frozenset(str(row[0]) for row in rows),
        item_row is not None,
    )


def _zotero_materialization_signature(
    *,
    data_dir: Path | None,
    include_pdfs: bool,
    include_notes: bool,
    include_attachments: bool,
    include_metadata_only: bool,
    pdf_policy: str,
    read_tags: tuple[str, ...],
    reading_tags: tuple[str, ...],
    to_read_tags: tuple[str, ...],
    min_chars: int,
    chunk_size: int,
    chunk_overlap: int,
) -> str:
    """Hash every option that can change shared Zotero document materialization."""

    def normalized(values: tuple[str, ...]) -> list[str]:
        # Keep this identical to reading._matches/infer_reading_status semantics.
        return sorted({value.lower() for value in values})

    payload = {
        "algorithm": "paper-galaxy-zotero-materialization-v1",
        "attachment_root": (
            str(data_dir.expanduser().resolve(strict=False)) if data_dir else None
        ),
        "chunk_overlap": chunk_overlap,
        "chunk_size": chunk_size,
        "include_attachments": include_attachments,
        "include_metadata_only": include_metadata_only,
        "include_notes": include_notes,
        "include_pdfs": include_pdfs,
        "min_chars": min_chars,
        "pdf_policy": pdf_policy,
        "read_tags": normalized(read_tags),
        "reading_tags": normalized(reading_tags),
        "to_read_tags": normalized(to_read_tags),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_materialization_change(
    requested: str,
    *,
    source_state: _SourceMaterializationState,
    full: bool,
) -> None:
    existing = source_state.signatures
    if len(existing) > 1 and not full:
        raise RuntimeError(
            "Zotero source has inconsistent materialization settings; run an "
            "explicit --full sync to repair it."
        )
    if existing and existing != {requested} and not full:
        raise RuntimeError(
            "Zotero materialization settings changed. Re-run with --full to "
            "rebuild the shared local documents safely."
        )


def load_zotero_materialization_config(
    project_dir: Path,
    *,
    profile_id: str,
) -> dict[str, object]:
    """Load the latest completed canonical config for a source generation.

    A newly registered filter profile may not have a run yet, so peers sharing
    its source-global materialization signature are valid configuration donors.
    The stored signature is checked before the job is allowed to reuse values.
    """

    database_path = resolve_database_path(project_dir)
    if not database_path.is_file():
        return {}
    connection = connect_read_only(project_dir)
    try:
        rows = connection.execute(
            """
            SELECT run.config_json, peer.materialization_signature
            FROM registered_sources target
            JOIN zotero_sync_profiles peer
              ON peer.source_id = target.zotero_source_id
            JOIN registered_sources peer_source
              ON peer_source.id = peer.id
             AND peer_source.kind = 'zotero_profile'
            JOIN zotero_import_runs run ON run.id = peer.last_run_id
            WHERE target.id = ?
              AND target.kind = 'zotero_profile'
              AND target.removed_at IS NULL
              AND peer.materialization_signature IS NOT NULL
              AND run.status = 'completed'
            ORDER BY peer.last_sync_at DESC, peer.id
            """,
            (profile_id,),
        ).fetchall()
    finally:
        connection.close()
    for row in rows:
        config = load_json_object(row["config_json"])
        signature = row["materialization_signature"]
        if config.get("materialization_signature") != signature:
            continue
        return {
            key: config[key]
            for key in (
                "include_pdfs",
                "include_notes",
                "include_attachments",
                "include_metadata_only",
                "read_tags",
                "reading_tags",
                "to_read_tags",
                "min_chars",
                "chunk_size",
                "chunk_overlap",
            )
            if key in config
        }
    return {}


def _validate_explicit_since_version(
    since_version: int | None,
    *,
    profile: _StoredSyncProfile | None,
) -> None:
    if since_version is None:
        return
    if profile is None or profile.requires_full_sync:
        if since_version != 0:
            raise ZoteroFilterError(
                "A new or full-sync-required Zotero profile must start at "
                "--since-version 0 (or omit the option)."
            )
        return
    if profile.last_version is None:
        raise RuntimeError("Stored Zotero profile cursor is missing.")
    if since_version != profile.last_version:
        raise ZoteroFilterError(
            "--since-version must exactly match this profile's saved cursor "
            f"({profile.last_version}); use --full for reconciliation."
        )


def _api_cancel_callback(
    cancel_requested: Callable[[], bool] | None,
) -> Callable[[], bool] | None:
    if cancel_requested is None:
        return None

    def check() -> bool:
        _raise_if_zotero_cancelled(cancel_requested)
        return False

    return check


def _import_incremental_from_zotero(
    *,
    project_dir: Path,
    database_path: Path,
    api_url: str,
    data_dir: Path | None,
    client: ZoteroClient,
    source_id: str,
    source_corpus_id: str,
    run_id: str,
    started_at: str,
    collection: str | None,
    tags: tuple[str, ...],
    item_types: tuple[str, ...],
    include_pdfs: bool,
    include_notes: bool,
    include_attachments: bool,
    include_metadata_only: bool,
    pdf_policy: str,
    read_tags: tuple[str, ...],
    reading_tags: tuple[str, ...],
    to_read_tags: tuple[str, ...],
    include_status: str,
    limit: int | None,
    since_version: int | None,
    full: bool,
    force: bool,
    dry_run: bool,
    build_reading_map: bool,
    map_name: str,
    min_chars: int,
    chunk_size: int,
    chunk_overlap: int,
    cancel_requested: Callable[[], bool] | None,
    commit_guard: Callable[[], None] | None,
    final_commit_guard: Callable[[sqlite3.Connection], None] | None,
    progress_callback: Callable[[int, int, str], None] | None,
    warnings: list[str],
    filters: dict[str, object],
    requested_profile: ZoteroProfileRegistration,
    preflight_profile: _StoredSyncProfile | None,
    materialization_signature: str,
    rematerialize_source: bool,
) -> ZoteroImportRunSummary:
    """Apply one profile-scoped, version-fenced read-only Zotero sync."""

    started_monotonic = time.monotonic()
    counts = _ImportCounts(items_seen=0)
    connection: sqlite3.Connection | None = None
    repository: Repository | None = None
    profile_id: str | None = None
    profile_signature: str | None = None
    profile_revision = 0
    profile_materialization_before: str | None = None
    profile_preexisting = False
    last_version_before: int | None = None
    response_version: int | None = None
    effective_full = full
    changed_parent_count = 0
    changed_child_count = 0
    deleted_record_count = 0
    items_fetched = 0
    selected_collection: CollectionSelection | None = None
    selected: list[tuple[ZoteroItem, str]] = []
    collections: list[ZoteroCollection] = []
    collection_paths_by_key: dict[str, str] = {}
    collection_id_by_key: dict[str, str] = {}
    complete_fetch = True
    atomic_materialization_publish = rematerialize_source
    prepared_external_by_key: dict[str, _PreparedZoteroExternal] = {}
    atomic_counts_before_publish: dict[str, object] | None = None
    atomic_warnings_before_publish: int | None = None
    run_created = False
    source_claim_unverified = False
    run_config: dict[str, object] = {
        "include_pdfs": include_pdfs,
        "include_notes": include_notes,
        "include_attachments": include_attachments,
        "include_metadata_only": include_metadata_only,
        "pdf_policy": pdf_policy,
        "read_tags": list(read_tags),
        "reading_tags": list(reading_tags),
        "to_read_tags": list(to_read_tags),
        "include_status": include_status,
        "min_chars": min_chars,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "limit": limit,
        "since_version": since_version,
        "full": full,
        "force": force,
        "filters": filters,
        "requested_collection": collection,
        "materialization_signature": materialization_signature,
    }

    try:
        if dry_run:
            profile_id = requested_profile.id
            profile_signature = requested_profile.profile_signature
            profile_revision = (
                preflight_profile.revision if preflight_profile is not None else 0
            )
            profile_materialization_before = (
                preflight_profile.materialization_signature
                if preflight_profile is not None
                else None
            )
            last_version_before = (
                preflight_profile.last_version
                if preflight_profile is not None
                else None
            )
            requires_full = (
                preflight_profile.requires_full_sync
                if preflight_profile is not None
                else True
            )
            profile_preexisting = preflight_profile is not None
        else:
            ensure_database_ready(project_dir)
            _raise_if_zotero_cancelled(cancel_requested)
            connection = connect_read_write(project_dir)
            repository = Repository(connection, database_path)
            connection.execute("BEGIN IMMEDIATE")
            try:
                source_claim_unverified = (
                    _transactional_zotero_locator_claim_is_unverified(
                        connection,
                        requested_source_id=source_id,
                        requested_config=requested_profile.config,
                    )
                )
                repository.upsert_corpus(
                    source_corpus_id,
                    f"zotero://sources/{source_id}",
                    started_at,
                )
                repository.upsert_zotero_source(
                    _zotero_source_payload(
                        source_id=source_id,
                        api_url=api_url,
                        data_dir=data_dir,
                        last_version=None,
                        created_at=started_at,
                        updated_at=started_at,
                    )
                )
                profile_created = repository.ensure_registered_zotero_profile(
                    profile_id=requested_profile.id,
                    source_id=source_id,
                    profile_signature=requested_profile.profile_signature,
                    display_name=requested_profile.display_name,
                    config=requested_profile.config,
                    now=started_at,
                )
                state = repository.ensure_zotero_sync_profile(
                    profile_id=requested_profile.id,
                    source_id=source_id,
                    profile_signature=requested_profile.profile_signature,
                    now=started_at,
                )
                profile_id = requested_profile.id
                profile_signature = requested_profile.profile_signature
                profile_revision = _profile_revision(state.get("revision"))
                raw_materialization = state.get("materialization_signature")
                profile_materialization_before = (
                    str(raw_materialization)
                    if raw_materialization is not None
                    else None
                )
                last_version_before = _optional_profile_version(
                    state.get("last_version")
                )
                requires_full = bool(state["requires_full_sync"])
                profile_preexisting = not profile_created
                if preflight_profile is not None and (
                    profile_id != preflight_profile.profile_id
                    or profile_signature != preflight_profile.profile_signature
                ):
                    raise RuntimeError(
                        "Zotero sync profile identity changed during preflight."
                    )
            except BaseException:
                connection.rollback()
                raise
        _validate_explicit_since_version(
            since_version,
            profile=_StoredSyncProfile(
                profile_id=profile_id,
                profile_signature=profile_signature,
                materialization_signature=materialization_signature,
                revision=profile_revision,
                last_version=last_version_before,
                requires_full_sync=requires_full,
            ),
        )
        if requires_full:
            effective_full = True
        effective_since = (
            since_version
            if since_version is not None
            else 0
            if effective_full or last_version_before is None
            else last_version_before
        )
        run_config.update(
            {
                "profile_id": profile_id,
                "profile_signature": profile_signature,
                "effective_since_version": effective_since,
                "effective_full": effective_full,
            }
        )
        if connection is not None and repository is not None:
            try:
                repository.create_zotero_import_run(
                    run_id,
                    source_id,
                    started_at=started_at,
                    config=run_config,
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            run_created = True

        _raise_if_zotero_cancelled(cancel_requested)
        api_cancel_requested = _api_cancel_callback(cancel_requested)
        collections_batch = client.sync_collections(
            cancel_requested=api_cancel_requested
        )
        collections = [normalize_collection(row) for row in collections_batch.records]
        collection_paths_by_key = collection_paths(collections)
        selected_collection = _resolve_incremental_collection_filter(
            collections=collections,
            collection=collection,
            project_dir=project_dir,
            source_id=source_id,
            profile_id=profile_id,
            profile_preexisting=profile_preexisting,
            connection=repository.connection if repository is not None else None,
        )
        collection_id_by_key = {
            row.key: stable_zotero_collection_id(source_id, row.key)
            for row in collections
        }
        run_config["selected_collection"] = _collection_payload(selected_collection)
        if connection is not None and repository is not None:
            with connection:
                repository.update_zotero_import_run_config(run_id, run_config)

        _raise_if_zotero_cancelled(cancel_requested)
        item_batch = client.sync_items(
            since=effective_since,
            limit=limit,
            cancel_requested=api_cancel_requested,
        )
        _raise_if_zotero_cancelled(cancel_requested)
        deleted_batch = client.deleted_since(
            since=effective_since,
            cancel_requested=api_cancel_requested,
        )
        response_version = _consistent_sync_version(
            collections_batch.library_version,
            item_batch.library_version,
            deleted_batch.library_version,
        )
        if repository is not None:
            repository.assert_zotero_source_version_fence(
                source_id=source_id,
                response_version=response_version,
            )
        if last_version_before is not None and response_version < last_version_before:
            raise RuntimeError(
                "Zotero library version moved backward; no profile cursor was advanced."
            )
        complete_fetch = item_batch.complete
        if not complete_fetch:
            if atomic_materialization_publish:
                warnings.append(
                    "Zotero full materialization response was incomplete; no "
                    "fetched rows or generation changes were published."
                )
            else:
                warnings.append(
                    "Zotero sync result was limited or incomplete; imported rows "
                    "were kept, but the profile cursor was not advanced."
                )

        parent_rows, child_rows = _partition_zotero_sync_rows(item_batch.records)
        changed_parent_count = len(parent_rows)
        changed_child_count = len(child_rows)
        items_fetched = len(item_batch.records)
        deleted_record_count = sum(
            len(keys) for keys in deleted_batch.object_keys.values()
        )

        existing_parent_keys: set[str] = set()
        deleted_child_parents: dict[str, str] = {}
        if repository is not None:
            existing_parent_keys = {
                str(row[0])
                for row in repository.connection.execute(
                    "SELECT zotero_key FROM zotero_items WHERE source_id = ?",
                    (source_id,),
                ).fetchall()
            }
            deleted_child_parents = repository.verified_child_deletions(
                source_id,
                deleted_batch.object_keys.get("items", ()),
                library_version=response_version,
            )
        deleted_item_keys = set(deleted_batch.object_keys.get("items", ()))
        deleted_parent_keys = deleted_item_keys & existing_parent_keys
        verified_deleted_child_keys = set(deleted_child_parents) - deleted_parent_keys
        collection_refresh_parent_keys: set[str] = set()
        if repository is not None:
            changed_collection_keys = _changed_collection_keys(
                repository.connection,
                source_id=source_id,
                current=collections,
                deleted=deleted_batch.object_keys.get("collections", ()),
            )
            collection_refresh_parent_keys = _parent_keys_for_collections(
                repository.connection,
                source_id=source_id,
                collection_keys=changed_collection_keys,
            )

        changed_children_by_parent = _children_by_parent(child_rows)
        parents_by_key = {
            str(row.get("key") or _payload_data(row).get("key") or ""): row
            for row in parent_rows
        }
        parent_keys_to_hydrate = (
            (
                set(changed_children_by_parent)
                | collection_refresh_parent_keys
                | {
                    parent
                    for key, parent in deleted_child_parents.items()
                    if key in verified_deleted_child_keys
                }
            )
            - set(parents_by_key)
            - deleted_parent_keys
        )
        if parent_keys_to_hydrate:
            _raise_if_zotero_cancelled(cancel_requested)
            hydrated = client.items_by_keys(
                tuple(sorted(parent_keys_to_hydrate)),
                cancel_requested=api_cancel_requested,
            )
            response_version = _consistent_sync_version(
                response_version, hydrated.library_version
            )
            hydrated_parents, unexpected_children = _partition_zotero_sync_rows(
                hydrated.records
            )
            if unexpected_children:
                raise ZoteroVersionConflictError(
                    "unexpected hydrated child",
                    str(unexpected_children[0].get("key", "")),
                )
            parents_by_key.update(
                {
                    str(row.get("key") or _payload_data(row).get("key") or ""): row
                    for row in hydrated_parents
                }
            )
            missing = parent_keys_to_hydrate - set(parents_by_key)
            recoverable = missing & collection_refresh_parent_keys
            if recoverable and repository is not None:
                stored_rows = repository.connection.execute(
                    f"""
                    SELECT zotero_key, data_json
                    FROM zotero_items
                    WHERE source_id = ?
                      AND zotero_key IN ({",".join("?" for _ in recoverable)})
                      AND deleted_at IS NULL
                    ORDER BY zotero_key
                    """,
                    (source_id, *sorted(recoverable)),
                ).fetchall()
                parents_by_key.update(
                    {
                        str(row["zotero_key"]): load_json_object(row["data_json"])
                        for row in stored_rows
                    }
                )
                missing = parent_keys_to_hydrate - set(parents_by_key)
            if missing:
                raise ZoteroVersionConflictError(
                    "missing parent", ",".join(sorted(missing))
                )

        child_payloads_by_parent: dict[str, list[dict[str, object]]] = {}
        if repository is not None and not effective_full:
            cached = repository.list_zotero_child_payloads(
                source_id, parents_by_key.keys()
            )
            child_payloads_by_parent = _children_by_parent(cached)
        for parent_key, rows in changed_children_by_parent.items():
            indexed = {
                _payload_key(row): row
                for row in child_payloads_by_parent.get(parent_key, [])
            }
            indexed.update({_payload_key(row): row for row in rows})
            child_payloads_by_parent[parent_key] = [
                indexed[key] for key in sorted(indexed)
            ]
        for child_key in verified_deleted_child_keys:
            parent_key = deleted_child_parents[child_key]
            child_payloads_by_parent[parent_key] = [
                row
                for row in child_payloads_by_parent.get(parent_key, [])
                if _payload_key(row) != child_key
            ]

        enriched_items: list[ZoteroItem] = []
        active_collection_keys = set(collection_id_by_key)
        deleted_collection_keys = set(deleted_batch.object_keys.get("collections", ()))
        for parent_key in sorted(parents_by_key):
            parent = normalize_item(parents_by_key[parent_key])
            unknown_collection_keys = set(parent.collections) - active_collection_keys
            if unknown_collection_keys - deleted_collection_keys:
                raise ZoteroVersionConflictError(
                    "missing collection",
                    ",".join(sorted(unknown_collection_keys)),
                )
            if unknown_collection_keys:
                parent = replace(
                    parent,
                    collections=tuple(
                        key
                        for key in parent.collections
                        if key in active_collection_keys
                    ),
                )
            children = [
                child
                for child in (
                    normalize_child(row)
                    for row in child_payloads_by_parent.get(parent_key, [])
                )
                if child is not None
            ]
            enriched_items.append(attach_children(parent, children))
        filtered_items = filter_items(
            enriched_items,
            collection_key=selected_collection.key if selected_collection else None,
            tags=tags,
            item_types=item_types,
        )
        statuses_by_key = {
            item.key: infer_reading_status(
                item,
                collection_names=[
                    collection_paths_by_key.get(key, key) for key in item.collections
                ],
                read_tags=read_tags,
                reading_tags=reading_tags,
                to_read_tags=to_read_tags,
            )
            for item in enriched_items
        }
        selected = [
            (item, statuses_by_key[item.key])
            for item in filtered_items
            if include_status == "all" or statuses_by_key[item.key] == include_status
        ]
        selected_keys = {item.key for item, _ in selected}
        items_to_process = list(selected)
        items_to_process.extend(
            (item, statuses_by_key[item.key])
            for item in enriched_items
            if item.key in existing_parent_keys and item.key not in selected_keys
        )
        selected.sort(key=lambda row: row[0].key)
        items_to_process.sort(key=lambda row: row[0].key)
        counts.items_seen = len(enriched_items)

        if dry_run:
            duration = max(0.0, time.monotonic() - started_monotonic)
            return ZoteroImportRunSummary(
                run_id=run_id,
                source_id=source_id,
                project_dir=project_dir,
                database_path=database_path,
                dry_run=True,
                items_seen=counts.items_seen,
                items_fetched=items_fetched,
                items_selected=len(selected),
                items_filtered_out=max(0, len(enriched_items) - len(selected)),
                filters=filters,
                selected_collection=_collection_payload(selected_collection),
                include_status=include_status,
                since_version=since_version,
                last_version_before=last_version_before,
                last_version_after=response_version
                if complete_fetch
                else last_version_before,
                warnings=tuple(warnings),
                reading_status_counts=reading_status_counts(
                    [item for item, _ in selected],
                    [status for _, status in selected],
                ),
                profile_id=profile_id,
                profile_signature=profile_signature,
                full_sync=effective_full,
                changed_parents=changed_parent_count,
                changed_children=changed_child_count,
                deleted_records=deleted_record_count,
                duration_seconds=duration,
            )

        assert connection is not None and repository is not None
        assert profile_id is not None
        if atomic_materialization_publish and not complete_fetch:
            raise RuntimeError(
                "Incomplete full Zotero response; the existing materialization "
                "generation was preserved and no fetched rows were published."
            )
        if atomic_materialization_publish:
            preparation_total = len(items_to_process)
            for item_index, (item, _status) in enumerate(items_to_process):
                _raise_if_zotero_cancelled(cancel_requested)
                if progress_callback is not None:
                    progress_callback(
                        item_index,
                        preparation_total,
                        "Preparing local Zotero item "
                        f"{item_index + 1} of {preparation_total} outside the "
                        "atomic publish transaction.",
                    )
                prepared_external_by_key[item.key] = _prepare_zotero_external(
                    item=item,
                    source_id=source_id,
                    source_corpus_id=source_corpus_id,
                    collection_names=[
                        collection_paths_by_key.get(key, key)
                        for key in item.collections
                    ],
                    data_dir=data_dir,
                    include_pdfs=include_pdfs,
                    include_notes=include_notes,
                    include_attachments=include_attachments,
                    include_metadata_only=include_metadata_only,
                    pdf_policy=pdf_policy,
                    min_chars=min_chars,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    now=_utc_now(),
                )
                _raise_if_zotero_cancelled(cancel_requested)
            atomic_counts_before_publish = counts.snapshot()
            atomic_warnings_before_publish = len(warnings)
            if progress_callback is not None:
                progress_callback(
                    preparation_total,
                    preparation_total,
                    "Publishing the prepared Zotero generation atomically.",
                )
        _raise_if_zotero_cancelled(cancel_requested)
        connection.execute("BEGIN IMMEDIATE")
        try:
            repository.assert_zotero_source_version_fence(
                source_id=source_id,
                response_version=response_version,
            )
            repository.assert_zotero_sync_profile_preparation_fence(
                profile_id=profile_id,
                expected_revision=profile_revision,
                expected_last_version=last_version_before,
                expected_materialization_signature=profile_materialization_before,
            )
            prepared_state = repository.prepare_zotero_sync_profile_materialization(
                profile_id=profile_id,
                materialization_signature=materialization_signature,
                now=_utc_now(),
                explicit_full=full,
            )
            prepared_last_version = _optional_profile_version(
                prepared_state.get("last_version")
            )
            if prepared_last_version != last_version_before:
                raise RuntimeError(
                    "Zotero profile cursor changed during generation preparation."
                )
            profile_revision = _profile_revision(prepared_state.get("revision"))
            requires_full = bool(prepared_state["requires_full_sync"])
            effective_full = effective_full or requires_full
            run_config["effective_full"] = effective_full
            repository.update_zotero_import_run_config(run_id, run_config)
            if not atomic_materialization_publish:
                connection.commit()
        except BaseException:
            connection.rollback()
            raise

        with _zotero_sync_write_scope(
            connection,
            atomic=atomic_materialization_publish,
        ):
            repository.assert_zotero_source_version_fence(
                source_id=source_id,
                response_version=response_version,
            )
            for collection_row in collections:
                _raise_if_zotero_cancelled(cancel_requested)
                accepted = repository.upsert_zotero_collection(
                    {
                        "id": collection_id_by_key[collection_row.key],
                        "source_id": source_id,
                        "zotero_key": collection_row.key,
                        "parent_key": collection_row.parent_key,
                        "name": collection_row.name,
                        "path": collection_paths_by_key.get(collection_row.key),
                        "version": collection_row.version,
                        "data": collection_row.raw,
                    }
                )
                if not accepted:
                    raise ZoteroVersionConflictError("collection", collection_row.key)
                repository.clear_zotero_tombstone(
                    source_id=source_id,
                    object_type="collection",
                    zotero_key=collection_row.key,
                )

        for child_row in child_rows:
            _raise_if_zotero_cancelled(cancel_requested)
            child = normalize_child(child_row)
            if child is None or child.parent_key is None:
                raise ZoteroVersionConflictError(
                    "invalid child", _payload_key(child_row)
                )
            with _zotero_sync_write_scope(
                connection,
                atomic=atomic_materialization_publish,
            ):
                repository.assert_zotero_source_version_fence(
                    source_id=source_id,
                    response_version=response_version,
                )
                accepted = repository.upsert_zotero_child_item(
                    source_id=source_id,
                    zotero_key=child.key,
                    parent_key=child.parent_key,
                    item_type=_child_kind(child),
                    version=child.version,
                    data=child.raw,
                    now=_utc_now(),
                )
                if not accepted:
                    raise ZoteroVersionConflictError("child", child.key)
                repository.clear_zotero_tombstone(
                    source_id=source_id,
                    object_type="item",
                    zotero_key=child.key,
                )

        deletion_time = _utc_now()
        with _zotero_sync_write_scope(
            connection,
            atomic=atomic_materialization_publish,
        ):
            repository.assert_zotero_source_version_fence(
                source_id=source_id,
                response_version=response_version,
            )
            for object_group, object_type in (
                ("items", "item"),
                ("collections", "collection"),
                ("searches", "search"),
                ("tags", "tag"),
                ("settings", "setting"),
            ):
                for key in deleted_batch.object_keys.get(object_group, ()):
                    _raise_if_zotero_cancelled(cancel_requested)
                    repository.record_zotero_tombstone(
                        source_id=source_id,
                        object_type=object_type,
                        zotero_key=key,
                        library_version=response_version,
                        deleted_at=deletion_time,
                    )
            for key in sorted(deleted_parent_keys):
                repository.mark_zotero_parent_deleted(
                    source_id=source_id,
                    zotero_key=key,
                    library_version=response_version,
                    deleted_at=deletion_time,
                )
            for key in sorted(verified_deleted_child_keys):
                repository.mark_zotero_child_deleted(
                    source_id=source_id,
                    zotero_key=key,
                    library_version=response_version,
                    deleted_at=deletion_time,
                )
            for key in deleted_batch.object_keys.get("collections", ()):
                repository.mark_zotero_collection_deleted(
                    source_id=source_id,
                    zotero_key=key,
                    library_version=response_version,
                    deleted_at=deletion_time,
                )

        selected_count = len(items_to_process)
        for item_index, (item, status) in enumerate(items_to_process):
            _raise_if_zotero_cancelled(cancel_requested)
            if progress_callback is not None and not atomic_materialization_publish:
                progress_callback(
                    item_index,
                    selected_count,
                    f"Syncing local Zotero item {item_index + 1} of {selected_count}.",
                )
            item_rematerialize = (
                rematerialize_source or item.key in collection_refresh_parent_keys
            )
            prepared_for_item = prepared_external_by_key.get(item.key)
            if prepared_for_item is None and _zotero_item_requires_external_preparation(
                repository,
                item=item,
                source_id=source_id,
                force=force,
                rematerialize=item_rematerialize,
                allow_unknown_child_state=effective_full,
                verified_deleted_child_keys=verified_deleted_child_keys,
            ):
                prepared_for_item = _prepare_zotero_external(
                    item=item,
                    source_id=source_id,
                    source_corpus_id=source_corpus_id,
                    collection_names=[
                        collection_paths_by_key.get(key, key)
                        for key in item.collections
                    ],
                    data_dir=data_dir,
                    include_pdfs=include_pdfs,
                    include_notes=include_notes,
                    include_attachments=include_attachments,
                    include_metadata_only=include_metadata_only,
                    pdf_policy=pdf_policy,
                    min_chars=min_chars,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    now=_utc_now(),
                )
            if atomic_materialization_publish:
                _raise_if_zotero_cancelled(cancel_requested)
            snapshot = counts.snapshot()
            try:
                with _zotero_sync_write_scope(
                    connection,
                    atomic=atomic_materialization_publish,
                ):
                    repository.assert_zotero_source_version_fence(
                        source_id=source_id,
                        response_version=response_version,
                    )
                    repository.assert_zotero_sync_profile_fence(
                        profile_id=profile_id,
                        expected_revision=profile_revision,
                        expected_last_version=last_version_before,
                        expected_materialization_signature=materialization_signature,
                    )
                    skipped_before = counts.skipped
                    accepted = _import_one_item(
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
                        now=_utc_now(),
                        counts=counts,
                        warnings=warnings,
                        commit_guard=commit_guard,
                        source_response_version=response_version,
                        verified_deleted_child_keys=verified_deleted_child_keys,
                        allow_unknown_child_state=effective_full,
                        rematerialize=item_rematerialize,
                        prepared_external=prepared_for_item,
                        external_preparation_complete=True,
                    )
                    if accepted:
                        repository.clear_zotero_tombstone(
                            source_id=source_id,
                            object_type="item",
                            zotero_key=item.key,
                        )
                    zotero_item_id = stable_zotero_item_id(source_id, item.key)
                    current_state = repository.get_zotero_item_sync_state(
                        source_id, item.key
                    )
                    current_version = _sync_state_version(current_state)
                    superseded = current_version is not None and (
                        item.version is None or current_version > item.version
                    )
                    if not superseded:
                        document = repository.get_document(
                            stable_zotero_document_id(source_id, item.key)
                        )
                        _reconcile_known_parent_profile_memberships(
                            repository=repository,
                            project_dir=project_dir,
                            source_id=source_id,
                            item=item,
                            reading_status=status,
                            collections=collections,
                            zotero_item_id=zotero_item_id,
                            observed_version=response_version,
                            current_profile_id=profile_id,
                            materialization_signature=materialization_signature,
                            can_materialize=(
                                counts.skipped == skipped_before
                                and document is not None
                            ),
                            now=_utc_now(),
                        )
                        repository.reconcile_zotero_document_activity(
                            (zotero_item_id,),
                            now=_utc_now(),
                        )
            except BaseException:
                counts.restore(snapshot)
                raise
            if not accepted:
                counts.restore(snapshot)
                counts.items_unchanged += 1

        _raise_if_zotero_cancelled(cancel_requested)
        if commit_guard is not None:
            commit_guard()
        finished_at = _utc_now()
        duration_ms = max(0, int((time.monotonic() - started_monotonic) * 1000))
        committed_version = last_version_before
        if not atomic_materialization_publish:
            connection.execute("BEGIN IMMEDIATE")
        try:
            if final_commit_guard is not None:
                final_commit_guard(connection)
            repository.assert_zotero_source_version_fence(
                source_id=source_id,
                response_version=response_version,
            )
            if complete_fetch:
                repository.complete_zotero_sync_profile(
                    profile_id=profile_id,
                    expected_last_version=last_version_before,
                    expected_revision=profile_revision,
                    expected_materialization_signature=materialization_signature,
                    last_version=response_version,
                    run_id=run_id,
                    now=finished_at,
                )
                repository.upsert_zotero_source(
                    _zotero_source_payload(
                        source_id=source_id,
                        api_url=api_url,
                        data_dir=data_dir,
                        last_version=response_version,
                        created_at=started_at,
                        updated_at=finished_at,
                    )
                )
                committed_version = response_version
                connection.execute(
                    """
                    UPDATE registered_sources
                    SET last_success_at = ?, last_error_code = NULL,
                        last_error_message = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (finished_at, finished_at, profile_id),
                )
            repository.record_zotero_sync_run_details(
                run_id=run_id,
                profile_id=profile_id,
                full_sync=effective_full,
                previous_version=last_version_before,
                response_version=response_version,
                committed_version=committed_version,
                changed_parents=changed_parent_count,
                changed_children=changed_child_count,
                deleted_records=deleted_record_count,
                metadata_only_documents=counts.metadata_only_documents,
                pdf_failures=counts.pdfs_extraction_failed,
                duration_ms=duration_ms,
            )
            repository.finish_zotero_import_run(
                run_id,
                finished_at=finished_at,
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
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    except BaseException as exc:
        if connection is not None and connection.in_transaction:
            connection.rollback()
        if atomic_counts_before_publish is not None:
            counts.restore(atomic_counts_before_publish)
        if atomic_warnings_before_publish is not None:
            del warnings[atomic_warnings_before_publish:]
        retire_unverified_claim = (
            connection is not None
            and repository is not None
            and run_created
            and source_claim_unverified
            and response_version is None
            and profile_id is not None
            and not isinstance(exc, (ZoteroImportCancelled, KeyboardInterrupt))
        )
        audit_persisted = False
        if connection is not None and repository is not None and run_created:
            try:
                with connection:
                    if profile_id is not None:
                        repository.record_zotero_sync_run_details(
                            run_id=run_id,
                            profile_id=profile_id,
                            full_sync=effective_full,
                            previous_version=last_version_before,
                            response_version=response_version,
                            committed_version=None,
                            changed_parents=changed_parent_count,
                            changed_children=changed_child_count,
                            deleted_records=deleted_record_count,
                            metadata_only_documents=counts.metadata_only_documents,
                            pdf_failures=counts.pdfs_extraction_failed,
                            duration_ms=max(
                                0,
                                int((time.monotonic() - started_monotonic) * 1000),
                            ),
                        )
                _mark_zotero_run_failed(
                    connection=connection,
                    repository=repository,
                    run_id=run_id,
                    counts=counts,
                    warnings=warnings,
                    error=exc,
                )
                audit_persisted = True
            except BaseException:
                pass
            if retire_unverified_claim and audit_persisted and profile_id is not None:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    retired = repository.retire_unverified_zotero_source_claim(
                        source_id=source_id,
                        profile_id=profile_id,
                        run_id=run_id,
                        corpus_id=source_corpus_id,
                        retired_at=_utc_now(),
                        error_code=type(exc).__name__,
                        error_message=_safe_error_message(exc),
                    )
                    if retired:
                        connection.commit()
                    else:
                        connection.rollback()
                except BaseException:
                    connection.rollback()
        elif connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()

    map_run_id: str | None = None
    if build_reading_map and selected:
        try:
            saved = build_and_store_zotero_reading_map(
                project_dir=project_dir,
                name=map_name,
                status=include_status,
                collection=collection,
                tag=tags[0] if tags else None,
            )
            map_run = saved.get("map_run")
            if isinstance(map_run, dict):
                map_run_id = str(map_run.get("id"))
        except Exception as exc:
            warnings.append(
                "Zotero sync completed, but the reading map build failed "
                f"({type(exc).__name__}). Run `paper-galaxy zotero graph` to retry."
            )

    duration = max(0.0, time.monotonic() - started_monotonic)
    return ZoteroImportRunSummary(
        run_id=run_id,
        source_id=source_id,
        project_dir=project_dir,
        database_path=database_path,
        dry_run=False,
        items_seen=counts.items_seen,
        items_fetched=items_fetched,
        items_selected=len(selected),
        items_filtered_out=max(0, counts.items_seen - len(selected)),
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
        last_version_after=response_version if complete_fetch else last_version_before,
        warnings=tuple(warnings),
        reading_status_counts=reading_status_counts(
            [item for item, _ in selected],
            [status for _, status in selected],
        ),
        map_run_id=map_run_id,
        profile_id=profile_id,
        profile_signature=profile_signature,
        full_sync=effective_full,
        changed_parents=changed_parent_count,
        changed_children=changed_child_count,
        deleted_records=deleted_record_count,
        duration_seconds=duration,
    )


def _reconcile_known_parent_profile_memberships(
    *,
    repository: Repository,
    project_dir: Path,
    source_id: str,
    item: ZoteroItem,
    reading_status: str,
    collections: list[ZoteroCollection],
    zotero_item_id: str,
    observed_version: int,
    current_profile_id: str,
    materialization_signature: str,
    can_materialize: bool,
    now: str,
) -> None:
    """Recompute one known-latest parent across every active filter profile.

    Another profile's cursor remains unchanged: only its versioned membership
    observation is refreshed from metadata already returned by Zotero at the
    current library version. This prevents an old positive match from keeping a
    document visible after its tags, collection, item type, or status changed.
    """

    rows = repository.connection.execute(
        """
        SELECT profile.id, profile.revision, profile.materialization_signature,
               source.config_json
        FROM zotero_sync_profiles AS profile
        JOIN registered_sources AS source ON source.id = profile.id
        WHERE profile.source_id = ?
          AND profile.materialization_signature = ?
          AND (profile.id = ? OR profile.requires_full_sync = 0)
          AND source.kind = 'zotero_profile'
          AND source.removed_at IS NULL
        ORDER BY profile.id
        """,
        (source_id, materialization_signature, current_profile_id),
    ).fetchall()
    for row in rows:
        config = load_json_object(row["config_json"])
        raw_filters = config.get("filters", {})
        if not isinstance(raw_filters, dict):
            raise RuntimeError("Stored Zotero profile filters are malformed.")
        profile_id = str(row["id"])
        collection_values = _stored_profile_filter_sequence(raw_filters, "collections")
        if len(collection_values) > 1:
            raise RuntimeError("Stored Zotero profile collection filter is invalid.")
        collection_key: str | None = None
        collection_matches = True
        if collection_values:
            requested_collection = collection_values[0]
            try:
                collection_key = resolve_collection(
                    collections, requested_collection
                ).key
            except ZoteroFilterError:
                saved = _saved_profile_collection_selection(
                    project_dir=project_dir,
                    source_id=source_id,
                    profile_id=profile_id,
                    collection=requested_collection,
                    connection=repository.connection,
                )
                if saved is None:
                    collection_matches = False
                else:
                    collection_key = saved.key
        include_status = raw_filters.get("include_status", "all")
        if not isinstance(include_status, str) or include_status not in {
            "all",
            "read",
            "reading",
            "to_read",
            "unknown",
        }:
            raise RuntimeError("Stored Zotero profile status filter is invalid.")
        matches = (
            can_materialize
            and collection_matches
            and (include_status == "all" or include_status == reading_status)
            and bool(
                filter_items(
                    [item],
                    collection_key=collection_key,
                    tags=_stored_profile_filter_sequence(raw_filters, "tags"),
                    item_types=_stored_profile_filter_sequence(
                        raw_filters, "item_types"
                    ),
                )
            )
        )
        revision = _profile_revision(row["revision"])
        if matches:
            changed = repository.upsert_zotero_profile_item(
                profile_id=profile_id,
                zotero_item_id=zotero_item_id,
                observed_version=observed_version,
                expected_profile_revision=revision,
                expected_materialization_signature=materialization_signature,
                now=now,
            )
            if not changed and not _membership_observation_is_newer(
                repository,
                profile_id=profile_id,
                zotero_item_id=zotero_item_id,
                observed_version=observed_version,
            ):
                raise RuntimeError("Zotero profile membership changed concurrently.")
        else:
            repository.remove_zotero_profile_item(
                profile_id=profile_id,
                zotero_item_id=zotero_item_id,
                observed_version=observed_version,
                expected_profile_revision=revision,
                expected_materialization_signature=materialization_signature,
                now=now,
            )


def _stored_profile_filter_sequence(
    filters: dict[str, object], name: str
) -> tuple[str, ...]:
    raw = filters.get(name, [])
    if not isinstance(raw, list) or any(not isinstance(value, str) for value in raw):
        raise RuntimeError(f"Stored Zotero profile {name} filter is malformed.")
    return tuple(raw)


def _membership_observation_is_newer(
    repository: Repository,
    *,
    profile_id: str,
    zotero_item_id: str,
    observed_version: int,
) -> bool:
    row = repository.connection.execute(
        """
        SELECT observed_version
        FROM zotero_profile_items
        WHERE profile_id = ? AND zotero_item_id = ?
        """,
        (profile_id, zotero_item_id),
    ).fetchone()
    return row is not None and int(row["observed_version"]) > observed_version


def _optional_profile_version(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError("Stored Zotero profile cursor must be a non-negative integer.")
    return value


def _profile_revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError(
            "Stored Zotero profile revision must be a non-negative integer."
        )
    return value


def _consistent_sync_version(*versions: int) -> int:
    if not versions or any(
        isinstance(version, bool) or not isinstance(version, int) or version < 0
        for version in versions
    ):
        raise RuntimeError("Zotero sync returned an invalid library version.")
    if len(set(versions)) != 1:
        raise RuntimeError(
            "The library changed during Zotero sync; no profile cursor was advanced."
        )
    return versions[0]


def _partition_zotero_sync_rows(
    rows: Sequence[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    parents: list[dict[str, object]] = []
    children: list[dict[str, object]] = []
    seen: dict[str, str] = {}
    for row in rows:
        key = _payload_key(row)
        encoded = _canonical_json_sha256(row)
        previous = seen.get(key)
        if previous is not None:
            if previous != encoded:
                raise ZoteroVersionConflictError("duplicate item", key)
            continue
        seen[key] = encoded
        if _payload_data(row).get("parentItem"):
            children.append(row)
        else:
            parents.append(row)
    return parents, children


def _payload_data(row: dict[str, object]) -> dict[str, object]:
    data = row.get("data")
    return data if isinstance(data, dict) else row


def _payload_key(row: dict[str, object]) -> str:
    key = row.get("key") or _payload_data(row).get("key")
    if not isinstance(key, str) or not key:
        raise ZoteroVersionConflictError("invalid item", "missing-key")
    return key


def _children_by_parent(
    rows: Sequence[dict[str, object]],
) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        parent = _payload_data(row).get("parentItem")
        if not isinstance(parent, str) or not parent:
            raise ZoteroVersionConflictError("child without parent", _payload_key(row))
        grouped.setdefault(parent, []).append(row)
    for parent in grouped:
        grouped[parent].sort(key=_payload_key)
    return grouped


def _child_kind(child: ZoteroAttachment | ZoteroNote | ZoteroAnnotation) -> str:
    if isinstance(child, ZoteroAttachment):
        return "attachment"
    if isinstance(child, ZoteroNote):
        return "note"
    return "annotation"


@dataclass(frozen=True)
class _PreparedZoteroImport:
    collections: list[ZoteroCollection]
    collection_paths_by_key: dict[str, str]
    selected_collection: CollectionSelection | None
    collection_id_by_key: dict[str, str]
    items_fetched: int
    enriched_items: list[ZoteroItem]
    selected: list[tuple[ZoteroItem, str]]


def _prepare_zotero_import(
    *,
    client: ZoteroClient,
    collection: str | None,
    tags: tuple[str, ...],
    item_types: tuple[str, ...],
    include_status: str,
    limit: int | None,
    since_version: int | None,
    read_tags: tuple[str, ...],
    reading_tags: tuple[str, ...],
    to_read_tags: tuple[str, ...],
    source_id: str,
    warnings: list[str],
) -> _PreparedZoteroImport:
    collections = [normalize_collection(row) for row in client.collections()]
    collection_paths_by_key = collection_paths(collections)
    selected_collection = _resolve_collection_filter(collections, collection)
    collection_id_by_key = {
        row.key: stable_zotero_collection_id(source_id, row.key) for row in collections
    }
    fetched_rows = (
        client.collection_items(
            selected_collection.key,
            limit=limit,
            since=since_version,
        )
        if selected_collection
        else client.top_items(limit=limit, since=since_version)
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
                normalize_child(row) for row in client.item_children(item.key)
            )
            if child is not None
        ]
        enriched_items.append(attach_children(item, children))
    for item in enriched_items:
        for collection_key in item.collections:
            if collection_key not in collection_id_by_key:
                raise ZoteroVersionConflictError("missing collection", collection_key)
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
    return _PreparedZoteroImport(
        collections=collections,
        collection_paths_by_key=collection_paths_by_key,
        selected_collection=selected_collection,
        collection_id_by_key=collection_id_by_key,
        items_fetched=items_fetched,
        enriched_items=enriched_items,
        selected=selected,
    )


def _zotero_source_payload(
    *,
    source_id: str,
    api_url: str,
    data_dir: Path | None,
    last_version: int | None,
    created_at: str,
    updated_at: str,
) -> dict[str, object]:
    return {
        "id": source_id,
        "source_type": "local_api",
        "local_api_url": api_url,
        "data_dir": str(data_dir.expanduser().resolve()) if data_dir else None,
        "library_id": "0",
        "library_type": "user",
        "name": "Zotero Local Library",
        "last_version": last_version,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _mark_zotero_run_failed(
    *,
    connection: sqlite3.Connection,
    repository: Repository,
    run_id: str,
    counts: _ImportCounts,
    warnings: list[str],
    error: BaseException,
) -> None:
    status = (
        "interrupted"
        if isinstance(error, (KeyboardInterrupt, ZoteroImportCancelled))
        else "failed"
    )
    error_message = _safe_error_message(error)
    warnings.append(f"Zotero import failed ({type(error).__name__}).")
    with connection:
        repository.finish_zotero_import_run(
            run_id,
            finished_at=_utc_now(),
            status=status,
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
            error_code=type(error).__name__,
            error_message=error_message,
        )


def _raise_if_zotero_cancelled(
    cancel_requested: Callable[[], bool] | None,
) -> None:
    if cancel_requested is not None and cancel_requested():
        raise ZoteroImportCancelled("Zotero sync cancelled at an item boundary.")


def _preflight_zotero_project_paths(
    *,
    project_dir: Path,
    database_path: Path,
    data_dir: Path | None,
) -> None:
    """Prevent project writes anywhere inside a read-only Zotero data tree."""

    if data_dir is None:
        return
    lexical_data = data_dir.expanduser().absolute()
    if lexical_data.is_symlink():
        raise ValueError("Zotero data directory must not be a symbolic link.")
    resolved_data = lexical_data.resolve(strict=False)
    metadata = project_dir / ".paper-galaxy"
    mutable_roots = (
        project_dir,
        metadata,
        metadata / "backups",
        database_path,
    )
    data_is_metadata = resolved_data == metadata or resolved_data.is_relative_to(
        metadata
    )
    if data_is_metadata or any(
        root == resolved_data or root.is_relative_to(resolved_data)
        for root in mutable_roots
    ):
        raise ValueError(
            "Paper Galaxy project metadata or database cannot be stored inside "
            "the read-only Zotero data directory. Choose a separate project."
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

    def snapshot(self) -> dict[str, object]:
        return {
            name: value.copy() if isinstance(value, Counter) else value
            for name, value in vars(self).items()
        }

    def restore(self, snapshot: dict[str, object]) -> None:
        for name, value in snapshot.items():
            setattr(self, name, value.copy() if isinstance(value, Counter) else value)


@dataclass(frozen=True)
class _PreparedZoteroExternal:
    """Filesystem/PDF work prepared before an atomic generation transaction."""

    attachments: tuple[tuple[dict[str, object], AttachmentResolution], ...]
    primary_pdf: ZoteroAttachment | None
    primary_resolution: AttachmentResolution | None
    pdf_text: str
    pdf_extract_attempted: bool
    observed_at: str
    skip_reason: str | None
    text: str
    digest: str
    document: IndexedDocument | None
    chunks: tuple[IndexedChunk, ...]
    warnings: tuple[str, ...]
    attachments_seen: int
    attachments_resolved: int
    attachment_status_counts: tuple[tuple[str, int], ...]
    stored_attachments: int
    linked_attachments: int
    pdfs_seen: int
    pdfs_extracted: int
    pdfs_missing: int
    pdfs_extraction_failed: int
    notes_imported: int
    annotations_imported: int


@contextmanager
def _zotero_sync_write_scope(
    connection: sqlite3.Connection,
    *,
    atomic: bool,
) -> Iterator[None]:
    """Commit normal batches while keeping a generation switch all-or-nothing."""

    if atomic:
        yield
        return
    with connection:
        yield


def _prepare_zotero_external(
    *,
    item: ZoteroItem,
    source_id: str,
    source_corpus_id: str,
    collection_names: list[str],
    data_dir: Path | None,
    include_pdfs: bool,
    include_notes: bool,
    include_attachments: bool,
    include_metadata_only: bool,
    pdf_policy: str,
    min_chars: int,
    chunk_size: int,
    chunk_overlap: int,
    now: str,
) -> _PreparedZoteroExternal:
    """Resolve attachments, read PDFs, and chunk text without a write lock."""

    zotero_item_id = stable_zotero_item_id(source_id, item.key)
    document_id = stable_zotero_document_id(source_id, item.key)
    attachments, primary_pdf, primary_resolution = _attachment_records(
        item,
        source_id=source_id,
        zotero_item_id=zotero_item_id,
        data_dir=data_dir,
        include_attachments=include_attachments,
        now=now,
    )
    status_counts = Counter(
        str(attachment["path_status"]) for attachment, _ in attachments
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
    pdf_text = ""
    pdf_extract_attempted = False
    pdfs_extracted = 0
    pdfs_extraction_failed = 0
    prepared_warnings: list[str] = []
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
            pdfs_extracted = 1
        else:
            pdfs_extraction_failed = 1
            prepared_warnings.append(
                f"PDF extraction failed for Zotero item {item.key}; "
                "created a metadata-only record when policy allowed it."
            )
    skip_reason = (
        "missing_pdf"
        if pdf_policy == "skip-missing" and primary_pdf is not None and not pdf_text
        else None
    )
    text = ""
    if skip_reason is None:
        text = build_zotero_document_text(
            item,
            collection_names=collection_names,
            notes=notes,
            annotations=annotation_texts,
            pdf_text=pdf_text,
            primary_attachment=primary_pdf,
        )
        if len(text) < min_chars and not include_metadata_only:
            skip_reason = "min_chars"
    digest = hashlib.sha256(text.encode()).hexdigest()
    document = (
        _document_record(
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
        if skip_reason is None
        else None
    )
    chunks = (
        tuple(
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
        )
        if skip_reason is None
        else ()
    )
    return _PreparedZoteroExternal(
        attachments=tuple(attachments),
        primary_pdf=primary_pdf,
        primary_resolution=primary_resolution,
        pdf_text=pdf_text,
        pdf_extract_attempted=pdf_extract_attempted,
        observed_at=now,
        skip_reason=skip_reason,
        text=text,
        digest=digest,
        document=document,
        chunks=chunks,
        warnings=tuple(prepared_warnings),
        attachments_seen=len(attachments),
        attachments_resolved=sum(
            1
            for attachment, _ in attachments
            if str(attachment["path_status"]) in RESOLVED_STATUSES
        ),
        attachment_status_counts=tuple(sorted(status_counts.items())),
        stored_attachments=sum(
            1
            for attachment, _ in attachments
            if str(attachment.get("zotero_path", "")).startswith("storage:")
        ),
        linked_attachments=sum(
            1
            for attachment, _ in attachments
            if attachment.get("zotero_path")
            and not str(attachment.get("zotero_path", "")).startswith("storage:")
        ),
        pdfs_seen=int(primary_pdf is not None),
        pdfs_extracted=pdfs_extracted,
        pdfs_missing=int(
            primary_pdf is not None
            and primary_resolution is not None
            and primary_resolution.status not in RESOLVED_STATUSES
        ),
        pdfs_extraction_failed=pdfs_extraction_failed,
        notes_imported=len(notes),
        annotations_imported=len(item.annotations) if include_notes else 0,
    )


def _record_prepared_zotero_external(
    prepared: _PreparedZoteroExternal,
    *,
    counts: _ImportCounts,
    warnings: list[str],
) -> None:
    counts.attachments_seen += prepared.attachments_seen
    counts.attachments_resolved += prepared.attachments_resolved
    counts.attachment_status_counts.update(dict(prepared.attachment_status_counts))
    counts.stored_attachments += prepared.stored_attachments
    counts.linked_attachments += prepared.linked_attachments
    counts.pdfs_seen += prepared.pdfs_seen
    counts.pdfs_extracted += prepared.pdfs_extracted
    counts.pdfs_missing += prepared.pdfs_missing
    counts.pdfs_extraction_failed += prepared.pdfs_extraction_failed
    counts.notes_imported += prepared.notes_imported
    counts.annotations_imported += prepared.annotations_imported
    warnings.extend(prepared.warnings)


def _zotero_item_requires_external_preparation(
    repository: Repository,
    *,
    item: ZoteroItem,
    source_id: str,
    force: bool,
    rematerialize: bool,
    allow_unknown_child_state: bool,
    verified_deleted_child_keys: set[str],
) -> bool:
    """Decide without a write transaction whether filesystem work is needed."""

    incoming_child_manifest = _zotero_child_manifest(item)
    existing_item = repository.get_zotero_item_sync_state(source_id, item.key)
    _assert_zotero_item_state_is_monotonic(
        existing_item,
        item=item,
        incoming_child_manifest=incoming_child_manifest,
        allow_unknown_child_state=force or allow_unknown_child_state,
        verified_deleted_child_keys=verified_deleted_child_keys,
    )
    existing_version = _sync_state_version(existing_item)
    if existing_version is not None and (
        item.version is None or item.version < existing_version
    ):
        if rematerialize:
            raise ZoteroVersionConflictError("item", item.key)
        return False
    existing_document = repository.get_document(
        stable_zotero_document_id(source_id, item.key)
    )
    return not (
        not force
        and not rematerialize
        and existing_item is not None
        and existing_document is not None
        and existing_document.status == "active"
        and _zotero_item_materialization_is_identical(
            existing_item,
            item=item,
            incoming_child_manifest=incoming_child_manifest,
        )
    )


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
    commit_guard: Callable[[], None] | None,
    source_response_version: int | None = None,
    verified_deleted_child_keys: set[str] | None = None,
    allow_unknown_child_state: bool = False,
    rematerialize: bool = False,
    prepared_external: _PreparedZoteroExternal | None = None,
    external_preparation_complete: bool = False,
) -> bool:
    zotero_item_id = stable_zotero_item_id(source_id, item.key)
    document_id = stable_zotero_document_id(source_id, item.key)
    incoming_child_manifest = _zotero_child_manifest(item)
    existing_item = repository.get_zotero_item_sync_state(source_id, item.key)
    if (
        force
        and existing_item is not None
        and existing_item.get("child_manifest") is None
    ):
        warnings.append(
            f"Initialized the legacy child-version baseline for Zotero item "
            f"{item.key} from the explicit --force full child fetch."
        )
    _assert_zotero_item_state_is_monotonic(
        existing_item,
        item=item,
        incoming_child_manifest=incoming_child_manifest,
        allow_unknown_child_state=force or allow_unknown_child_state,
        verified_deleted_child_keys=verified_deleted_child_keys or set(),
    )
    existing_version_value = (
        existing_item.get("version") if existing_item is not None else None
    )
    if existing_version_value is not None and not isinstance(
        existing_version_value, int
    ):
        raise TypeError("stored Zotero item version must be an integer")
    existing_version = existing_version_value
    if existing_version is not None and (
        item.version is None or item.version < existing_version
    ):
        if rematerialize:
            raise ZoteroVersionConflictError("item", item.key)
        warnings.append(
            f"Ignored older Zotero item version for {item.key}; "
            "the newer local record was preserved."
        )
        return False
    existing_document = repository.get_document(document_id)
    if (
        not force
        and not rematerialize
        and existing_item is not None
        and existing_document is not None
        and existing_document.status == "active"
        and _zotero_item_materialization_is_identical(
            existing_item,
            item=item,
            incoming_child_manifest=incoming_child_manifest,
        )
    ):
        return False
    if prepared_external is None and external_preparation_complete:
        raise RuntimeError(
            "Zotero item changed after external preparation; retry the sync."
        )
    external = prepared_external or _prepare_zotero_external(
        item=item,
        source_id=source_id,
        source_corpus_id=source_corpus_id,
        collection_names=[
            collection_paths.get(collection_key, collection_key)
            for collection_key in item.collections
        ],
        data_dir=data_dir,
        include_pdfs=include_pdfs,
        include_notes=include_notes,
        include_attachments=include_attachments,
        include_metadata_only=include_metadata_only,
        pdf_policy=pdf_policy,
        min_chars=min_chars,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        now=now,
    )
    _record_prepared_zotero_external(external, counts=counts, warnings=warnings)
    attachments = external.attachments
    primary_pdf = external.primary_pdf
    primary_resolution = external.primary_resolution
    pdf_text = external.pdf_text
    pdf_extract_attempted = external.pdf_extract_attempted
    if commit_guard is not None:
        commit_guard()
    if source_response_version is not None:
        repository.assert_zotero_source_version_fence(
            source_id=source_id,
            response_version=source_response_version,
        )
    accepted = repository.upsert_zotero_item(
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
            "created_at": external.observed_at,
            "updated_at": external.observed_at,
        }
    )
    if not accepted:
        current_item = repository.get_zotero_item_sync_state(source_id, item.key)
        current_version = _sync_state_version(current_item)
        if current_version is not None and (
            item.version is None or current_version > item.version
        ):
            warnings.append(
                f"Ignored concurrently superseded Zotero item {item.key}; "
                "the newer local record was preserved."
            )
            return False
        raise ZoteroVersionConflictError("item", item.key)
    current_item = repository.get_zotero_item_sync_state(source_id, item.key)
    if existing_item is not None or (
        current_item is not None and current_item.get("child_manifest") is not None
    ):
        _assert_child_manifest_is_monotonic(
            current_item,
            item_key=item.key,
            incoming_child_manifest=incoming_child_manifest,
            allow_unknown_child_state=force or allow_unknown_child_state,
            verified_deleted_child_keys=verified_deleted_child_keys or set(),
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
        if not repository.upsert_zotero_attachment(attachment):
            raise ZoteroVersionConflictError(
                "attachment", str(attachment["zotero_key"])
            )
    repository.retain_zotero_attachments(
        zotero_item_id,
        (str(attachment["id"]) for attachment, _ in attachments),
    )
    repository.update_zotero_child_manifest(
        zotero_item_id,
        incoming_child_manifest,
    )
    if external.skip_reason == "missing_pdf":
        counts.skipped += 1
        warnings.append(
            f"Skipped Zotero item {item.key}: --pdf-policy skip-missing requires "
            "a resolvable, extractable PDF."
        )
        return True
    text = external.text
    if external.skip_reason == "min_chars":
        counts.skipped += 1
        warnings.append(
            f"Skipped Zotero item {item.key}: metadata text shorter than {min_chars}."
        )
        return True
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
    digest = external.digest
    document_changed = (
        existing_document is None
        or existing_document.sha256 != digest
        or force
        or rematerialize
    )
    if existing_document is None:
        counts.items_imported += 1
    elif not document_changed:
        counts.items_unchanged += 1
    else:
        counts.items_updated += 1
    if document_changed:
        if external.document is None:
            raise RuntimeError("Prepared Zotero document is missing.")
        repository.upsert_document(external.document, text, list(external.chunks))
    repository.upsert_zotero_document_link(
        document_id=document_id,
        zotero_item_id=zotero_item_id,
        attachment_id=stable_zotero_attachment_id(source_id, primary_pdf.key)
        if primary_pdf
        else None,
        role="primary",
    )
    return True


class ZoteroVersionConflictError(RuntimeError):
    """Raised when one API response would regress persisted Zotero state."""

    def __init__(self, entity_kind: str, entity_key: str) -> None:
        self.entity_kind = entity_kind
        self.entity_key = entity_key
        super().__init__(
            f"Zotero {entity_kind} {entity_key} has a regressive or divergent "
            "versioned payload; the sync was stopped before advancing its cursor."
        )


def _zotero_child_manifest(item: ZoteroItem) -> list[dict[str, object]]:
    children: tuple[ZoteroAttachment | ZoteroNote | ZoteroAnnotation, ...] = (
        *item.attachments,
        *item.notes,
        *item.annotations,
    )
    entries: list[dict[str, object]] = []
    seen_keys: set[str] = set()
    for child in children:
        if child.key in seen_keys:
            raise ZoteroVersionConflictError("duplicate child", child.key)
        seen_keys.add(child.key)
        if isinstance(child, ZoteroAttachment):
            kind = "attachment"
        elif isinstance(child, ZoteroNote):
            kind = "note"
        else:
            kind = "annotation"
        entries.append(
            {
                "key": child.key,
                "kind": kind,
                "version": child.version,
                "content_sha256": _canonical_json_sha256(child.raw),
            }
        )
    return sorted(entries, key=lambda entry: (str(entry["kind"]), str(entry["key"])))


def _assert_zotero_item_state_is_monotonic(
    existing_state: dict[str, object] | None,
    *,
    item: ZoteroItem,
    incoming_child_manifest: list[dict[str, object]],
    allow_unknown_child_state: bool,
    verified_deleted_child_keys: set[str] | None = None,
) -> None:
    if existing_state is None:
        return
    stored_version = _sync_state_version(existing_state)
    if stored_version is not None and (
        item.version is None or item.version < stored_version
    ):
        return
    if stored_version == item.version:
        stored_data = existing_state.get("data")
        if not isinstance(stored_data, dict):
            raise TypeError("stored Zotero item data must be an object")
        if _canonical_json_sha256(stored_data) != _canonical_json_sha256(item.raw):
            raise ZoteroVersionConflictError("item", item.key)
    _assert_child_manifest_is_monotonic(
        existing_state,
        item_key=item.key,
        incoming_child_manifest=incoming_child_manifest,
        allow_unknown_child_state=allow_unknown_child_state,
        verified_deleted_child_keys=verified_deleted_child_keys or set(),
    )


def _assert_child_manifest_is_monotonic(
    existing_state: dict[str, object] | None,
    *,
    item_key: str,
    incoming_child_manifest: list[dict[str, object]],
    allow_unknown_child_state: bool = False,
    verified_deleted_child_keys: set[str] | None = None,
) -> None:
    if existing_state is None:
        return
    stored_entries = existing_state.get("child_manifest")
    if stored_entries is None:
        if allow_unknown_child_state:
            return
        raise ZoteroVersionConflictError("legacy child state", item_key)
    if not isinstance(stored_entries, list):
        raise TypeError("stored Zotero child manifest must be a list")
    stored_by_key = _manifest_by_key(stored_entries)
    incoming_by_key = _manifest_by_key(incoming_child_manifest)
    allowed_missing = verified_deleted_child_keys or set()
    for child_key, stored in stored_by_key.items():
        incoming = incoming_by_key.get(child_key)
        if incoming is None:
            if child_key in allowed_missing:
                continue
            raise ZoteroVersionConflictError(str(stored["kind"]), child_key)
        stored_version = _manifest_version(stored, item_key=item_key)
        incoming_version = _manifest_version(incoming, item_key=item_key)
        if stored_version is not None and (
            incoming_version is None or incoming_version < stored_version
        ):
            raise ZoteroVersionConflictError(str(incoming["kind"]), child_key)
        if stored_version == incoming_version and (
            str(stored["content_sha256"]) != str(incoming["content_sha256"])
        ):
            raise ZoteroVersionConflictError(str(incoming["kind"]), child_key)


def _zotero_item_materialization_is_identical(
    existing_state: dict[str, object],
    *,
    item: ZoteroItem,
    incoming_child_manifest: list[dict[str, object]],
) -> bool:
    stored_data = existing_state.get("data")
    stored_manifest = existing_state.get("child_manifest")
    if not isinstance(stored_data, dict) or not isinstance(stored_manifest, list):
        return False
    return _canonical_json_sha256(stored_data) == _canonical_json_sha256(
        item.raw
    ) and _canonical_json_sha256(stored_manifest) == _canonical_json_sha256(
        incoming_child_manifest
    )


def _manifest_by_key(entries: Sequence[object]) -> dict[str, dict[str, object]]:
    indexed: dict[str, dict[str, object]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise TypeError("stored Zotero child manifest entries must be objects")
        key = entry.get("key")
        kind = entry.get("kind")
        content_sha256 = entry.get("content_sha256")
        if (
            not isinstance(key, str)
            or not key
            or kind not in {"attachment", "note", "annotation"}
            or not isinstance(content_sha256, str)
            or len(content_sha256) != 64
        ):
            raise TypeError("stored Zotero child manifest entry is invalid")
        if key in indexed:
            raise TypeError("stored Zotero child manifest contains duplicate keys")
        indexed[key] = dict(entry)
    return indexed


def _manifest_version(entry: dict[str, object], *, item_key: str) -> int | None:
    value = entry.get("version")
    if value is not None and not isinstance(value, int):
        raise TypeError(f"Zotero child version for {item_key} must be an integer")
    return value


def _sync_state_version(state: dict[str, object] | None) -> int | None:
    value = state.get("version") if state is not None else None
    if value is not None and not isinstance(value, int):
        raise TypeError("stored Zotero item version must be an integer")
    return value


def _canonical_json_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def _resolve_incremental_collection_filter(
    *,
    collections: list[ZoteroCollection],
    collection: str | None,
    project_dir: Path,
    source_id: str,
    profile_id: str,
    profile_preexisting: bool,
    connection: sqlite3.Connection | None,
) -> CollectionSelection | None:
    if collection is None:
        return None
    try:
        return resolve_collection(collections, collection)
    except ZoteroFilterError:
        if not profile_preexisting:
            raise
    saved = _saved_profile_collection_selection(
        project_dir=project_dir,
        source_id=source_id,
        profile_id=profile_id,
        collection=collection,
        connection=connection,
    )
    if saved is None:
        return resolve_collection(collections, collection)
    current_by_key = {row.key: row for row in collections}
    current = current_by_key.get(saved.key)
    if current is None:
        return saved
    paths = collection_paths(collections)
    return CollectionSelection(
        key=current.key,
        name=current.name,
        path=paths.get(current.key, current.name),
        matched_by="saved_profile_key",
    )


def _saved_profile_collection_selection(
    *,
    project_dir: Path,
    source_id: str,
    profile_id: str,
    collection: str,
    connection: sqlite3.Connection | None,
) -> CollectionSelection | None:
    owned_connection = connection is None
    if connection is None:
        database_path = resolve_database_path(project_dir)
        if not database_path.is_file():
            return None
        connection = connect_read_only(project_dir)
    try:
        run_row = connection.execute(
            """
            SELECT zir.config_json
            FROM zotero_sync_profiles AS zsp
            LEFT JOIN zotero_import_runs AS zir ON zir.id = zsp.last_run_id
            WHERE zsp.id = ? AND zsp.source_id = ?
            """,
            (profile_id, source_id),
        ).fetchone()
        if run_row is not None and run_row["config_json"] is not None:
            config = load_json_object(run_row["config_json"])
            selected = config.get("selected_collection")
            if isinstance(selected, dict):
                key = selected.get("key")
                name = selected.get("name")
                path = selected.get("path")
                if (
                    isinstance(key, str)
                    and key
                    and isinstance(name, str)
                    and name
                    and isinstance(path, str)
                    and path
                ):
                    return CollectionSelection(
                        key=key,
                        name=name,
                        path=path,
                        matched_by="saved_profile_key",
                    )
        rows = connection.execute(
            """
            SELECT zotero_key, parent_key, name, path, version, data_json
            FROM zotero_collections
            WHERE source_id = ?
            ORDER BY zotero_key
            """,
            (source_id,),
        ).fetchall()
        stored_collections = [
            ZoteroCollection(
                key=str(row["zotero_key"]),
                name=str(row["name"]),
                parent_key=(
                    str(row["parent_key"]) if row["parent_key"] is not None else None
                ),
                version=(int(row["version"]) if row["version"] is not None else None),
                path=str(row["path"]) if row["path"] is not None else None,
                raw=load_json_object(row["data_json"]),
            )
            for row in rows
        ]
        if not stored_collections:
            return None
        return resolve_collection(stored_collections, collection)
    except ZoteroFilterError:
        return None
    finally:
        if owned_connection:
            connection.close()


def _changed_collection_keys(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    current: Sequence[ZoteroCollection],
    deleted: Sequence[str],
) -> set[str]:
    stored_rows = connection.execute(
        """
        SELECT zotero_key, parent_key, name, path, version, data_json, deleted_at
        FROM zotero_collections
        WHERE source_id = ?
        """,
        (source_id,),
    ).fetchall()
    stored = {str(row["zotero_key"]): row for row in stored_rows}
    changed: set[str] = set(deleted)
    current_paths = collection_paths(list(current))
    for row in current:
        previous = stored.get(row.key)
        if previous is None:
            continue
        if (
            previous["deleted_at"] is not None
            or str(previous["name"]) != row.name
            or (
                str(previous["parent_key"])
                if previous["parent_key"] is not None
                else None
            )
            != row.parent_key
            or (str(previous["path"]) if previous["path"] is not None else row.name)
            != current_paths.get(row.key, row.name)
        ):
            changed.add(row.key)

    parent_by_key = {
        str(row["zotero_key"]): (
            str(row["parent_key"]) if row["parent_key"] is not None else None
        )
        for row in stored_rows
    }
    parent_by_key.update({row.key: row.parent_key for row in current})
    expanded = set(changed)
    made_progress = True
    while made_progress:
        made_progress = False
        for key, parent_key in parent_by_key.items():
            if parent_key in expanded and key not in expanded:
                expanded.add(key)
                made_progress = True
    return expanded


def _parent_keys_for_collections(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    collection_keys: Sequence[str] | set[str],
) -> set[str]:
    keys = tuple(sorted(set(collection_keys)))
    parents: set[str] = set()
    for offset in range(0, len(keys), 400):
        batch = keys[offset : offset + 400]
        placeholders = ",".join("?" for _ in batch)
        rows = connection.execute(
            f"""
            SELECT DISTINCT zi.zotero_key
            FROM zotero_item_collections AS zic
            JOIN zotero_collections AS zc ON zc.id = zic.collection_id
            JOIN zotero_items AS zi ON zi.id = zic.zotero_item_id
            WHERE zc.source_id = ? AND zc.zotero_key IN ({placeholders})
              AND zi.deleted_at IS NULL
            """,
            (source_id, *batch),
        ).fetchall()
        parents.update(str(row["zotero_key"]) for row in rows)
    return parents


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
    if not database_path.is_file():
        return None
    connection = connect_read_only(project_dir)
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
    if row is None or row[0] is None:
        return None
    return int(row[0])


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _safe_error_message(error: BaseException, *, limit: int = 500) -> str:
    text = " ".join(str(error).split()) or type(error).__name__
    return text[:limit]
