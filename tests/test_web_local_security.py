from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from paper_galaxy.services.sources import SOURCE_KIND_CORPUS
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.sqlite import connect_database
from paper_galaxy.web.server import create_app

ORIGIN = "http://127.0.0.1"
WRITE_TOKEN_HEADER = "X-Paper-Galaxy-Write-Token"


class _InertJobManager:
    """Keep API queue tests deterministic without starting real local work."""

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def notify(self) -> None:
        return None


def _client(project_dir: Path, *, inert_jobs: bool = False) -> TestClient:
    manager = _InertJobManager() if inert_jobs else None
    return TestClient(
        create_app(project_dir, job_manager=manager),
        base_url=ORIGIN,
        headers={"host": "127.0.0.1"},
    )


def _initialize_project(project_dir: Path) -> None:
    connection = connect_database(project_dir)
    try:
        initialize_database(connection)
    finally:
        connection.close()


def _write_headers(client: TestClient) -> dict[str, str]:
    response = client.get("/api/config")
    assert response.status_code == 200
    token = response.json().get("write_token")
    assert isinstance(token, str)
    assert len(token) >= 32
    assert token not in str(response.request.url)
    assert response.headers["cache-control"] == "no-store"
    return {"Origin": ORIGIN, WRITE_TOKEN_HEADER: token}


def _assert_paths_are_private(payload: Any, *paths: Path) -> None:
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    for path in paths:
        assert str(path.resolve()) not in serialized


@pytest.mark.parametrize(
    "host",
    [
        "attacker.example",
        "127.0.0.1.attacker.example",
        "localhost.attacker.example:8765",
    ],
)
def test_host_header_rejects_non_loopback_allowlist_values(
    tmp_path: Path,
    host: str,
) -> None:
    project_dir = tmp_path / "private-project"
    with _client(project_dir) as client:
        response = client.get("/api/health", headers={"host": host})

    assert response.status_code in {400, 421}
    _assert_paths_are_private(response.json(), project_dir)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost:8765", "[::1]:8765"])
def test_host_header_accepts_explicit_loopback_values(
    tmp_path: Path,
    host: str,
) -> None:
    with _client(tmp_path / "project") as client:
        response = client.get("/api/health", headers={"host": host})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1/private/path",
        "127.0.0.1?next=private",
        "127.0.0.1#fragment",
        "127.0.0.1:not-a-port",
        "127.0.0.1:99999",
        "user@127.0.0.1",
        "http://127.0.0.1",
    ],
)
def test_host_header_rejects_non_authority_syntax(
    tmp_path: Path,
    host: str,
) -> None:
    project_dir = tmp_path / "private-project"
    with _client(project_dir) as client:
        response = client.get("/api/health", headers={"host": host})

    assert response.status_code in {400, 421}
    if response.headers.get("content-type", "").startswith("application/json"):
        _assert_paths_are_private(response.json(), project_dir)


@pytest.mark.parametrize("path", ["/", "/api/health", "/api/not-found"])
def test_every_response_has_local_web_security_headers(
    tmp_path: Path,
    path: str,
) -> None:
    with _client(tmp_path / "project") as client:
        response = client.get(path)

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    policy = response.headers["content-security-policy"]
    assert "default-src 'self'" in policy
    assert "connect-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "base-uri 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert response.headers["cache-control"] == "no-store"


def test_unhandled_error_is_safe_json_with_security_headers(tmp_path: Path) -> None:
    project_dir = tmp_path / "private-project"
    app = create_app(project_dir)

    @app.get("/api/test-unhandled-error")
    def fail_safely() -> None:
        raise RuntimeError(f"cannot read {project_dir / 'private.sqlite3'}")

    with TestClient(
        app,
        base_url=ORIGIN,
        headers={"host": "127.0.0.1"},
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/api/test-unhandled-error")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_server_error"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    _assert_paths_are_private(response.json(), project_dir)


def test_config_returns_stable_high_entropy_token_without_path_or_url_leak(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "private-project"
    with _client(project_dir) as client:
        first = client.get("/api/config")
        second = client.get("/api/config")
        health = client.get("/api/health")

    assert first.status_code == 200
    assert second.status_code == 200
    token = first.json()["write_token"]
    assert token == second.json()["write_token"]
    assert isinstance(token, str)
    assert len(token) >= 32
    assert token not in str(first.request.url)
    assert token not in health.text
    assert "write_token=" not in str(first.request.url)
    _assert_paths_are_private(first.json(), project_dir)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "POST",
            "/api/sources",
            {"kind": SOURCE_KIND_CORPUS, "path": "/synthetic/corpus"},
        ),
        (
            "PUT",
            "/api/clusters/synthetic-signature/label",
            {"label": "Synthetic label"},
        ),
        ("DELETE", "/api/map-runs/synthetic-run", None),
    ],
)
def test_all_write_methods_require_same_origin_and_write_token(
    tmp_path: Path,
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    project_dir = tmp_path / "project"
    _initialize_project(project_dir)
    with _client(project_dir) as client:
        token = _write_headers(client)[WRITE_TOKEN_HEADER]
        missing_both = client.request(method, path, json=body)
        missing_origin = client.request(
            method,
            path,
            json=body,
            headers={WRITE_TOKEN_HEADER: token},
        )
        missing_token = client.request(
            method,
            path,
            json=body,
            headers={"Origin": ORIGIN},
        )
        wrong_token = client.request(
            method,
            path,
            json=body,
            headers={"Origin": ORIGIN, WRITE_TOKEN_HEADER: "wrong-token"},
        )
        cross_origin = client.request(
            method,
            path,
            json=body,
            headers={
                "Origin": "http://attacker.example",
                WRITE_TOKEN_HEADER: token,
            },
        )

    for response in (
        missing_both,
        missing_origin,
        missing_token,
        wrong_token,
        cross_origin,
    ):
        assert response.status_code == 403
        _assert_paths_are_private(response.json(), project_dir)


def test_read_only_source_and_job_gets_do_not_create_a_missing_project(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "does-not-exist"
    assert not project_dir.exists()

    with _client(project_dir) as client:
        config = client.get("/api/config")
        sources = client.get("/api/sources", params={"limit": 20, "offset": 0})
        jobs = client.get("/api/jobs", params={"limit": 20, "offset": 0})

    assert config.status_code == 200
    assert config.json()["database_exists"] is False
    assert sources.status_code == 200
    assert sources.json()["sources"] == []
    assert jobs.status_code == 200
    assert jobs.json()["jobs"] == []
    assert not project_dir.exists()


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/sources", {"limit": 101}),
        ("/api/sources", {"limit": 1, "offset": -1}),
        ("/api/jobs", {"limit": 101}),
        ("/api/jobs", {"limit": 1, "offset": -1}),
    ],
)
def test_source_and_job_list_pagination_is_bounded(
    tmp_path: Path,
    path: str,
    params: dict[str, int],
) -> None:
    project_dir = tmp_path / "project"
    _initialize_project(project_dir)
    with _client(project_dir) as client:
        response = client.get(path, params=params)

    assert response.status_code == 422
    _assert_paths_are_private(response.json(), project_dir)


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/sources", {"kind": "x" * 513}),
        ("/api/sources", {"offset": 1_000_001}),
        ("/api/jobs", {"status": "x" * 513}),
        ("/api/jobs", {"offset": 10**100}),
        ("/api/zotero/items", {"limit": 0}),
        ("/api/zotero/items", {"limit": 501}),
        ("/api/zotero/items", {"q": "x" * 513}),
        ("/api/zotero/reading-map", {"limit": 2_001}),
        ("/api/zotero/reading-map", {"seed": -1}),
        ("/api/zotero/reading-map", {"clusters": 201}),
        ("/api/zotero/reading-map", {"neighbors": 51}),
        ("/api/zotero/reading-map", {"collection": "x" * 513}),
        ("/api/search", {"limit": 101, "q": "synthetic"}),
        ("/api/search", {"q": "x" * 513}),
        ("/api/documents", {"limit": 501}),
        ("/api/documents", {"offset": 10**100}),
        ("/api/documents", {"status": "x" * 513}),
        ("/api/documents/missing", {"chunk_limit": 101}),
        ("/api/map", {"limit": 0}),
        ("/api/map", {"seed": 4_294_967_296}),
        ("/api/map", {"clusters": 0}),
        ("/api/map", {"neighbors": -1}),
        ("/api/map", {"run_id": "x" * 513}),
        ("/api/clusters", {"limit": 2_001}),
        ("/api/clusters", {"clusters": 201}),
        (
            "/api/explain/pair",
            {"source": "source", "target": "target", "chunk_limit": 101},
        ),
        (
            "/api/explain/pair",
            {"source": "source", "target": "target", "term_limit": -1},
        ),
        (
            "/api/explain/pair",
            {"source": "x" * 513, "target": "target"},
        ),
    ],
)
def test_all_api_query_bounds_reject_oversized_values_before_database_work(
    tmp_path: Path,
    path: str,
    params: dict[str, int | str],
) -> None:
    project_dir = tmp_path / "private-project"
    _initialize_project(project_dir)
    with _client(project_dir) as client:
        response = client.get(path, params=params)

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    _assert_paths_are_private(response.json(), project_dir)


def test_sources_and_jobs_ui_uses_safe_status_fields_without_raw_error_text() -> None:
    static_dir = Path(__file__).parents[1] / "src" / "paper_galaxy" / "web" / "static"
    app_source = (static_dir / "app.js").read_text(encoding="utf-8")
    translations = (static_dir / "i18n.js").read_text(encoding="utf-8")

    assert "source.last_success_at" in app_source
    assert "source.last_error" in app_source
    assert "job.error" in app_source
    assert "job.error.message" not in app_source
    assert "job.message" not in app_source
    assert '"work.errorAction"' in translations
    assert "Inspect the local CLI" in translations
    assert "请在本地 CLI 中查看详情" in translations


def test_source_and_index_job_api_is_idempotent_cancellable_and_path_free(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "private-project"
    corpus_dir = tmp_path / "private-corpus"
    corpus_dir.mkdir()
    (corpus_dir / "paper.md").write_text(
        "# Synthetic\n\nA local-only synthetic research note.",
        encoding="utf-8",
    )
    _initialize_project(project_dir)

    responses = []
    with _client(project_dir, inert_jobs=True) as client:
        headers = _write_headers(client)
        created_source = client.post(
            "/api/sources",
            headers=headers,
            json={
                "kind": SOURCE_KIND_CORPUS,
                "path": str(corpus_dir),
                "display_name": "Synthetic corpus",
            },
        )
        responses.append(created_source)
        assert created_source.status_code in {200, 201}
        assert created_source.json()["created"] is True
        source = created_source.json()["source"]
        source_id = source["id"]
        assert source["kind"] == SOURCE_KIND_CORPUS
        assert "path" not in source
        assert "root_path" not in source

        repeated_source = client.post(
            "/api/sources",
            headers=headers,
            json={
                "kind": SOURCE_KIND_CORPUS,
                "path": str(corpus_dir),
                "display_name": "Synthetic corpus",
            },
        )
        responses.append(repeated_source)
        assert repeated_source.status_code == 200
        assert repeated_source.json()["created"] is False
        assert repeated_source.json()["source"]["id"] == source_id

        listed_sources = client.get("/api/sources", params={"limit": 100, "offset": 0})
        responses.append(listed_sources)
        assert listed_sources.status_code == 200
        assert [item["id"] for item in listed_sources.json()["sources"]] == [source_id]

        rejected_browser_path = client.post(
            "/api/jobs/index",
            headers=headers,
            json={"source_id": source_id, "path": str(corpus_dir)},
        )
        responses.append(rejected_browser_path)
        assert rejected_browser_path.status_code == 422

        queued = client.post(
            "/api/jobs/index",
            headers=headers,
            json={"source_id": source_id},
        )
        responses.append(queued)
        assert queued.status_code in {200, 201}
        assert queued.json()["created"] is True
        job = queued.json()["job"]
        job_id = job["id"]
        assert job["source_id"] == source_id
        assert job["kind"] == "index_corpus"
        assert job["status"] == "queued"

        duplicate = client.post(
            "/api/jobs/index",
            headers=headers,
            json={"source_id": source_id},
        )
        responses.append(duplicate)
        assert duplicate.status_code == 200
        assert duplicate.json()["created"] is False
        assert duplicate.json()["job"]["id"] == job_id

        listed_jobs = client.get("/api/jobs", params={"limit": 100, "offset": 0})
        job_detail = client.get(f"/api/jobs/{job_id}")
        responses.extend((listed_jobs, job_detail))
        assert listed_jobs.status_code == 200
        assert [item["id"] for item in listed_jobs.json()["jobs"]] == [job_id]
        assert job_detail.status_code == 200
        assert job_detail.json()["job"]["id"] == job_id

        cancelled = client.post(
            f"/api/jobs/{job_id}/cancel",
            headers=headers,
        )
        responses.append(cancelled)
        assert cancelled.status_code == 200
        assert cancelled.json()["job"]["status"] == "cancelled"

        after_cancel = client.get(f"/api/jobs/{job_id}")
        responses.append(after_cancel)
        assert after_cancel.json()["job"]["status"] == "cancelled"

    for response in responses:
        _assert_paths_are_private(response.json(), project_dir, corpus_dir)
