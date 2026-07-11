from __future__ import annotations

import copy
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

import pytest
from click import unstyle
from typer.testing import CliRunner

from paper_galaxy.cli import app
from paper_galaxy.models import ExtractedContent
from paper_galaxy.services.sources import register_zotero_source
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
    stable_zotero_source_id,
)
from paper_galaxy.zotero.local_api import (
    LocalZoteroAPIClient,
    ZoteroAPICancelled,
    ZoteroAPIError,
)
from paper_galaxy.zotero.models import ZoteroDeletedBatch, ZoteroSyncBatch


def _parent(
    *,
    version: int = 5,
    tags: tuple[str, ...] = ("alpha",),
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
            "title": "Synthetic incremental paper",
            "date": "2024",
            "creators": [
                {
                    "creatorType": "author",
                    "firstName": "Ada",
                    "lastName": "Test",
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


def _attachment(*, version: int = 5) -> dict[str, Any]:
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


def _named_attachment(filename: str, *, version: int) -> dict[str, Any]:
    row = _attachment(version=version)
    row["data"]["filename"] = filename
    row["data"]["title"] = filename
    return row


def _annotation(text: str, *, version: int) -> dict[str, Any]:
    return {
        "key": "ANNOT001",
        "version": version,
        "data": {
            "key": "ANNOT001",
            "version": version,
            "itemType": "annotation",
            "parentItem": "PARENT01",
            "annotationType": "highlight",
            "annotationText": text,
            "annotationComment": f"comment: {text}",
        },
    }


def _collection(
    *,
    version: int,
    name: str = "Synthetic collection",
) -> dict[str, Any]:
    return {
        "key": "COLL0001",
        "version": version,
        "data": {
            "key": "COLL0001",
            "version": version,
            "name": name,
        },
    }


class IncrementalZoteroClient:
    """Synthetic read-only sync feed; it never reaches a real Zotero profile."""

    def __init__(
        self,
        *,
        version: int,
        changed: list[dict[str, Any]],
        parents: list[dict[str, Any]] | None = None,
        deleted: dict[str, tuple[str, ...]] | None = None,
        deleted_version: int | None = None,
        collections: list[dict[str, Any]] | None = None,
    ) -> None:
        self.version = version
        self.changed = copy.deepcopy(changed)
        self.parents = copy.deepcopy(parents or [])
        self.deleted_payload = deleted or {}
        self.deleted_version = deleted_version or version
        self.collection_rows = copy.deepcopy(
            [_collection(version=version)] if collections is None else collections
        )
        self.since_calls: list[int] = []
        self.parent_key_calls: list[tuple[str, ...]] = []
        self.children_calls: list[str] = []

    def root(self) -> dict[str, Any]:
        return {"ok": True}

    def sync_collections(
        self,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ZoteroSyncBatch:
        if cancel_requested is not None:
            cancel_requested()
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
        if cancel_requested is not None:
            cancel_requested()
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
        if cancel_requested is not None:
            cancel_requested()
        self.parent_key_calls.append(keys)
        wanted = set(keys)
        return ZoteroSyncBatch(
            records=tuple(row for row in self.parents if row["key"] in wanted),
            library_version=self.version,
        )

    def deleted_since(
        self,
        *,
        since: int,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ZoteroDeletedBatch:
        if cancel_requested is not None:
            cancel_requested()
        assert since == self.since_calls[-1]
        return ZoteroDeletedBatch(
            object_keys=self.deleted_payload,
            library_version=self.deleted_version,
        )

    def item_children(self, item_key: str) -> list[dict[str, Any]]:
        self.children_calls.append(item_key)
        raise AssertionError("incremental sync must not use per-parent child requests")


def test_local_api_sync_preserves_page_version_and_reads_deleted_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        (
            b'[{"key":"PARENT01"}]',
            {
                "Link": (
                    "<http://localhost:23119/api/users/0/items?since=4&start=1>; "
                    'rel="next"'
                ),
                "Last-Modified-Version": "9",
            },
        ),
        (b'[{"key":"NOTE0001"}]', {"Last-Modified-Version": "9"}),
        (
            b'{"items":["OLDITEM1"],"collections":[],"searches":[],"tags":[]}',
            {"Last-Modified-Version": "9"},
        ),
    ]
    calls: list[str] = []

    class FakeHeaders(dict[str, str]):
        def items(self) -> Any:
            return super().items()

    class FakeResponse:
        def __init__(self, raw: bytes, headers: dict[str, str]) -> None:
            self._raw = raw
            self.headers = FakeHeaders(headers)

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return self._raw

    def fake_open(request: Any, *, timeout: float) -> FakeResponse:
        del timeout
        calls.append(request.full_url)
        raw, headers = responses.pop(0)
        return FakeResponse(raw, headers)

    monkeypatch.setattr("paper_galaxy.zotero.local_api._open_local_request", fake_open)
    client = LocalZoteroAPIClient("http://localhost:23119/api")

    changed = client.sync_items(since=4)
    deleted = client.deleted_since(since=4)

    assert [row["key"] for row in changed.records] == ["PARENT01", "NOTE0001"]
    assert changed.library_version == 9
    assert changed.complete is True
    assert deleted.object_keys["items"] == ("OLDITEM1",)
    assert deleted.library_version == 9
    assert calls == [
        "http://localhost:23119/api/users/0/items?since=4&start=0",
        "http://localhost:23119/api/users/0/items?since=4&start=1",
        "http://localhost:23119/api/users/0/deleted?since=4",
    ]


def test_local_api_sync_rejects_version_change_between_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        (b"[]", "8", '<http://localhost:23119/api/users/0/items?start=1>; rel="next"'),
        (b"[]", "9", ""),
    ]

    class FakeResponse:
        def __init__(self, raw: bytes, version: str, link: str) -> None:
            self._raw = raw
            self.headers = {
                "Last-Modified-Version": version,
                "Link": link,
            }

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return self._raw

    def fake_open(request: Any, *, timeout: float) -> FakeResponse:
        del request, timeout
        return FakeResponse(*responses.pop(0))

    monkeypatch.setattr("paper_galaxy.zotero.local_api._open_local_request", fake_open)

    with pytest.raises(ZoteroAPIError, match="changed during pagination"):
        LocalZoteroAPIClient("http://localhost:23119/api").sync_items(since=4)


def test_local_api_sync_retries_transient_read_without_changing_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeResponse:
        def __init__(self) -> None:
            self.headers = {"Last-Modified-Version": "4", "Link": ""}

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"[]"

    def flaky_open(request: Any, *, timeout: float) -> FakeResponse:
        del timeout
        calls.append(request.full_url)
        if len(calls) == 1:
            raise URLError("synthetic transient failure")
        return FakeResponse()

    monkeypatch.setattr("paper_galaxy.zotero.local_api._open_local_request", flaky_open)
    result = LocalZoteroAPIClient(
        "http://localhost:23119/api", max_attempts=2
    ).sync_items(since=3)

    assert result.library_version == 4
    assert calls == [
        "http://localhost:23119/api/users/0/items?since=3&start=0",
        "http://localhost:23119/api/users/0/items?since=3&start=0",
    ]


def test_local_api_sync_does_not_retry_non_transient_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def reject(request: Any, *, timeout: float) -> Any:
        nonlocal calls
        del timeout
        calls += 1
        raise HTTPError(request.full_url, 400, "bad request", {}, None)

    monkeypatch.setattr("paper_galaxy.zotero.local_api._open_local_request", reject)
    with pytest.raises(ZoteroAPIError, match="HTTP 400"):
        LocalZoteroAPIClient("http://localhost:23119/api", max_attempts=3).sync_items(
            since=3
        )
    assert calls == 1


def test_local_api_sync_rejects_pagination_cycle_and_missing_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_number = 0

    class FakeResponse:
        def __init__(self, headers: dict[str, str]) -> None:
            self.headers = headers

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"[]"

    def cycle(request: Any, *, timeout: float) -> FakeResponse:
        nonlocal response_number
        del timeout
        response_number += 1
        return FakeResponse(
            {
                "Last-Modified-Version": "4",
                "Link": f'<{request.full_url}>; rel="next"',
            }
        )

    monkeypatch.setattr("paper_galaxy.zotero.local_api._open_local_request", cycle)
    with pytest.raises(ZoteroAPIError, match="repeated page URL"):
        LocalZoteroAPIClient("http://localhost:23119/api").sync_items(since=3)
    assert response_number == 1

    monkeypatch.setattr(
        "paper_galaxy.zotero.local_api._open_local_request",
        lambda request, *, timeout: FakeResponse({"Link": ""}),
    )
    with pytest.raises(ZoteroAPIError, match="Last-Modified-Version"):
        LocalZoteroAPIClient("http://localhost:23119/api").sync_items(since=3)


def test_default_sync_uses_persisted_profile_cursor_and_isolates_filters(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    first_client = IncrementalZoteroClient(
        version=5,
        changed=[_parent(tags=("alpha", "beta")), _note("first note", version=5)],
    )
    first = import_from_zotero(
        project_dir=project_dir,
        client=first_client,
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    warm_client = IncrementalZoteroClient(version=5, changed=[])
    warm = import_from_zotero(
        project_dir=project_dir,
        client=warm_client,
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    alpha_client = IncrementalZoteroClient(
        version=5,
        changed=[_parent(tags=("alpha", "beta")), _note("first note", version=5)],
    )
    beta_client = IncrementalZoteroClient(
        version=5,
        changed=[_parent(tags=("alpha", "beta")), _note("first note", version=5)],
    )
    import_from_zotero(
        project_dir=project_dir,
        client=alpha_client,
        tags=("alpha",),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    import_from_zotero(
        project_dir=project_dir,
        client=beta_client,
        tags=("beta",),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )

    assert first.last_version_before is None
    assert first.last_version_after == 5
    assert warm.last_version_before == 5
    assert warm.last_version_after == 5
    assert first_client.since_calls == [0]
    assert warm_client.since_calls == [5]
    assert alpha_client.since_calls == [0]
    assert beta_client.since_calls == [0]
    with sqlite3.connect(resolve_database_path(project_dir)) as connection:
        rows = connection.execute(
            "SELECT profile_signature, last_version FROM zotero_sync_profiles"
        ).fetchall()
    assert len(rows) == 3
    assert {row[1] for row in rows} == {5}


def test_child_only_update_refreshes_parent_without_children_n_plus_one(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    import_from_zotero(
        project_dir=project_dir,
        client=IncrementalZoteroClient(
            version=5,
            changed=[_parent(), _note("old personal note", version=5)],
        ),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    changed_client = IncrementalZoteroClient(
        version=6,
        changed=[_note("new personal note", version=6)],
        parents=[_parent()],
    )
    summary = import_from_zotero(
        project_dir=project_dir,
        client=changed_client,
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )

    connection = connect_read_only(project_dir)
    try:
        text = connection.execute("SELECT text FROM document_texts").fetchone()[0]
        child = connection.execute(
            "SELECT version, data_json FROM zotero_child_items WHERE zotero_key = ?",
            ("NOTE0001",),
        ).fetchone()
    finally:
        connection.close()

    assert changed_client.since_calls == [5]
    assert changed_client.parent_key_calls == [("PARENT01",)]
    assert changed_client.children_calls == []
    assert "new personal note" in text
    assert "old personal note" not in text
    assert tuple(child)[:1] == (6,)
    assert summary.changed_parents == 0
    assert summary.changed_children == 1
    assert summary.last_version_after == 6


@pytest.mark.parametrize(
    ("old_child", "new_child", "old_marker", "new_marker"),
    [
        (
            _annotation("old annotation", version=5),
            _annotation("new annotation", version=6),
            "old annotation",
            "new annotation",
        ),
        (
            _named_attachment("old-paper.pdf", version=5),
            _named_attachment("new-paper.pdf", version=6),
            "old-paper.pdf",
            "new-paper.pdf",
        ),
    ],
)
def test_annotation_and_attachment_only_updates_refresh_parent(
    tmp_path: Path,
    old_child: dict[str, Any],
    new_child: dict[str, Any],
    old_marker: str,
    new_marker: str,
) -> None:
    project_dir = tmp_path / "project"
    import_from_zotero(
        project_dir=project_dir,
        client=IncrementalZoteroClient(
            version=5,
            changed=[_parent(), old_child],
        ),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    summary = import_from_zotero(
        project_dir=project_dir,
        client=IncrementalZoteroClient(
            version=6,
            changed=[new_child],
            parents=[_parent()],
        ),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    connection = connect_read_only(project_dir)
    try:
        text = connection.execute("SELECT text FROM document_texts").fetchone()[0]
    finally:
        connection.close()

    assert new_marker in text
    assert old_marker not in text
    assert summary.changed_children == 1


def test_remote_parent_delete_is_tombstoned_and_hidden_from_search(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    import_from_zotero(
        project_dir=project_dir,
        client=IncrementalZoteroClient(
            version=5,
            changed=[_parent(), _note("supporting evidence", version=5)],
        ),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    summary = import_from_zotero(
        project_dir=project_dir,
        client=IncrementalZoteroClient(
            version=6,
            changed=[],
            deleted={"items": ("PARENT01",)},
        ),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )

    connection = connect_read_only(project_dir)
    try:
        item = connection.execute(
            "SELECT deleted_at, deleted_version FROM zotero_items"
        ).fetchone()
        document = connection.execute("SELECT status FROM documents").fetchone()
        tombstone = connection.execute(
            "SELECT object_type, zotero_key, library_version FROM zotero_tombstones"
        ).fetchone()
        repository = Repository(connection, resolve_database_path(project_dir))
        search = repository.search_documents("Synthetic")
        visible_items = repository.list_zotero_items()
    finally:
        connection.close()

    assert item[0] is not None and item[1] == 6
    assert document[0] == "missing"
    assert tuple(tombstone) == ("item", "PARENT01", 6)
    assert search == []
    assert visible_items == []
    assert summary.deleted_records == 1
    assert summary.last_version_before == 5
    assert summary.last_version_after == 6


def test_concurrent_remote_version_change_preserves_cursor_and_content(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    import_from_zotero(
        project_dir=project_dir,
        client=IncrementalZoteroClient(
            version=5,
            changed=[_parent(), _note("stable evidence", version=5)],
        ),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    database_path = resolve_database_path(project_dir)
    before = database_path.read_bytes()

    with pytest.raises(RuntimeError, match="changed during Zotero sync"):
        import_from_zotero(
            project_dir=project_dir,
            client=IncrementalZoteroClient(
                version=6,
                changed=[_note("unstable evidence", version=6)],
                parents=[_parent()],
                deleted_version=7,
            ),
            pdf_policy="metadata",
            build_reading_map=False,
            min_chars=1,
        )

    # The failed run audit may change SQLite bytes, but content and cursor may not.
    assert database_path.read_bytes() != before
    with sqlite3.connect(database_path) as connection:
        cursor = connection.execute(
            "SELECT last_version FROM zotero_sync_profiles"
        ).fetchone()[0]
        text = connection.execute("SELECT text FROM document_texts").fetchone()[0]
        runs = connection.execute(
            "SELECT status FROM zotero_import_runs ORDER BY started_at, rowid"
        ).fetchall()
    assert cursor == 5
    assert "stable evidence" in text
    assert "unstable evidence" not in text
    assert runs == [("completed",), ("failed",)]


def test_verified_child_delete_rebuilds_parent_and_keeps_it_active(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    import_from_zotero(
        project_dir=project_dir,
        client=IncrementalZoteroClient(
            version=5,
            changed=[_parent(), _note("temporary note evidence", version=5)],
        ),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    deleted_client = IncrementalZoteroClient(
        version=6,
        changed=[],
        parents=[_parent()],
        deleted={"items": ("NOTE0001",)},
    )
    summary = import_from_zotero(
        project_dir=project_dir,
        client=deleted_client,
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )

    connection = connect_read_only(project_dir)
    try:
        text = connection.execute("SELECT text FROM document_texts").fetchone()[0]
        document_status = connection.execute("SELECT status FROM documents").fetchone()[
            0
        ]
        child = connection.execute(
            "SELECT deleted_at, deleted_version FROM zotero_child_items"
        ).fetchone()
        parent = connection.execute("SELECT deleted_at FROM zotero_items").fetchone()
    finally:
        connection.close()

    assert deleted_client.parent_key_calls == [("PARENT01",)]
    assert "temporary note evidence" not in text
    assert document_status == "active"
    assert child[0] is not None and child[1] == 6
    assert parent[0] is None
    assert summary.deleted_records == 1
    assert summary.last_version_after == 6


def test_incomplete_limited_sync_never_advances_profile_cursor(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    partial = IncrementalZoteroClient(
        version=5,
        changed=[_parent(), _note("not fetched", version=5)],
    )
    summary = import_from_zotero(
        project_dir=project_dir,
        client=partial,
        limit=1,
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    retry = IncrementalZoteroClient(
        version=5,
        changed=[_parent(), _note("now complete", version=5)],
    )
    retried = import_from_zotero(
        project_dir=project_dir,
        client=retry,
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )

    assert partial.since_calls == [0]
    assert summary.last_version_after is None
    assert any("cursor was not advanced" in warning for warning in summary.warnings)
    assert retry.since_calls == [0]
    assert retried.last_version_after == 5


def test_empty_complete_delta_advances_to_response_header_version(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    import_from_zotero(
        project_dir=project_dir,
        client=IncrementalZoteroClient(version=5, changed=[_parent()]),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    empty = IncrementalZoteroClient(version=7, changed=[])
    summary = import_from_zotero(
        project_dir=project_dir,
        client=empty,
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )

    assert empty.since_calls == [5]
    assert summary.items_seen == 0
    assert summary.last_version_before == 5
    assert summary.last_version_after == 7


def test_force_rematerialization_does_not_silently_become_full_sync(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    import_from_zotero(
        project_dir=project_dir,
        client=IncrementalZoteroClient(version=5, changed=[_parent()]),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    forced_client = IncrementalZoteroClient(version=6, changed=[])
    summary = import_from_zotero(
        project_dir=project_dir,
        client=forced_client,
        force=True,
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )

    assert forced_client.since_calls == [5]
    assert summary.full_sync is False
    assert summary.last_version_after == 6


def test_explicit_full_sync_skips_pdf_extraction_for_identical_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    data_dir = tmp_path / "Zotero"
    attachment_dir = data_dir / "storage" / "ATTACH01"
    attachment_dir.mkdir(parents=True)
    (attachment_dir / "synthetic.pdf").write_bytes(b"synthetic pdf bytes")
    calls: list[Path] = []

    def extract_once(path: Path) -> tuple[ExtractedContent, None]:
        calls.append(path)
        return (
            ExtractedContent(
                title="Synthetic PDF",
                text="stable extracted text",
                method="synthetic",
            ),
            None,
        )

    monkeypatch.setattr("paper_galaxy.zotero.importers.extract_pdf_file", extract_once)
    rows = [_parent(), _attachment()]
    import_from_zotero(
        project_dir=project_dir,
        data_dir=data_dir,
        client=IncrementalZoteroClient(version=5, changed=rows),
        build_reading_map=False,
        min_chars=1,
    )
    before_connection = connect_read_only(project_dir)
    try:
        before_chunks = before_connection.execute(
            "SELECT id, text, text_sha256 FROM chunks ORDER BY id"
        ).fetchall()
    finally:
        before_connection.close()

    summary = import_from_zotero(
        project_dir=project_dir,
        data_dir=data_dir,
        client=IncrementalZoteroClient(version=5, changed=rows),
        full=True,
        build_reading_map=False,
        min_chars=1,
    )
    after_connection = connect_read_only(project_dir)
    try:
        after_chunks = after_connection.execute(
            "SELECT id, text, text_sha256 FROM chunks ORDER BY id"
        ).fetchall()
    finally:
        after_connection.close()

    assert len(calls) == 1
    assert [tuple(row) for row in after_chunks] == [tuple(row) for row in before_chunks]
    assert summary.items_unchanged == 1


def test_profile_cursor_compare_and_swap_rejects_lost_race(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    first = import_from_zotero(
        project_dir=project_dir,
        client=IncrementalZoteroClient(version=5, changed=[_parent()]),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    assert first.profile_id is not None
    connection = connect_read_write(project_dir)
    try:
        repository = Repository(connection, resolve_database_path(project_dir))
        state = connection.execute(
            """
            SELECT revision, last_version, materialization_signature
            FROM zotero_sync_profiles WHERE id = ?
            """,
            (first.profile_id,),
        ).fetchone()
        with connection:
            connection.execute(
                "UPDATE zotero_sync_profiles SET revision = revision + 1 WHERE id = ?",
                (first.profile_id,),
            )
        with pytest.raises(RuntimeError, match="changed concurrently"):
            with connection:
                repository.complete_zotero_sync_profile(
                    profile_id=first.profile_id,
                    expected_last_version=state["last_version"],
                    expected_revision=state["revision"],
                    expected_materialization_signature=state[
                        "materialization_signature"
                    ],
                    last_version=6,
                    run_id=first.run_id,
                    now="2026-01-01T00:00:00+00:00",
                )
    finally:
        connection.close()


def test_migrated_default_profile_keeps_its_identity_for_job_sync(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    ensure_database_ready(project_dir)
    api_url = "http://localhost:23119/api"
    zotero_source_id = stable_zotero_source_id(api_url, "0")
    connection = connect_read_write(project_dir)
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO zotero_sources(
                  id, source_type, local_api_url, library_id, library_type,
                  name, created_at, updated_at
                ) VALUES (?, 'local_api', ?, '0', 'user', 'Legacy profile', ?, ?)
                """,
                (
                    zotero_source_id,
                    api_url,
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
    finally:
        connection.close()
    profile, _ = register_zotero_source(
        project_dir,
        zotero_source_id,
        filters={},
    )

    summary = import_from_zotero(
        project_dir=project_dir,
        api_url=api_url,
        client=IncrementalZoteroClient(version=5, changed=[_parent()]),
        registered_profile_id=profile.id,
        pdf_policy="extract",
        build_reading_map=False,
        min_chars=1,
    )

    assert summary.profile_id == profile.id
    assert summary.last_version_after == 5


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"limit": 0}, "--limit"),
        ({"limit": 10_001}, "--limit"),
        ({"limit": True}, "--limit"),
        ({"since_version": -1}, "--since-version"),
        ({"since_version": True}, "--since-version"),
    ],
)
def test_invalid_incremental_bounds_are_rejected_before_project_writes(
    tmp_path: Path,
    kwargs: dict[str, Any],
    message: str,
) -> None:
    project_dir = tmp_path / "project"

    with pytest.raises(ValueError, match=message):
        import_from_zotero(
            project_dir=project_dir,
            client=IncrementalZoteroClient(version=5, changed=[]),
            build_reading_map=False,
            **kwargs,
        )

    assert not project_dir.exists()


def test_explicit_since_must_match_cursor_before_any_write(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    import_from_zotero(
        project_dir=project_dir,
        client=IncrementalZoteroClient(version=5, changed=[_parent()]),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    database_path = resolve_database_path(project_dir)
    before = database_path.read_bytes()

    with pytest.raises(ValueError, match="exactly match"):
        import_from_zotero(
            project_dir=project_dir,
            client=IncrementalZoteroClient(version=100, changed=[]),
            since_version=100,
            pdf_policy="metadata",
            build_reading_map=False,
            min_chars=1,
        )

    assert database_path.read_bytes() == before
    with sqlite3.connect(database_path) as connection:
        assert (
            connection.execute(
                "SELECT last_version FROM zotero_sync_profiles"
            ).fetchone()[0]
            == 5
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM zotero_import_runs").fetchone()[0]
            == 1
        )


def test_new_profile_rejects_nonzero_since_without_creating_project(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"

    with pytest.raises(ValueError, match="must start"):
        import_from_zotero(
            project_dir=project_dir,
            client=IncrementalZoteroClient(version=5, changed=[]),
            since_version=4,
            build_reading_map=False,
        )

    assert not project_dir.exists()


def test_dry_run_reads_real_cursor_without_modifying_existing_database(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    first = import_from_zotero(
        project_dir=project_dir,
        client=IncrementalZoteroClient(version=5, changed=[_parent()]),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    database_path = resolve_database_path(project_dir)
    before_bytes = database_path.read_bytes()
    before_mtime = database_path.stat().st_mtime_ns
    client = IncrementalZoteroClient(version=6, changed=[])

    summary = import_from_zotero(
        project_dir=project_dir,
        client=client,
        dry_run=True,
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )

    assert summary.profile_id == first.profile_id
    assert summary.last_version_before == 5
    assert summary.last_version_after == 6
    assert client.since_calls == [5]
    assert database_path.read_bytes() == before_bytes
    assert database_path.stat().st_mtime_ns == before_mtime


def test_new_project_dry_run_never_creates_project_state(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"

    summary = import_from_zotero(
        project_dir=project_dir,
        client=IncrementalZoteroClient(version=5, changed=[_parent()]),
        dry_run=True,
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )

    assert summary.last_version_before is None
    assert summary.last_version_after == 5
    assert not project_dir.exists()


def test_saved_collection_name_survives_remote_rename_and_refreshes_documents(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    import_from_zotero(
        project_dir=project_dir,
        client=IncrementalZoteroClient(version=5, changed=[_parent()]),
        collection="Synthetic collection",
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    renamed = IncrementalZoteroClient(
        version=6,
        changed=[],
        parents=[_parent()],
        collections=[_collection(version=6, name="Renamed collection")],
    )

    summary = import_from_zotero(
        project_dir=project_dir,
        client=renamed,
        collection="Synthetic collection",
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )

    assert renamed.parent_key_calls == [("PARENT01",)]
    assert summary.last_version_after == 6
    connection = connect_read_only(project_dir)
    try:
        text = connection.execute("SELECT text FROM document_texts").fetchone()[0]
    finally:
        connection.close()
    assert "Renamed collection" in text
    assert "Synthetic collection" not in text

    warm = IncrementalZoteroClient(
        version=7,
        changed=[],
        collections=[_collection(version=6, name="Renamed collection")],
    )
    repeated = import_from_zotero(
        project_dir=project_dir,
        client=warm,
        collection="Synthetic collection",
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    assert repeated.last_version_before == 6
    assert repeated.last_version_after == 7


def test_saved_collection_delete_rebuilds_parent_and_advances_cursor(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    import_from_zotero(
        project_dir=project_dir,
        client=IncrementalZoteroClient(version=5, changed=[_parent()]),
        collection="Synthetic collection",
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    deleted = IncrementalZoteroClient(
        version=6,
        changed=[],
        parents=[_parent(version=6, collections=())],
        collections=[],
        deleted={"collections": ("COLL0001",)},
    )

    summary = import_from_zotero(
        project_dir=project_dir,
        client=deleted,
        collection="Synthetic collection",
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )

    assert deleted.parent_key_calls == [("PARENT01",)]
    assert summary.last_version_after == 6
    connection = connect_read_only(project_dir)
    try:
        text = connection.execute("SELECT text FROM document_texts").fetchone()[0]
        collection_row = connection.execute(
            "SELECT deleted_at, deleted_version FROM zotero_collections"
        ).fetchone()
        cursor = connection.execute(
            "SELECT last_version FROM zotero_sync_profiles"
        ).fetchone()[0]
    finally:
        connection.close()
    assert "Synthetic collection" not in text
    assert collection_row[0] is not None and collection_row[1] == 6
    assert cursor == 6


def test_local_api_cancels_between_pagination_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    checks = 0

    class FakeResponse:
        def __init__(self) -> None:
            self.headers = {
                "Last-Modified-Version": "5",
                "Link": (
                    "<http://localhost:23119/api/users/0/items?since=4&start=1>; "
                    'rel="next"'
                ),
            }

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'[{"key":"PARENT01"}]'

    def fake_open(request: Any, *, timeout: float) -> FakeResponse:
        nonlocal calls
        del request, timeout
        calls += 1
        return FakeResponse()

    def cancel_after_first_page() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    monkeypatch.setattr("paper_galaxy.zotero.local_api._open_local_request", fake_open)

    with pytest.raises(ZoteroAPICancelled, match="pagination boundary"):
        LocalZoteroAPIClient("http://localhost:23119/api").sync_items(
            since=4,
            cancel_requested=cancel_after_first_page,
        )

    assert calls == 1


def test_api_boundary_cancel_maps_to_interrupted_import_without_cursor(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    cancel_state = {"requested": False}

    class CancellingClient(IncrementalZoteroClient):
        def sync_items(
            self,
            *,
            since: int,
            limit: int | None = None,
            cancel_requested: Callable[[], bool] | None = None,
        ) -> ZoteroSyncBatch:
            del limit
            self.since_calls.append(since)
            cancel_state["requested"] = True
            assert cancel_requested is not None
            cancel_requested()
            raise AssertionError("cancel callback must interrupt the API batch")

    with pytest.raises(ZoteroImportCancelled):
        import_from_zotero(
            project_dir=project_dir,
            client=CancellingClient(version=5, changed=[]),
            cancel_requested=lambda: cancel_state["requested"],
            build_reading_map=False,
        )

    with sqlite3.connect(resolve_database_path(project_dir)) as connection:
        assert (
            connection.execute("SELECT status FROM zotero_import_runs").fetchone()[0]
            == "interrupted"
        )
        assert (
            connection.execute(
                "SELECT last_version FROM zotero_sync_profiles"
            ).fetchone()[0]
            is None
        )


def test_zotero_import_help_distinguishes_force_from_full() -> None:
    result = CliRunner().invoke(app, ["zotero", "import", "--help"])
    normalized = " ".join(unstyle(result.output).replace("│", " ").split())

    assert result.exit_code == 0
    assert "Rematerialize records returned by the changed feed" in normalized
    assert "Combine with --full" in normalized
