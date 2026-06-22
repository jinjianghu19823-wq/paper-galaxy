"""Optional PDF extraction via pypdf."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from paper_galaxy.models import ExtractedContent

from .text import first_meaningful_line, normalize_whitespace


def extract_pdf_file(path: Path) -> tuple[ExtractedContent | None, str | None]:
    """Extract PDF text with pypdf when that optional dependency is installed."""

    if not _pypdf_available():
        return None, "PDF skipped because optional dependency pypdf is not installed"

    try:
        from pypdf import PdfReader
    except ImportError:
        return None, "PDF skipped because optional dependency pypdf is not installed"

    try:
        reader = PdfReader(path)
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        return None, f"PDF extraction failed: {exc}"

    text = normalize_whitespace("\n".join(pages))
    metadata_title = _metadata_title(getattr(reader, "metadata", None))
    title = metadata_title or first_meaningful_line(text) or path.stem
    return ExtractedContent(title=title, text=text), None


def _pypdf_available() -> bool:
    return importlib.util.find_spec("pypdf") is not None


def _metadata_title(metadata: Any) -> str | None:
    title = getattr(metadata, "title", None) if metadata is not None else None
    if isinstance(title, str) and title.strip():
        return title.strip()
    return None
