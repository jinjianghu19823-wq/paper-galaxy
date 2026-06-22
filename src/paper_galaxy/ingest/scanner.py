"""Recursive deterministic file scanner for Phase 1 corpora."""

from __future__ import annotations

import os
from pathlib import Path

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".tex"}
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | PDF_EXTENSIONS
IGNORED_DIR_NAMES = {
    ".git",
    ".paper-galaxy",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
}


def discover_files(
    corpus_dir: Path, *, include_pdf: bool = True, include_images: bool = False
) -> list[Path]:
    """Discover supported files under a corpus directory.

    Hidden directories and common generated folders are skipped. Symlinks are
    not followed.
    """

    root = corpus_dir.expanduser().resolve()
    supported_extensions = set(TEXT_EXTENSIONS)
    if include_pdf:
        supported_extensions.update(PDF_EXTENSIONS)
    if include_images:
        supported_extensions.update(IMAGE_EXTENSIONS)

    files: list[Path] = []
    for current_dir_raw, dirnames, filenames in os.walk(root, followlinks=False):
        current_dir = Path(current_dir_raw)
        dirnames[:] = [
            dirname
            for dirname in sorted(dirnames)
            if _should_descend(current_dir / dirname, root)
        ]
        for filename in sorted(filenames):
            path = current_dir / filename
            if path.is_symlink() or path.suffix.lower() not in supported_extensions:
                continue
            files.append(path)
    return sorted(files, key=lambda path: relative_path(path, root))


def relative_path(path: Path, corpus_dir: Path) -> str:
    """Return a stable POSIX relative path for display and IDs."""

    return path.resolve().relative_to(corpus_dir.resolve()).as_posix()


def _should_descend(path: Path, root: Path) -> bool:
    if path.is_symlink() or not path.is_dir():
        return False
    if path == root:
        return True
    name = path.name
    return name not in IGNORED_DIR_NAMES and not name.startswith(".")
