"""Optional local OCR helpers for image files."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

from paper_galaxy.models import ExtractedContent

from .text import normalize_whitespace


def ocr_available() -> bool:
    """Return whether optional Python OCR deps and the tesseract binary exist."""

    return _pillow_available() and _pytesseract_available() and detect_tesseract()


def detect_tesseract() -> bool:
    """Return whether the local tesseract binary is on PATH."""

    return shutil.which("tesseract") is not None


def extract_image_ocr(
    path: Path, *, language: str = "eng"
) -> tuple[ExtractedContent | None, str | None]:
    """Extract text from an image with local Tesseract OCR when available."""

    if not _pillow_available():
        return None, "OCR skipped because optional dependency Pillow is not installed"
    if not _pytesseract_available():
        return (
            None,
            "OCR skipped because optional dependency pytesseract is not installed",
        )
    if not detect_tesseract():
        return None, "OCR skipped because the tesseract binary is not available"

    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return None, "OCR skipped because optional OCR dependencies are not installed"

    try:
        with Image.open(path) as image:
            image_size = [int(image.width), int(image.height)]
            raw_text = pytesseract.image_to_string(image, lang=language)
    except Exception as exc:
        return None, f"OCR extraction failed: {exc}"

    text = normalize_whitespace(raw_text)
    warnings: list[str] = []
    if len(text) < 20:
        warnings.append("OCR extracted very little text")
    return (
        ExtractedContent(
            title=path.stem,
            text=text,
            warnings=tuple(warnings),
            method="image-ocr-tesseract",
            metadata={
                "image_size": image_size,
                "ocr_language": language,
                "extraction_quality": "low" if len(text) < 80 else "ok",
            },
        ),
        None,
    )


def _pillow_available() -> bool:
    return importlib.util.find_spec("PIL") is not None


def _pytesseract_available() -> bool:
    return importlib.util.find_spec("pytesseract") is not None
