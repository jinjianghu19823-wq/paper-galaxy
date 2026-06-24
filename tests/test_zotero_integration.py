from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from paper_galaxy.cli import app
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path
from paper_galaxy.web.server import create_app
from paper_galaxy.zotero.attachments import resolve_attachment_path
from paper_galaxy.zotero.detect import default_data_dir_guesses, detect_zotero
from paper_galaxy.zotero.filters import ZoteroFilterError
from paper_galaxy.zotero.importers import import_from_zotero
from paper_galaxy.zotero.local_api import LocalZoteroAPIClient, ZoteroAPIError
from paper_galaxy.zotero.models import ZoteroAttachment, ZoteroDetection
from paper_galaxy.zotero.normalize import normalize_child, normalize_item

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "zotero"


class FakeZoteroClient:
    def __init__(self) -> None:
        self.children_calls: list[str] = []

    def root(self) -> dict[str, Any]:
        return {"ok": True}

    def collections(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        return _load("collections.json")[:limit]

    def tags(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        return _load("tags.json")[:limit]

    def top_items(
        self,
        *,
        limit: int | None = None,
        start: int = 0,
        since: int | None = None,
    ) -> list[dict[str, Any]]:
        del start, since
        return _load("top_items.json")[:limit]

    def items(
        self,
        *,
        limit: int | None = None,
        start: int = 0,
        since: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.top_items(limit=limit, start=start, since=since)

    def item_children(self, item_key: str) -> list[dict[str, Any]]:
        self.children_calls.append(item_key)
        path = FIXTURE_DIR / f"children_{item_key}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def collection_items(
        self,
        collection_key: str,
        *,
        limit: int | None = None,
        since: int | None = None,
    ) -> list[dict[str, Any]]:
        del since
        rows = [
            row
            for row in self.top_items(limit=None)
            if collection_key in row["data"].get("collections", [])
        ]
        return rows[:limit]


def test_normalize_item_child_note_and_attachment() -> None:
    item = normalize_item(_load("top_items.json")[0])
    attachment = normalize_child(_load("children_AAAA1111.json")[0])
    note = normalize_child(_load("children_AAAA1111.json")[1])

    assert item.key == "AAAA1111"
    assert item.year == "2021"
    assert item.creators[0].display_name == "Ada Lovelace"
    assert item.tags[0].tag == "read"
    assert isinstance(attachment, ZoteroAttachment)
    assert attachment.path == "storage:fourier.pdf"
    assert note is not None
    assert "spectral kernels" in note.text


def test_attachment_resolution_handles_storage_and_missing(tmp_path: Path) -> None:
    attachment = ZoteroAttachment(
        key="ATTACH11",
        version=1,
        title="Stored PDF",
        filename="fourier.pdf",
        content_type="application/pdf",
        path="storage:fourier.pdf",
        link_mode="imported_file",
        parent_key="AAAA1111",
    )
    stored = tmp_path / "storage" / "ATTACH11"
    stored.mkdir(parents=True)
    pdf = stored / "fourier.pdf"
    pdf.write_bytes(b"%PDF-1.4 synthetic")

    resolved = resolve_attachment_path(attachment, data_dir=tmp_path)
    missing = resolve_attachment_path(
        ZoteroAttachment(
            key="MISSING1",
            version=1,
            title="Missing",
            filename="missing.pdf",
            content_type="application/pdf",
            path="storage:missing.pdf",
            link_mode="imported_file",
            parent_key="AAAA1111",
        ),
        data_dir=tmp_path,
    )

    assert resolved.status == "resolved"
    assert resolved.resolved_path == pdf.resolve()
    assert missing.status == "missing"


def test_detect_explicit_data_dir_without_real_zotero(
    monkeypatch: object, tmp_path: Path
) -> None:
    (tmp_path / "zotero.sqlite").write_bytes(b"not a sqlite db")
    (tmp_path / "storage").mkdir()

    class FakeClient:
        def __init__(self, api_url: str, *, timeout: float) -> None:
            del api_url, timeout

        def root(self) -> dict[str, object]:
            return {"ok": True}

    monkeypatch.setattr("paper_galaxy.zotero.detect.LocalZoteroAPIClient", FakeClient)
    detection = detect_zotero(data_dir=tmp_path)

    assert detection.api_reachable is True
    assert detection.database_exists is True
    assert detection.storage_exists is True
    assert default_data_dir_guesses()


def test_local_api_client_sends_zotero_header(monkeypatch: object) -> None:
    captured: dict[str, str] = {}

    class FakeHeaders(dict[str, str]):
        def items(self) -> Any:
            return super().items()

    class FakeResponse:
        headers = FakeHeaders({"Link": ""})

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        del timeout
        captured["version"] = request.headers["Zotero-api-version"]
        return FakeResponse()

    monkeypatch.setattr("paper_galaxy.zotero.local_api.urlopen", fake_urlopen)
    payload = LocalZoteroAPIClient("http://localhost:23119/api").root()

    assert payload["ok"] is True
    assert captured["version"] == "3"


def test_local_api_client_accepts_plain_text_root(monkeypatch: object) -> None:
    class FakeHeaders(dict[str, str]):
        def items(self) -> Any:
            return super().items()

    class FakeResponse:
        headers = FakeHeaders(
            {
                "Content-Type": "text/plain",
                "Zotero-API-Version": "3",
                "Zotero-Schema-Version": "42",
            }
        )

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"Nothing to see here."

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        del request, timeout
        return FakeResponse()

    monkeypatch.setattr("paper_galaxy.zotero.local_api.urlopen", fake_urlopen)
    payload = LocalZoteroAPIClient("http://localhost:23119/api").root()

    assert payload["value"] == "Nothing to see here."
    assert payload["headers"]["zotero-api-version"] == "3"


def test_local_api_client_follows_next_link(monkeypatch: object) -> None:
    calls: list[str] = []
    responses = [
        (
            b'[{"key": "AAAA1111"}]',
            '<http://localhost:23119/api/users/0/items/top?start=1>; rel="next"',
        ),
        (b'[{"key": "BBBB2222"}]', ""),
    ]

    class FakeHeaders(dict[str, str]):
        def items(self) -> Any:
            return super().items()

    class FakeResponse:
        def __init__(self, raw: bytes, link: str) -> None:
            self._raw = raw
            self.headers = FakeHeaders({"Link": link})

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return self._raw

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        del timeout
        calls.append(request.full_url)
        raw, link = responses.pop(0)
        return FakeResponse(raw, link)

    monkeypatch.setattr("paper_galaxy.zotero.local_api.urlopen", fake_urlopen)
    items = LocalZoteroAPIClient("http://localhost:23119/api").top_items()

    assert [item["key"] for item in items] == ["AAAA1111", "BBBB2222"]
    assert calls == [
        "http://localhost:23119/api/users/0/items/top?start=0",
        "http://localhost:23119/api/users/0/items/top?start=1",
    ]


def test_local_api_collection_items_passes_since(monkeypatch: object) -> None:
    captured: dict[str, str] = {}

    class FakeHeaders(dict[str, str]):
        def items(self) -> Any:
            return super().items()

    class FakeResponse:
        headers = FakeHeaders({"Link": ""})

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"[]"

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        del timeout
        captured["url"] = request.full_url
        return FakeResponse()

    monkeypatch.setattr("paper_galaxy.zotero.local_api.urlopen", fake_urlopen)
    rows = LocalZoteroAPIClient("http://localhost:23119/api").collection_items(
        "COLLREAD",
        limit=2,
        since=7,
    )

    assert rows == []
    assert captured["url"].endswith(
        "/users/0/collections/COLLREAD/items?since=7&limit=2"
    )


def test_import_creates_zotero_rows_documents_and_map_run(tmp_path: Path) -> None:
    data_dir = tmp_path / "Zotero"
    pdf_dir = data_dir / "storage" / "ATTACH11"
    pdf_dir.mkdir(parents=True)
    (pdf_dir / "fourier.pdf").write_bytes(b"not a real pdf")

    summary = import_from_zotero(
        project_dir=tmp_path,
        data_dir=data_dir,
        client=FakeZoteroClient(),
        build_reading_map=True,
        min_chars=20,
    )
    connection = connect_database(tmp_path)
    try:
        initialize_database(connection)
        repository = Repository(connection, resolve_database_path(tmp_path))
        stats = repository.zotero_stats()
        items = repository.list_zotero_items()
        map_runs = repository.list_map_runs()
    finally:
        connection.close()

    assert summary.items_seen == 3
    assert summary.items_imported == 3
    assert summary.attachments_seen == 1
    assert summary.notes_imported == 2
    assert summary.annotations_imported == 1
    assert summary.map_run_id
    assert stats["imported_item_count"] == 3
    assert stats["imported_document_count"] == 3
    assert {item["reading_status"] for item in items} >= {"read", "reading", "to_read"}
    assert any(run["name"] == "Zotero Reading Graph" for run in map_runs)


def test_import_collection_name_is_idempotent(tmp_path: Path) -> None:
    client = FakeZoteroClient()
    first = import_from_zotero(
        project_dir=tmp_path,
        client=client,
        collection="Read Papers",
        build_reading_map=False,
        min_chars=20,
    )
    second = import_from_zotero(
        project_dir=tmp_path,
        client=FakeZoteroClient(),
        collection="COLLREAD",
        build_reading_map=False,
        min_chars=20,
    )

    connection = connect_database(tmp_path)
    try:
        initialize_database(connection)
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in [
                "documents",
                "zotero_items",
                "zotero_creators",
                "zotero_item_tags",
                "zotero_item_collections",
                "zotero_attachments",
                "zotero_document_links",
            ]
        }
        text = connection.execute(
            """
            SELECT dt.text
            FROM document_texts dt
            JOIN documents d ON d.id = dt.document_id
            WHERE d.title = 'Deep Operator Network Reading Note'
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert first.items_imported == 2
    assert second.items_unchanged == 2
    assert counts["documents"] == 2
    assert counts["zotero_items"] == 2
    assert counts["zotero_creators"] == 2
    assert counts["zotero_item_tags"] == 4
    assert counts["zotero_item_collections"] == 2
    assert counts["zotero_attachments"] == 1
    assert counts["zotero_document_links"] == 2
    assert "Branch net carries sensor values" in text
    assert client.children_calls == ["AAAA1111", "BBBB2222"]


def test_import_dry_run_writes_nothing(tmp_path: Path) -> None:
    summary = import_from_zotero(
        project_dir=tmp_path,
        client=FakeZoteroClient(),
        dry_run=True,
        build_reading_map=False,
    )

    assert summary.dry_run is True
    assert summary.items_seen == 3
    assert not resolve_database_path(tmp_path).exists()


def test_import_invalid_status_and_alias(tmp_path: Path) -> None:
    try:
        import_from_zotero(
            project_dir=tmp_path,
            client=FakeZoteroClient(),
            include_status="finished",
            dry_run=True,
        )
    except ZoteroFilterError as exc:
        assert "Invalid --include-status" in str(exc)
    else:
        raise AssertionError("invalid include status should fail")

    summary = import_from_zotero(
        project_dir=tmp_path,
        client=FakeZoteroClient(),
        include_status="unclassified",
        dry_run=True,
    )

    assert summary.include_status == "unknown"
    assert any("deprecated" in warning for warning in summary.warnings)


def test_zotero_items_cli_filters_collection_key_and_name(
    monkeypatch: object,
) -> None:
    runner = CliRunner()

    class FakeCLIClient(FakeZoteroClient):
        def __init__(self, api_url: str) -> None:
            del api_url
            super().__init__()

    monkeypatch.setattr("paper_galaxy.cli.LocalZoteroAPIClient", FakeCLIClient)

    by_key = runner.invoke(app, ["zotero", "items", "--collection", "COLLTODO"])
    by_name = runner.invoke(app, ["zotero", "items", "--collection", "Read Papers"])

    assert by_key.exit_code == 0, by_key.output
    assert "Queue Paper About Sparse Solvers" in by_key.output
    assert "Fourier Neural Operator Memory" not in by_key.output
    assert by_name.exit_code == 0, by_name.output
    assert "AAAA1111" in by_name.output
    assert "BBBB2222" in by_name.output
    assert "CCCC3333" not in by_name.output
    assert "Queue Paper About Sparse Solvers" not in by_name.output


def test_zotero_doctor_json_out_does_not_create_project_db(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "Zotero"
    data_dir.mkdir()
    (data_dir / "zotero.sqlite").write_bytes(b"not sqlite")
    (data_dir / "storage").mkdir()

    class FakeRootResponse:
        data = "Nothing to see here."
        headers: ClassVar[dict[str, str]] = {
            "zotero-api-version": "3",
            "zotero-schema-version": "42",
        }

    class FakeDoctorClient(FakeZoteroClient):
        def __init__(self, api_url: str, *, timeout: float) -> None:
            del api_url, timeout
            super().__init__()

        def root_response(self) -> FakeRootResponse:
            return FakeRootResponse()

        def top_items_page(
            self, *, limit: int | None = None, start: int = 0, since: int | None = None
        ) -> tuple[list[dict[str, Any]], dict[str, str]]:
            return (
                self.top_items(limit=limit, start=start, since=since),
                {"total-results": "3", "last-modified-version": "7"},
            )

        def collections_page(
            self, *, limit: int | None = None
        ) -> tuple[list[dict[str, Any]], dict[str, str]]:
            return self.collections(limit=limit), {"total-results": "2"}

        def tags_page(
            self, *, limit: int | None = None
        ) -> tuple[list[dict[str, Any]], dict[str, str]]:
            return self.tags(limit=limit), {"total-results": "4"}

    def fake_detect(*args: object, **kwargs: object) -> ZoteroDetection:
        del args, kwargs
        return ZoteroDetection(
            api_url="http://localhost:23119/api",
            api_reachable=True,
            api_error=None,
            data_dir=data_dir,
            database_exists=True,
            storage_exists=True,
        )

    monkeypatch.setattr(
        "paper_galaxy.zotero.doctor.LocalZoteroAPIClient",
        FakeDoctorClient,
    )
    monkeypatch.setattr("paper_galaxy.zotero.doctor.detect_zotero", fake_detect)

    report_path = tmp_path / "zotero-doctor.json"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "zotero",
            "doctor",
            "--project-dir",
            str(tmp_path / "project"),
            "--json-out",
            str(report_path),
        ],
    )

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert result.exit_code == 0, result.output
    assert payload["readiness"] in {"ready", "warning"}
    assert payload["counts"]["top_items_total"] == 3
    assert not resolve_database_path(tmp_path / "project").exists()


def test_zotero_cli_help_commands() -> None:
    runner = CliRunner()
    commands = [
        ["zotero", "--help"],
        ["zotero", "detect", "--help"],
        ["zotero", "status", "--help"],
        ["zotero", "doctor", "--help"],
        ["zotero", "validate-local", "--help"],
        ["zotero", "collections", "--help"],
        ["zotero", "items", "--help"],
        ["zotero", "import", "--help"],
        ["zotero", "graph", "--help"],
        ["zotero", "imported", "--help"],
        ["zotero", "validate", "--help"],
        ["zotero", "smoke-test", "--help"],
    ]

    for command in commands:
        result = runner.invoke(app, command)
        assert result.exit_code == 0, result.output


def test_zotero_smoke_test_reports_unavailable_api(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    runner = CliRunner()

    def raise_unavailable(*args: object, **kwargs: object) -> None:
        raise ZoteroAPIError("local API unavailable")

    monkeypatch.setattr("paper_galaxy.cli.import_from_zotero", raise_unavailable)

    result = runner.invoke(
        app,
        ["zotero", "smoke-test", "--project-dir", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "local API unavailable" in result.output


def test_zotero_web_api_empty_and_imported_states(tmp_path: Path) -> None:
    empty_client = TestClient(create_app(tmp_path))
    empty_status = empty_client.get("/api/zotero/status").json()
    empty_map = empty_client.get("/api/zotero/reading-map").json()

    assert empty_status["database_exists"] is False
    assert empty_status["zotero"]["imported_item_count"] == 0
    assert empty_map["points"] == []

    import_from_zotero(
        project_dir=tmp_path,
        client=FakeZoteroClient(),
        build_reading_map=False,
        min_chars=20,
    )
    client = TestClient(create_app(tmp_path))
    status = client.get("/api/zotero/status").json()
    items = client.get("/api/zotero/items").json()
    fno_id = next(
        item["id"]
        for item in items["items"]
        if item["title"] == "Fourier Neural Operator Memory"
    )
    detail = client.get(f"/api/zotero/item/{fno_id}").json()
    reading_map = client.get("/api/zotero/reading-map").json()
    invalid = client.get("/api/zotero/items?status=finished")

    assert status["zotero"]["imported_item_count"] == 3
    assert len(items["items"]) == 3
    assert detail["item"]["id"] == fno_id
    assert detail["item"]["doi"] == "10.0000/fno"
    assert detail["item"]["zotero_uri"] == "zotero://select/items/AAAA1111"
    assert len(reading_map["points"]) == 3
    assert reading_map["clusters"]
    assert reading_map["documents"][0]["zotero"]["item_type"]
    assert invalid.status_code == 422


def _load(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
