"""JSON payload helpers for cluster explanations."""

from __future__ import annotations

from paper_galaxy.explain.models import ClusterLabel


def cluster_payload(label: ClusterLabel) -> dict[str, object]:
    """Convert a cluster label value object to an API-safe payload."""

    return {
        "cluster_id": label.cluster_id,
        "cluster_signature": label.cluster_signature,
        "generated_label": label.generated_label,
        "display_label": label.display_label,
        "source": label.source,
        "size": len(label.document_ids),
        "document_ids": label.document_ids,
        "top_terms": [
            {"term": term.term, "score": term.score} for term in label.top_terms
        ],
        "representatives": [
            {
                "document_id": document.document_id,
                "title": document.title,
                "relative_path": document.relative_path,
                "score": document.score,
            }
            for document in label.representatives
        ],
        "warnings": label.warnings,
    }


def clusters_payload(labels: list[ClusterLabel]) -> list[dict[str, object]]:
    """Convert all cluster labels to stable API payload order."""

    return [cluster_payload(label) for label in sorted(labels, key=_sort_key)]


def _sort_key(label: ClusterLabel) -> tuple[int, str]:
    return (label.cluster_id, label.cluster_signature)
