"""Reading-status heuristics and Zotero reading graph payloads."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from paper_galaxy.explain.clusters import clusters_payload
from paper_galaxy.explain.labels import (
    apply_label_overrides,
    fallback_cluster_labels,
    label_clusters_ctfidf,
)
from paper_galaxy.ml.cluster import compute_clusters
from paper_galaxy.ml.labels import label_clusters
from paper_galaxy.ml.layout import compute_layout
from paper_galaxy.ml.neighbors import compute_neighbors
from paper_galaxy.ml.tfidf import compute_tfidf, top_terms_for_documents
from paper_galaxy.models import Document, MapPoint
from paper_galaxy.records import IndexedDocument
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path
from paper_galaxy.zotero.models import ZoteroItem

DEFAULT_READ_TAGS = ("read", "Read", "finished")
DEFAULT_READING_TAGS = ("reading", "Reading", "current")
DEFAULT_TO_READ_TAGS = ("to read", "To Read", "unread", "queue")
VALID_READING_STATUSES = {"read", "reading", "to_read", "unknown", "all"}


def infer_reading_status(
    item: ZoteroItem,
    *,
    collection_names: list[str],
    read_tags: tuple[str, ...] = DEFAULT_READ_TAGS,
    reading_tags: tuple[str, ...] = DEFAULT_READING_TAGS,
    to_read_tags: tuple[str, ...] = DEFAULT_TO_READ_TAGS,
) -> str:
    """Infer a conservative reading status from tags, collections, and notes."""

    signals = {tag.tag.lower() for tag in item.tags}
    signals.update(name.lower() for name in collection_names)
    if _matches(signals, reading_tags):
        return "reading"
    if _matches(signals, to_read_tags):
        return "to_read"
    if _matches(signals, read_tags) or item.notes:
        return "read"
    return "unknown"


def build_zotero_reading_map_payload(
    *,
    repository: Repository,
    project_dir: Path,
    status: str = "all",
    collection: str | None = None,
    tag: str | None = None,
    seed: int = 42,
    clusters: int | None = None,
    neighbors: int = 5,
    limit: int = 1000,
) -> dict[str, object]:
    """Build a live reading graph from imported Zotero documents."""

    rows = repository.list_zotero_documents_with_text(
        status=status,
        collection=collection,
        tag=tag,
        limit=max(0, limit),
    )
    if not rows:
        return {
            "documents": [],
            "points": [],
            "cluster_labels": {},
            "clusters": [],
            "stats": repository.zotero_stats(),
            "warnings": ["No imported Zotero documents match the selected filters."],
        }
    indexed_documents = [document for document, _, _ in rows]
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
        for document, text, _ in rows
    ]
    _, matrix, terms = compute_tfidf([document.text for document in documents])
    coordinates = compute_layout(matrix, seed=seed)
    cluster_ids = compute_clusters(matrix, requested=clusters, seed=seed)
    label_rows = _cluster_label_metadata(
        repository=repository,
        documents=documents,
        cluster_ids=cluster_ids,
        matrix=matrix,
        terms=terms,
    )
    cluster_by_id = {label.cluster_id: label for label in label_rows}
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
            cluster_label=cluster_by_id[cluster_ids[index]].display_label,
            cluster_signature=cluster_by_id[cluster_ids[index]].cluster_signature,
            nearest_neighbors=document_neighbors[document.id],
            top_terms=document_terms[index],
        )
        for index, document in enumerate(documents)
    ]
    return {
        "documents": [
            _document_payload(document, zotero_meta)
            for document, _, zotero_meta in zip(
                indexed_documents,
                [text for _, text, _ in rows],
                [meta for _, _, meta in rows],
                strict=False,
            )
        ],
        "points": [_point_payload(point) for point in points],
        "cluster_labels": {
            str(label.cluster_id): label.display_label
            for label in sorted(label_rows, key=lambda item: item.cluster_id)
        },
        "clusters": clusters_payload(label_rows),
        "stats": repository.zotero_stats(),
        "warnings": [],
    }


def build_and_store_zotero_reading_map(
    *,
    project_dir: Path,
    name: str = "Zotero Reading Graph",
    status: str = "all",
    collection: str | None = None,
    tag: str | None = None,
    seed: int = 42,
    clusters: int | None = None,
    neighbors: int = 5,
    limit: int = 1000,
) -> dict[str, object]:
    """Build and persist a Zotero reading graph map run."""

    resolved_project_dir = project_dir.expanduser().resolve()
    connection = connect_database(resolved_project_dir)
    try:
        initialize_database(connection)
        repository = Repository(connection, resolve_database_path(resolved_project_dir))
        payload = build_zotero_reading_map_payload(
            repository=repository,
            project_dir=resolved_project_dir,
            status=status,
            collection=collection,
            tag=tag,
            seed=seed,
            clusters=clusters,
            neighbors=neighbors,
            limit=limit,
        )
        points = _dict_list(payload.get("points"))
        cluster_rows = _dict_list(payload.get("clusters"))
        documents = _dict_list(payload.get("documents"))
        run_id = _map_run_id(name, documents, seed, clusters, neighbors, limit)
        metadata = {
            "source": "zotero",
            "builder": "paper_galaxy.zotero.reading",
            "parameters": {
                "status": status,
                "collection": collection,
                "tag": tag,
                "seed": seed,
                "clusters": clusters,
                "neighbors": neighbors,
                "limit": limit,
            },
        }
        with connection:
            run = repository.save_map_run(
                run_id=run_id,
                name=name,
                status="completed",
                similarity_mode="tfidf",
                model_id=None,
                seed=seed,
                requested_clusters=clusters,
                requested_neighbors=neighbors,
                requested_limit=limit,
                document_count=len(points),
                cluster_count=len(cluster_rows),
                document_set_signature=_document_set_signature(documents),
                warnings=[str(w) for w in _list(payload.get("warnings"))],
                metadata=metadata,
                points=points,
                clusters=cluster_rows,
            )
    finally:
        connection.close()
    return {**payload, "map_run": run}


def reading_status_counts(
    items: list[ZoteroItem],
    statuses: list[str],
) -> dict[str, int]:
    """Return deterministic counts for import summaries."""

    del items
    counts = Counter(statuses)
    return {
        status: counts.get(status, 0)
        for status in sorted(VALID_READING_STATUSES - {"all"})
    }


def _matches(signals: set[str], needles: tuple[str, ...]) -> bool:
    lowered = {needle.lower() for needle in needles}
    return any(needle in signals for needle in lowered)


def _cluster_label_metadata(
    *,
    repository: Repository,
    documents: list[Document],
    cluster_ids: list[int],
    matrix: object,
    terms: list[str],
) -> list[Any]:
    try:
        labels = label_clusters_ctfidf(documents, cluster_ids)
    except Exception:
        simple = label_clusters(matrix, cluster_ids, terms)
        labels = fallback_cluster_labels(documents, cluster_ids, simple)
    signatures = [label.cluster_signature for label in labels]
    overrides = repository.get_cluster_label_overrides(signatures)
    return apply_label_overrides(labels, overrides)


def _document_payload(
    document: IndexedDocument, zotero_meta: dict[str, object]
) -> dict[str, object]:
    return {
        "document_id": document.id,
        "id": document.id,
        "title": document.title,
        "relative_path": document.relative_path,
        "file_type": document.file_type,
        "char_count": document.char_count,
        "status": document.status,
        "updated_at": document.updated_at,
        "zotero": zotero_meta,
    }


def _point_payload(point: MapPoint) -> dict[str, object]:
    return {
        "document_id": point.document_id,
        "x": point.x,
        "y": point.y,
        "cluster_id": point.cluster_id,
        "cluster_label": point.cluster_label,
        "cluster_signature": point.cluster_signature,
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


def _document_set_signature(documents: list[dict[str, object]]) -> str:
    data = [
        {
            "document_id": str(document.get("document_id", "")),
            "updated_at": str(document.get("updated_at", "")),
            "char_count": _int_value(document.get("char_count")),
        }
        for document in sorted(
            documents,
            key=lambda item: str(item.get("document_id", "")),
        )
    ]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _map_run_id(
    name: str,
    documents: list[dict[str, object]],
    seed: int,
    clusters: int | None,
    neighbors: int,
    limit: int,
) -> str:
    signature = {
        "name": name,
        "documents": _document_set_signature(documents),
        "seed": seed,
        "clusters": clusters,
        "neighbors": neighbors,
        "limit": limit,
    }
    digest = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    return f"map_run_{digest[:16]}"


def _finite(value: float) -> float:
    return value if math.isfinite(value) else 0.0


def _dict_list(value: object) -> list[dict[str, object]]:
    return [item for item in _list(value) if isinstance(item, dict)]


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _int_value(value: object) -> int:
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
