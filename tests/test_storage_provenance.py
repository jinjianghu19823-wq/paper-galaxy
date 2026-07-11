from __future__ import annotations

from paper_galaxy.storage.provenance import document_content_revision_sha256


def test_document_content_revision_encoding_is_unambiguous() -> None:
    first_title = "A\0paper.md"
    first_text = "D"
    second_title = "A"
    second_text = "paper.md\0D"

    assert (
        f"{first_title}\0paper.md\0{first_text}"
        == f"{second_title}\0paper.md\0{second_text}"
    )
    assert document_content_revision_sha256(
        title=first_title,
        relative_path="paper.md",
        text=first_text,
    ) != document_content_revision_sha256(
        title=second_title,
        relative_path="paper.md",
        text=second_text,
    )
