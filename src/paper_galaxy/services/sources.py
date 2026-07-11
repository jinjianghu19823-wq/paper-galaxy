"""Persistent registry for local corpus and read-only Zotero sources."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paper_galaxy.storage.json import load_json_object
from paper_galaxy.storage.provenance import registered_source_identity
from paper_galaxy.storage.sqlite import (
    connect_read_only,
    connect_read_write,
    ensure_database_ready,
    resolve_database_path,
)
from paper_galaxy.zotero.local_api import canonical_local_api_url

SOURCE_KIND_CORPUS = "corpus_directory"
SOURCE_KIND_ZOTERO = "zotero_profile"
_SOURCE_KINDS = {SOURCE_KIND_CORPUS, SOURCE_KIND_ZOTERO}
MAX_SOURCE_LIST_LIMIT = 100
MAX_SOURCE_ID_LENGTH = 200
_MAX_FILTER_VALUES = 100
_MAX_FILTER_VALUE_LENGTH = 200
_SOURCE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z")
_PUBLIC_ERROR_CODE_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
_FILTER_SEQUENCE_KEYS = frozenset({"collections", "tags", "item_types"})
_FILTER_KEYS = _FILTER_SEQUENCE_KEYS | {"include_status", "pdf_policy"}
_INCLUDE_STATUSES = frozenset({"all", "read", "reading", "to_read", "unknown"})
_PDF_POLICIES = frozenset({"extract", "metadata", "skip-missing"})


@dataclass(frozen=True)
class SourceRecord:
    """One registered source; private locator fields never cross the Web API."""

    id: str
    kind: str
    display_name: str
    root_path: str | None
    zotero_source_id: str | None
    profile_signature: str
    config: dict[str, Any]
    last_success_at: str | None
    last_error_code: str | None
    last_error_message: str | None
    created_at: str
    updated_at: str
    removed_at: str | None


def register_corpus_source(
    project_dir: Path | str,
    corpus_dir: Path | str,
    *,
    display_name: str | None = None,
) -> tuple[SourceRecord, bool]:
    """Register an existing non-symlink local directory idempotently."""

    resolved_corpus = preflight_corpus_source(project_dir, corpus_dir)
    resolved_project = Path(project_dir).expanduser().resolve()
    normalized_locator = os.path.normcase(os.path.normpath(str(resolved_corpus)))
    source_id, signature = registered_source_identity(
        kind=SOURCE_KIND_CORPUS,
        locator=normalized_locator,
    )
    selected_name = _display_name(display_name, resolved_corpus.name or "Corpus")
    return _register_source(
        resolved_project,
        source_id=source_id,
        kind=SOURCE_KIND_CORPUS,
        display_name=selected_name,
        replace_display_name=display_name is not None,
        root_path=str(resolved_corpus),
        zotero_source_id=None,
        profile_signature=signature,
        config={},
    )


def preflight_corpus_source(
    project_dir: Path | str,
    corpus_dir: Path | str,
) -> Path:
    """Validate one corpus/project relationship without writing either tree."""

    lexical_project = Path(project_dir).expanduser().absolute()
    if lexical_project.is_symlink():
        raise ValueError("Project directory must not be a symbolic link.")
    resolved_project = lexical_project.resolve(strict=False)
    raw_locator = os.fspath(corpus_dir)
    if "://" in raw_locator:
        raise ValueError(
            "Corpus source must be an existing local directory, not a URL."
        )
    lexical_path = Path(corpus_dir).expanduser()
    if lexical_path.is_symlink():
        raise ValueError("Corpus source root must not be a symbolic link.")
    if not lexical_path.is_dir():
        raise ValueError("Corpus source must be an existing directory.")
    resolved_corpus = lexical_path.resolve()
    _reject_project_writes_inside_source(
        resolved_project,
        resolved_corpus,
        label="corpus source",
    )
    return resolved_corpus


def register_zotero_source(
    project_dir: Path | str,
    zotero_source_id: str,
    *,
    filters: Mapping[str, object] | None = None,
    display_name: str | None = None,
) -> tuple[SourceRecord, bool]:
    """Register a profile for an already discovered read-only Zotero source.

    The locator and private connection configuration are loaded only from the
    project's ``zotero_sources`` table. Callers cannot use this registry as an
    arbitrary URL or filesystem-path reader. Different canonical filters get
    independent identities and, consequently, independent future cursors.
    """

    resolved_project = Path(project_dir).expanduser().resolve()
    selected_zotero_id = _source_id(zotero_source_id, label="Zotero source id")
    normalized_filters = _normalize_zotero_filters(filters)
    ensure_database_ready(resolved_project)
    connection = connect_read_only(resolved_project)
    try:
        row = connection.execute(
            """
            SELECT id, source_type, local_api_url, data_dir, library_id,
                   library_type, name
            FROM zotero_sources
            WHERE id = ?
            """,
            (selected_zotero_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError(
            "Zotero source was not found in this project; discover or import the "
            "local read-only profile first."
        )
    if str(row["source_type"]) != "local_api":
        raise ValueError(
            "Only a read-only Zotero Desktop local API source is supported."
        )

    api_url = _canonical_loopback_api_url(row["local_api_url"])
    data_dir = _canonical_data_dir(row["data_dir"])
    if data_dir is not None:
        _reject_project_writes_inside_source(
            resolved_project,
            Path(data_dir),
            label="Zotero data directory",
        )
    library_id = _bounded_stored_text(row["library_id"], "Zotero library id")
    library_type = _bounded_stored_text(row["library_type"], "Zotero library type")
    config: dict[str, object] = {
        "local_api_url": api_url,
        "data_dir": data_dir,
        "library_id": library_id,
        "library_type": library_type,
        "filters": normalized_filters,
    }
    source_id, signature = registered_source_identity(
        kind=SOURCE_KIND_ZOTERO,
        locator=selected_zotero_id,
        config=config,
    )
    selected_name = _display_name(display_name, str(row["name"]))
    return _register_source(
        resolved_project,
        source_id=source_id,
        kind=SOURCE_KIND_ZOTERO,
        display_name=selected_name,
        replace_display_name=display_name is not None,
        root_path=None,
        zotero_source_id=selected_zotero_id,
        profile_signature=signature,
        config=config,
    )


def validate_source_locator_for_use(
    project_dir: Path | str,
    source: SourceRecord,
) -> SourceRecord:
    """Revalidate a private source immediately before local I/O.

    Registration is not a permanent trust decision: an attacker or an
    accidental filesystem operation can replace a corpus directory after it
    was registered. This guard deliberately performs fresh ``lstat`` and
    strict resolution on every use.
    """

    if source.removed_at is not None:
        raise ValueError("Removed source cannot be used; register it again first.")
    resolved_project = Path(project_dir).expanduser().resolve()
    if source.kind == SOURCE_KIND_CORPUS:
        if source.root_path is None:
            raise ValueError("Corpus source has no registered local directory.")
        lexical = Path(source.root_path).expanduser()
        if not lexical.is_absolute():
            raise ValueError("Corpus source locator must be an absolute local path.")
        try:
            metadata = lexical.lstat()
            resolved = lexical.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise ValueError(
                "Corpus source directory is missing or inaccessible."
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or lexical != resolved:
            raise ValueError(
                "Corpus source locator changed or contains a symbolic link; "
                "re-register the intended directory explicitly."
            )
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("Corpus source locator is no longer a directory.")
        _reject_project_writes_inside_source(
            resolved_project,
            resolved,
            label="corpus source",
        )
        return source
    if source.kind == SOURCE_KIND_ZOTERO:
        if source.zotero_source_id is None:
            raise ValueError("Zotero profile has no registered local source id.")
        canonical_config = dict(source.config)
        canonical_config["local_api_url"] = _canonical_loopback_api_url(
            canonical_config.get("local_api_url")
        )
        canonical_config["data_dir"] = _canonical_data_dir_for_use(
            canonical_config.get("data_dir")
        )
        if canonical_config["data_dir"] is not None:
            _reject_project_writes_inside_source(
                resolved_project,
                Path(str(canonical_config["data_dir"])),
                label="Zotero data directory",
            )
        filters = canonical_config.get("filters")
        if filters is not None and not isinstance(filters, Mapping):
            raise ValueError("Zotero profile filters are malformed.")
        canonical_config["filters"] = _normalize_zotero_filters(filters)
        return replace(source, config=canonical_config)
    raise ValueError("Unknown source kind.")


def remove_source(project_dir: Path | str, source_id: str) -> SourceRecord:
    """Soft-remove one source while preserving data, jobs, and audit history."""

    selected_id = _source_id(source_id)
    now = _utc_now()
    connection = connect_read_write(project_dir)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM registered_sources WHERE id = ?", (selected_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Registered source was not found.")
        if row["removed_at"] is not None:
            connection.commit()
            return _source_from_row(row)
        active_job = connection.execute(
            """
            SELECT 1 FROM jobs
            WHERE source_id = ? AND status IN ('queued', 'running', 'cancelling')
            LIMIT 1
            """,
            (selected_id,),
        ).fetchone()
        if active_job is not None:
            raise ValueError("Source has active work; cancel or finish its jobs first.")
        connection.execute(
            """
            UPDATE registered_sources
            SET removed_at = ?, updated_at = ?
            WHERE id = ? AND removed_at IS NULL
            """,
            (now, now, selected_id),
        )
        updated = connection.execute(
            "SELECT * FROM registered_sources WHERE id = ?", (selected_id,)
        ).fetchone()
        connection.commit()
        if updated is None:
            raise RuntimeError("Registered source disappeared during removal.")
        return _source_from_row(updated)
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_sources(
    project_dir: Path | str,
    *,
    kind: str | None = None,
    include_removed: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[SourceRecord]:
    """List registered sources without mutating project state."""

    if kind is not None and kind not in _SOURCE_KINDS:
        raise ValueError("Unknown source kind.")
    clauses = [] if include_removed else ["removed_at IS NULL"]
    parameters: list[object] = []
    if kind is not None:
        clauses.append("kind = ?")
        parameters.append(kind)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    connection = connect_read_only(project_dir)
    try:
        rows = connection.execute(
            f"""
            SELECT * FROM registered_sources
            {where}
            ORDER BY kind, display_name, id
            LIMIT ? OFFSET ?
            """,
            (
                *parameters,
                min(max(0, limit), MAX_SOURCE_LIST_LIMIT),
                max(0, offset),
            ),
        ).fetchall()
        return [_source_from_row(row) for row in rows]
    finally:
        connection.close()


def get_source(
    project_dir: Path | str,
    source_id: str,
    *,
    include_removed: bool = False,
) -> SourceRecord | None:
    """Load one private source record by stable id."""

    selected_id = _source_id(source_id)
    connection = connect_read_only(project_dir)
    try:
        row = connection.execute(
            """
            SELECT * FROM registered_sources
            WHERE id = ? AND (? OR removed_at IS NULL)
            """,
            (selected_id, int(include_removed)),
        ).fetchone()
        return _source_from_row(row) if row is not None else None
    finally:
        connection.close()


def public_source_payload(source: SourceRecord) -> dict[str, object]:
    """Return the path-free source shape used by ordinary Web responses."""

    return {
        "id": source.id,
        "kind": source.kind,
        "display_name": source.display_name,
        "status": "removed" if source.removed_at else "active",
        "last_success_at": source.last_success_at,
        "last_error": (
            {
                "code": _public_error_code(source.last_error_code),
                "message": (
                    "Source operation failed; inspect the local CLI for details."
                ),
            }
            if source.last_error_code
            else None
        ),
        "created_at": source.created_at,
        "updated_at": source.updated_at,
    }


def _register_source(
    project_dir: Path,
    *,
    source_id: str,
    kind: str,
    display_name: str,
    replace_display_name: bool,
    root_path: str | None,
    zotero_source_id: str | None,
    profile_signature: str,
    config: dict[str, object],
) -> tuple[SourceRecord, bool]:
    ensure_database_ready(project_dir)
    now = _utc_now()
    canonical_config = json.dumps(
        config,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    connection = connect_read_write(project_dir)
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT * FROM registered_sources
            WHERE kind = ? AND profile_signature = ?
            """,
            (kind, profile_signature),
        ).fetchone()
        created = existing is None
        if existing is None:
            connection.execute(
                """
                INSERT INTO registered_sources(
                  id, kind, display_name, root_path, zotero_source_id,
                  profile_signature, config_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    kind,
                    display_name,
                    root_path,
                    zotero_source_id,
                    profile_signature,
                    canonical_config,
                    now,
                    now,
                ),
            )
        else:
            source_id = str(existing["id"])
            persisted_display_name = (
                display_name if replace_display_name else str(existing["display_name"])
            )
            unchanged = (
                str(existing["display_name"]) == persisted_display_name
                and existing["root_path"] == root_path
                and existing["zotero_source_id"] == zotero_source_id
                and str(existing["config_json"]) == canonical_config
                and existing["removed_at"] is None
            )
            if not unchanged:
                connection.execute(
                    """
                    UPDATE registered_sources
                    SET display_name = ?, root_path = ?, zotero_source_id = ?,
                        config_json = ?, removed_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        persisted_display_name,
                        root_path,
                        zotero_source_id,
                        canonical_config,
                        now,
                        source_id,
                    ),
                )
        row = connection.execute(
            "SELECT * FROM registered_sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        connection.commit()
        if row is None:
            raise RuntimeError("Registered source disappeared during creation.")
        return _source_from_row(row), created
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _source_from_row(row: sqlite3.Row) -> SourceRecord:
    return SourceRecord(
        id=str(row["id"]),
        kind=str(row["kind"]),
        display_name=str(row["display_name"]),
        root_path=str(row["root_path"]) if row["root_path"] is not None else None,
        zotero_source_id=(
            str(row["zotero_source_id"])
            if row["zotero_source_id"] is not None
            else None
        ),
        profile_signature=str(row["profile_signature"]),
        config=load_json_object(row["config_json"]),
        last_success_at=(
            str(row["last_success_at"]) if row["last_success_at"] is not None else None
        ),
        last_error_code=(
            str(row["last_error_code"]) if row["last_error_code"] is not None else None
        ),
        last_error_message=(
            str(row["last_error_message"])
            if row["last_error_message"] is not None
            else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        removed_at=str(row["removed_at"]) if row["removed_at"] else None,
    )


def _display_name(value: str | None, fallback: str) -> str:
    raw = fallback if value is None else value
    if not isinstance(raw, str) or any(
        unicodedata.category(character).startswith("C") for character in raw
    ):
        raise ValueError("Source display name contains unsupported control characters.")
    selected = " ".join(raw.split())
    if not selected or len(selected) > 120:
        raise ValueError("Source display name must contain 1 to 120 characters.")
    return selected


def _source_id(value: object, *, label: str = "Source id") -> str:
    if not isinstance(value, str) or not _SOURCE_ID_PATTERN.fullmatch(value):
        raise ValueError(
            f"{label} must contain 1 to {MAX_SOURCE_ID_LENGTH} safe characters."
        )
    return value


def _public_error_code(value: str | None) -> str:
    if value is not None and _PUBLIC_ERROR_CODE_PATTERN.fullmatch(value):
        return value
    return "source_failed"


def _bounded_stored_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"{label} in the local source record is invalid.")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError(f"{label} in the local source record is invalid.")
    return value


def _canonical_loopback_api_url(value: object) -> str:
    return canonical_local_api_url(value)


def _canonical_data_dir(value: object) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise ValueError("Zotero data directory in the local source record is invalid.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Zotero data directory must be an absolute local path.")
    return str(path.resolve(strict=False))


def _canonical_data_dir_for_use(value: object) -> str | None:
    lexical_input: Path | None = None
    if value is not None and isinstance(value, str):
        lexical_input = Path(value).expanduser()
        if lexical_input.is_symlink():
            raise ValueError("Zotero data directory cannot be a symbolic link.")
    canonical = _canonical_data_dir(value)
    if canonical is None:
        return None
    if lexical_input is None or lexical_input != Path(canonical):
        raise ValueError("Zotero data directory changed or contains a symbolic link.")
    lexical = Path(canonical)
    if lexical.exists():
        try:
            metadata = lexical.lstat()
            resolved = lexical.resolve(strict=True)
        except OSError as exc:
            raise ValueError("Zotero data directory is inaccessible.") from exc
        if stat.S_ISLNK(metadata.st_mode) or lexical != resolved:
            raise ValueError(
                "Zotero data directory changed or contains a symbolic link."
            )
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("Zotero data directory is not a directory.")
    return canonical


def _reject_project_writes_inside_source(
    project_dir: Path,
    source_root: Path,
    *,
    label: str,
) -> None:
    project = project_dir.expanduser().resolve()
    source = source_root.expanduser().resolve(strict=False)
    metadata = project / ".paper-galaxy"
    database = resolve_database_path(project).expanduser().resolve(strict=False)
    mutable_roots = (project, metadata, metadata / "backups", database)
    source_is_project_metadata = source == metadata or source.is_relative_to(metadata)
    if source_is_project_metadata or any(
        root == source or root.is_relative_to(source) for root in mutable_roots
    ):
        raise ValueError(
            f"Paper Galaxy project metadata or database cannot be stored inside the "
            f"registered {label}. Choose a separate project directory."
        )


def _normalize_zotero_filters(
    filters: Mapping[str, object] | None,
) -> dict[str, object]:
    if filters is None:
        return {}
    if not all(isinstance(key, str) for key in filters):
        raise ValueError("Zotero source filter names must be strings.")
    extras = set(filters) - _FILTER_KEYS
    if extras:
        raise ValueError(
            f"Unsupported Zotero source filters: {', '.join(sorted(extras))}."
        )
    normalized: dict[str, object] = {}
    for key in sorted(_FILTER_SEQUENCE_KEYS):
        if key not in filters:
            continue
        value = filters[key]
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValueError(f"Zotero filter {key} must be a list of strings.")
        if len(value) > _MAX_FILTER_VALUES:
            raise ValueError(f"Zotero filter {key} has too many values.")
        selected: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError(f"Zotero filter {key} must contain only strings.")
            if any(unicodedata.category(char).startswith("C") for char in item):
                raise ValueError(f"Zotero filter {key} contains an invalid value.")
            item = " ".join(item.split())
            if not item or len(item) > _MAX_FILTER_VALUE_LENGTH:
                raise ValueError(f"Zotero filter {key} contains an invalid value.")
            selected.add(item)
        normalized[key] = sorted(selected, key=lambda item: (item.casefold(), item))
    collections = normalized.get("collections")
    if isinstance(collections, list) and len(collections) > 1:
        raise ValueError(
            "One Zotero source profile currently supports at most one collection; "
            "register separate profiles for additional collections."
        )
    if "include_status" in filters:
        status_value = filters["include_status"]
        if status_value == "to-read":
            status_value = "to_read"
        if not isinstance(status_value, str) or status_value not in _INCLUDE_STATUSES:
            raise ValueError("Unsupported Zotero include_status filter.")
        normalized["include_status"] = status_value
    if "pdf_policy" in filters:
        policy = filters["pdf_policy"]
        if not isinstance(policy, str) or policy not in _PDF_POLICIES:
            raise ValueError("Unsupported Zotero pdf_policy filter.")
        normalized["pdf_policy"] = policy
    return normalized


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")
