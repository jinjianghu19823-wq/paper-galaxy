"""Built-in local plugins shipped with Paper Galaxy."""

from __future__ import annotations

from paper_galaxy.plugins.base import PluginInfo

BUILTIN_PLUGINS: tuple[PluginInfo, ...] = (
    PluginInfo(
        id="extractor.text",
        name="Plain text extractor",
        kind="extractor",
        enabled_by_default=True,
        local_only=True,
        description="Extracts UTF-8 plain text files.",
        file_extensions=(".txt",),
    ),
    PluginInfo(
        id="extractor.markdown",
        name="Markdown extractor",
        kind="extractor",
        enabled_by_default=True,
        local_only=True,
        description="Extracts Markdown text, frontmatter hints, headings, and links.",
        file_extensions=(".md", ".markdown"),
    ),
    PluginInfo(
        id="extractor.latex",
        name="LaTeX extractor",
        kind="extractor",
        enabled_by_default=True,
        local_only=True,
        description="Extracts readable text from local LaTeX source files.",
        file_extensions=(".tex",),
    ),
    PluginInfo(
        id="extractor.pdf-pypdf",
        name="PDF extractor via pypdf",
        kind="extractor",
        enabled_by_default=True,
        local_only=True,
        description="Extracts text from local PDF files when pypdf is installed.",
        file_extensions=(".pdf",),
        optional_dependencies=("pypdf",),
    ),
    PluginInfo(
        id="extractor.image-ocr-tesseract",
        name="Image OCR via Tesseract",
        kind="extractor",
        enabled_by_default=False,
        local_only=True,
        description="Extracts image text only when local OCR is explicitly enabled.",
        file_extensions=(".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"),
        optional_dependencies=("Pillow", "pytesseract", "tesseract"),
    ),
)
