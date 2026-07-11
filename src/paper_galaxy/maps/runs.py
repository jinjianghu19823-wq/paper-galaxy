"""Build and persist deterministic local map snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from paper_galaxy.records import DatabaseStats, IndexedDocument
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import (
    connect_read_only,
    connect_read_write,
    ensure_database_ready,
    resolve_database_path,
)
from paper_galaxy.web.map_builder import build_map_payload


def build_and_store_map_run(
    *,
    project_dir: Path,
    name: str | None = None,
    seed: int = 42,
    clusters: int | None = None,
    neighbors: int = 5,
    limit: int = 1000,
    similarity_mode: str = "tfidf",
    model_id: str | None = None,
) -> dict[str, object]:
    """Build a live map payload and persist it as a saved run."""

    if similarity_mode != "tfidf":
        raise ValueError("Phase 7 saved map runs support similarity_mode=tfidf only.")
    if model_id:
        raise ValueError("model_id is reserved for future dense map runs.")

    resolved_project_dir = project_dir.expanduser().resolve()
    payload = build_map_payload(
        project_dir=resolved_project_dir,
        seed=seed,
        clusters=clusters,
        neighbors=neighbors,
        limit=limit,
    )
    timestamp = _utc_now()
    run_name = name.strip() if name and name.strip() else f"Map run {timestamp}"
    points = _dict_list(payload.get("points"))
    cluster_rows = _dict_list(payload.get("clusters"))
    documents = _dict_list(payload.get("documents"))
    document_set_signature = _document_set_signature(
        documents=documents,
        seed=seed,
        clusters=clusters,
        neighbors=neighbors,
        limit=limit,
        similarity_mode=similarity_mode,
    )
    run_id = _map_run_id(run_name, document_set_signature, timestamp)
    warnings = [str(warning) for warning in _list(payload.get("warnings"))]
    metadata = {
        "builder": "paper_galaxy.web.map_builder.build_map_payload",
        "parameters": {
            "seed": seed,
            "clusters": clusters,
            "neighbors": neighbors,
            "limit": limit,
            "similarity_mode": similarity_mode,
        },
    }

    ensure_database_ready(resolved_project_dir)
    connection = connect_read_write(resolved_project_dir)
    try:
        repository = Repository(connection, resolve_database_path(resolved_project_dir))
        with connection:
            run = repository.save_map_run(
                run_id=run_id,
                name=run_name,
                status="completed",
                similarity_mode=similarity_mode,
                model_id=model_id,
                seed=seed,
                requested_clusters=clusters,
                requested_neighbors=neighbors,
                requested_limit=limit,
                document_count=len(points),
                cluster_count=len(cluster_rows),
                document_set_signature=document_set_signature,
                warnings=warnings,
                metadata=metadata,
                points=points,
                clusters=cluster_rows,
                now=timestamp,
            )
    finally:
        connection.close()
    return {
        "map_run": run,
        "documents": documents,
        "points": points,
        "cluster_labels": payload.get("cluster_labels", {}),
        "clusters": cluster_rows,
        "stats": payload.get("stats"),
        "warnings": warnings,
    }


def persisted_map_payload(*, project_dir: Path, run_id: str) -> dict[str, object]:
    """Return a saved run using the same shape as the live map endpoint."""

    resolved_project_dir = project_dir.expanduser().resolve()
    connection = connect_read_only(resolved_project_dir)
    try:
        repository = Repository(connection, resolve_database_path(resolved_project_dir))
        run = repository.get_map_run(run_id)
        if run is None:
            raise ValueError(f"No saved map run found for id {run_id}.")
        points = _dict_list(run.pop("points", []))
        clusters = _dict_list(run.pop("clusters", []))
        documents = _documents_for_points(repository, points)
        stats = _stats_payload(repository.get_stats())
    finally:
        connection.close()

    cluster_labels = {
        str(cluster["cluster_id"]): str(cluster["display_label"])
        for cluster in clusters
    }
    missing_docs = len(points) - len(documents)
    warnings = [str(warning) for warning in _list(run.get("warnings"))]
    if missing_docs:
        warnings.append(
            f"{missing_docs} saved map point(s) no longer match a current document."
        )
    return {
        "map_run": run,
        "documents": documents,
        "points": points,
        "cluster_labels": cluster_labels,
        "clusters": clusters,
        "stats": stats,
        "warnings": warnings,
    }


def export_map_run(*, project_dir: Path, run_id: str, output_path: Path) -> Path:
    """Write a saved map run JSON export without source document text."""

    payload = safe_persisted_map_payload(
        persisted_map_payload(project_dir=project_dir, run_id=run_id)
    )
    resolved_output = output_path.expanduser().resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return resolved_output


def safe_persisted_map_payload(payload: dict[str, object]) -> dict[str, object]:
    """Return a deeply typed map projection safe for ordinary API/export use."""

    run = payload.get("map_run")
    documents = payload.get("documents")
    points = payload.get("points")
    clusters = payload.get("clusters")
    labels = payload.get("cluster_labels")
    stats = payload.get("stats")
    warnings = payload.get("warnings")
    warning_count = len(warnings) if isinstance(warnings, list) else 0
    return {
        "map_run": _safe_map_run(run if isinstance(run, dict) else {}),
        "documents": [_safe_document(document) for document in _dict_list(documents)],
        "points": [_safe_point(point) for point in _dict_list(points)],
        "cluster_labels": {
            str(key): str(value)
            for key, value in (labels.items() if isinstance(labels, dict) else [])
        },
        "clusters": [_safe_cluster(cluster) for cluster in _dict_list(clusters)],
        "stats": _safe_stats(stats if isinstance(stats, dict) else {}),
        "warnings": (
            [f"This saved map reported {warning_count} warning(s)."]
            if warning_count
            else []
        ),
    }


def _safe_map_run(run: dict[str, object]) -> dict[str, object]:
    allowed = {
        "id",
        "name",
        "created_at",
        "status",
        "similarity_mode",
        "seed",
        "requested_clusters",
        "requested_neighbors",
        "requested_limit",
        "document_count",
        "cluster_count",
        "document_set_signature",
    }
    public = {key: value for key, value in run.items() if key in allowed}
    model_id = run.get("model_id")
    public["model_id"] = _safe_opaque_id(model_id)
    warnings = run.get("warnings")
    public["warning_count"] = len(warnings) if isinstance(warnings, list) else 0
    return public


def _safe_document(document: dict[str, object]) -> dict[str, object]:
    document_id = _safe_opaque_id(document.get("document_id")) or ""
    return {
        "document_id": document_id,
        "id": _safe_opaque_id(document.get("id")) or document_id,
        "title": str(document.get("title", "")),
        "relative_path": _safe_relative_path(document.get("relative_path")),
        "file_type": str(document.get("file_type", "")),
        "char_count": _int_value(document.get("char_count", 0)),
        "status": str(document.get("status", "")),
        "updated_at": str(document.get("updated_at", "")),
    }


def _safe_point(point: dict[str, object]) -> dict[str, object]:
    top_terms = point.get("top_terms")
    neighbors = point.get("nearest_neighbors")
    return {
        "document_id": _safe_opaque_id(point.get("document_id")) or "",
        "x": _number_value(point.get("x", 0.0)),
        "y": _number_value(point.get("y", 0.0)),
        "cluster_id": _int_value(point.get("cluster_id", 0)),
        "cluster_label": str(point.get("cluster_label", "")),
        "cluster_signature": _safe_opaque_id(point.get("cluster_signature")) or "",
        "top_terms": [str(term) for term in _list(top_terms) if isinstance(term, str)],
        "nearest_neighbors": [
            _safe_scored_document(neighbor) for neighbor in _dict_list(neighbors)
        ],
    }


def _safe_cluster(cluster: dict[str, object]) -> dict[str, object]:
    document_ids = cluster.get("document_ids")
    top_terms = cluster.get("top_terms")
    representatives = cluster.get("representatives")
    return {
        "cluster_id": _int_value(cluster.get("cluster_id", 0)),
        "cluster_signature": _safe_opaque_id(cluster.get("cluster_signature")) or "",
        "display_label": str(cluster.get("display_label", "")),
        "generated_label": str(cluster.get("generated_label", "")),
        "source": str(cluster.get("source", "")),
        "size": _int_value(cluster.get("size", 0)),
        "document_ids": [
            safe_id
            for document_id in _list(document_ids)
            if (safe_id := _safe_opaque_id(document_id)) is not None
        ],
        "top_terms": [
            {
                "term": str(term.get("term", "")),
                "score": _number_value(term.get("score", 0.0)),
            }
            for term in _dict_list(top_terms)
        ],
        "representatives": [
            _safe_scored_document(representative)
            for representative in _dict_list(representatives)
        ],
    }


def _safe_scored_document(value: dict[str, object]) -> dict[str, object]:
    return {
        "document_id": _safe_opaque_id(value.get("document_id")) or "",
        "title": str(value.get("title", "")),
        "relative_path": _safe_relative_path(value.get("relative_path")),
        "score": _number_value(value.get("score", 0.0)),
    }


def _safe_stats(stats: dict[str, object]) -> dict[str, object]:
    allowed = {
        "documents",
        "active_documents",
        "missing_documents",
        "unindexed_documents",
        "chunks",
        "scan_runs",
        "last_scan_time",
        "total_indexed_characters",
    }
    return {key: value for key, value in stats.items() if key in allowed}


def _safe_relative_path(value: object) -> str:
    text = str(value or "").replace("\\", "/")
    posix = PurePosixPath(text)
    windows = PureWindowsPath(str(value or ""))
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        return ""
    if any(part in {"", ".", ".."} for part in posix.parts):
        return ""
    return posix.as_posix()


def _safe_opaque_id(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text or any(token in text for token in ("/", "\\", "..")):
        return None
    return text


def _number_value(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _documents_for_points(
    repository: Repository, points: list[dict[str, object]]
) -> list[dict[str, object]]:
    documents: list[dict[str, object]] = []
    for point in points:
        document_id = str(point.get("document_id", ""))
        document = repository.get_document(document_id)
        if document is not None:
            documents.append(_document_payload(document))
    return documents


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


def _document_set_signature(
    *,
    documents: list[dict[str, object]],
    seed: int,
    clusters: int | None,
    neighbors: int,
    limit: int,
    similarity_mode: str,
) -> str:
    data = {
        "documents": [
            {
                "document_id": str(document.get("document_id", "")),
                "updated_at": str(document.get("updated_at", "")),
                "char_count": _int_value(document.get("char_count", 0)),
            }
            for document in sorted(
                documents, key=lambda item: str(item.get("document_id", ""))
            )
        ],
        "parameters": {
            "seed": seed,
            "clusters": clusters,
            "neighbors": neighbors,
            "limit": limit,
            "similarity_mode": similarity_mode,
        },
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _map_run_id(name: str, document_set_signature: str, timestamp: str) -> str:
    digest = hashlib.sha256(
        f"{name}\0{document_set_signature}\0{timestamp}".encode()
    ).hexdigest()
    return f"map_run_{digest[:16]}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _dict_list(value: object) -> list[dict[str, object]]:
    return [item for item in _list(value) if isinstance(item, dict)]


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _int_value(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0
