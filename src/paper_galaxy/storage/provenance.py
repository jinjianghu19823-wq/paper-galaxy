"""Stable hashes for persisted source revisions."""

from __future__ import annotations

import hashlib
import json

DOCUMENT_CONTENT_REVISION_ALGORITHM = "paper-galaxy-document-content-canonical-json-v1"


def document_content_revision_sha256(
    *,
    title: str,
    relative_path: str,
    text: str,
) -> str:
    """Hash every persisted field that can change document embedding input."""

    payload = json.dumps(
        [
            DOCUMENT_CONTENT_REVISION_ALGORITHM,
            title,
            relative_path,
            text,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()
