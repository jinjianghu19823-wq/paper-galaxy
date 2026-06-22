"""Text extraction helpers for Phase 1 local document types."""

from __future__ import annotations

from pathlib import Path

from paper_galaxy.models import ExtractedContent

from .latex import extract_latex_file
from .markdown import extract_markdown_file
from .ocr import extract_image_ocr
from .pdf import extract_pdf_file
from .text import extract_text_file

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}


def extract_file(
    path: Path,
    *,
    include_pdf: bool = True,
    include_images: bool = False,
    ocr: bool = False,
    ocr_language: str = "eng",
) -> tuple[ExtractedContent | None, str | None]:
    """Extract supported local text, returning a skip reason on failure."""

    suffix = path.suffix.lower()
    try:
        if suffix == ".txt":
            return extract_text_file(path), None
        if suffix in {".md", ".markdown"}:
            return extract_markdown_file(path), None
        if suffix == ".tex":
            return extract_latex_file(path), None
        if suffix == ".pdf":
            if not include_pdf:
                return None, "PDF support disabled"
            return extract_pdf_file(path)
        if suffix in IMAGE_EXTENSIONS:
            if not include_images:
                return None, "image support disabled"
            if not ocr:
                return None, "image OCR disabled; pass --ocr to extract image text"
            return extract_image_ocr(path, language=ocr_language)
    except OSError as exc:
        return None, f"could not read file: {exc}"
    except UnicodeError as exc:
        return None, f"could not decode file: {exc}"
    return None, f"unsupported extension: {suffix}"
