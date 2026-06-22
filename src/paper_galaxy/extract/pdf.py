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

    warnings: list[str] = []
    page_texts: list[str] = []
    page_char_counts: list[int] = []
    page_count = 0
    encrypted = False
    try:
        reader = PdfReader(path)
        encrypted = bool(getattr(reader, "is_encrypted", False))
        if encrypted:
            warnings.append("encrypted PDF; text extraction may be incomplete")
            try:
                reader.decrypt("")
            except Exception as exc:
                warnings.append(f"encrypted PDF could not be decrypted: {exc}")
        try:
            pages = list(reader.pages)
        except Exception as exc:
            warnings.append(f"could not enumerate PDF pages: {exc}")
            pages = []
        page_count = len(pages)
        for index, page in enumerate(pages, start=1):
            try:
                page_text = page.extract_text() or ""
            except Exception as exc:
                warnings.append(f"page {index} text extraction failed: {exc}")
                page_text = ""
            page_texts.append(page_text)
            page_char_counts.append(len(page_text.strip()))
    except Exception as exc:
        return None, f"PDF extraction failed: {exc}"

    text = normalize_whitespace("\n".join(page_texts))
    metadata_title = _metadata_title(getattr(reader, "metadata", None))
    text_title = first_meaningful_line(text)
    title = metadata_title or text_title or path.stem
    detected_title_source = (
        "metadata" if metadata_title else "text" if text_title else "filename"
    )
    average_chars = (sum(page_char_counts) / page_count) if page_count else 0
    scanned_candidate = page_count > 0 and (len(text) < 80 or average_chars < 40)
    if scanned_candidate:
        warnings.append("likely scanned PDF or image-only PDF; OCR may be needed")
    return (
        ExtractedContent(
            title=title,
            text=text,
            warnings=tuple(warnings),
            method="pdf-pypdf",
            metadata={
                "page_count": page_count,
                "page_char_counts": page_char_counts,
                "detected_title_source": detected_title_source,
                "scanned_pdf_candidate": scanned_candidate,
                "encrypted": encrypted,
                "extraction_quality": _quality_label(len(text), page_count),
            },
        ),
        None,
    )


def _pypdf_available() -> bool:
    return importlib.util.find_spec("pypdf") is not None


def _metadata_title(metadata: Any) -> str | None:
    title = getattr(metadata, "title", None) if metadata is not None else None
    if isinstance(title, str) and title.strip():
        return title.strip()
    return None


def _quality_label(char_count: int, page_count: int) -> str:
    if page_count <= 0 or char_count <= 0:
        return "empty"
    average = char_count / page_count
    if average < 40:
        return "very_low"
    if average < 200:
        return "low"
    return "ok"
