"""Project validation for local Paper Galaxy workspaces."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
from typing import Any

from paper_galaxy.paths import project_config_path
from paper_galaxy.storage.migrations import SCHEMA_VERSION, initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import resolve_database_path

OPTIONAL_DEPENDENCIES: tuple[tuple[str, str], ...] = (
    ("pypdf", "pypdf"),
    ("Pillow", "PIL"),
    ("pytesseract", "pytesseract"),
    ("sklearn", "sklearn"),
    ("umap", "umap"),
    ("sentence_transformers", "sentence_transformers"),
    ("faiss", "faiss"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("plotly", "plotly"),
)

REQUIRED_TABLES = {
    "schema_meta",
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
    "documents_fts",
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
        "counts": {},
        "tables": {},
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

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        try:
            initialize_database(connection)
        except Exception as exc:
            _issue(
                issues,
                "error",
                "schema_initialize_failed",
                f"Could not initialize schema: {exc}",
            )
            _finalize_status(report)
            return report
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
        report["counts"] = _counts(repository)
        report["zotero"] = repository.zotero_stats()
        report["dangling_rows"] = repository.dangling_row_counts()
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
        report["map_runs"] = _map_run_status(connection)
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
    return {table_name: repository.count_rows(table_name) for table_name in table_names}


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
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM documents_fts"
        ).fetchone()
        return {"available": True, "row_count": int(row["count"]) if row else 0}
    except sqlite3.Error as exc:
        return {"available": False, "error": str(exc), "row_count": 0}


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
