"""Plain text extraction and cleaning."""

from __future__ import annotations

import re
from pathlib import Path

from paper_galaxy.models import ExtractedContent

WHITESPACE_RE = re.compile(r"\s+")


def extract_text_file(path: Path) -> ExtractedContent:
    """Extract a UTF-8 text file, falling back to replacement decoding."""

    text, warnings = read_text_with_fallback(path)
    cleaned = normalize_whitespace(text)
    return ExtractedContent(
        title=first_meaningful_line(text) or path.stem,
        text=cleaned,
        warnings=tuple(warnings),
        method="text",
    )


def read_text_with_fallback(path: Path) -> tuple[str, list[str]]:
    """Read text using UTF-8 with a replacement fallback."""

    try:
        return path.read_text(encoding="utf-8"), []
    except UnicodeDecodeError:
        return (
            path.read_text(encoding="utf-8", errors="replace"),
            ["UTF-8 decoding failed; used replacement characters"],
        )


def normalize_whitespace(text: str) -> str:
    """Collapse repeated whitespace while preserving readable text."""

    return WHITESPACE_RE.sub(" ", text).strip()


def first_meaningful_line(text: str) -> str | None:
    """Return the first non-empty line-like fragment from text."""

    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    fragments = [fragment.strip() for fragment in re.split(r"[.!?]", text)]
    return next((fragment for fragment in fragments if fragment), None)
