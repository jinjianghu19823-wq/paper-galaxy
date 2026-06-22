"""Term-based labels for clusters."""

from __future__ import annotations

from typing import Any

from paper_galaxy.errors import MissingDependencyError


def label_clusters(
    matrix: Any, labels: list[int], terms: list[str], *, limit: int = 3
) -> dict[int, str]:
    """Create simple cluster labels from top TF-IDF terms."""

    try:
        import numpy as np
    except ImportError as exc:
        raise MissingDependencyError("numpy") from exc

    cluster_labels: dict[int, str] = {}
    for cluster_id in sorted(set(labels)):
        row_indices = [
            index for index, label in enumerate(labels) if label == cluster_id
        ]
        if not row_indices:
            cluster_labels[cluster_id] = f"Cluster {cluster_id}"
            continue
        scores = np.asarray(matrix[row_indices].sum(axis=0)).ravel()
        ordered = scores.argsort()[::-1]
        top_terms = [terms[int(index)] for index in ordered if scores[int(index)] > 0][
            :limit
        ]
        cluster_labels[cluster_id] = (
            " / ".join(top_terms) if top_terms else f"Cluster {cluster_id}"
        )
    return cluster_labels
