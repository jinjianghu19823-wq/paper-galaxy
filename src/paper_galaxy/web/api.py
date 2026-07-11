"""FastAPI route registration for the local Phase 3 app."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_galaxy import __version__
from paper_galaxy.embeddings.search import vector_stats as embedding_vector_stats
from paper_galaxy.errors import DatabaseError, MissingDependencyError
from paper_galaxy.explain.labels import validate_manual_label
from paper_galaxy.explain.pairs import explain_pair, pair_explanation_payload
from paper_galaxy.maps import persisted_map_payload, safe_persisted_map_payload
from paper_galaxy.paths import project_config_path
from paper_galaxy.records import (
    DatabaseStats,
    IndexedChunk,
    IndexedDocument,
    SearchResult,
)
from paper_galaxy.search import get_database_stats, search_index
from paper_galaxy.storage.json import StoredJSONError
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import (
    connect_read_only,
    connect_read_write,
    resolve_database_path,
)
from paper_galaxy.web.map_builder import build_map_payload
from paper_galaxy.zotero.filters import ZoteroFilterError, normalize_reading_status
from paper_galaxy.zotero.reading import build_zotero_reading_map_payload


@dataclass(frozen=True)
class WebAppConfig:
    """Read-only runtime config for the local web app."""

    project_dir: Path
    seed: int = 42
    clusters: int | None = None
    neighbors: int = 5
    map_limit: int = 1000

    @property
    def database_path(self) -> Path:
        return resolve_database_path(self.project_dir)


def register_api_routes(app: Any, config: WebAppConfig) -> None:
    """Register JSON API routes on a FastAPI app instance."""

    from fastapi.responses import JSONResponse

    async def database_error_handler(_request: Any, exc: DatabaseError) -> Any:
        return JSONResponse(
            status_code=_database_error_status(exc),
            content={
                "database_exists": exc.code != "database_missing",
                "error": {"code": exc.code, "message": exc.safe_message},
                "warnings": [exc.safe_message],
            },
        )

    app.add_exception_handler(DatabaseError, database_error_handler)

    async def stored_data_error_handler(_request: Any, exc: Exception) -> Any:
        code = exc.code if isinstance(exc, StoredJSONError) else "database_read_failed"
        return JSONResponse(
            status_code=500,
            content={
                "database_exists": True,
                "error": {
                    "code": code,
                    "message": (
                        "Stored project data is invalid. Run "
                        "`paper-galaxy validate-project` for local details."
                    ),
                },
                "warnings": ["The project database could not be read safely."],
            },
        )

    app.add_exception_handler(sqlite3.Error, stored_data_error_handler)
    app.add_exception_handler(StoredJSONError, stored_data_error_handler)

    def normalize_zotero_status_query(raw: str) -> tuple[str, list[str], Any | None]:
        try:
            selection = normalize_reading_status(raw, option_name="status")
        except ZoteroFilterError as exc:
            return (
                "all",
                [],
                JSONResponse(
                    status_code=422,
                    content={
                        "database_exists": True,
                        "error": {
                            "code": "invalid_zotero_status",
                            "message": str(exc),
                        },
                    },
                ),
            )
        return selection.value, ([selection.warning] if selection.warning else []), None

    @app.get("/api/health")
    def health() -> dict[str, object]:
        database_path = config.database_path
        return {
            "app": "Paper Galaxy",
            "version": __version__,
            "status": "ok",
            "database_exists": database_path.exists(),
            "project_configured": project_config_path(config.project_dir).exists(),
        }

    @app.get("/api/config")
    def app_config() -> dict[str, object]:
        database_path = config.database_path
        return {
            "database_exists": database_path.exists(),
            "project_configured": project_config_path(config.project_dir).exists(),
            "map_limit": config.map_limit,
            "seed": config.seed,
            "clusters": config.clusters,
            "neighbors": config.neighbors,
        }

    @app.get("/api/stats")
    def stats() -> dict[str, object]:
        missing = _missing_database_payload(config)
        if missing is not None:
            return {"database_exists": False, **missing}
        return {
            "database_exists": True,
            "stats": _stats_payload(get_database_stats(project_dir=config.project_dir)),
            "warnings": [],
        }

    @app.get("/api/vector-stats")
    def vector_stats() -> dict[str, object]:
        missing = _missing_database_payload(config)
        if missing is not None:
            return {
                "database_exists": False,
                "vector_stats": {
                    "models": [],
                    "vector_counts": [],
                    "last_run": None,
                    "vector_indexes": [],
                },
                **missing,
            }
        return {
            "database_exists": True,
            "vector_stats": _public_vector_stats(
                embedding_vector_stats(config.project_dir)
            ),
            "warnings": [],
        }

    @app.get("/api/zotero/status")
    def zotero_status() -> dict[str, object]:
        missing = _missing_database_payload(config)
        if missing is not None:
            return {
                "database_exists": False,
                "zotero": _empty_zotero_status(),
                **missing,
            }
        repository = _read_repository(config.project_dir)
        try:
            stats_payload = _public_zotero_stats(repository.zotero_stats())
        finally:
            repository.connection.close()
        return {
            "database_exists": True,
            "zotero": stats_payload,
            "warnings": stats_payload.get("warnings", []),
        }

    @app.get("/api/zotero/items")
    def zotero_items(
        limit: int = 100,
        status: str = "all",
        collection: str | None = None,
        tag: str | None = None,
        q: str | None = None,
    ) -> dict[str, object]:
        missing = _missing_database_payload(config)
        if missing is not None:
            return {
                "database_exists": False,
                "items": [],
                **missing,
            }
        normalized_status, status_warnings, error = normalize_zotero_status_query(
            status
        )
        if error is not None:
            return error
        repository = _read_repository(config.project_dir)
        try:
            items = repository.list_zotero_items(
                limit=max(0, limit),
                status=normalized_status,
                collection=collection,
                tag=tag,
                q=q,
            )
        finally:
            repository.connection.close()
        return {"database_exists": True, "items": items, "warnings": status_warnings}

    @app.get("/api/zotero/item/{zotero_item_id}")
    def zotero_item_detail(zotero_item_id: str) -> Any:
        missing = _missing_database_payload(config)
        if missing is not None:
            return JSONResponse(status_code=404, content=missing)
        repository = _read_repository(config.project_dir)
        try:
            item = repository.get_zotero_item_detail(zotero_item_id)
        finally:
            repository.connection.close()
        if item is None:
            return JSONResponse(
                status_code=404,
                content={
                    "database_exists": True,
                    "error": {
                        "code": "zotero_item_not_found",
                        "message": f"No imported Zotero item found: {zotero_item_id}",
                    },
                },
            )
        return {"database_exists": True, "item": item, "warnings": []}

    @app.get("/api/zotero/reading-map")
    def zotero_reading_map(
        status: str = "all",
        collection: str | None = None,
        tag: str | None = None,
        limit: int | None = None,
        seed: int | None = None,
        clusters: int | None = None,
        neighbors: int | None = None,
        run_id: str | None = None,
    ) -> Any:
        missing = _missing_database_payload(config)
        if missing is not None:
            return {
                "database_exists": False,
                "documents": [],
                "points": [],
                "cluster_labels": {},
                "clusters": [],
                **missing,
            }
        if run_id:
            try:
                return {
                    "database_exists": True,
                    **_public_saved_map_payload(
                        persisted_map_payload(
                            project_dir=config.project_dir,
                            run_id=run_id,
                        )
                    ),
                }
            except ValueError as exc:
                return JSONResponse(
                    status_code=404,
                    content={
                        "database_exists": True,
                        "error": {
                            "code": "map_run_not_found",
                            "message": str(exc),
                        },
                    },
                )
        normalized_status, status_warnings, error = normalize_zotero_status_query(
            status
        )
        if error is not None:
            return error
        repository = _read_repository(config.project_dir)
        try:
            payload = build_zotero_reading_map_payload(
                repository=repository,
                project_dir=config.project_dir,
                status=normalized_status,
                collection=collection,
                tag=tag,
                seed=config.seed if seed is None else seed,
                clusters=config.clusters if clusters is None else clusters,
                neighbors=config.neighbors if neighbors is None else neighbors,
                limit=config.map_limit if limit is None else limit,
            )
        except MissingDependencyError as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "database_exists": True,
                    "documents": [],
                    "points": [],
                    "cluster_labels": {},
                    "clusters": [],
                    "warnings": [
                        "Missing optional dependency for Zotero reading graph: "
                        f"{exc.dependency}."
                    ],
                    "error": {
                        "code": "missing_dependency",
                        "dependency": exc.dependency,
                        "message": (
                            'Install with: python -m pip install -e ".[dev,ml,pdf,app]"'
                        ),
                    },
                },
            )
        finally:
            repository.connection.close()
        payload = _public_zotero_reading_payload(payload)
        payload_warnings = payload.get("warnings", [])
        if not isinstance(payload_warnings, list):
            payload_warnings = []
        warnings = [*status_warnings, *[str(item) for item in payload_warnings]]
        return {"database_exists": True, **payload, "warnings": warnings}

    @app.get("/api/search")
    def search(
        q: str = "",
        limit: int = 10,
        include_missing: bool = False,
    ) -> dict[str, object]:
        missing = _missing_database_payload(config)
        if missing is not None:
            return {
                "database_exists": False,
                "query": q,
                "results": [],
                **missing,
            }
        if not q.strip():
            return {
                "database_exists": True,
                "query": q,
                "results": [],
                "warnings": ["Search query is empty."],
            }
        results = search_index(
            q,
            project_dir=config.project_dir,
            limit=max(0, limit),
            include_missing=include_missing,
        )
        return {
            "database_exists": True,
            "query": q,
            "results": [_search_result_payload(result) for result in results],
            "warnings": [],
        }

    @app.get("/api/documents")
    def documents(
        status: str = "active",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, object]:
        missing = _missing_database_payload(config)
        if missing is not None:
            return {
                "database_exists": False,
                "documents": [],
                "limit": max(0, limit),
                "offset": max(0, offset),
                **missing,
            }
        selected_statuses = None if status == "all" else {status}
        repository = _read_repository(config.project_dir)
        try:
            rows = repository.list_documents(
                statuses=selected_statuses,
                limit=max(0, limit),
                offset=max(0, offset),
            )
        finally:
            repository.connection.close()
        return {
            "database_exists": True,
            "documents": [_document_payload(document) for document in rows],
            "limit": max(0, limit),
            "offset": max(0, offset),
            "warnings": [],
        }

    @app.get("/api/documents/{document_id}")
    def document_detail(document_id: str, chunk_limit: int = 20) -> Any:
        missing = _missing_database_payload(config)
        if missing is not None:
            return JSONResponse(status_code=404, content=missing)
        repository = _read_repository(config.project_dir)
        try:
            document = repository.get_document(document_id)
            if document is None:
                return JSONResponse(
                    status_code=404,
                    content={
                        "error": {
                            "code": "document_not_found",
                            "message": f"No document found for id {document_id}.",
                        }
                    },
                )
            text = repository.get_document_text(document_id)
            chunks = repository.get_document_chunks(
                document_id,
                limit=max(0, chunk_limit),
            )
            chunk_count = repository.count_document_chunks(document_id)
        finally:
            repository.connection.close()
        return {
            "database_exists": True,
            "metadata": _document_payload(document),
            "chunk_count": chunk_count,
            "chunks": [_chunk_payload(chunk) for chunk in chunks],
            "text_preview": _preview(text or ""),
            "warnings": [],
        }

    @app.get("/api/map")
    def map_data(
        limit: int | None = None,
        seed: int | None = None,
        clusters: int | None = None,
        neighbors: int | None = None,
        run_id: str | None = None,
    ) -> Any:
        missing = _missing_database_payload(config)
        if missing is not None:
            return {
                "database_exists": False,
                "documents": [],
                "points": [],
                "cluster_labels": {},
                "clusters": [],
                "stats": None,
                **missing,
            }
        if run_id:
            try:
                return {
                    "database_exists": True,
                    **_public_saved_map_payload(
                        persisted_map_payload(
                            project_dir=config.project_dir,
                            run_id=run_id,
                        )
                    ),
                }
            except ValueError as exc:
                return JSONResponse(
                    status_code=404,
                    content={
                        "database_exists": True,
                        "error": {
                            "code": "map_run_not_found",
                            "message": str(exc),
                        },
                    },
                )
        try:
            payload = build_map_payload(
                project_dir=config.project_dir,
                seed=config.seed if seed is None else seed,
                clusters=config.clusters if clusters is None else clusters,
                neighbors=config.neighbors if neighbors is None else neighbors,
                limit=config.map_limit if limit is None else limit,
            )
        except MissingDependencyError as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "database_exists": True,
                    "documents": [],
                    "points": [],
                    "cluster_labels": {},
                    "clusters": [],
                    "warnings": [
                        "Missing optional dependency for map generation: "
                        f"{exc.dependency}."
                    ],
                    "error": {
                        "code": "missing_dependency",
                        "dependency": exc.dependency,
                        "message": (
                            'Install with: python -m pip install -e ".[dev,ml,pdf,app]"'
                        ),
                    },
                },
            )
        return {"database_exists": True, **payload}

    @app.get("/api/map-runs")
    def map_runs() -> dict[str, object]:
        missing = _missing_database_payload(config)
        if missing is not None:
            return {"database_exists": False, "map_runs": [], **missing}
        repository = _read_repository(config.project_dir)
        try:
            runs = [_public_map_run(run) for run in repository.list_map_runs()]
        finally:
            repository.connection.close()
        return {"database_exists": True, "map_runs": runs, "warnings": []}

    @app.get("/api/map-runs/{run_id}")
    def map_run_detail(run_id: str) -> Any:
        missing = _missing_database_payload(config)
        if missing is not None:
            return JSONResponse(status_code=404, content=missing)
        try:
            return {
                "database_exists": True,
                **_public_saved_map_payload(
                    persisted_map_payload(
                        project_dir=config.project_dir,
                        run_id=run_id,
                    )
                ),
            }
        except ValueError as exc:
            return JSONResponse(
                status_code=404,
                content={
                    "database_exists": True,
                    "error": {
                        "code": "map_run_not_found",
                        "message": str(exc),
                    },
                },
            )

    @app.delete("/api/map-runs/{run_id}")
    def delete_map_run(run_id: str) -> dict[str, object]:
        missing = _missing_database_payload(config)
        if missing is not None:
            return {"database_exists": False, "deleted": False, **missing}
        repository = _write_repository(config.project_dir)
        try:
            with repository.connection:
                deleted = repository.delete_map_run(run_id)
        finally:
            repository.connection.close()
        return {
            "database_exists": True,
            "map_run_id": run_id,
            "deleted": deleted,
            "warnings": [],
        }

    @app.get("/api/clusters")
    def cluster_data(
        limit: int | None = None,
        seed: int | None = None,
        clusters: int | None = None,
    ) -> Any:
        missing = _missing_database_payload(config)
        if missing is not None:
            return {
                "database_exists": False,
                "clusters": [],
                **missing,
            }
        try:
            payload = build_map_payload(
                project_dir=config.project_dir,
                seed=config.seed if seed is None else seed,
                clusters=config.clusters if clusters is None else clusters,
                neighbors=config.neighbors,
                limit=config.map_limit if limit is None else limit,
            )
        except MissingDependencyError as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "database_exists": True,
                    "clusters": [],
                    "warnings": [
                        "Missing optional dependency for cluster generation: "
                        f"{exc.dependency}."
                    ],
                    "error": {
                        "code": "missing_dependency",
                        "dependency": exc.dependency,
                        "message": (
                            'Install with: python -m pip install -e ".[dev,ml,pdf,app]"'
                        ),
                    },
                },
            )
        return {
            "database_exists": True,
            "clusters": payload.get("clusters", []),
            "warnings": payload.get("warnings", []),
        }

    @app.put("/api/clusters/{cluster_signature}/label")
    def rename_cluster(cluster_signature: str, body: dict[str, str]) -> Any:
        missing = _missing_database_payload(config)
        if missing is not None:
            return JSONResponse(status_code=404, content=missing)
        try:
            label = validate_manual_label(str(body.get("label", "")))
        except ValueError as exc:
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": "invalid_cluster_label",
                        "message": str(exc),
                    }
                },
            )
        repository = _write_repository(config.project_dir)
        try:
            with repository.connection:
                override = repository.upsert_cluster_label_override(
                    cluster_signature=cluster_signature,
                    label=label,
                )
        finally:
            repository.connection.close()
        return {
            "database_exists": True,
            "override": override,
            "warnings": [],
        }

    @app.delete("/api/clusters/{cluster_signature}/label")
    def reset_cluster_label(cluster_signature: str) -> Any:
        missing = _missing_database_payload(config)
        if missing is not None:
            return JSONResponse(status_code=404, content=missing)
        repository = _write_repository(config.project_dir)
        try:
            with repository.connection:
                deleted = repository.delete_cluster_label_override(cluster_signature)
        finally:
            repository.connection.close()
        return {
            "database_exists": True,
            "cluster_signature": cluster_signature,
            "deleted": deleted,
            "warnings": [],
        }

    @app.get("/api/explain/pair")
    def pair_data(
        source: str = "",
        target: str = "",
        model_id: str | None = None,
        chunk_limit: int = 3,
        term_limit: int = 8,
    ) -> Any:
        missing = _missing_database_payload(config)
        if missing is not None:
            return JSONResponse(status_code=404, content=missing)
        if not source.strip() or not target.strip():
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": "missing_pair_id",
                        "message": "Both source and target are required.",
                    }
                },
            )
        repository = _read_repository(config.project_dir)
        try:
            explanation = explain_pair(
                repository,
                source,
                target,
                model_id=model_id,
                chunk_limit=max(0, chunk_limit),
                term_limit=max(0, term_limit),
            )
        except ValueError as exc:
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "code": "document_not_found",
                        "message": str(exc),
                    }
                },
            )
        finally:
            repository.connection.close()
        return {
            "database_exists": True,
            "explanation": pair_explanation_payload(explanation),
            "warnings": explanation.warnings,
        }


def _read_repository(project_dir: Path) -> Repository:
    connection = connect_read_only(project_dir)
    return Repository(connection, resolve_database_path(project_dir))


def _write_repository(project_dir: Path) -> Repository:
    connection = connect_read_write(project_dir)
    return Repository(connection, resolve_database_path(project_dir))


def _missing_database_payload(config: WebAppConfig) -> dict[str, object] | None:
    database_path = config.database_path
    if database_path.exists():
        return None
    return {
        "warnings": ["No Paper Galaxy database found."],
        "error": {
            "code": "database_missing",
            "message": "No Paper Galaxy database found.",
            "command": (
                "paper-galaxy index /path/to/corpus --project-dir /path/to/project"
            ),
        },
    }


def _empty_zotero_status() -> dict[str, object]:
    return {
        "source_count": 0,
        "imported_item_count": 0,
        "imported_document_count": 0,
        "attachment_count": 0,
        "missing_attachment_count": 0,
        "reading_status_counts": {},
        "last_import_run": None,
        "warnings": ["No Paper Galaxy database found."],
    }


def _document_payload(document: IndexedDocument) -> dict[str, object]:
    return {
        "document_id": document.id,
        "id": document.id,
        "title": document.title,
        "relative_path": document.relative_path,
        "file_type": document.file_type,
        "char_count": document.char_count,
        "status": document.status,
        "updated_at": document.updated_at,
    }


def _public_vector_stats(payload: dict[str, object]) -> dict[str, object]:
    public = {key: value for key, value in payload.items() if key != "database_path"}
    models = public.get("models")
    if isinstance(models, list):
        public["models"] = [
            {
                key: (_safe_model_name(value) if key == "name" else value)
                for key, value in row.items()
                if key != "config"
            }
            for row in models
            if isinstance(row, dict)
        ]
    counts = public.get("vector_counts")
    if isinstance(counts, list):
        public["vector_counts"] = [
            {
                key: (_safe_model_name(value) if key == "model_name" else value)
                for key, value in row.items()
            }
            for row in counts
            if isinstance(row, dict)
        ]
    last_run = public.get("last_run")
    if isinstance(last_run, dict):
        public["last_run"] = {
            key: (_safe_model_name(value) if key == "model_name" else value)
            for key, value in last_run.items()
            if key != "config"
        }
    indexes = public.get("vector_indexes")
    if isinstance(indexes, list):
        public["vector_indexes"] = [
            {
                key: value
                for key, value in row.items()
                if key not in {"index_path", "metadata"}
            }
            for row in indexes
            if isinstance(row, dict)
        ]
    return public


def _public_zotero_stats(payload: dict[str, object]) -> dict[str, object]:
    public = {
        key: value
        for key, value in payload.items()
        if key not in {"last_import_run", "warnings"}
    }
    run = payload.get("last_import_run")
    warning_count = 0
    if isinstance(run, dict):
        warnings = run.get("warnings")
        warning_count = len(warnings) if isinstance(warnings, list) else 0
        allowed = {
            "id",
            "source_id",
            "started_at",
            "finished_at",
            "status",
            "items_seen",
            "items_imported",
            "items_updated",
            "items_unchanged",
            "attachments_seen",
            "attachments_resolved",
            "pdfs_extracted",
            "notes_imported",
            "skipped",
        }
        public_run = {key: value for key, value in run.items() if key in allowed}
        public_run["warning_count"] = warning_count
        public["last_import_run"] = public_run
    else:
        public["last_import_run"] = None
    public["warnings"] = (
        [f"The last Zotero import reported {warning_count} warning(s)."]
        if warning_count
        else []
    )
    return public


def _public_zotero_reading_payload(payload: dict[str, object]) -> dict[str, object]:
    public = dict(payload)
    stats = public.get("stats")
    if isinstance(stats, dict):
        public["stats"] = _public_zotero_stats(stats)
    warnings = public.get("warnings")
    warning_count = len(warnings) if isinstance(warnings, list) else 0
    public["warnings"] = (
        [f"The reading map reported {warning_count} warning(s)."]
        if warning_count
        else []
    )
    return public


def _public_map_run(run: dict[str, object]) -> dict[str, object]:
    allowed = {
        "id",
        "name",
        "created_at",
        "status",
        "similarity_mode",
        "model_id",
        "seed",
        "requested_clusters",
        "requested_neighbors",
        "requested_limit",
        "document_count",
        "cluster_count",
        "document_set_signature",
    }
    public = {key: value for key, value in run.items() if key in allowed}
    warnings = run.get("warnings")
    public["warning_count"] = len(warnings) if isinstance(warnings, list) else 0
    return public


def _public_saved_map_payload(payload: dict[str, object]) -> dict[str, object]:
    return safe_persisted_map_payload(payload)


def _safe_model_name(value: object) -> str:
    parts = str(value).replace("\\", "/").rstrip("/").split("/")
    return parts[-1] if parts and parts[-1] else "local-model"


def _database_error_status(error: DatabaseError) -> int:
    if error.code == "database_missing":
        return 404
    if error.code in {"future_schema", "database_needs_migration"}:
        return 409
    if error.code == "database_locked":
        return 423
    return 500


def _chunk_payload(chunk: IndexedChunk) -> dict[str, object]:
    return {
        "id": chunk.id,
        "chunk_index": chunk.chunk_index,
        "text": chunk.text,
        "char_count": chunk.char_count,
    }


def _search_result_payload(result: SearchResult) -> dict[str, object]:
    return {
        "rank": result.rank,
        "document_id": result.document_id,
        "title": result.title,
        "relative_path": result.relative_path,
        "file_type": result.file_type,
        "char_count": result.char_count,
        "updated_at": result.updated_at,
        "snippet": result.snippet,
        "score": result.score,
    }


def _stats_payload(stats: DatabaseStats) -> dict[str, object]:
    return {
        "documents": stats.documents,
        "active_documents": stats.active_documents,
        "missing_documents": stats.missing_documents,
        "unindexed_documents": stats.unindexed_documents,
        "chunks": stats.chunks,
        "scan_runs": stats.scan_runs,
        "last_scan_time": stats.last_scan_time,
        "total_indexed_characters": stats.total_indexed_characters,
    }


def _preview(text: str, *, limit: int = 1200) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "..."
