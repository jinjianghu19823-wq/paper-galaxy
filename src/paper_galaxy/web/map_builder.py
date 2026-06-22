"""Ephemeral Phase 3 map building from the local SQLite index."""

from __future__ import annotations

import math
from pathlib import Path

from paper_galaxy.ml.cluster import compute_clusters
from paper_galaxy.ml.labels import label_clusters
from paper_galaxy.ml.layout import compute_layout
from paper_galaxy.ml.neighbors import compute_neighbors
from paper_galaxy.ml.tfidf import compute_tfidf, top_terms_for_documents
from paper_galaxy.models import Document, MapPoint
from paper_galaxy.records import DatabaseStats, IndexedDocument
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path


def build_map_payload(
    *,
    project_dir: Path,
    seed: int = 42,
    clusters: int | None = None,
    neighbors: int = 5,
    limit: int = 1000,
) -> dict[str, object]:
    """Build JSON-serializable map data from active indexed documents."""

    database_path = resolve_database_path(project_dir)
    connection = connect_database(project_dir)
    try:
        initialize_database(connection)
        repository = Repository(connection, database_path)
        stats = repository.get_stats()
        limited_rows = repository.list_documents_with_text(
            statuses={"active"},
            limit=max(0, limit),
        )
    finally:
        connection.close()

    warnings = _limit_warnings(stats, len(limited_rows), limit)
    if not limited_rows:
        if stats.active_documents == 0:
            warnings.append("No active indexed documents found.")
        return {
            "documents": [],
            "points": [],
            "cluster_labels": {},
            "stats": _stats_payload(stats),
            "warnings": warnings,
        }

    indexed_documents = [document for document, _ in limited_rows]
    documents = [
        Document(
            id=document.id,
            path=Path(document.path),
            relative_path=document.relative_path,
            file_type=document.file_type,
            title=document.title,
            text=text,
            char_count=document.char_count,
        )
        for document, text in limited_rows
    ]

    _, matrix, terms = compute_tfidf([document.text for document in documents])
    coordinates = compute_layout(matrix, seed=seed)
    cluster_ids = compute_clusters(matrix, requested=clusters, seed=seed)
    cluster_labels = label_clusters(matrix, cluster_ids, terms)
    document_neighbors = compute_neighbors(
        matrix,
        documents,
        neighbor_count=neighbors,
    )
    document_terms = top_terms_for_documents(matrix, terms)
    points = [
        MapPoint(
            document_id=document.id,
            x=_finite(coordinates[index][0]),
            y=_finite(coordinates[index][1]),
            cluster_id=cluster_ids[index],
            cluster_label=cluster_labels[cluster_ids[index]],
            nearest_neighbors=document_neighbors[document.id],
            top_terms=document_terms[index],
        )
        for index, document in enumerate(documents)
    ]

    return {
        "documents": [_document_payload(document) for document in indexed_documents],
        "points": [_point_payload(point) for point in points],
        "cluster_labels": {
            str(cluster_id): label
            for cluster_id, label in sorted(cluster_labels.items())
        },
        "stats": _stats_payload(stats),
        "warnings": warnings,
    }


def _limit_warnings(
    stats: DatabaseStats, returned_count: int, requested_limit: int
) -> list[str]:
    if stats.active_documents > returned_count:
        return [
            "Map uses the first "
            f"{returned_count} active documents because limit={requested_limit}."
        ]
    return []


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


def _point_payload(point: MapPoint) -> dict[str, object]:
    return {
        "document_id": point.document_id,
        "x": point.x,
        "y": point.y,
        "cluster_id": point.cluster_id,
        "cluster_label": point.cluster_label,
        "top_terms": point.top_terms,
        "nearest_neighbors": [
            {
                "document_id": neighbor.document_id,
                "title": neighbor.title,
                "relative_path": neighbor.relative_path,
                "score": neighbor.score,
            }
            for neighbor in point.nearest_neighbors
        ],
    }


def _stats_payload(stats: DatabaseStats) -> dict[str, object]:
    return {
        "database_path": str(stats.database_path),
        "documents": stats.documents,
        "active_documents": stats.active_documents,
        "missing_documents": stats.missing_documents,
        "unindexed_documents": stats.unindexed_documents,
        "chunks": stats.chunks,
        "scan_runs": stats.scan_runs,
        "last_scan_time": stats.last_scan_time,
        "total_indexed_characters": stats.total_indexed_characters,
    }


def _finite(value: float) -> float:
    return value if math.isfinite(value) else 0.0
