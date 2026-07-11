"""Stable hashes for persisted source revisions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

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


def registered_source_identity(
    *,
    kind: str,
    locator: str,
    config: Mapping[str, object] | None = None,
) -> tuple[str, str]:
    """Return the stable source id and full profile signature."""

    payload = json.dumps(
        [
            "paper-galaxy-registered-source-v1",
            kind,
            locator,
            dict(config or {}),
        ],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    signature = hashlib.sha256(payload).hexdigest()
    return f"source_{signature[:20]}", signature
