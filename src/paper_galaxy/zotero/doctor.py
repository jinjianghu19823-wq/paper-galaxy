"""Real-machine Zotero readiness validation."""

from __future__ import annotations

import importlib.util
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import resolve_database_path
from paper_galaxy.zotero.attachments import RESOLVED_STATUSES, resolve_attachment_path
from paper_galaxy.zotero.detect import detect_zotero
from paper_galaxy.zotero.local_api import DEFAULT_LOCAL_API_URL, LocalZoteroAPIClient
from paper_galaxy.zotero.models import ZoteroAttachment, ZoteroDetection
from paper_galaxy.zotero.normalize import normalize_child, normalize_item


@dataclass(frozen=True)
class ZoteroDoctorCheck:
    """One local Zotero readiness check."""

    name: str
    status: str
    severity: str
    message: str
    value: object = None
    next_steps: tuple[str, ...] = ()

    def payload(self) -> dict[str, object]:
        """Return a JSON-safe payload."""

        return {
            "name": self.name,
            "status": self.status,
            "severity": self.severity,
            "message": self.message,
            "value": self.value,
            "next_steps": list(self.next_steps),
        }


@dataclass(frozen=True)
class ZoteroDoctorReport:
    """No-write local Zotero readiness report."""

    readiness: str
    api_url: str
    project_dir: Path
    database_path: Path
    checks: tuple[ZoteroDoctorCheck, ...]
    counts: dict[str, object] = field(default_factory=dict)
    paths: dict[str, object] = field(default_factory=dict)
    headers: dict[str, object] = field(default_factory=dict)
    project: dict[str, object] = field(default_factory=dict)
    last_import_run: dict[str, object] | None = None
    samples: dict[str, object] = field(default_factory=dict)
    next_steps: tuple[str, ...] = ()

    def payload(self) -> dict[str, object]:
        """Return a JSON-safe report payload."""

        return {
            "readiness": self.readiness,
            "api_url": self.api_url,
            "project_dir": str(self.project_dir),
            "database_path": str(self.database_path),
            "checks": [check.payload() for check in self.checks],
            "counts": self.counts,
            "paths": self.paths,
            "headers": self.headers,
            "project": self.project,
            "last_import_run": self.last_import_run,
            "samples": self.samples,
            "next_steps": list(self.next_steps),
        }


def validate_local_zotero(
    *,
    project_dir: Path,
    api_url: str = DEFAULT_LOCAL_API_URL,
    data_dir: Path | None = None,
    timeout: float = 2.0,
    limit: int = 20,
    verbose: bool = False,
) -> ZoteroDoctorReport:
    """Validate local Zotero readiness without writing project state."""

    resolved_project_dir = project_dir.expanduser().resolve()
    database_path = resolve_database_path(resolved_project_dir)
    sample_limit = max(0, limit)
    checks: list[ZoteroDoctorCheck] = []
    counts: dict[str, object] = {"sample_limit": sample_limit}
    headers: dict[str, object] = {}
    samples: dict[str, object] = {}

    detection = detect_zotero(api_url=api_url, data_dir=data_dir, timeout=timeout)
    paths = _paths_payload(detection)
    _add_path_checks(checks, detection)

    client = LocalZoteroAPIClient(api_url, timeout=timeout)
    api_ready = False
    top_items: list[dict[str, Any]] = []
    collections: list[dict[str, Any]] = []
    tags: list[dict[str, Any]] = []
    try:
        root = client.root_response()
        headers.update(_interesting_headers(root.headers))
        checks.append(
            ZoteroDoctorCheck(
                name="local_api_root",
                status="pass",
                severity="info",
                message="Zotero Desktop local API root is reachable.",
                value={"content_preview": str(root.data)[:80]},
            )
        )
        api_ready = True
        top_items, top_headers = client.top_items_page(limit=sample_limit)
        collections, collection_headers = client.collections_page(limit=sample_limit)
        tags, tag_headers = client.tags_page(limit=sample_limit)
        headers.update(_interesting_headers(top_headers))
        headers.setdefault(
            "collections_last_modified_version",
            collection_headers.get("last-modified-version"),
        )
        headers.setdefault(
            "tags_last_modified_version",
            tag_headers.get("last-modified-version"),
        )
        counts["top_items_sample_count"] = len(top_items)
        counts["top_items_total"] = _optional_int(top_headers.get("total-results"))
        counts["collections_sample_count"] = len(collections)
        counts["collections_total"] = _optional_int(
            collection_headers.get("total-results")
        )
        counts["tags_sample_count"] = len(tags)
        counts["tags_total"] = _optional_int(tag_headers.get("total-results"))
        has_top_items = bool(top_items) or counts["top_items_total"] != 0
        checks.append(
            ZoteroDoctorCheck(
                name="top_item_probe",
                status="pass" if has_top_items else "warning",
                severity="info" if top_items else "warning",
                message="Top-level Zotero item probe completed.",
                value={
                    "sample_count": len(top_items),
                    "total": counts["top_items_total"],
                },
                next_steps=()
                if top_items
                else ("Add Zotero items or run a metadata-only test import.",),
            )
        )
    except Exception as exc:
        checks.append(
            ZoteroDoctorCheck(
                name="local_api_root",
                status="fail",
                severity="error",
                message=str(exc),
                value=False,
                next_steps=(
                    "Open Zotero Desktop.",
                    "Enable the local API in Zotero Settings.",
                    "Run paper-galaxy zotero doctor --limit 20 again.",
                ),
            )
        )

    child_counts = _sample_children(
        client=client,
        top_items=top_items,
        data_dir=detection.data_dir,
        sample_limit=min(sample_limit, 10),
        checks=checks,
    )
    counts.update(child_counts)
    pypdf_available = importlib.util.find_spec("pypdf") is not None
    checks.append(
        ZoteroDoctorCheck(
            name="pypdf_importable",
            status="pass" if pypdf_available else "warning",
            severity="info" if pypdf_available else "warning",
            message=(
                "pypdf is importable."
                if pypdf_available
                else (
                    "pypdf is missing; PDF extraction will be skipped or metadata-only."
                )
            ),
            value=pypdf_available,
            next_steps=()
            if pypdf_available
            else ('Install with: python -m pip install -e ".[dev,ml,pdf,app]"',),
        )
    )

    project_payload, last_import = _project_payload(database_path)
    checks.append(
        ZoteroDoctorCheck(
            name="project_database",
            status="pass" if project_payload["database_exists"] else "warning",
            severity="info" if project_payload["database_exists"] else "warning",
            message=(
                "Paper Galaxy project database exists."
                if project_payload["database_exists"]
                else "Paper Galaxy project database does not exist yet."
            ),
            value=project_payload["database_exists"],
            next_steps=()
            if project_payload["database_exists"]
            else ("Run paper-galaxy init . before a full import.",),
        )
    )

    if api_ready and top_items:
        checks.append(
            ZoteroDoctorCheck(
                name="dry_run_readiness",
                status="pass",
                severity="info",
                message="A small metadata dry-run is likely to work.",
                value=True,
                next_steps=(
                    "paper-galaxy zotero import --project-dir . --dry-run --limit 10",
                ),
            )
        )
    if verbose:
        samples["top_items"] = [
            {
                "key": str(row.get("key", "")),
                "title": str(row.get("data", {}).get("title", ""))[:160]
                if isinstance(row.get("data"), dict)
                else "",
            }
            for row in top_items[:sample_limit]
        ]

    readiness = _readiness(checks)
    return ZoteroDoctorReport(
        readiness=readiness,
        api_url=api_url,
        project_dir=resolved_project_dir,
        database_path=database_path,
        checks=tuple(checks),
        counts=counts,
        paths=paths,
        headers=headers,
        project=project_payload,
        last_import_run=last_import,
        samples=samples,
        next_steps=_next_steps(checks, readiness),
    )


def _sample_children(
    *,
    client: LocalZoteroAPIClient,
    top_items: list[dict[str, Any]],
    data_dir: Path | None,
    sample_limit: int,
    checks: list[ZoteroDoctorCheck],
) -> dict[str, int]:
    counts: dict[str, int] = {
        "child_record_probe_count": 0,
        "attachment_count_sample": 0,
        "stored_attachment_count_sample": 0,
        "linked_attachment_count_sample": 0,
        "missing_attachment_count_sample": 0,
        "linked_outside_data_dir_count_sample": 0,
        "resolved_pdf_count_sample": 0,
        "missing_pdf_count_sample": 0,
    }
    for row in top_items[:sample_limit]:
        item = normalize_item(row)
        try:
            children = [
                child
                for child in (
                    normalize_child(child) for child in client.item_children(item.key)
                )
                if child is not None
            ]
        except Exception as exc:
            checks.append(
                ZoteroDoctorCheck(
                    name="child_probe",
                    status="warning",
                    severity="warning",
                    message=f"Could not inspect child records for sample item: {exc}",
                    value=item.key,
                )
            )
            continue
        counts["child_record_probe_count"] = int(
            counts["child_record_probe_count"]
        ) + len(children)
        for child in children:
            if not isinstance(child, ZoteroAttachment):
                continue
            counts["attachment_count_sample"] = (
                int(counts["attachment_count_sample"]) + 1
            )
            resolution = resolve_attachment_path(child, data_dir=data_dir)
            raw_path = child.path or ""
            if raw_path.startswith("storage:"):
                counts["stored_attachment_count_sample"] = (
                    int(counts["stored_attachment_count_sample"]) + 1
                )
            elif raw_path:
                counts["linked_attachment_count_sample"] = (
                    int(counts["linked_attachment_count_sample"]) + 1
                )
            if resolution.status in {"missing", "no_local_file", "unsupported"}:
                counts["missing_attachment_count_sample"] = (
                    int(counts["missing_attachment_count_sample"]) + 1
                )
            if resolution.status == "linked_outside_data_dir":
                counts["linked_outside_data_dir_count_sample"] = (
                    int(counts["linked_outside_data_dir_count_sample"]) + 1
                )
            filename = (child.filename or child.path or "").lower()
            is_pdf = child.content_type == "application/pdf" or filename.endswith(
                ".pdf"
            )
            if is_pdf and resolution.status in RESOLVED_STATUSES:
                counts["resolved_pdf_count_sample"] = (
                    int(counts["resolved_pdf_count_sample"]) + 1
                )
            elif is_pdf:
                counts["missing_pdf_count_sample"] = (
                    int(counts["missing_pdf_count_sample"]) + 1
                )
    checks.append(
        ZoteroDoctorCheck(
            name="child_attachment_probe",
            status="pass",
            severity="info",
            message="Sample child/attachment probe completed.",
            value={
                "children": counts["child_record_probe_count"],
                "attachments": counts["attachment_count_sample"],
            },
        )
    )
    return counts


def _paths_payload(detection: ZoteroDetection) -> dict[str, object]:
    return {
        "data_dir": str(detection.data_dir) if detection.data_dir else None,
        "zotero_sqlite_exists": detection.database_exists,
        "storage_exists": detection.storage_exists,
        "sqlite_diagnostics": detection.sqlite_diagnostics,
    }


def _add_path_checks(
    checks: list[ZoteroDoctorCheck], detection: ZoteroDetection
) -> None:
    data_dir_exists = bool(detection.data_dir and detection.data_dir.exists())
    checks.append(
        ZoteroDoctorCheck(
            name="data_dir",
            status="pass" if data_dir_exists else "warning",
            severity="info" if data_dir_exists else "warning",
            message=(
                "Zotero data directory exists."
                if detection.data_dir and detection.data_dir.exists()
                else "Zotero data directory was not found."
            ),
            value=str(detection.data_dir) if detection.data_dir else None,
            next_steps=("Pass --data-dir /path/to/Zotero.",)
            if not (detection.data_dir and detection.data_dir.exists())
            else (),
        )
    )
    checks.append(
        ZoteroDoctorCheck(
            name="zotero_sqlite",
            status="pass" if detection.database_exists else "warning",
            severity="info" if detection.database_exists else "warning",
            message=(
                "zotero.sqlite exists for read-only diagnostics."
                if detection.database_exists
                else "zotero.sqlite was not found."
            ),
            value=detection.database_exists,
        )
    )
    checks.append(
        ZoteroDoctorCheck(
            name="storage_dir",
            status="pass" if detection.storage_exists else "warning",
            severity="info" if detection.storage_exists else "warning",
            message=(
                "Zotero storage/ folder exists."
                if detection.storage_exists
                else (
                    "Zotero storage/ folder was not found; stored PDFs may not resolve."
                )
            ),
            value=detection.storage_exists,
        )
    )


def _project_payload(
    database_path: Path,
) -> tuple[dict[str, object], dict[str, object] | None]:
    payload: dict[str, object] = {
        "database_exists": database_path.exists(),
        "previous_zotero_import_state": False,
        "zotero_stats": None,
    }
    if not database_path.exists():
        return payload, None
    uri = f"file:{database_path}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            repository = Repository(connection, database_path)
            stats = repository.zotero_stats()
            last_import = repository.zotero_import_status()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        payload["error"] = str(exc)
        return payload, None
    payload["previous_zotero_import_state"] = bool(stats.get("source_count"))
    payload["zotero_stats"] = stats
    return payload, last_import


def _interesting_headers(headers: dict[str, str]) -> dict[str, object]:
    keys = {
        "zotero-api-version",
        "zotero-schema-version",
        "x-zotero-version",
        "x-zotero-connector-api-version",
        "last-modified-version",
        "total-results",
    }
    return {
        key.replace("-", "_"): value for key, value in headers.items() if key in keys
    }


def _readiness(checks: list[ZoteroDoctorCheck]) -> str:
    if any(check.status == "fail" for check in checks):
        return "blocked"
    if any(check.status == "warning" for check in checks):
        return "warning"
    return "ready"


def _next_steps(checks: list[ZoteroDoctorCheck], readiness: str) -> tuple[str, ...]:
    steps: list[str] = []
    for check in checks:
        steps.extend(check.next_steps)
    if readiness != "blocked":
        steps.append(
            "paper-galaxy zotero import --project-dir . --dry-run --limit 10 "
            "--json-out zotero-dry-run.json"
        )
    return tuple(dict.fromkeys(steps))


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    try:
        return int(value)
    except ValueError:
        return None
