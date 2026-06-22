"""Deterministic JSON sidecar export."""

from __future__ import annotations

import json
from pathlib import Path

from paper_galaxy.models import Document, GalaxyBuildResult, MapPoint, SkippedFile


def write_json_export(result: GalaxyBuildResult, output_path: Path) -> None:
    """Write a deterministic JSON export without full source text."""

    payload = {
        "corpus_path": str(result.corpus_path),
        "files_found": result.files_found,
        "documents": [_document_payload(document) for document in result.documents],
        "points": [_point_payload(point) for point in result.points],
        "cluster_labels": {
            str(cluster_id): label
            for cluster_id, label in sorted(result.cluster_labels.items())
        },
        "skipped_files": [
            _skipped_payload(skipped) for skipped in result.skipped_files
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _document_payload(document: Document) -> dict[str, object]:
    return {
        "id": document.id,
        "title": document.title,
        "relative_path": document.relative_path,
        "file_type": document.file_type,
        "char_count": document.char_count,
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


def _skipped_payload(skipped: SkippedFile) -> dict[str, str]:
    return {
        "relative_path": skipped.relative_path,
        "reason": skipped.reason,
    }
