"""Transparent local explainability helpers for Paper Galaxy."""

from paper_galaxy.explain.labels import (
    apply_label_overrides,
    cluster_signature,
    label_clusters_ctfidf,
    validate_manual_label,
)
from paper_galaxy.explain.pairs import explain_pair

__all__ = [
    "apply_label_overrides",
    "cluster_signature",
    "explain_pair",
    "label_clusters_ctfidf",
    "validate_manual_label",
]
