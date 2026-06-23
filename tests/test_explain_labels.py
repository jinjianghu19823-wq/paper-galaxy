from pathlib import Path

import pytest

from paper_galaxy.explain.labels import (
    apply_label_overrides,
    cluster_signature,
    label_clusters_ctfidf,
    validate_manual_label,
)
from paper_galaxy.models import Document


def test_cluster_signature_is_deterministic_for_sorted_document_ids() -> None:
    assert cluster_signature(["b", "a", "c"]) == cluster_signature(["c", "b", "a"])
    assert cluster_signature(["a", "b"]) != cluster_signature(["a", "c"])


def test_ctfidf_labels_filter_generic_filler_and_preserve_evidence() -> None:
    documents = [
        Document(
            id="doc_neural",
            path=Path("neural.md"),
            relative_path="neural.md",
            file_type="md",
            title="Fourier Neural Operator",
            text=(
                "paper method results neural operator fourier operator "
                "spectral convolution neural operator"
            ),
            char_count=120,
        ),
        Document(
            id="doc_privacy",
            path=Path("privacy.md"),
            relative_path="privacy.md",
            file_type="md",
            title="Local Privacy",
            text="paper method results local privacy private indexing browser",
            char_count=100,
        ),
    ]

    labels = label_clusters_ctfidf(documents, [0, 1])

    generated = {label.cluster_id: label for label in labels}
    neural_terms = {term.term for term in generated[0].top_terms}
    privacy_terms = {term.term for term in generated[1].top_terms}
    all_terms = neural_terms | privacy_terms
    assert "paper" not in all_terms
    assert "method" not in all_terms
    assert any("neural" in term or "operator" in term for term in neural_terms)
    assert any("privacy" in term or "local" in term for term in privacy_terms)
    assert generated[0].representatives[0].document_id == "doc_neural"


def test_manual_override_changes_display_label_only() -> None:
    document = Document(
        id="doc",
        path=Path("doc.md"),
        relative_path="doc.md",
        file_type="md",
        title="Fourier Neural Operator",
        text="neural operator fourier spectral",
        char_count=80,
    )
    label = label_clusters_ctfidf([document], [0])[0]

    patched = apply_label_overrides(
        [label],
        {label.cluster_signature: "Neural Operators"},
    )[0]

    assert patched.display_label == "Neural Operators"
    assert patched.generated_label == label.generated_label
    assert patched.top_terms == label.top_terms
    assert patched.source == "manual"


def test_manual_label_validation_rejects_empty_and_too_long() -> None:
    assert validate_manual_label("  Neural   Operators ") == "Neural Operators"
    with pytest.raises(ValueError):
        validate_manual_label("")
    with pytest.raises(ValueError):
        validate_manual_label("x" * 121)
