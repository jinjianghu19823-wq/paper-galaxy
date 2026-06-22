"""Deterministic text chunking for local indexing."""

from __future__ import annotations


def chunk_text(
    text: str, *, chunk_size: int = 2000, chunk_overlap: int = 200
) -> list[str]:
    """Split text into deterministic chunks.

    Paragraph boundaries are respected when they are visible. If paragraphs are
    unavailable or too large, the function falls back to character windows.
    """

    normalized_size = max(1, chunk_size)
    normalized_overlap = max(0, min(chunk_overlap, normalized_size - 1))
    paragraphs = [
        paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()
    ]
    if len(paragraphs) <= 1:
        return _window_chunks(text.strip(), normalized_size, normalized_overlap)

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > normalized_size:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(
                _window_chunks(paragraph, normalized_size, normalized_overlap)
            )
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= normalized_size:
            current = candidate
        else:
            chunks.append(current.strip())
            current = _overlap_prefix(current, normalized_overlap, paragraph)
    if current:
        chunks.append(current.strip())
    return [chunk for chunk in chunks if chunk]


def _window_chunks(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    step = max(1, chunk_size - chunk_overlap)
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start += step
    return [chunk for chunk in chunks if chunk]


def _overlap_prefix(previous: str, chunk_overlap: int, paragraph: str) -> str:
    if chunk_overlap <= 0:
        return paragraph
    prefix = previous[-chunk_overlap:].strip()
    return f"{prefix}\n\n{paragraph}".strip() if prefix else paragraph
