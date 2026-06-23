"""Optional local vector index helpers."""

from __future__ import annotations

from pathlib import Path


def vector_index_dir(project_dir: Path | str) -> Path:
    """Return the local-only directory for optional vector index files."""

    return Path(project_dir).expanduser().resolve() / ".paper-galaxy" / "vector_indexes"


def faiss_available() -> bool:
    """Return whether optional FAISS support is importable."""

    try:
        import faiss  # noqa: F401
    except ImportError:
        return False
    return True
