"""Project validation for local Paper Galaxy workspaces."""

from __future__ import annotations

import importlib.util
import json
import math
import sqlite3
import struct
from pathlib import Path
from typing import Any

from paper_galaxy.embeddings.builder import (
    CHUNK_VECTOR_ALGORITHM_VERSION,
    DOCUMENT_VECTOR_ALGORITHM_VERSION,
)
from paper_galaxy.embeddings.models import (
    LEGACY_UNKNOWN_PROVENANCE,
    stable_embedding_model_id,
)
from paper_galaxy.errors import DatabaseError, UnsupportedSchemaError
from paper_galaxy.paths import project_config_path
from paper_galaxy.storage.json import StoredJSONError, load_json_list, load_json_object
from paper_galaxy.storage.migrations import (
    MIGRATIONS,
    SCHEMA_VERSION,
    validate_schema_capability,
)
from paper_galaxy.storage.provenance import document_content_revision_sha256
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import (
    connect_diagnostic_read_only,
    resolve_database_path,
)

OPTIONAL_DEPENDENCIES: tuple[tuple[str, str], ...] = (
    ("pypdf", "pypdf"),
    ("Pillow", "PIL"),
    ("pytesseract", "pytesseract"),
    ("sklearn", "sklearn"),
    ("umap", "umap"),
    ("sentence_transformers", "sentence_transformers"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("plotly", "plotly"),
)

REQUIRED_TABLES = {
    "schema_meta",
    "schema_migrations",
    "corpora",
    "scan_runs",
    "documents",
    "document_texts",
    "chunks",
    "skipped_files",
    "extraction_reports",
    "embedding_models",
    "vectors",
    "embedding_runs",
    "vector_indexes",
    "cluster_label_overrides",
    "map_runs",
    "map_run_points",
    "map_run_clusters",
    "zotero_sources",
    "zotero_import_runs",
    "zotero_items",
    "zotero_creators",
    "zotero_collections",
    "zotero_item_collections",
    "zotero_item_tags",
    "zotero_attachments",
    "zotero_document_links",
    "registered_sources",
    "jobs",
    "documents_fts",
}

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "schema_meta": {"key", "value"},
    "schema_migrations": {"version", "name", "applied_at"},
    "scan_runs": {
        "id",
        "corpus_id",
        "started_at",
        "status",
        "error_code",
        "error_message",
        "owner_pid",
    },
    "documents": {
        "id",
        "relative_path",
        "sha256",
        "content_revision_sha256",
        "status",
    },
    "document_texts": {"document_id", "text"},
    "chunks": {"id", "document_id", "chunk_index", "text", "text_sha256"},
    "embedding_models": {
        "id",
        "dimension",
        "config_json",
        "model_fingerprint",
        "fingerprint_algorithm",
    },
    "embedding_runs": {
        "id",
        "status",
        "error_code",
        "error_message",
        "sources_changed",
        "owner_pid",
    },
    "vectors": {
        "id",
        "model_id",
        "object_type",
        "object_id",
        "text_sha256",
        "source_content_sha256",
        "model_fingerprint",
        "algorithm_version",
        "dimension",
        "dtype",
        "vector",
        "metadata_json",
    },
    "vector_indexes": {
        "id",
        "model_id",
        "object_type",
        "model_fingerprint",
        "algorithm_version",
        "vector_set_sha256",
    },
    "zotero_sources": {"id", "last_version"},
    "zotero_import_runs": {
        "id",
        "source_id",
        "status",
        "config_json",
        "error_code",
        "error_message",
        "owner_pid",
    },
    "zotero_items": {
        "id",
        "source_id",
        "version",
        "reading_status",
        "data_json",
        "child_manifest_json",
    },
    "registered_sources": {
        "id",
        "kind",
        "root_path",
        "zotero_source_id",
        "profile_signature",
        "config_json",
        "removed_at",
    },
    "jobs": {
        "id",
        "queue_sequence",
        "kind",
        "source_id",
        "request_key",
        "status",
        "params_json",
        "result_summary_json",
        "cancel_requested",
        "owner_pid",
        "owner_instance_id",
        "heartbeat_at",
        "started_at",
        "finished_at",
    },
}


def validate_project(project_dir: Path, *, check_stale: bool = True) -> dict[str, Any]:
    """Validate a project directory and return a JSON-safe report."""

    resolved_project_dir = project_dir.expanduser().resolve()
    database_path = resolve_database_path(resolved_project_dir)
    issues: list[dict[str, str]] = []
    report: dict[str, Any] = {
        "project_dir": str(resolved_project_dir),
        "project_config_path": str(project_config_path(resolved_project_dir)),
        "project_config_exists": project_config_path(resolved_project_dir).exists(),
        "database_path": str(database_path),
        "database_exists": database_path.exists(),
        "schema_version": None,
        "expected_schema_version": SCHEMA_VERSION,
        "check_errors": 0,
        "counts": {},
        "tables": {},
        "integrity": _empty_integrity(),
        "schema_capability": {"ok": False, "missing_columns": []},
        "migration_history": {"ok": False, "missing": [], "mismatched": []},
        "fts": _empty_fts_status(),
        "vector_consistency": _empty_vector_consistency(),
        "zotero_consistency": _empty_zotero_consistency(),
        "source_job_consistency": _empty_source_job_consistency(),
        "optional_dependencies": _optional_dependency_status(),
        "issues": issues,
    }
    if not report["project_config_exists"]:
        _issue(
            issues,
            "warning",
            "project_config_missing",
            ".paper-galaxy/project.toml was not found; defaults will be used.",
        )
    if not database_path.exists():
        _issue(
            issues,
            "error",
            "database_missing",
            "No Paper Galaxy database found. Run paper-galaxy index first.",
        )
        _finalize_status(report)
        return report

    try:
        connection = connect_diagnostic_read_only(resolved_project_dir)
    except DatabaseError as exc:
        if hasattr(exc, "found_version"):
            report["schema_version"] = str(exc.found_version)
        report["integrity"] = _not_run_integrity(exc.safe_message)
        _issue(issues, "error", exc.code, exc.safe_message)
        _finalize_status(report)
        return report
    except sqlite3.Error as exc:
        message = str(exc)
        report["integrity"] = {
            "quick_check": {"ok": False, "messages": [message]},
            "foreign_key_check": {
                "ok": None,
                "status": "not_run",
                "violation_count": 0,
                "violations": [],
            },
        }
        _issue(issues, "error", "quick_check_failed", message)
        _finalize_status(report)
        return report
    try:
        report["integrity"] = _integrity_status(connection)
        if not report["integrity"]["quick_check"]["ok"]:
            _issue(
                issues,
                "error",
                "quick_check_failed",
                "SQLite quick_check reported database corruption.",
            )
            _finalize_status(report)
            return report
        if not report["integrity"]["foreign_key_check"]["ok"]:
            _issue(
                issues,
                "error",
                "foreign_key_check_failed",
                "SQLite foreign_key_check reported invalid references.",
            )
        repository = Repository(connection, database_path)
        report["schema_version"] = _schema_version(connection)
        if report["schema_version"] != SCHEMA_VERSION:
            _issue(
                issues,
                "error",
                "schema_version_mismatch",
                f"Expected schema {SCHEMA_VERSION}, found {report['schema_version']}.",
            )
        report["tables"] = _table_status(connection)
        for table_name, exists in report["tables"].items():
            if not exists:
                _issue(
                    issues,
                    "error",
                    "missing_table",
                    f"Required table is missing: {table_name}.",
                )
        report["schema_capability"] = _schema_capability(connection)
        if not report["schema_capability"]["ok"]:
            _issue(
                issues,
                "error",
                "schema_capability_missing",
                "The database is missing columns required by this build.",
            )
        report["migration_history"] = _migration_history_status(connection)
        if not report["migration_history"]["ok"]:
            _issue(
                issues,
                "error",
                "migration_history_invalid",
                "The schema migration history is incomplete or inconsistent.",
            )
        report["counts"] = _safe_repository_call(
            lambda: _counts(repository),
            {},
            check_name="row counts",
            report=report,
            issues=issues,
        )
        report["zotero"] = _safe_repository_call(
            repository.zotero_stats,
            {},
            check_name="Zotero summary",
            report=report,
            issues=issues,
        )
        report["dangling_rows"] = _safe_repository_call(
            repository.dangling_row_counts,
            {},
            check_name="dangling rows",
            report=report,
            issues=issues,
        )
        for code, count in report["dangling_rows"].items():
            if count:
                _issue(
                    issues,
                    "error",
                    code,
                    f"Found {count} dangling row(s) for {code}.",
                )
        report["fts"] = _fts_status(connection)
        if not report["fts"]["available"]:
            _issue(
                issues,
                "error",
                "fts_unavailable",
                "documents_fts is unavailable or unreadable.",
            )
        elif any(
            int(report["fts"].get(key, 0))
            for key in (
                "documents_without_fts",
                "fts_without_documents",
                "documents_without_chunks",
                "documents_without_texts",
                "content_mismatches",
                "duplicate_document_ids",
            )
        ):
            _issue(
                issues,
                "error",
                "fts_inconsistent",
                "FTS, document text, and chunk rows are inconsistent.",
            )
        report["vector_consistency"] = _vector_consistency(connection)
        if any(
            int(value)
            for key, value in report["vector_consistency"].items()
            if key != "unverifiable_vectors"
        ):
            _issue(
                issues,
                "error",
                "vector_consistency_failed",
                "Stored vectors include orphaned, stale, or malformed rows.",
            )
        if int(report["vector_consistency"].get("unverifiable_vectors", 0)):
            _issue(
                issues,
                "warning",
                "vector_provenance_missing",
                "Some legacy vectors cannot be proven fresh and should be rebuilt.",
            )
        report["zotero_consistency"] = _zotero_consistency(connection)
        if any(
            int(value)
            for key, value in report["zotero_consistency"].items()
            if key != "unknown_child_manifests"
        ):
            _issue(
                issues,
                "error",
                "zotero_consistency_failed",
                "Zotero cursor or import profile state is inconsistent.",
            )
        if int(report["zotero_consistency"].get("unknown_child_manifests", 0)):
            _issue(
                issues,
                "warning",
                "zotero_child_manifest_unknown",
                "Some migrated Zotero items need an explicit full reconciliation.",
            )
        report["source_job_consistency"] = _source_job_consistency(connection)
        if any(int(value) for value in report["source_job_consistency"].values()):
            _issue(
                issues,
                "error",
                "source_job_consistency_failed",
                "Registered source or durable job state is inconsistent.",
            )
        report["map_runs"] = _safe_repository_call(
            lambda: _map_run_status(connection),
            {"count": 0, "mismatches": []},
            check_name="saved map runs",
            report=report,
            issues=issues,
        )
        for mismatch in report["map_runs"]["mismatches"]:
            _issue(
                issues,
                "warning",
                "map_run_count_mismatch",
                str(mismatch),
            )
        if check_stale:
            report["cluster_label_overrides"] = _cluster_override_status(
                resolved_project_dir, repository
            )
            stale_count = int(report["cluster_label_overrides"].get("stale_count", 0))
            if stale_count:
                _issue(
                    issues,
                    "warning",
                    "stale_cluster_label_overrides",
                    f"{stale_count} cluster label override(s) are not in the live map.",
                )
    finally:
        connection.close()

    _finalize_status(report)
    return report


def write_validation_report(report: dict[str, Any], output_path: Path) -> Path:
    """Write a validation report without document text or chunk contents."""

    resolved_output = output_path.expanduser().resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return resolved_output


def validation_exit_code(report: dict[str, Any], *, strict: bool = False) -> int:
    """Return the CLI exit code implied by a validation report."""

    status = str(report.get("status", "ERRORS"))
    if status == "ERRORS":
        return 1
    if strict and status == "WARNINGS":
        return 1
    return 0


def _empty_integrity() -> dict[str, object]:
    return {
        "quick_check": {"ok": False, "messages": []},
        "foreign_key_check": {
            "ok": False,
            "violation_count": 0,
            "violations": [],
        },
    }


def _not_run_integrity(reason: str) -> dict[str, object]:
    return {
        "quick_check": {"ok": None, "status": "not_run", "messages": [reason]},
        "foreign_key_check": {
            "ok": None,
            "status": "not_run",
            "violation_count": 0,
            "violations": [],
        },
    }


def _integrity_status(connection: sqlite3.Connection) -> dict[str, object]:
    try:
        quick_messages = [
            str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()
        ]
    except sqlite3.Error as exc:
        quick_messages = [str(exc)]
    quick_ok = quick_messages == ["ok"]
    violations: list[dict[str, object]] = []
    if quick_ok:
        try:
            rows = connection.execute("PRAGMA foreign_key_check").fetchall()
            violations = [
                {
                    "table": str(row[0]),
                    "rowid": row[1],
                    "parent": str(row[2]),
                    "foreign_key_id": int(row[3]),
                }
                for row in rows
            ]
        except sqlite3.Error as exc:
            violations = [
                {
                    "table": "unknown",
                    "rowid": None,
                    "parent": "unknown",
                    "foreign_key_id": -1,
                    "error": str(exc),
                }
            ]
    foreign_key_status: dict[str, object] = {
        "ok": not violations if quick_ok else None,
        "violation_count": len(violations),
        "violations": violations,
    }
    if not quick_ok:
        foreign_key_status["status"] = "not_run"
    return {
        "quick_check": {"ok": quick_ok, "messages": quick_messages},
        "foreign_key_check": foreign_key_status,
    }


def _schema_capability(connection: sqlite3.Connection) -> dict[str, object]:
    missing: list[dict[str, str]] = []
    for table_name, required_columns in sorted(REQUIRED_COLUMNS.items()):
        try:
            rows = connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        except sqlite3.Error:
            rows = []
        columns = {str(row[1]) for row in rows}
        for column in sorted(required_columns - columns):
            missing.append({"table": table_name, "column": column})
    structural_ok = True
    try:
        declared_version = _schema_version(connection)
        if declared_version is None:
            raise ValueError("schema version is missing")
        validate_schema_capability(
            connection,
            version=int(declared_version),
            database_path=Path(":validation:"),
        )
    except (UnsupportedSchemaError, ValueError):
        structural_ok = False
    return {
        "ok": not missing and structural_ok,
        "missing_columns": missing,
        "structural_ok": structural_ok,
    }


def _migration_history_status(connection: sqlite3.Connection) -> dict[str, object]:
    entries = (
        list(MIGRATIONS.values()) if isinstance(MIGRATIONS, dict) else list(MIGRATIONS)
    )
    expected = {int(entry.version): str(entry.name) for entry in entries}
    try:
        rows = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
    except sqlite3.Error as exc:
        return {
            "ok": False,
            "missing": sorted(expected),
            "mismatched": [],
            "error": str(exc),
        }
    actual = {int(row[0]): str(row[1]) for row in rows}
    missing = sorted(set(expected) - set(actual))
    mismatched = [
        {"version": version, "expected": name, "found": actual.get(version)}
        for version, name in sorted(expected.items())
        if version in actual and actual[version] != name
    ]
    unexpected = sorted(set(actual) - set(expected))
    return {
        "ok": not missing and not mismatched and not unexpected,
        "missing": missing,
        "mismatched": mismatched,
        "unexpected": unexpected,
    }


def _empty_fts_status() -> dict[str, object]:
    return {
        "available": False,
        "row_count": 0,
        "documents_without_fts": 0,
        "fts_without_documents": 0,
        "documents_without_chunks": 0,
        "documents_without_texts": 0,
        "content_mismatches": 0,
        "duplicate_document_ids": 0,
    }


def _empty_vector_consistency() -> dict[str, int]:
    return {
        "unknown_object_types": 0,
        "vectors_without_targets": 0,
        "vectors_without_documents": 0,
        "vectors_without_chunks": 0,
        "vectors_without_models": 0,
        "vectors_for_inactive_targets": 0,
        "vectors_for_inactive_documents": 0,
        "vectors_for_inactive_chunks": 0,
        "document_source_hash_mismatches": 0,
        "chunk_source_hash_mismatches": 0,
        "source_hash_mismatches": 0,
        "invalid_source_hashes": 0,
        "invalid_embedding_input_hashes": 0,
        "document_content_hash_mismatches": 0,
        "chunk_text_hash_mismatches": 0,
        "model_fingerprint_mismatches": 0,
        "fingerprint_algorithm_mismatches": 0,
        "model_identity_mismatches": 0,
        "unknown_algorithm_versions": 0,
        "dimension_mismatches": 0,
        "dtype_mismatches": 0,
        "blob_size_mismatches": 0,
        "nonfinite_float32_vectors": 0,
        "vector_index_provenance_mismatches": 0,
        "check_errors": 0,
        "invalid_vector_metadata": 0,
        "unverifiable_vectors": 0,
        "stale_vectors": 0,
    }


def _vector_consistency(connection: sqlite3.Connection) -> dict[str, int]:
    status = _empty_vector_consistency()
    queries: dict[str, tuple[str, tuple[object, ...]]] = {
        "unknown_object_types": (
            """
            SELECT COUNT(*) FROM vectors
            WHERE object_type NOT IN ('document', 'chunk')
            """,
            (),
        ),
        "vectors_without_documents": (
            """
            SELECT COUNT(*) FROM vectors v
            LEFT JOIN documents d
              ON v.object_type = 'document' AND d.id = v.object_id
            WHERE v.object_type = 'document' AND d.id IS NULL
            """,
            (),
        ),
        "vectors_without_chunks": (
            """
            SELECT COUNT(*) FROM vectors v
            LEFT JOIN chunks c
              ON v.object_type = 'chunk' AND c.id = v.object_id
            WHERE v.object_type = 'chunk' AND c.id IS NULL
            """,
            (),
        ),
        "vectors_without_models": (
            """
            SELECT COUNT(*) FROM vectors v
            LEFT JOIN embedding_models m ON m.id = v.model_id
            WHERE m.id IS NULL
            """,
            (),
        ),
        "vectors_for_inactive_documents": (
            """
            SELECT COUNT(*) FROM vectors v
            JOIN documents d
              ON v.object_type = 'document' AND d.id = v.object_id
            WHERE v.object_type = 'document' AND d.status != 'active'
            """,
            (),
        ),
        "vectors_for_inactive_chunks": (
            """
            SELECT COUNT(*) FROM vectors v
            JOIN chunks c
              ON v.object_type = 'chunk' AND c.id = v.object_id
            JOIN documents d ON d.id = c.document_id
            WHERE v.object_type = 'chunk' AND d.status != 'active'
            """,
            (),
        ),
        "document_source_hash_mismatches": (
            """
            SELECT COUNT(*) FROM vectors v
            JOIN documents d ON v.object_type = 'document' AND d.id = v.object_id
            WHERE v.object_type = 'document'
              AND v.source_content_sha256 != d.content_revision_sha256
            """,
            (),
        ),
        "chunk_source_hash_mismatches": (
            """
            SELECT COUNT(*) FROM vectors v
            JOIN chunks c ON v.object_type = 'chunk' AND c.id = v.object_id
            WHERE v.object_type = 'chunk'
              AND v.source_content_sha256 != c.text_sha256
            """,
            (),
        ),
        "invalid_source_hashes": (
            """
            SELECT COUNT(*) FROM vectors
            WHERE typeof(source_content_sha256) != 'text'
               OR length(source_content_sha256) != 64
               OR source_content_sha256 GLOB '*[^0-9a-f]*'
            """,
            (),
        ),
        "invalid_embedding_input_hashes": (
            """
            SELECT COUNT(*) FROM vectors
            WHERE typeof(text_sha256) != 'text'
               OR length(text_sha256) != 64
               OR text_sha256 GLOB '*[^0-9a-f]*'
            """,
            (),
        ),
        "model_fingerprint_mismatches": (
            """
            SELECT COUNT(*) FROM vectors v
            JOIN embedding_models m ON m.id = v.model_id
            WHERE v.model_fingerprint = ?
               OR m.model_fingerprint = ?
               OR length(v.model_fingerprint) != 64
               OR length(m.model_fingerprint) != 64
               OR v.model_fingerprint GLOB '*[^0-9a-f]*'
               OR m.model_fingerprint GLOB '*[^0-9a-f]*'
               OR v.model_fingerprint != m.model_fingerprint
            """,
            (LEGACY_UNKNOWN_PROVENANCE, LEGACY_UNKNOWN_PROVENANCE),
        ),
        "unknown_algorithm_versions": (
            """
            SELECT COUNT(*) FROM vectors
            WHERE (object_type = 'document' AND algorithm_version != ?)
               OR (object_type = 'chunk' AND algorithm_version != ?)
            """,
            (
                DOCUMENT_VECTOR_ALGORITHM_VERSION,
                CHUNK_VECTOR_ALGORITHM_VERSION,
            ),
        ),
        "dimension_mismatches": (
            """
            SELECT COUNT(*) FROM vectors v
            LEFT JOIN embedding_models m ON m.id = v.model_id
            WHERE typeof(v.dimension) != 'integer'
               OR v.dimension <= 0
               OR (
                 m.id IS NOT NULL
                 AND (
                   typeof(m.dimension) != 'integer'
                   OR m.dimension <= 0
                   OR v.dimension != m.dimension
                 )
               )
            """,
            (),
        ),
        "dtype_mismatches": (
            """
            SELECT COUNT(*) FROM vectors
            WHERE dtype != 'float32'
            """,
            (),
        ),
        "blob_size_mismatches": (
            """
            SELECT COUNT(*) FROM vectors
            WHERE typeof(vector) != 'blob'
               OR typeof(dimension) != 'integer'
               OR dimension <= 0
               OR length(vector) != dimension * 4
            """,
            (),
        ),
        "unverifiable_vectors": (
            """
            SELECT COUNT(*) FROM vectors v
            LEFT JOIN embedding_models m ON m.id = v.model_id
            WHERE v.source_content_sha256 = ?
               OR v.model_fingerprint = ?
               OR v.algorithm_version = ?
               OR (m.id IS NOT NULL AND m.model_fingerprint = ?)
               OR (m.id IS NOT NULL AND m.fingerprint_algorithm IN (?, ''))
            """,
            (
                LEGACY_UNKNOWN_PROVENANCE,
                LEGACY_UNKNOWN_PROVENANCE,
                LEGACY_UNKNOWN_PROVENANCE,
                LEGACY_UNKNOWN_PROVENANCE,
                LEGACY_UNKNOWN_PROVENANCE,
            ),
        ),
        "stale_vectors": (
            """
            SELECT COUNT(*)
            FROM vectors v
            LEFT JOIN embedding_models m ON m.id = v.model_id
            LEFT JOIN documents d
              ON v.object_type = 'document' AND d.id = v.object_id
            LEFT JOIN chunks c
              ON v.object_type = 'chunk' AND c.id = v.object_id
            WHERE (
                v.object_type = 'document'
                AND d.id IS NOT NULL
                AND v.source_content_sha256 != ?
                AND v.source_content_sha256 != d.content_revision_sha256
              )
               OR (
                v.object_type = 'chunk'
                AND c.id IS NOT NULL
                AND v.source_content_sha256 != ?
                AND v.source_content_sha256 != c.text_sha256
              )
               OR (
                m.id IS NOT NULL
                AND v.model_fingerprint != ?
                AND m.model_fingerprint != ?
                AND (
                  length(v.model_fingerprint) != 64
                  OR length(m.model_fingerprint) != 64
                  OR v.model_fingerprint GLOB '*[^0-9a-f]*'
                  OR m.model_fingerprint GLOB '*[^0-9a-f]*'
                  OR v.model_fingerprint != m.model_fingerprint
                )
              )
               OR (
                v.algorithm_version != ?
                AND (
                  (v.object_type = 'document' AND v.algorithm_version != ?)
                  OR (v.object_type = 'chunk' AND v.algorithm_version != ?)
                )
              )
            """,
            (
                LEGACY_UNKNOWN_PROVENANCE,
                LEGACY_UNKNOWN_PROVENANCE,
                LEGACY_UNKNOWN_PROVENANCE,
                LEGACY_UNKNOWN_PROVENANCE,
                LEGACY_UNKNOWN_PROVENANCE,
                DOCUMENT_VECTOR_ALGORITHM_VERSION,
                CHUNK_VECTOR_ALGORITHM_VERSION,
            ),
        ),
        "vector_index_provenance_mismatches": (
            """
            SELECT COUNT(*) FROM vector_indexes i
            LEFT JOIN embedding_models m ON m.id = i.model_id
            WHERE i.object_type NOT IN ('document', 'chunk')
               OR m.id IS NULL
               OR i.model_fingerprint = ?
               OR m.model_fingerprint = ?
               OR i.model_fingerprint != m.model_fingerprint
               OR length(i.vector_set_sha256) != 64
               OR i.vector_set_sha256 GLOB '*[^0-9a-f]*'
               OR (i.object_type = 'document' AND i.algorithm_version != ?)
               OR (i.object_type = 'chunk' AND i.algorithm_version != ?)
            """,
            (
                LEGACY_UNKNOWN_PROVENANCE,
                LEGACY_UNKNOWN_PROVENANCE,
                DOCUMENT_VECTOR_ALGORITHM_VERSION,
                CHUNK_VECTOR_ALGORITHM_VERSION,
            ),
        ),
    }
    try:
        for key, (query, parameters) in queries.items():
            row = connection.execute(query, parameters).fetchone()
            status[key] = int(row[0]) if row else 0

        status["vectors_without_targets"] = (
            status["vectors_without_documents"] + status["vectors_without_chunks"]
        )
        status["vectors_for_inactive_targets"] = (
            status["vectors_for_inactive_documents"]
            + status["vectors_for_inactive_chunks"]
        )
        status["source_hash_mismatches"] = (
            status["document_source_hash_mismatches"]
            + status["chunk_source_hash_mismatches"]
        )

        metadata_cursor = connection.execute(
            "SELECT metadata_json FROM vectors ORDER BY id"
        )
        for rows in iter(lambda: metadata_cursor.fetchmany(256), []):
            for row in rows:
                try:
                    load_json_object(row["metadata_json"])
                except StoredJSONError:
                    status["invalid_vector_metadata"] += 1

        identity_cursor = connection.execute(
            """
            SELECT
              m.id,
              m.name,
              m.provider,
              m.dimension,
              typeof(m.dimension) AS dimension_type,
              m.distance,
              m.config_json,
              m.model_fingerprint,
              m.fingerprint_algorithm
            FROM vectors v
            JOIN embedding_models m ON m.id = v.model_id
            ORDER BY v.id
            """
        )
        for rows in iter(lambda: identity_cursor.fetchmany(256), []):
            for row in rows:
                try:
                    config = load_json_object(row["config_json"])
                except StoredJSONError:
                    status["fingerprint_algorithm_mismatches"] += 1
                    status["model_identity_mismatches"] += 1
                    continue
                if config.get("model_fingerprint") != str(
                    row["model_fingerprint"]
                ) or config.get("fingerprint_algorithm") != str(
                    row["fingerprint_algorithm"]
                ):
                    status["fingerprint_algorithm_mismatches"] += 1
                try:
                    expected_model_id = stable_embedding_model_id(
                        provider=str(row["provider"]),
                        name=str(row["name"]),
                        dimension=int(row["dimension"]),
                        distance=str(row["distance"]),
                        config=config,
                        model_fingerprint=str(row["model_fingerprint"]),
                        fingerprint_algorithm=str(row["fingerprint_algorithm"]),
                    )
                except (TypeError, ValueError):
                    status["model_identity_mismatches"] += 1
                    continue
                if (
                    str(row["dimension_type"]) != "integer"
                    or json.dumps(config, sort_keys=True) != str(row["config_json"])
                    or expected_model_id != str(row["id"])
                ):
                    status["model_identity_mismatches"] += 1

        vector_cursor = connection.execute(
            """
            SELECT vector
            FROM vectors
            WHERE dtype = 'float32'
              AND typeof(dimension) = 'integer'
              AND dimension > 0
              AND typeof(vector) = 'blob'
              AND length(vector) = dimension * 4
              AND length(vector) % 4 = 0
            ORDER BY id
            """
        )
        for rows in iter(lambda: vector_cursor.fetchmany(256), []):
            for row in rows:
                blob = bytes(row["vector"])
                if any(
                    not math.isfinite(value[0])
                    for value in struct.iter_unpack("<f", blob)
                ):
                    status["nonfinite_float32_vectors"] += 1

        chunk_cursor = connection.execute(
            "SELECT text, text_sha256 FROM chunks ORDER BY id"
        )
        for rows in iter(lambda: chunk_cursor.fetchmany(256), []):
            for row in rows:
                current_hash = _text_sha256(str(row["text"]))
                if current_hash != str(row["text_sha256"]):
                    status["chunk_text_hash_mismatches"] += 1

        document_cursor = connection.execute(
            """
            SELECT d.title, d.relative_path, d.content_revision_sha256, dt.text
            FROM documents d
            JOIN document_texts dt ON dt.document_id = d.id
            ORDER BY d.id
            """
        )
        for rows in iter(lambda: document_cursor.fetchmany(256), []):
            for row in rows:
                current_revision = document_content_revision_sha256(
                    title=str(row["title"]),
                    relative_path=str(row["relative_path"]),
                    text=str(row["text"]),
                )
                if current_revision != str(row["content_revision_sha256"]):
                    status["document_content_hash_mismatches"] += 1
    except sqlite3.Error:
        status["check_errors"] += 1
        return status
    return status


def _empty_zotero_consistency() -> dict[str, int]:
    return {
        "check_errors": 0,
        "invalid_cursors": 0,
        "invalid_record_versions": 0,
        "cursor_behind_records": 0,
        "profile_mismatches": 0,
        "invalid_child_manifests": 0,
        "unknown_child_manifests": 0,
    }


def _zotero_consistency(connection: sqlite3.Connection) -> dict[str, int]:
    status = _empty_zotero_consistency()
    try:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM zotero_sources
            WHERE last_version IS NOT NULL
              AND (typeof(last_version) != 'integer' OR last_version < 0)
            """
        ).fetchone()
        status["invalid_cursors"] = int(row[0]) if row else 0
        row = connection.execute(
            """
            SELECT SUM(invalid_count)
            FROM (
              SELECT COUNT(*) AS invalid_count
              FROM zotero_items
              WHERE version IS NOT NULL
                AND (typeof(version) != 'integer' OR version < 0)
              UNION ALL
              SELECT COUNT(*)
              FROM zotero_collections
              WHERE version IS NOT NULL
                AND (typeof(version) != 'integer' OR version < 0)
              UNION ALL
              SELECT COUNT(*)
              FROM zotero_attachments
              WHERE version IS NOT NULL
                AND (typeof(version) != 'integer' OR version < 0)
            )
            """
        ).fetchone()
        status["invalid_record_versions"] = int(row[0]) if row else 0
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM zotero_sources zs
            WHERE EXISTS (
                SELECT 1
                FROM (
                  SELECT source_id, version FROM zotero_items
                  UNION ALL
                  SELECT source_id, version FROM zotero_collections
                  UNION ALL
                  SELECT source_id, version FROM zotero_attachments
                ) records
                WHERE records.source_id = zs.id
                  AND records.version IS NOT NULL
                  AND (
                    zs.last_version IS NULL
                    OR records.version > zs.last_version
                  )
              )
            """
        ).fetchone()
        status["cursor_behind_records"] = int(row[0]) if row else 0
        rows = connection.execute(
            "SELECT config_json FROM zotero_import_runs"
        ).fetchall()
        mismatches = 0
        for row in rows:
            try:
                config = load_json_object(row["config_json"])
            except StoredJSONError:
                mismatches += 1
                continue
            filters = config.get("filters")
            if not isinstance(filters, dict):
                continue
            shared_keys = set(config) & set(filters)
            if any(config[key] != filters[key] for key in shared_keys):
                mismatches += 1
        status["profile_mismatches"] = mismatches
        manifest_rows = connection.execute(
            "SELECT child_manifest_json FROM zotero_items"
        ).fetchall()
        invalid_manifests = 0
        unknown_manifests = 0
        for row in manifest_rows:
            if row["child_manifest_json"] is None:
                unknown_manifests += 1
                continue
            try:
                manifest = load_json_list(row["child_manifest_json"])
            except StoredJSONError:
                invalid_manifests += 1
                continue
            if not _valid_zotero_child_manifest(manifest):
                invalid_manifests += 1
        status["invalid_child_manifests"] = invalid_manifests
        status["unknown_child_manifests"] = unknown_manifests
    except sqlite3.Error:
        status["check_errors"] += 1
        return status
    return status


def _valid_zotero_child_manifest(entries: list[object]) -> bool:
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return False
        key = entry.get("key")
        kind = entry.get("kind")
        version = entry.get("version")
        content_hash = entry.get("content_sha256")
        if (
            not isinstance(key, str)
            or not key
            or key in seen
            or kind not in {"attachment", "note", "annotation"}
            or (version is not None and (not isinstance(version, int) or version < 0))
            or not isinstance(content_hash, str)
            or len(content_hash) != 64
            or any(character not in "0123456789abcdef" for character in content_hash)
        ):
            return False
        seen.add(key)
    return True


def _empty_source_job_consistency() -> dict[str, int]:
    return {
        "check_errors": 0,
        "invalid_source_config_json": 0,
        "invalid_job_params_json": 0,
        "invalid_job_result_json": 0,
        "jobs_missing_required_source": 0,
        "jobs_with_unexpected_source": 0,
        "job_source_kind_mismatches": 0,
        "active_jobs_for_removed_sources": 0,
        "invalid_job_state_rows": 0,
    }


def _source_job_consistency(connection: sqlite3.Connection) -> dict[str, int]:
    status = _empty_source_job_consistency()
    try:
        source_cursor = connection.execute(
            "SELECT config_json FROM registered_sources ORDER BY id"
        )
        for rows in iter(lambda: source_cursor.fetchmany(256), []):
            for row in rows:
                try:
                    load_json_object(row["config_json"])
                except StoredJSONError:
                    status["invalid_source_config_json"] += 1

        job_cursor = connection.execute(
            "SELECT params_json, result_summary_json FROM jobs ORDER BY queue_sequence"
        )
        for rows in iter(lambda: job_cursor.fetchmany(256), []):
            for row in rows:
                try:
                    load_json_object(row["params_json"])
                except StoredJSONError:
                    status["invalid_job_params_json"] += 1
                try:
                    load_json_object(row["result_summary_json"])
                except StoredJSONError:
                    status["invalid_job_result_json"] += 1

        queries = {
            "jobs_missing_required_source": """
                SELECT COUNT(*) FROM jobs
                WHERE kind IN ('index_corpus', 'zotero_sync')
                  AND source_id IS NULL
            """,
            "jobs_with_unexpected_source": """
                SELECT COUNT(*) FROM jobs
                WHERE kind IN ('rebuild_analysis', 'backup_project')
                  AND source_id IS NOT NULL
            """,
            "job_source_kind_mismatches": """
                SELECT COUNT(*)
                FROM jobs j
                JOIN registered_sources s ON s.id = j.source_id
                WHERE (j.kind = 'index_corpus' AND s.kind != 'corpus_directory')
                   OR (j.kind = 'zotero_sync' AND s.kind != 'zotero_profile')
            """,
            "active_jobs_for_removed_sources": """
                SELECT COUNT(*)
                FROM jobs j
                JOIN registered_sources s ON s.id = j.source_id
                WHERE j.status IN ('queued', 'running', 'cancelling')
                  AND s.removed_at IS NOT NULL
            """,
            "invalid_job_state_rows": """
                SELECT COUNT(*) FROM jobs
                WHERE (status = 'queued' AND cancel_requested != 0)
                   OR (status = 'running' AND cancel_requested != 0)
                   OR (status = 'cancelled' AND cancel_requested != 1)
                   OR (
                     status IN ('completed', 'failed', 'interrupted', 'cancelled')
                     AND (
                       owner_pid IS NOT NULL
                       OR owner_instance_id IS NOT NULL
                       OR heartbeat_at IS NOT NULL
                     )
                   )
            """,
        }
        for key, query in queries.items():
            row = connection.execute(query).fetchone()
            status[key] = int(row[0]) if row else 0
    except sqlite3.Error:
        status["check_errors"] += 1
    return status


def _text_sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_repository_call(
    function: Any,
    fallback: Any,
    *,
    check_name: str,
    report: dict[str, Any],
    issues: list[dict[str, str]],
) -> Any:
    try:
        return function()
    except StoredJSONError as exc:
        _issue(
            issues,
            "error",
            "stored_json_invalid",
            f"Persisted JSON is invalid ({exc.code}).",
        )
        return fallback
    except sqlite3.Error:
        report["check_errors"] = int(report.get("check_errors", 0)) + 1
        _issue(
            issues,
            "error",
            "repository_check_failed",
            f"Database validation check could not be completed: {check_name}.",
        )
        return fallback


def _counts(repository: Repository) -> dict[str, int]:
    table_names = (
        "documents",
        "document_texts",
        "chunks",
        "scan_runs",
        "skipped_files",
        "extraction_reports",
        "embedding_models",
        "vectors",
        "embedding_runs",
        "vector_indexes",
        "cluster_label_overrides",
        "map_runs",
        "map_run_points",
        "map_run_clusters",
        "zotero_sources",
        "zotero_import_runs",
        "zotero_items",
        "zotero_creators",
        "zotero_collections",
        "zotero_item_collections",
        "zotero_item_tags",
        "zotero_attachments",
        "zotero_document_links",
    )
    counts = {
        table_name: repository.count_rows(table_name) for table_name in table_names
    }
    for table_name in ("registered_sources", "jobs"):
        row = repository.connection.execute(
            f'SELECT COUNT(*) FROM "{table_name}"'
        ).fetchone()
        counts[table_name] = int(row[0]) if row else 0
    return counts


def _table_status(connection: sqlite3.Connection) -> dict[str, bool]:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type IN ('table', 'virtual')
        """
    ).fetchall()
    table_names = {str(row["name"]) for row in rows}
    return {
        table_name: table_name in table_names for table_name in sorted(REQUIRED_TABLES)
    }


def _schema_version(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    return str(row["value"]) if row is not None else None


def _fts_status(connection: sqlite3.Connection) -> dict[str, Any]:
    try:
        definition = connection.execute(
            """
            SELECT sql
            FROM sqlite_schema
            WHERE type = 'table' AND name = 'documents_fts'
            """
        ).fetchone()
        declaration = str(definition["sql"] or "") if definition else ""
        if "create virtual table" not in declaration.lower() or (
            "using fts5" not in declaration.lower()
        ):
            return {
                **_empty_fts_status(),
                "error": "documents_fts is not an FTS5 virtual table",
            }
        connection.execute(
            """
            SELECT COUNT(*)
            FROM documents_fts
            WHERE documents_fts MATCH ?
            """,
            ("paper_galaxy_validation_probe",),
        ).fetchone()
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM documents_fts"
        ).fetchone()
        return {
            "available": True,
            "row_count": int(row["count"]) if row else 0,
            "documents_without_fts": _scalar_query(
                connection,
                """
                SELECT COUNT(*)
                FROM documents d
                LEFT JOIN documents_fts f ON f.document_id = d.id
                WHERE f.document_id IS NULL
                """,
            ),
            "fts_without_documents": _scalar_query(
                connection,
                """
                SELECT COUNT(*)
                FROM documents_fts f
                LEFT JOIN documents d ON d.id = f.document_id
                WHERE d.id IS NULL
                """,
            ),
            "documents_without_chunks": _scalar_query(
                connection,
                """
                SELECT COUNT(*)
                FROM documents d
                LEFT JOIN chunks c ON c.document_id = d.id
                WHERE c.id IS NULL
                """,
            ),
            "documents_without_texts": _scalar_query(
                connection,
                """
                SELECT COUNT(*)
                FROM documents d
                LEFT JOIN document_texts dt ON dt.document_id = d.id
                WHERE dt.document_id IS NULL
                """,
            ),
            "content_mismatches": _scalar_query(
                connection,
                """
                SELECT COUNT(*)
                FROM documents d
                JOIN document_texts dt ON dt.document_id = d.id
                JOIN documents_fts f ON f.document_id = d.id
                WHERE f.text != dt.text
                   OR f.title != d.title
                   OR f.relative_path != d.relative_path
                """,
            ),
            "duplicate_document_ids": _scalar_query(
                connection,
                """
                SELECT COUNT(*)
                FROM (
                  SELECT document_id
                  FROM documents_fts
                  GROUP BY document_id
                  HAVING COUNT(*) > 1
                )
                """,
            ),
        }
    except sqlite3.Error as exc:
        return {**_empty_fts_status(), "error": str(exc)}


def _scalar_query(connection: sqlite3.Connection, query: str) -> int:
    row = connection.execute(query).fetchone()
    return int(row[0]) if row else 0


def _map_run_status(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT
          r.id,
          r.name,
          r.document_count,
          r.cluster_count,
          COUNT(DISTINCT p.document_id) AS point_count,
          COUNT(DISTINCT c.cluster_id) AS stored_cluster_count
        FROM map_runs r
        LEFT JOIN map_run_points p ON p.map_run_id = r.id
        LEFT JOIN map_run_clusters c ON c.map_run_id = r.id
        GROUP BY r.id
        ORDER BY r.created_at DESC
        """
    ).fetchall()
    mismatches: list[dict[str, object]] = []
    for row in rows:
        point_count = int(row["point_count"])
        cluster_count = int(row["stored_cluster_count"])
        if point_count != int(row["document_count"]) or cluster_count != int(
            row["cluster_count"]
        ):
            mismatches.append(
                {
                    "id": str(row["id"]),
                    "name": str(row["name"]),
                    "document_count": int(row["document_count"]),
                    "point_count": point_count,
                    "cluster_count": int(row["cluster_count"]),
                    "stored_cluster_count": cluster_count,
                }
            )
    return {"count": len(rows), "mismatches": mismatches}


def _cluster_override_status(
    project_dir: Path, repository: Repository
) -> dict[str, object]:
    overrides = repository.list_cluster_label_overrides()
    if not overrides:
        return {"count": 0, "stale_count": 0, "checked": True}
    try:
        from paper_galaxy.web.map_builder import build_map_payload

        payload = build_map_payload(project_dir=project_dir)
        live_signatures = {
            str(cluster.get("cluster_signature", ""))
            for cluster in _dict_list(payload.get("clusters"))
        }
        stale = [
            override
            for override in overrides
            if str(override.get("cluster_signature", "")) not in live_signatures
        ]
        return {
            "count": len(overrides),
            "stale_count": len(stale),
            "checked": True,
        }
    except Exception as exc:
        return {
            "count": len(overrides),
            "stale_count": 0,
            "checked": False,
            "warning": f"Could not check stale overrides: {exc}",
        }


def _optional_dependency_status() -> dict[str, str]:
    return {
        label: "available" if importlib.util.find_spec(module) else "missing"
        for label, module in OPTIONAL_DEPENDENCIES
    }


def _issue(
    issues: list[dict[str, str]],
    severity: str,
    code: str,
    message: str,
) -> None:
    issues.append({"severity": severity, "code": code, "message": message})


def _finalize_status(report: dict[str, Any]) -> None:
    severities = {str(issue["severity"]) for issue in report["issues"]}
    if "error" in severities:
        report["status"] = "ERRORS"
    elif "warning" in severities:
        report["status"] = "WARNINGS"
    else:
        report["status"] = "OK"


def _dict_list(value: object) -> list[dict[str, object]]:
    return [item for item in _list(value) if isinstance(item, dict)]


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []
