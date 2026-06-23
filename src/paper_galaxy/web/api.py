"""FastAPI route registration for the local Phase 3 app."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_galaxy import __version__
from paper_galaxy.embeddings.search import vector_stats as embedding_vector_stats
from paper_galaxy.errors import MissingDependencyError
from paper_galaxy.records import (
    DatabaseStats,
    IndexedChunk,
    IndexedDocument,
    SearchResult,
)
from paper_galaxy.search import get_database_stats, search_index
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path
from paper_galaxy.web.map_builder import build_map_payload


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

    @app.get("/api/health")
    def health() -> dict[str, object]:
        database_path = config.database_path
        return {
            "app": "Paper Galaxy",
            "version": __version__,
            "status": "ok",
            "project_dir": str(config.project_dir),
            "database_exists": database_path.exists(),
            "database_path": str(database_path),
        }

    @app.get("/api/config")
    def app_config() -> dict[str, object]:
        database_path = config.database_path
        return {
            "project_dir": str(config.project_dir),
            "database_path": str(database_path),
            "database_exists": database_path.exists(),
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
            "vector_stats": embedding_vector_stats(config.project_dir),
            "warnings": [],
        }

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
        repository = _repository(config.project_dir)
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
        repository = _repository(config.project_dir)
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
            "metadata": _document_payload(document, include_path=True),
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
    ) -> Any:
        missing = _missing_database_payload(config)
        if missing is not None:
            return {
                "database_exists": False,
                "documents": [],
                "points": [],
                "cluster_labels": {},
                "stats": None,
                **missing,
            }
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


def _repository(project_dir: Path) -> Repository:
    connection = connect_database(project_dir)
    initialize_database(connection)
    return Repository(connection, resolve_database_path(project_dir))


def _missing_database_payload(config: WebAppConfig) -> dict[str, object] | None:
    database_path = config.database_path
    if database_path.exists():
        return None
    return {
        "warnings": ["No Paper Galaxy database found."],
        "error": {
            "code": "database_missing",
            "message": "No Paper Galaxy database found",
            "database_path": str(database_path),
            "project_dir": str(config.project_dir),
            "command": (
                "paper-galaxy index /path/to/corpus "
                f"--project-dir {shlex.quote(str(config.project_dir))}"
            ),
        },
    }


def _document_payload(
    document: IndexedDocument, *, include_path: bool = False
) -> dict[str, object]:
    payload: dict[str, object] = {
        "document_id": document.id,
        "id": document.id,
        "title": document.title,
        "relative_path": document.relative_path,
        "file_type": document.file_type,
        "char_count": document.char_count,
        "status": document.status,
        "updated_at": document.updated_at,
    }
    if include_path:
        payload["local_path"] = document.path
    return payload


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
