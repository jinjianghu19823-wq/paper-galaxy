from __future__ import annotations

import copy
import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import paper_galaxy.services.jobs as job_service
import paper_galaxy.zotero.importers as zotero_importers
from paper_galaxy.services.sources import (
    get_source,
    register_zotero_source,
    remove_source,
)
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import (
    connect_read_only,
    connect_read_write,
    ensure_database_ready,
    resolve_database_path,
)
from paper_galaxy.zotero.importers import (
    ZoteroImportCancelled,
    import_from_zotero,
    load_zotero_materialization_config,
    stable_zotero_document_id,
    stable_zotero_item_id,
)
from paper_galaxy.zotero.models import ZoteroDeletedBatch, ZoteroSyncBatch


def _parent(
    *,
    version: int,
    tags: tuple[str, ...] = ("alpha",),
    title: str = "Synthetic profile paper",
    collections: tuple[str, ...] = ("COLL0001",),
) -> dict[str, Any]:
    return {
        "key": "PARENT01",
        "version": version,
        "library": {"id": 0, "type": "user", "name": "Synthetic"},
        "data": {
            "key": "PARENT01",
            "version": version,
            "itemType": "journalArticle",
            "title": title,
            "date": "2024",
            "dateModified": f"2026-01-{min(version, 28):02d}T00:00:00Z",
            "creators": [
                {
                    "creatorType": "author",
                    "firstName": "Ada",
                    "lastName": "Safety",
                }
            ],
            "tags": [{"tag": tag} for tag in tags],
            "collections": list(collections),
        },
    }


def _note(text: str, *, version: int) -> dict[str, Any]:
    return {
        "key": "NOTE0001",
        "version": version,
        "data": {
            "key": "NOTE0001",
            "version": version,
            "itemType": "note",
            "parentItem": "PARENT01",
            "note": f"<p>{text}</p>",
        },
    }


def _attachment(*, version: int) -> dict[str, Any]:
    return {
        "key": "ATTACH01",
        "version": version,
        "data": {
            "key": "ATTACH01",
            "version": version,
            "itemType": "attachment",
            "parentItem": "PARENT01",
            "title": "Synthetic PDF",
            "filename": "synthetic.pdf",
            "contentType": "application/pdf",
            "linkMode": "imported_file",
            "path": "storage:synthetic.pdf",
        },
    }


def _collection(*, version: int) -> dict[str, Any]:
    return {
        "key": "COLL0001",
        "version": version,
        "data": {
            "key": "COLL0001",
            "version": version,
            "name": "Synthetic collection",
        },
    }


class SyntheticProfileClient:
    """In-memory, read-only Zotero feed used only inside pytest temp projects."""

    def __init__(
        self,
        *,
        version: int,
        changed: list[dict[str, Any]],
        parents: list[dict[str, Any]] | None = None,
        deleted: dict[str, tuple[str, ...]] | None = None,
        collections: list[dict[str, Any]] | None = None,
    ) -> None:
        self.version = version
        self.changed = copy.deepcopy(changed)
        self.parents = copy.deepcopy(parents or [])
        self.deleted = dict(deleted or {})
        self.collection_rows = copy.deepcopy(
            [_collection(version=version)] if collections is None else collections
        )
        self.since_calls: list[int] = []
        self.parent_key_calls: list[tuple[str, ...]] = []

    def root(self) -> dict[str, object]:
        return {"ok": True}

    @staticmethod
    def _cancel_boundary(
        cancel_requested: Callable[[], bool] | None,
    ) -> None:
        if cancel_requested is not None and cancel_requested():
            raise RuntimeError("synthetic Zotero request cancelled")

    def sync_collections(
        self,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ZoteroSyncBatch:
        self._cancel_boundary(cancel_requested)
        return ZoteroSyncBatch(
            records=tuple(copy.deepcopy(self.collection_rows)),
            library_version=self.version,
        )

    def sync_items(
        self,
        *,
        since: int,
        limit: int | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ZoteroSyncBatch:
        self._cancel_boundary(cancel_requested)
        self.since_calls.append(since)
        records = self.changed if limit is None else self.changed[:limit]
        return ZoteroSyncBatch(
            records=tuple(copy.deepcopy(records)),
            library_version=self.version,
            complete=limit is None or len(self.changed) <= limit,
        )

    def items_by_keys(
        self,
        keys: tuple[str, ...],
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ZoteroSyncBatch:
        self._cancel_boundary(cancel_requested)
        self.parent_key_calls.append(keys)
        wanted = set(keys)
        return ZoteroSyncBatch(
            records=tuple(
                copy.deepcopy(row)
                for row in self.parents
                if str(row.get("key")) in wanted
            ),
            library_version=self.version,
        )

    def deleted_since(
        self,
        *,
        since: int,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ZoteroDeletedBatch:
        self._cancel_boundary(cancel_requested)
        assert self.since_calls and since == self.since_calls[-1]
        return ZoteroDeletedBatch(
            object_keys=self.deleted,
            library_version=self.version,
        )

    def item_children(self, item_key: str) -> list[dict[str, Any]]:
        raise AssertionError(
            f"incremental profile sync must not request children for {item_key}"
        )


class FailingLocatorClient(SyntheticProfileClient):
    """Fail before any remote payload can justify persisted locator state."""

    def __init__(self) -> None:
        super().__init__(version=6, changed=[])
        self.collection_attempts = 0

    def sync_collections(
        self,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ZoteroSyncBatch:
        self._cancel_boundary(cancel_requested)
        self.collection_attempts += 1
        raise RuntimeError("synthetic locator change failure")


def _sync(
    project_dir: Path,
    client: SyntheticProfileClient,
    **kwargs: object,
) -> Any:
    options: dict[str, object] = {
        "pdf_policy": "metadata",
        "build_reading_map": False,
        "min_chars": 1,
    }
    options.update(kwargs)
    return import_from_zotero(
        project_dir=project_dir,
        client=client,
        **options,
    )


def _profile_cursor(project_dir: Path, profile_id: str) -> int | None:
    connection = connect_read_only(project_dir)
    try:
        row = connection.execute(
            "SELECT last_version FROM zotero_sync_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        assert row is not None
        return int(row[0]) if row[0] is not None else None
    finally:
        connection.close()


def _document_text(project_dir: Path, document_id: str) -> str:
    connection = connect_read_only(project_dir)
    try:
        row = connection.execute(
            "SELECT text FROM document_texts WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        assert row is not None
        return str(row[0])
    finally:
        connection.close()


def _zotero_lifecycle_state_bytes(project_dir: Path) -> bytes:
    """Serialize exact source/profile/run rows for zero-write assertions."""

    tables = (
        ("zotero_sources", "id"),
        ("registered_sources", "id"),
        ("zotero_sync_profiles", "id"),
        ("zotero_profile_items", "profile_id, zotero_item_id"),
        ("zotero_import_runs", "id"),
        ("zotero_sync_run_details", "run_id"),
    )
    connection = connect_read_only(project_dir)
    try:
        payload: dict[str, list[dict[str, object]]] = {}
        for table, ordering in tables:
            rows = connection.execute(
                f'SELECT * FROM "{table}" ORDER BY {ordering}'
            ).fetchall()
            payload[table] = [{key: row[key] for key in row.keys()} for row in rows]
    finally:
        connection.close()
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _published_zotero_generation_bytes(project_dir: Path) -> bytes:
    """Serialize user-visible Zotero materialization, excluding run audit rows."""

    queries = {
        "profiles": """
            SELECT id, source_id, profile_signature, materialization_signature,
                   last_version, requires_full_sync, revision, last_run_id,
                   last_sync_at
            FROM zotero_sync_profiles
            ORDER BY id
        """,
        "memberships": """
            SELECT profile_id, zotero_item_id, is_member, observed_version,
                   first_matched_at, updated_at
            FROM zotero_profile_items
            ORDER BY profile_id, zotero_item_id
        """,
        "items": "SELECT * FROM zotero_items ORDER BY id",
        "documents": "SELECT * FROM documents ORDER BY id",
        "texts": "SELECT * FROM document_texts ORDER BY document_id",
        "chunks": "SELECT * FROM chunks ORDER BY document_id, chunk_index, id",
        "vectors": "SELECT * FROM vectors ORDER BY id",
    }
    connection = connect_read_only(project_dir)
    try:
        payload = {
            name: [tuple(row) for row in connection.execute(query).fetchall()]
            for name, query in queries.items()
        }
    finally:
        connection.close()
    return repr(payload).encode("utf-8")


def _parent_with_key(
    key: str,
    *,
    version: int,
    title: str,
    tags: tuple[str, ...] = ("alpha",),
) -> dict[str, Any]:
    row = _parent(version=version, title=title, tags=tags, collections=())
    row["key"] = key
    data = row["data"]
    assert isinstance(data, dict)
    data["key"] = key
    return row


def test_materialization_change_requires_full_before_fetch_or_write(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    first = _sync(
        project_dir,
        SyntheticProfileClient(
            version=5,
            changed=[_parent(version=5), _note("private note v5", version=5)],
        ),
        include_notes=False,
    )
    assert first.profile_id is not None
    document_id = stable_zotero_document_id(first.source_id, "PARENT01")
    before_text = _document_text(project_dir, document_id)
    before_cursor = _profile_cursor(project_dir, first.profile_id)
    connection = connect_read_only(project_dir)
    try:
        before_run_count = int(
            connection.execute("SELECT COUNT(*) FROM zotero_import_runs").fetchone()[0]
        )
    finally:
        connection.close()
    assert "private note v5" not in before_text

    rejected_client = SyntheticProfileClient(
        version=6,
        changed=[_parent(version=6), _note("new personal note", version=6)],
    )
    with pytest.raises((ValueError, RuntimeError), match=r"(?i)(full|materialization)"):
        _sync(
            project_dir,
            rejected_client,
            include_notes=True,
        )

    assert rejected_client.since_calls == []
    assert _profile_cursor(project_dir, first.profile_id) == before_cursor
    assert _document_text(project_dir, document_id) == before_text
    connection = connect_read_only(project_dir)
    try:
        assert (
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM zotero_import_runs"
                ).fetchone()[0]
            )
            == before_run_count
        )
    finally:
        connection.close()

    rebuilt = _sync(
        project_dir,
        SyntheticProfileClient(
            version=6,
            changed=[_parent(version=6), _note("new personal note", version=6)],
        ),
        include_notes=True,
        full=True,
    )

    assert rebuilt.full_sync is True
    assert rebuilt.last_version_after == 6
    assert "new personal note" in _document_text(project_dir, document_id)


def test_filter_membership_removal_deactivates_single_profile_document(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "single-profile"
    first = _sync(
        project_dir,
        SyntheticProfileClient(
            version=5,
            changed=[_parent(version=5, tags=("alpha",), title="Alpha title")],
        ),
        tags=("alpha",),
    )
    assert first.profile_id is not None

    changed = _sync(
        project_dir,
        SyntheticProfileClient(
            version=6,
            changed=[_parent(version=6, tags=("beta",), title="Beta revision")],
        ),
        tags=("alpha",),
    )

    connection = connect_read_only(project_dir)
    try:
        item = connection.execute(
            "SELECT id, title, deleted_at FROM zotero_items WHERE zotero_key = ?",
            ("PARENT01",),
        ).fetchone()
        assert item is not None
        tags = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT tag FROM zotero_item_tags
                WHERE zotero_item_id = ?
                ORDER BY tag
                """,
                (str(item["id"]),),
            ).fetchall()
        ]
        document_status = str(
            connection.execute("SELECT status FROM documents").fetchone()[0]
        )
    finally:
        connection.close()

    assert changed.last_version_after == 6
    assert str(item["title"]) == "Beta revision"
    assert item["deleted_at"] is None
    assert tags == ["beta"]
    assert document_status == "unindexed"


def test_filter_membership_removal_keeps_document_with_another_profile(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "shared-profile"
    initial = _parent(
        version=5,
        tags=("alpha", "beta"),
        title="Shared membership",
    )
    alpha = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[initial]),
        tags=("alpha",),
    )
    beta = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[initial]),
        tags=("beta",),
    )
    assert alpha.profile_id is not None
    assert beta.profile_id is not None
    assert alpha.profile_id != beta.profile_id

    _sync(
        project_dir,
        SyntheticProfileClient(
            version=6,
            changed=[
                _parent(
                    version=6,
                    tags=("beta",),
                    title="Beta-only revision",
                )
            ],
        ),
        tags=("alpha",),
    )

    connection = connect_read_only(project_dir)
    try:
        repository = Repository(connection, resolve_database_path(project_dir))
        item = connection.execute(
            "SELECT id, title FROM zotero_items WHERE zotero_key = ?",
            ("PARENT01",),
        ).fetchone()
        assert item is not None
        tags = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT tag FROM zotero_item_tags
                WHERE zotero_item_id = ?
                ORDER BY tag
                """,
                (str(item["id"]),),
            ).fetchall()
        ]
        status = str(connection.execute("SELECT status FROM documents").fetchone()[0])
        alpha_documents = repository.list_zotero_documents_with_text(tag="alpha")
        beta_documents = repository.list_zotero_documents_with_text(tag="beta")
    finally:
        connection.close()

    assert str(item["title"]) == "Beta-only revision"
    assert tags == ["beta"]
    assert status == "active"
    assert alpha_documents == []
    assert len(beta_documents) == 1


def test_materialization_change_invalidates_other_profile_membership(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "materialization-generation"
    initial = _parent(version=5, tags=("alpha", "beta"))
    alpha = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[initial]),
        tags=("alpha",),
        include_notes=False,
    )
    beta = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[initial]),
        tags=("beta",),
        include_notes=False,
    )
    assert alpha.profile_id is not None
    assert beta.profile_id is not None

    _sync(
        project_dir,
        SyntheticProfileClient(
            version=6,
            changed=[
                _parent(version=6, tags=("beta",)),
                _note("new materialization", version=6),
            ],
        ),
        tags=("alpha",),
        include_notes=True,
        full=True,
    )

    connection = connect_read_only(project_dir)
    try:
        status = str(connection.execute("SELECT status FROM documents").fetchone()[0])
        memberships = connection.execute(
            """
            SELECT profile_id, is_member
            FROM zotero_profile_items
            ORDER BY profile_id
            """
        ).fetchall()
        beta_state = connection.execute(
            """
            SELECT requires_full_sync
            FROM zotero_sync_profiles
            WHERE id = ?
            """,
            (beta.profile_id,),
        ).fetchone()
    finally:
        connection.close()

    assert status == "unindexed"
    assert memberships
    assert all(int(row["is_member"]) == 0 for row in memberships)
    assert beta_state is not None and int(beta_state[0]) == 1


def test_stricter_materialization_retires_previously_active_document(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "stricter-materialization"
    first = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[_parent(version=5)]),
        include_metadata_only=True,
    )
    assert first.profile_id is not None

    rebuilt = import_from_zotero(
        project_dir=project_dir,
        client=SyntheticProfileClient(
            version=6,
            changed=[
                _parent(
                    version=6,
                    tags=("beta",),
                    title="Skipped but synchronized metadata",
                )
            ],
        ),
        pdf_policy="metadata",
        include_metadata_only=False,
        min_chars=10_000,
        full=True,
        build_reading_map=False,
    )

    connection = connect_read_only(project_dir)
    try:
        status = str(connection.execute("SELECT status FROM documents").fetchone()[0])
        membership = connection.execute(
            """
            SELECT is_member FROM zotero_profile_items WHERE profile_id = ?
            """,
            (first.profile_id,),
        ).fetchone()
        item = connection.execute(
            """
            SELECT version, title FROM zotero_items WHERE zotero_key = 'PARENT01'
            """
        ).fetchone()
        tags = connection.execute(
            "SELECT tag FROM zotero_item_tags ORDER BY tag"
        ).fetchall()
    finally:
        connection.close()

    assert rebuilt.skipped == 1
    assert status == "unindexed"
    assert membership is not None and int(membership[0]) == 0
    assert item is not None and tuple(item) == (
        6,
        "Skipped but synchronized metadata",
    )
    assert [str(row[0]) for row in tags] == ["beta"]


def test_min_chars_skip_does_not_construct_unused_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "skip-without-chunks"
    _sync(
        project_dir,
        SyntheticProfileClient(
            version=5,
            changed=[_parent(version=5, collections=())],
            collections=[],
        ),
    )

    def fail_if_chunked(*args: object, **kwargs: object) -> list[str]:
        del args, kwargs
        raise AssertionError("a skipped document must not be chunked")

    monkeypatch.setattr(zotero_importers, "chunk_text", fail_if_chunked)
    result = _sync(
        project_dir,
        SyntheticProfileClient(
            version=6,
            changed=[_parent(version=6, collections=())],
            collections=[],
        ),
        include_metadata_only=False,
        min_chars=10_000,
        full=True,
    )

    assert result.skipped == 1


def test_full_materialization_replaces_chunks_when_only_chunking_changes(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "chunk-generation"
    first = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[_parent(version=5)]),
        chunk_size=2_000,
        chunk_overlap=200,
    )
    connection = connect_read_only(project_dir)
    try:
        before = connection.execute(
            "SELECT id, text FROM chunks ORDER BY chunk_index"
        ).fetchall()
    finally:
        connection.close()

    _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[_parent(version=5)]),
        chunk_size=40,
        chunk_overlap=5,
        full=True,
    )
    connection = connect_read_only(project_dir)
    try:
        after = connection.execute(
            "SELECT id, text FROM chunks ORDER BY chunk_index"
        ).fetchall()
    finally:
        connection.close()

    assert first.profile_id is not None
    assert len(before) == 1
    assert len(after) > 1
    assert [tuple(row) for row in after] != [tuple(row) for row in before]


def test_zotero_external_materialization_runs_without_write_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "external-preparation"
    connections: list[Any] = []
    preparation_calls = 0
    original_connect = zotero_importers.connect_read_write
    original_prepare = zotero_importers._prepare_zotero_external

    def tracked_connect(*args: object, **kwargs: object) -> Any:
        connection = original_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    def checked_prepare(**kwargs: object) -> Any:
        nonlocal preparation_calls
        assert connections
        assert connections[-1].in_transaction is False
        preparation_calls += 1
        return original_prepare(**kwargs)

    monkeypatch.setattr(zotero_importers, "connect_read_write", tracked_connect)
    monkeypatch.setattr(
        zotero_importers,
        "_prepare_zotero_external",
        checked_prepare,
    )

    _sync(
        project_dir,
        SyntheticProfileClient(
            version=5,
            changed=[_parent(version=5), _note("first note", version=5)],
        ),
        include_notes=False,
    )
    _sync(
        project_dir,
        SyntheticProfileClient(
            version=6,
            changed=[_parent(version=6), _note("second note", version=6)],
        ),
        include_notes=True,
        full=True,
    )

    assert preparation_calls == 2
    connection = connect_read_only(project_dir)
    try:
        timestamps = connection.execute(
            """
            SELECT item.updated_at, document.last_seen_at, document.updated_at
            FROM zotero_items AS item
            JOIN zotero_document_links AS link
              ON link.zotero_item_id = item.id
            JOIN documents AS document ON document.id = link.document_id
            """
        ).fetchone()
    finally:
        connection.close()
    assert timestamps is not None
    assert len(set(tuple(timestamps))) == 1


def test_attachment_materialization_can_be_disabled_and_restored(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "attachment-generation"
    rows = [_parent(version=5), _attachment(version=5)]
    first = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=rows),
        include_attachments=True,
    )
    assert first.profile_id is not None
    item_id = stable_zotero_item_id(first.source_id, "PARENT01")

    _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=rows),
        include_attachments=False,
        full=True,
    )
    connection = connect_read_only(project_dir)
    try:
        repository = Repository(connection, resolve_database_path(project_dir))
        disabled = repository.get_zotero_item_detail(item_id)
        cached_children = int(
            connection.execute(
                "SELECT COUNT(*) FROM zotero_child_items WHERE deleted_at IS NULL"
            ).fetchone()[0]
        )
    finally:
        connection.close()

    assert disabled is not None and disabled["attachments"] == []
    assert cached_children == 1

    _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=rows),
        include_attachments=True,
        full=True,
    )
    connection = connect_read_only(project_dir)
    try:
        restored = Repository(
            connection, resolve_database_path(project_dir)
        ).get_zotero_item_detail(item_id)
    finally:
        connection.close()
    assert restored is not None and len(restored["attachments"]) == 1


def test_materialization_signature_uses_runtime_unicode_tag_semantics(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "unicode-config"
    first = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[_parent(version=5)]),
        read_tags=("ß",),
    )
    rejected = SyntheticProfileClient(version=5, changed=[])

    with pytest.raises(RuntimeError, match="materialization settings changed"):
        _sync(
            project_dir,
            rejected,
            read_tags=("ss",),
        )

    assert first.profile_id is not None
    assert rejected.since_calls == []


def test_removed_profile_reconciles_union_visibility_and_reregistration(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "source-lifecycle"
    initial = _parent(version=5, tags=("alpha", "beta"))
    alpha = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[initial]),
        tags=("alpha",),
    )
    beta = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[initial]),
        tags=("beta",),
    )
    assert alpha.profile_id is not None
    assert beta.profile_id is not None

    remove_source(project_dir, alpha.profile_id)
    connection = connect_read_only(project_dir)
    try:
        assert connection.execute("SELECT status FROM documents").fetchone()[0] == (
            "active"
        )
    finally:
        connection.close()

    remove_source(project_dir, beta.profile_id)
    connection = connect_read_only(project_dir)
    try:
        repository = Repository(connection, resolve_database_path(project_dir))
        assert connection.execute("SELECT status FROM documents").fetchone()[0] == (
            "unindexed"
        )
        assert repository.search_documents("Synthetic") == []
    finally:
        connection.close()

    removed = get_source(project_dir, beta.profile_id, include_removed=True)
    assert removed is not None
    restored, created = register_zotero_source(
        project_dir,
        beta.source_id,
        filters=removed.config["filters"],
    )
    assert created is False
    assert restored.id == beta.profile_id
    connection = connect_read_only(project_dir)
    try:
        assert connection.execute("SELECT status FROM documents").fetchone()[0] == (
            "active"
        )
    finally:
        connection.close()


def test_completed_run_exposes_reproducible_job_materialization_config(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "job-config"
    summary = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[_parent(version=5)]),
        include_notes=False,
        include_metadata_only=False,
        read_tags=("done",),
        reading_tags=("active-reading",),
        to_read_tags=("queue-next",),
        min_chars=7,
        chunk_size=73,
        chunk_overlap=11,
    )
    assert summary.profile_id is not None

    config = load_zotero_materialization_config(
        project_dir,
        profile_id=summary.profile_id,
    )

    assert config == {
        "include_pdfs": True,
        "include_notes": False,
        "include_attachments": True,
        "include_metadata_only": False,
        "read_tags": ["done"],
        "reading_tags": ["active-reading"],
        "to_read_tags": ["queue-next"],
        "min_chars": 7,
        "chunk_size": 73,
        "chunk_overlap": 11,
    }


def test_new_filter_profile_inherits_completed_peer_materialization_config(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "new-profile-job-config"
    summary = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[_parent(version=5)]),
        include_notes=False,
        include_metadata_only=False,
        read_tags=("done",),
        reading_tags=("active-reading",),
        to_read_tags=("queue-next",),
        min_chars=7,
        chunk_size=73,
        chunk_overlap=11,
    )
    peer, created = register_zotero_source(
        project_dir,
        summary.source_id,
        filters={
            "tags": ["beta"],
            "include_status": "all",
            "pdf_policy": "metadata",
        },
    )
    assert created is True

    assert load_zotero_materialization_config(
        project_dir,
        profile_id=peer.id,
    ) == {
        "include_pdfs": True,
        "include_notes": False,
        "include_attachments": True,
        "include_metadata_only": False,
        "read_tags": ["done"],
        "reading_tags": ["active-reading"],
        "to_read_tags": ["queue-next"],
        "min_chars": 7,
        "chunk_size": 73,
        "chunk_overlap": 11,
    }


def test_new_filter_profile_inherits_removed_peer_materialization_config(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "removed-peer-job-config"
    summary = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[_parent(version=5)]),
        include_notes=False,
        min_chars=7,
        chunk_size=73,
        chunk_overlap=11,
    )
    assert summary.profile_id is not None
    remove_source(project_dir, summary.profile_id)
    peer, created = register_zotero_source(
        project_dir,
        summary.source_id,
        filters={
            "tags": ["beta"],
            "include_status": "all",
            "pdf_policy": "metadata",
        },
    )
    assert created is True

    inherited = load_zotero_materialization_config(
        project_dir,
        profile_id=peer.id,
    )
    assert inherited["include_notes"] is False
    assert inherited["min_chars"] == 7
    assert inherited["chunk_size"] == 73
    assert inherited["chunk_overlap"] == 11


def test_default_job_inherits_completed_materialization_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "job-inheritance"
    summary = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[_parent(version=5)]),
        include_notes=False,
        read_tags=("done",),
        min_chars=7,
        chunk_size=73,
        chunk_overlap=11,
    )
    assert summary.profile_id is not None
    captured: dict[str, object] = {}

    def fake_import(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            run_id="run",
            items_seen=0,
            items_imported=0,
            items_updated=0,
            items_unchanged=0,
            last_version_before=5,
            last_version_after=5,
            changed_parents=0,
            changed_children=0,
            deleted_records=0,
            metadata_only_documents=0,
            pdfs_extraction_failed=0,
            duration_seconds=0.0,
            full_sync=False,
        )

    monkeypatch.setattr(
        "paper_galaxy.zotero.importers.import_from_zotero",
        fake_import,
    )

    context = SimpleNamespace(
        project_dir=project_dir,
        job=SimpleNamespace(source_id=summary.profile_id, params={"full": False}),
        raise_if_interrupted=lambda: None,
        cancel_requested=lambda: False,
        raise_if_worker_stopped_or_ownership_lost=lambda: None,
        fence_owned_write_transaction=lambda connection: None,
        report_progress=lambda current, total, message: None,
    )

    job_service._run_zotero_job(context)

    assert captured["include_notes"] is False
    assert captured["read_tags"] == ("done",)
    assert captured["min_chars"] == 7
    assert captured["chunk_size"] == 73
    assert captured["chunk_overlap"] == 11


def test_parent_delete_soft_deletes_cached_children_and_attachments(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "parent-delete"
    _sync(
        project_dir,
        SyntheticProfileClient(
            version=5,
            changed=[
                _parent(version=5),
                _note("delete with parent", version=5),
                _attachment(version=5),
            ],
        ),
    )

    deleted = _sync(
        project_dir,
        SyntheticProfileClient(
            version=6,
            changed=[],
            deleted={"items": ("PARENT01",)},
        ),
    )

    connection = connect_read_only(project_dir)
    try:
        parent = connection.execute(
            "SELECT deleted_at, deleted_version FROM zotero_items WHERE zotero_key = ?",
            ("PARENT01",),
        ).fetchone()
        children = connection.execute(
            """
            SELECT zotero_key, deleted_at, deleted_version
            FROM zotero_child_items
            WHERE parent_key = ?
            ORDER BY zotero_key
            """,
            ("PARENT01",),
        ).fetchall()
        attachments = connection.execute(
            """
            SELECT zotero_key, deleted_at, deleted_version
            FROM zotero_attachments
            ORDER BY zotero_key
            """
        ).fetchall()
        document_status = str(
            connection.execute("SELECT status FROM documents").fetchone()[0]
        )
    finally:
        connection.close()

    assert deleted.deleted_records == 1
    assert parent is not None
    assert parent["deleted_at"] is not None
    assert int(parent["deleted_version"]) == 6
    assert [str(row["zotero_key"]) for row in children] == ["ATTACH01", "NOTE0001"]
    assert all(row["deleted_at"] is not None for row in children)
    assert all(int(row["deleted_version"]) == 6 for row in children)
    assert [str(row["zotero_key"]) for row in attachments] == ["ATTACH01"]
    assert attachments[0]["deleted_at"] is not None
    assert int(attachments[0]["deleted_version"]) == 6
    assert document_status == "missing"


def test_stale_item_cannot_clear_a_newer_tombstone_or_revive_document(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "tombstone-race"
    summary = _sync(
        project_dir,
        SyntheticProfileClient(
            version=10,
            changed=[_parent(version=10, title="Version ten")],
        ),
    )
    document_id = stable_zotero_document_id(summary.source_id, "PARENT01")
    item_id = stable_zotero_item_id(summary.source_id, "PARENT01")
    connection = connect_read_write(project_dir)
    try:
        repository = Repository(connection, resolve_database_path(project_dir))
        with connection:
            repository.record_zotero_tombstone(
                source_id=summary.source_id,
                object_type="item",
                zotero_key="PARENT01",
                library_version=12,
                deleted_at="2026-02-12T00:00:00+00:00",
            )
            assert repository.mark_zotero_parent_deleted(
                source_id=summary.source_id,
                zotero_key="PARENT01",
                library_version=12,
                deleted_at="2026-02-12T00:00:00+00:00",
            )

        # Model the final write of an older in-flight sync. A stale accepted row
        # must not be allowed to clear deletion evidence from version 12.
        with connection:
            accepted = repository.upsert_zotero_item(
                {
                    "id": item_id,
                    "source_id": summary.source_id,
                    "zotero_key": "PARENT01",
                    "version": 11,
                    "item_type": "journalArticle",
                    "title": "Stale version eleven",
                    "year": "2024",
                    "reading_status": "unknown",
                    "data": _parent(version=11, title="Stale version eleven"),
                    "created_at": "2026-02-11T00:00:00+00:00",
                    "updated_at": "2026-02-11T00:00:00+00:00",
                }
            )
            if accepted:
                repository.clear_zotero_tombstone(
                    source_id=summary.source_id,
                    object_type="item",
                    zotero_key="PARENT01",
                )
    finally:
        connection.close()

    connection = connect_read_only(project_dir)
    try:
        item = connection.execute(
            """
            SELECT deleted_at, deleted_version
            FROM zotero_items
            WHERE source_id = ? AND zotero_key = ?
            """,
            (summary.source_id, "PARENT01"),
        ).fetchone()
        tombstone = connection.execute(
            """
            SELECT library_version
            FROM zotero_tombstones
            WHERE source_id = ? AND object_type = 'item' AND zotero_key = ?
            """,
            (summary.source_id, "PARENT01"),
        ).fetchone()
        document_status = str(
            connection.execute(
                "SELECT status FROM documents WHERE id = ?", (document_id,)
            ).fetchone()[0]
        )
    finally:
        connection.close()

    assert item is not None
    assert item["deleted_at"] is not None
    assert int(item["deleted_version"]) == 12
    assert tombstone is not None
    assert int(tombstone["library_version"]) == 12
    assert document_status == "missing"


def test_deleted_collection_cannot_match_live_collection_filters(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "deleted-collection"
    _sync(
        project_dir,
        SyntheticProfileClient(
            version=5,
            changed=[_parent(version=5)],
        ),
    )
    _sync(
        project_dir,
        SyntheticProfileClient(
            version=6,
            changed=[],
            deleted={"collections": ("COLL0001",)},
            collections=[],
        ),
    )

    connection = connect_read_only(project_dir)
    try:
        repository = Repository(connection, resolve_database_path(project_dir))
        collection = connection.execute(
            """
            SELECT deleted_at, deleted_version
            FROM zotero_collections
            WHERE zotero_key = ?
            """,
            ("COLL0001",),
        ).fetchone()
        unfiltered = repository.list_zotero_items()
        by_key = repository.list_zotero_items(collection="COLL0001")
        by_name = repository.list_zotero_items(collection="Synthetic collection")
        documents = repository.list_zotero_documents_with_text(collection="COLL0001")
    finally:
        connection.close()

    assert collection is not None
    assert collection["deleted_at"] is not None
    assert int(collection["deleted_version"]) == 6
    assert len(unfiltered) == 1
    assert by_key == []
    assert by_name == []
    assert documents == []


def test_changed_parent_reconciles_every_active_profile_membership(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "all-profile-memberships"
    initial = _parent(
        version=5,
        tags=("alpha", "beta"),
        title="Initially shared",
    )
    alpha = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[initial]),
        tags=("alpha",),
    )
    beta = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[initial]),
        tags=("beta",),
    )
    gamma = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[initial]),
        tags=("gamma",),
    )
    assert alpha.profile_id is not None
    assert beta.profile_id is not None
    assert gamma.profile_id is not None

    first_change = _sync(
        project_dir,
        SyntheticProfileClient(
            version=6,
            changed=[
                _parent(
                    version=6,
                    tags=("gamma",),
                    title="Now gamma only",
                )
            ],
        ),
        tags=("alpha",),
    )

    connection = connect_read_only(project_dir)
    try:
        memberships_after_gamma = {
            str(row["profile_id"]): int(row["is_member"])
            for row in connection.execute(
                """
                SELECT profile_id, is_member
                FROM zotero_profile_items
                ORDER BY profile_id
                """
            ).fetchall()
        }
        status_after_gamma = str(
            connection.execute("SELECT status FROM documents").fetchone()[0]
        )
    finally:
        connection.close()

    assert first_change.last_version_after == 6
    assert memberships_after_gamma == {
        alpha.profile_id: 0,
        beta.profile_id: 0,
        gamma.profile_id: 1,
    }
    assert status_after_gamma == "active"
    assert _profile_cursor(project_dir, beta.profile_id) == 5
    assert _profile_cursor(project_dir, gamma.profile_id) == 5

    _sync(
        project_dir,
        SyntheticProfileClient(
            version=7,
            changed=[
                _parent(
                    version=7,
                    tags=("delta",),
                    title="Matches no active profile",
                )
            ],
        ),
        tags=("alpha",),
    )

    connection = connect_read_only(project_dir)
    try:
        final_memberships = connection.execute(
            """
            SELECT is_member
            FROM zotero_profile_items
            ORDER BY profile_id
            """
        ).fetchall()
        final_status = str(
            connection.execute("SELECT status FROM documents").fetchone()[0]
        )
    finally:
        connection.close()

    assert [int(row[0]) for row in final_memberships] == [0, 0, 0]
    assert final_status == "unindexed"


@pytest.mark.parametrize("changed_locator", ("data_dir", "api_url"))
def test_failed_locator_change_is_zero_write_and_old_locator_remains_usable(
    tmp_path: Path,
    changed_locator: str,
) -> None:
    project_dir = tmp_path / f"failed-{changed_locator}"
    old_data_dir = tmp_path / "zotero-old"
    new_data_dir = tmp_path / "zotero-new"
    old_data_dir.mkdir()
    new_data_dir.mkdir()
    old_api_url = "http://127.0.0.1:23119/api"
    new_api_url = "http://127.0.0.1:23120/api"
    first = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[_parent(version=5)]),
        api_url=old_api_url,
        data_dir=old_data_dir,
    )
    assert first.profile_id is not None
    before_failure = _zotero_lifecycle_state_bytes(project_dir)
    failing_client = FailingLocatorClient()

    attempted_api_url = new_api_url if changed_locator == "api_url" else old_api_url
    attempted_data_dir = new_data_dir if changed_locator == "data_dir" else old_data_dir
    with pytest.raises((RuntimeError, ValueError)):
        _sync(
            project_dir,
            failing_client,
            api_url=attempted_api_url,
            data_dir=attempted_data_dir,
            full=True,
        )
    after_failure = _zotero_lifecycle_state_bytes(project_dir)

    recovery_error: Exception | None = None
    recovered = None
    try:
        recovered = _sync(
            project_dir,
            SyntheticProfileClient(
                version=6,
                changed=[_parent(version=6, title="Recovered through old locator")],
            ),
            api_url=old_api_url,
            data_dir=old_data_dir,
        )
    except Exception as exc:
        recovery_error = exc

    assert recovery_error is None, (
        "failed locator attempt blocked the previously valid locator; "
        f"state_changed={after_failure != before_failure}, error={recovery_error!r}"
    )
    assert recovered is not None
    assert recovered.profile_id == first.profile_id
    assert recovered.last_version_before == 5
    assert recovered.last_version_after == 6
    assert after_failure == before_failure


def test_new_profile_rejects_snapshot_older_than_known_source_version(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "source-version-rollback"
    first = _parent(version=10, tags=("alpha",), collections=())
    alpha = _sync(
        project_dir,
        SyntheticProfileClient(version=10, changed=[first], collections=[]),
        tags=("alpha",),
    )
    assert alpha.profile_id is not None

    connection = connect_read_only(project_dir)
    try:
        items_before = connection.execute(
            "SELECT zotero_key, version FROM zotero_items ORDER BY zotero_key"
        ).fetchall()
        memberships_before = connection.execute(
            """
            SELECT profile_id, zotero_item_id, is_member, observed_version
            FROM zotero_profile_items
            ORDER BY profile_id, zotero_item_id
            """
        ).fetchall()
        alpha_cursor_before = connection.execute(
            "SELECT last_version FROM zotero_sync_profiles WHERE id = ?",
            (alpha.profile_id,),
        ).fetchone()
    finally:
        connection.close()

    unseen = _parent(version=6, tags=("beta",), collections=())
    unseen["key"] = "PARENT02"
    unseen_data = unseen["data"]
    assert isinstance(unseen_data, dict)
    unseen_data["key"] = "PARENT02"
    stale_client = SyntheticProfileClient(
        version=6,
        changed=[unseen],
        collections=[],
    )

    with pytest.raises(RuntimeError, match=r"(?i)(source|version|backward|older)"):
        _sync(
            project_dir,
            stale_client,
            tags=("beta",),
        )

    assert stale_client.since_calls == [0]
    connection = connect_read_only(project_dir)
    try:
        assert (
            connection.execute(
                "SELECT zotero_key, version FROM zotero_items ORDER BY zotero_key"
            ).fetchall()
            == items_before
        )
        assert (
            connection.execute(
                """
            SELECT profile_id, zotero_item_id, is_member, observed_version
            FROM zotero_profile_items
            ORDER BY profile_id, zotero_item_id
            """
            ).fetchall()
            == memberships_before
        )
        assert (
            connection.execute(
                "SELECT last_version FROM zotero_sync_profiles WHERE id = ?",
                (alpha.profile_id,),
            ).fetchone()
            == alpha_cursor_before
        )
        source_version = connection.execute(
            "SELECT last_version FROM zotero_sources WHERE id = ?",
            (alpha.source_id,),
        ).fetchone()
        assert source_version is not None and tuple(source_version) == (10,)
        published_cursors = connection.execute(
            """
            SELECT last_version
            FROM zotero_sync_profiles
            WHERE last_version IS NOT NULL
            ORDER BY last_version
            """
        ).fetchall()
    finally:
        connection.close()

    assert [tuple(row) for row in published_cursors] == [(10,)]


def test_source_version_advance_fences_each_item_write_and_cursor_publish(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "source-version-race"
    initial = _parent(version=5, tags=("alpha",), collections=())
    first = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=[initial], collections=[]),
        tags=("alpha",),
    )
    assert first.profile_id is not None

    connection = connect_read_only(project_dir)
    try:
        items_before = connection.execute(
            "SELECT zotero_key, version FROM zotero_items ORDER BY zotero_key"
        ).fetchall()
        memberships_before = connection.execute(
            """
            SELECT profile_id, zotero_item_id, is_member, observed_version
            FROM zotero_profile_items
            ORDER BY profile_id, zotero_item_id
            """
        ).fetchall()
    finally:
        connection.close()

    unseen = _parent(version=6, tags=("alpha",), collections=())
    unseen["key"] = "PARENT02"
    unseen_data = unseen["data"]
    assert isinstance(unseen_data, dict)
    unseen_data["key"] = "PARENT02"
    advanced = False

    def advance_source_version_once() -> None:
        nonlocal advanced
        if advanced:
            return
        advanced = True
        writer = connect_read_write(project_dir)
        try:
            with writer:
                writer.execute(
                    "UPDATE zotero_sources SET last_version = 10 WHERE id = ?",
                    (first.source_id,),
                )
        finally:
            writer.close()

    with pytest.raises(RuntimeError, match=r"(?i)(source|version|concurrent|older)"):
        _sync(
            project_dir,
            SyntheticProfileClient(version=6, changed=[unseen], collections=[]),
            tags=("alpha",),
            commit_guard=advance_source_version_once,
        )

    assert advanced is True
    connection = connect_read_only(project_dir)
    try:
        source_version = connection.execute(
            "SELECT last_version FROM zotero_sources WHERE id = ?",
            (first.source_id,),
        ).fetchone()
        profile_version = connection.execute(
            "SELECT last_version FROM zotero_sync_profiles WHERE id = ?",
            (first.profile_id,),
        ).fetchone()
        assert source_version is not None and tuple(source_version) == (10,)
        assert profile_version is not None and tuple(profile_version) == (5,)
        assert (
            connection.execute(
                "SELECT zotero_key, version FROM zotero_items ORDER BY zotero_key"
            ).fetchall()
            == items_before
        )
        assert (
            connection.execute(
                """
            SELECT profile_id, zotero_item_id, is_member, observed_version
            FROM zotero_profile_items
            ORDER BY profile_id, zotero_item_id
            """
            ).fetchall()
            == memberships_before
        )
    finally:
        connection.close()


def test_failed_first_locator_does_not_claim_new_project(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "first-locator-failure"
    wrong_data_dir = tmp_path / "wrong-zotero"
    correct_data_dir = tmp_path / "correct-zotero"
    wrong_data_dir.mkdir()
    correct_data_dir.mkdir()
    failing_client = FailingLocatorClient()

    with pytest.raises(RuntimeError, match="synthetic locator change failure"):
        _sync(
            project_dir,
            failing_client,
            data_dir=wrong_data_dir,
        )

    assert failing_client.collection_attempts == 1
    recovered = _sync(
        project_dir,
        SyntheticProfileClient(
            version=5,
            changed=[_parent(version=5, collections=())],
            collections=[],
        ),
        data_dir=correct_data_dir,
    )

    assert recovered.last_version_before is None
    assert recovered.last_version_after == 5
    connection = connect_read_only(project_dir)
    try:
        source = connection.execute(
            "SELECT data_dir, last_version FROM zotero_sources WHERE id = ?",
            (recovered.source_id,),
        ).fetchone()
        active_profile_configs = connection.execute(
            """
            SELECT config_json
            FROM registered_sources
            WHERE kind = 'zotero_profile' AND removed_at IS NULL
            ORDER BY id
            """
        ).fetchall()
        item_keys = connection.execute(
            "SELECT zotero_key FROM zotero_items ORDER BY zotero_key"
        ).fetchall()
        run_statuses = connection.execute(
            "SELECT status FROM zotero_import_runs ORDER BY started_at, id"
        ).fetchall()
        removed_profile_configs = connection.execute(
            """
            SELECT config_json
            FROM registered_sources
            WHERE kind = 'zotero_profile' AND removed_at IS NOT NULL
            ORDER BY id
            """
        ).fetchall()
    finally:
        connection.close()

    assert source is not None and tuple(source) == (
        str(correct_data_dir.resolve()),
        5,
    )
    assert [tuple(row) for row in item_keys] == [("PARENT01",)]
    assert [str(row["status"]) for row in run_statuses] == ["failed", "completed"]
    assert len(removed_profile_configs) == 1
    assert json.loads(str(removed_profile_configs[0]["config_json"]))[
        "data_dir"
    ] == str(wrong_data_dir.resolve())
    assert active_profile_configs
    assert all(
        json.loads(str(row["config_json"])).get("data_dir")
        == str(correct_data_dir.resolve())
        for row in active_profile_configs
    )


def test_concurrent_first_locator_registration_cannot_overwrite_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "concurrent-locator-project"
    data_a = tmp_path / "zotero-a"
    data_b = tmp_path / "zotero-b"
    data_a.mkdir()
    data_b.mkdir()
    ensure_database_ready(project_dir)
    barrier = threading.Barrier(2)
    original = zotero_importers._read_incremental_profile_state

    def synchronized_preflight(**kwargs: object) -> object:
        result = original(**kwargs)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(
        zotero_importers,
        "_read_incremental_profile_state",
        synchronized_preflight,
    )

    parent = _parent(version=5, tags=("alpha", "beta"), collections=())

    def run(data_dir: Path, tag: str) -> Any:
        return _sync(
            project_dir,
            SyntheticProfileClient(
                version=5,
                changed=[parent],
                collections=[],
            ),
            data_dir=data_dir,
            tags=(tag,),
        )

    attempts: list[tuple[Path, Any | BaseException]] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(run, data_dir, tag): data_dir
            for data_dir, tag in ((data_a, "alpha"), (data_b, "beta"))
        }
        for future, data_dir in futures.items():
            try:
                attempts.append((data_dir, future.result(timeout=10)))
            except BaseException as exc:
                attempts.append((data_dir, exc))

    successes = [entry for entry in attempts if not isinstance(entry[1], BaseException)]
    failures = [entry for entry in attempts if isinstance(entry[1], BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert "locator" in str(failures[0][1]).lower()
    winning_data_dir = successes[0][0].resolve()

    connection = connect_read_only(project_dir)
    try:
        source = connection.execute(
            "SELECT data_dir, last_version FROM zotero_sources"
        ).fetchone()
        active_profiles = connection.execute(
            """
            SELECT config_json
            FROM registered_sources
            WHERE kind = 'zotero_profile' AND removed_at IS NULL
            ORDER BY id
            """
        ).fetchall()
        cursor_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM zotero_sync_profiles AS profile
                JOIN registered_sources AS source ON source.id = profile.id
                WHERE source.removed_at IS NULL
                """
            ).fetchone()[0]
        )
    finally:
        connection.close()

    assert source is not None and tuple(source) == (str(winning_data_dir), 5)
    assert len(active_profiles) == 1
    assert json.loads(str(active_profiles[0]["config_json"]))["data_dir"] == str(
        winning_data_dir
    )
    assert cursor_count == 1


def test_concurrent_first_api_locator_registration_cannot_create_two_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "concurrent-api-locator-project"
    ensure_database_ready(project_dir)
    barrier = threading.Barrier(2)
    original = zotero_importers._read_incremental_profile_state

    def synchronized_preflight(**kwargs: object) -> object:
        result = original(**kwargs)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(
        zotero_importers,
        "_read_incremental_profile_state",
        synchronized_preflight,
    )
    api_urls = (
        "http://127.0.0.1:23119/api",
        "http://127.0.0.1:23120/api",
    )

    def run(api_url: str, tag: str) -> Any:
        return _sync(
            project_dir,
            SyntheticProfileClient(
                version=5,
                changed=[
                    _parent(
                        version=5,
                        tags=("alpha", "beta"),
                        collections=(),
                    )
                ],
                collections=[],
            ),
            api_url=api_url,
            tags=(tag,),
        )

    attempts: list[tuple[str, Any | BaseException]] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(run, api_url, tag): api_url
            for api_url, tag in zip(api_urls, ("alpha", "beta"), strict=True)
        }
        for future, api_url in futures.items():
            try:
                attempts.append((api_url, future.result(timeout=10)))
            except BaseException as exc:
                attempts.append((api_url, exc))

    successes = [entry for entry in attempts if not isinstance(entry[1], BaseException)]
    failures = [entry for entry in attempts if isinstance(entry[1], BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert "locator" in str(failures[0][1]).lower()

    connection = connect_read_only(project_dir)
    try:
        sources = connection.execute(
            "SELECT local_api_url FROM zotero_sources ORDER BY id"
        ).fetchall()
        profiles = connection.execute(
            """
            SELECT config_json
            FROM registered_sources
            WHERE kind = 'zotero_profile' AND removed_at IS NULL
            ORDER BY id
            """
        ).fetchall()
    finally:
        connection.close()

    assert [str(row[0]) for row in sources] == [successes[0][0]]
    assert len(profiles) == 1
    assert json.loads(str(profiles[0]["config_json"]))["local_api_url"] == str(
        sources[0][0]
    )


def test_transactional_locator_fence_rejects_duplicate_established_source_id(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "duplicate-source-id"
    ensure_database_ready(project_dir)
    api_url = "http://127.0.0.1:23119/api"
    connection = connect_read_write(project_dir)
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO zotero_sources(
                  id, source_type, local_api_url, library_id, library_type,
                  name, last_version, created_at, updated_at
                ) VALUES (
                  'legacy-duplicate-source', 'local_api', ?, '0', 'user',
                  'Synthetic legacy identity', 5, '2026-01-01', '2026-01-01'
                )
                """,
                (api_url,),
            )
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match=r"(?i)(locator|identity|source)"):
            zotero_importers._transactional_zotero_locator_claim_is_unverified(
                connection,
                requested_source_id=zotero_importers.stable_zotero_source_id(
                    api_url, "0"
                ),
                requested_config={
                    "local_api_url": api_url,
                    "data_dir": None,
                    "library_id": "0",
                    "library_type": "user",
                },
            )
        connection.rollback()
    finally:
        connection.close()


def test_failed_first_run_cannot_retire_profile_used_by_concurrent_run(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "concurrent-first-run"
    first_remote = threading.Event()
    second_remote = threading.Event()
    release_second = threading.Event()

    class FirstFailingClient(SyntheticProfileClient):
        def sync_collections(
            self,
            *,
            cancel_requested: Callable[[], bool] | None = None,
        ) -> ZoteroSyncBatch:
            self._cancel_boundary(cancel_requested)
            first_remote.set()
            assert second_remote.wait(timeout=5)
            raise RuntimeError("synthetic first concurrent failure")

    class SecondPausedClient(SyntheticProfileClient):
        def sync_collections(
            self,
            *,
            cancel_requested: Callable[[], bool] | None = None,
        ) -> ZoteroSyncBatch:
            self._cancel_boundary(cancel_requested)
            second_remote.set()
            assert release_second.wait(timeout=5)
            return super().sync_collections(cancel_requested=cancel_requested)

    first_client = FirstFailingClient(version=5, changed=[], collections=[])
    second_client = SecondPausedClient(
        version=5,
        changed=[_parent(version=5, collections=())],
        collections=[],
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(_sync, project_dir, first_client)
        assert first_remote.wait(timeout=5)
        second_future = executor.submit(_sync, project_dir, second_client)
        assert second_remote.wait(timeout=5)
        with pytest.raises(RuntimeError, match="synthetic first concurrent failure"):
            first_future.result(timeout=5)
        release_second.set()
        completed = second_future.result(timeout=10)

    assert completed.last_version_after == 5
    connection = connect_read_only(project_dir)
    try:
        active_profiles = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM registered_sources
                WHERE kind = 'zotero_profile' AND removed_at IS NULL
                """
            ).fetchone()[0]
        )
        sync_profile = connection.execute(
            "SELECT last_version FROM zotero_sync_profiles"
        ).fetchone()
        statuses = [
            str(row[0])
            for row in connection.execute(
                "SELECT status FROM zotero_import_runs ORDER BY started_at, id"
            ).fetchall()
        ]
    finally:
        connection.close()

    assert active_profiles == 1
    assert sync_profile is not None and int(sync_profile[0]) == 5
    assert statuses == ["failed", "completed"]


def test_failed_first_run_cannot_retire_profile_with_completed_partial_run(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "concurrent-partial-first-run"
    first_remote = threading.Event()
    release_first = threading.Event()

    class PausedFailingClient(SyntheticProfileClient):
        def sync_collections(
            self,
            *,
            cancel_requested: Callable[[], bool] | None = None,
        ) -> ZoteroSyncBatch:
            self._cancel_boundary(cancel_requested)
            first_remote.set()
            assert release_first.wait(timeout=5)
            raise RuntimeError("synthetic delayed first failure")

    first_client = PausedFailingClient(version=5, changed=[], collections=[])
    with ThreadPoolExecutor(max_workers=1) as executor:
        first_future = executor.submit(
            _sync,
            project_dir,
            first_client,
            tags=("alpha",),
        )
        assert first_remote.wait(timeout=5)
        partial = _sync(
            project_dir,
            SyntheticProfileClient(
                version=5,
                changed=[
                    _parent_with_key(
                        "PARENT01",
                        version=5,
                        title="Filtered beta one",
                        tags=("beta",),
                    ),
                    _parent_with_key(
                        "PARENT02",
                        version=5,
                        title="Filtered beta two",
                        tags=("beta",),
                    ),
                ],
                collections=[],
            ),
            tags=("alpha",),
            limit=1,
        )
        assert partial.last_version_after is None
        assert partial.items_selected == 0
        release_first.set()
        with pytest.raises(RuntimeError, match="synthetic delayed first failure"):
            first_future.result(timeout=5)

    connection = connect_read_only(project_dir)
    try:
        active_profiles = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM registered_sources
                WHERE kind = 'zotero_profile' AND removed_at IS NULL
                """
            ).fetchone()[0]
        )
        sync_profiles = int(
            connection.execute("SELECT COUNT(*) FROM zotero_sync_profiles").fetchone()[
                0
            ]
        )
        statuses = sorted(
            str(row[0])
            for row in connection.execute(
                "SELECT status FROM zotero_import_runs"
            ).fetchall()
        )
    finally:
        connection.close()

    assert active_profiles == 1
    assert sync_profiles == 1
    assert statuses == ["completed", "failed"]


def test_failed_locator_cannot_overwrite_data_backed_cursorless_source(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "data-backed-cursorless-source"
    old_data_dir = tmp_path / "old-zotero-data"
    new_data_dir = tmp_path / "new-zotero-data"
    old_data_dir.mkdir()
    new_data_dir.mkdir()
    initial = _sync(
        project_dir,
        SyntheticProfileClient(
            version=5,
            changed=[
                _parent_with_key("PARENT01", version=5, title="First partial"),
                _parent_with_key("PARENT02", version=5, title="Second partial"),
            ],
            collections=[],
        ),
        data_dir=old_data_dir,
        limit=1,
    )
    assert initial.profile_id is not None
    assert initial.last_version_after is None
    remove_source(project_dir, initial.profile_id)

    failed = FailingLocatorClient()
    with pytest.raises(ValueError, match=r"(?i)locator"):
        _sync(project_dir, failed, data_dir=new_data_dir)
    assert failed.collection_attempts == 0

    connection = connect_read_only(project_dir)
    try:
        source = connection.execute(
            "SELECT data_dir, last_version FROM zotero_sources"
        ).fetchone()
        item_count = int(
            connection.execute("SELECT COUNT(*) FROM zotero_items").fetchone()[0]
        )
        active_profiles = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM registered_sources
                WHERE kind = 'zotero_profile' AND removed_at IS NULL
                """
            ).fetchone()[0]
        )
    finally:
        connection.close()

    assert source is not None and tuple(source) == (str(old_data_dir.resolve()), None)
    assert item_count == 1
    assert active_profiles == 0

    retried = _sync(
        project_dir,
        SyntheticProfileClient(
            version=5,
            changed=[
                _parent_with_key("PARENT01", version=5, title="First partial"),
                _parent_with_key("PARENT02", version=5, title="Second partial"),
            ],
            collections=[],
        ),
        data_dir=old_data_dir,
        full=True,
    )
    assert retried.last_version_after == 5


def test_stale_full_rematerialization_preserves_published_generation(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "stale-full-rematerialization"
    initial = _parent(version=10, collections=())
    completed = _sync(
        project_dir,
        SyntheticProfileClient(version=10, changed=[initial], collections=[]),
    )
    assert completed.profile_id is not None
    document_id = stable_zotero_document_id(completed.source_id, "PARENT01")

    connection = connect_read_write(project_dir)
    try:
        document = connection.execute(
            """
            SELECT sha256, content_revision_sha256
            FROM documents
            WHERE id = ?
            """,
            (document_id,),
        ).fetchone()
        assert document is not None
        with connection:
            connection.execute(
                """
                INSERT INTO embedding_models(
                  id, name, provider, dimension, distance, config_json,
                  model_fingerprint, fingerprint_algorithm, created_at
                )
                VALUES (
                  'stale-full-model', 'synthetic-local-model', 'test', 2,
                  'cosine', '{}', 'synthetic-fingerprint', 'test-sha256',
                  '2026-01-01T00:00:00+00:00'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO vectors(
                  id, model_id, object_type, object_id, text_sha256,
                  source_content_sha256, model_fingerprint, algorithm_version,
                  dimension, dtype, vector, metadata_json, created_at, updated_at
                )
                VALUES (
                  'stale-full-vector', 'stale-full-model', 'document', ?, ?, ?,
                  'synthetic-fingerprint', 'document-vector-v1', 2, 'float32',
                  ?, '{"evidence":"must-survive"}',
                  '2026-01-01T00:00:00+00:00',
                  '2026-01-01T00:00:00+00:00'
                )
                """,
                (
                    document_id,
                    str(document["sha256"]),
                    str(document["content_revision_sha256"]),
                    b"\x00\x00\x80?\x00\x00\x00?",
                ),
            )
    finally:
        connection.close()

    def published_generation() -> dict[str, list[tuple[object, ...]]]:
        reader = connect_read_only(project_dir)
        try:
            queries = {
                "profile": """
                    SELECT id, profile_signature, materialization_signature,
                           last_version, requires_full_sync, revision, last_run_id,
                           last_sync_at, created_at, updated_at
                    FROM zotero_sync_profiles
                    ORDER BY id
                """,
                "memberships": """
                    SELECT profile_id, zotero_item_id, is_member, observed_version,
                           first_matched_at, updated_at
                    FROM zotero_profile_items
                    ORDER BY profile_id, zotero_item_id
                """,
                "documents": "SELECT * FROM documents ORDER BY id",
                "texts": "SELECT * FROM document_texts ORDER BY document_id",
                "chunks": "SELECT * FROM chunks ORDER BY document_id, chunk_index, id",
                "vectors": "SELECT * FROM vectors ORDER BY id",
            }
            return {
                name: [tuple(row) for row in reader.execute(query).fetchall()]
                for name, query in queries.items()
            }
        finally:
            reader.close()

    before = published_generation()
    assert before["profile"][0][3:5] == (10, 0)
    assert int(before["profile"][0][5]) >= 1
    assert before["memberships"][0][2] == 1
    assert before["documents"][0][11] == "active"
    assert before["vectors"][0][10] == b"\x00\x00\x80?\x00\x00\x00?"

    stale_client = SyntheticProfileClient(
        version=6,
        changed=[_parent(version=6, collections=())],
        collections=[],
    )
    with pytest.raises(RuntimeError, match=r"(?i)(source|version|backward|older)"):
        _sync(
            project_dir,
            stale_client,
            include_notes=False,
            full=True,
        )

    assert stale_client.since_calls == [0]
    assert published_generation() == before


def test_incomplete_full_rematerialization_preserves_published_generation(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "incomplete-full-rematerialization"
    initial = [
        _parent_with_key("PARENT01", version=5, title="First published paper"),
        _parent_with_key("PARENT02", version=5, title="Second published paper"),
    ]
    first = _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=initial, collections=[]),
        include_notes=False,
    )
    assert first.profile_id is not None
    before = _published_zotero_generation_bytes(project_dir)

    changed = [
        _parent_with_key("PARENT01", version=6, title="Changed first paper"),
        _parent_with_key("PARENT02", version=6, title="Changed second paper"),
    ]
    with pytest.raises(RuntimeError, match=r"(?i)(incomplete|materialization)"):
        _sync(
            project_dir,
            SyntheticProfileClient(version=6, changed=changed, collections=[]),
            include_notes=True,
            full=True,
            limit=1,
        )

    assert _published_zotero_generation_bytes(project_dir) == before
    connection = connect_read_only(project_dir)
    try:
        latest_status = str(
            connection.execute(
                "SELECT status FROM zotero_import_runs "
                "ORDER BY started_at DESC, id DESC"
            ).fetchone()[0]
        )
    finally:
        connection.close()
    assert latest_status == "failed"


def test_regressive_item_aborts_full_rematerialization_generation(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "regressive-full-rematerialization"
    first = _sync(
        project_dir,
        SyntheticProfileClient(
            version=10,
            changed=[_parent(version=10, collections=())],
            collections=[],
        ),
        include_notes=False,
    )
    assert first.last_version_after == 10
    before = _published_zotero_generation_bytes(project_dir)

    with pytest.raises(RuntimeError, match=r"(?i)(regressive|divergent|version)"):
        _sync(
            project_dir,
            SyntheticProfileClient(
                version=11,
                changed=[_parent(version=9, collections=())],
                collections=[],
            ),
            include_notes=True,
            full=True,
        )

    assert _published_zotero_generation_bytes(project_dir) == before


def test_failed_full_rematerialization_rolls_back_published_generation(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "failed-full-rematerialization"
    initial = [
        _parent_with_key("PARENT01", version=5, title="First published paper"),
        _parent_with_key("PARENT02", version=5, title="Second published paper"),
    ]
    _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=initial, collections=[]),
        include_notes=False,
    )
    before = _published_zotero_generation_bytes(project_dir)
    guard_calls = 0

    def fail_after_one_item() -> None:
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 2:
            raise RuntimeError("synthetic full-generation commit failure")

    changed = [
        _parent_with_key("PARENT01", version=6, title="Changed first paper"),
        _parent_with_key("PARENT02", version=6, title="Changed second paper"),
    ]
    with pytest.raises(RuntimeError, match="synthetic full-generation"):
        _sync(
            project_dir,
            SyntheticProfileClient(version=6, changed=changed, collections=[]),
            include_notes=True,
            full=True,
            commit_guard=fail_after_one_item,
        )

    assert guard_calls == 2
    assert _published_zotero_generation_bytes(project_dir) == before


def test_cancelled_full_rematerialization_rolls_back_published_generation(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "cancelled-full-rematerialization"
    initial = [
        _parent_with_key("PARENT01", version=5, title="First published paper"),
        _parent_with_key("PARENT02", version=5, title="Second published paper"),
    ]
    _sync(
        project_dir,
        SyntheticProfileClient(version=5, changed=initial, collections=[]),
        include_notes=False,
    )
    before = _published_zotero_generation_bytes(project_dir)
    cancelled = False

    def request_cancel_after_first_item(
        current: int,
        total: int,
        message: str,
    ) -> None:
        nonlocal cancelled
        del total, message
        if current == 0:
            cancelled = True

    with pytest.raises(ZoteroImportCancelled):
        _sync(
            project_dir,
            SyntheticProfileClient(
                version=6,
                changed=[
                    _parent_with_key(
                        "PARENT01", version=6, title="Changed first paper"
                    ),
                    _parent_with_key(
                        "PARENT02", version=6, title="Changed second paper"
                    ),
                ],
                collections=[],
            ),
            include_notes=True,
            full=True,
            cancel_requested=lambda: cancelled,
            progress_callback=request_cancel_after_first_item,
        )

    assert cancelled is True
    assert _published_zotero_generation_bytes(project_dir) == before
    connection = connect_read_only(project_dir)
    try:
        latest_status = str(
            connection.execute(
                "SELECT status FROM zotero_import_runs "
                "ORDER BY started_at DESC, id DESC"
            ).fetchone()[0]
        )
    finally:
        connection.close()
    assert latest_status == "interrupted"


def test_profile_registration_failure_rolls_back_source_profile_and_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "atomic-profile-registration"
    ensure_database_ready(project_dir)
    before = _zotero_lifecycle_state_bytes(project_dir)
    client = FailingLocatorClient()

    def fail_registration(*args: object, **kwargs: object) -> bool:
        del args, kwargs
        raise RuntimeError("synthetic profile registration failure")

    monkeypatch.setattr(
        Repository,
        "ensure_registered_zotero_profile",
        fail_registration,
    )

    with pytest.raises(RuntimeError, match="synthetic profile registration failure"):
        _sync(project_dir, client)

    assert client.collection_attempts == 0
    assert client.since_calls == []
    assert _zotero_lifecycle_state_bytes(project_dir) == before
