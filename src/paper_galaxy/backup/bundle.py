"""Zip backup bundles for local Paper Galaxy project state."""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paper_galaxy import __version__
from paper_galaxy.paths import project_config_path
from paper_galaxy.storage.migrations import SCHEMA_VERSION, initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import DEFAULT_DATABASE_PATH, connect_database


def export_project(
    *,
    project_dir: Path,
    output_path: Path,
    include_db: bool = True,
    include_vector_indexes: bool = False,
    include_source_files: bool = False,
    yes: bool = False,
) -> dict[str, Any]:
    """Export a local project bundle without source documents by default."""

    if include_source_files:
        raise ValueError(
            "Source file export is intentionally not implemented in Phase 7."
        )
    if include_db and not yes:
        raise PermissionError(
            "Use --yes to confirm exporting the local SQLite database."
        )

    resolved_project_dir = project_dir.expanduser().resolve()
    resolved_output = output_path.expanduser().resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    config_path = project_config_path(resolved_project_dir)
    files: dict[str, bytes] = {}
    warnings: list[str] = []

    if config_path.exists():
        files["project.toml"] = config_path.read_bytes()
    else:
        warnings.append(".paper-galaxy/project.toml was not found.")

    counts: dict[str, int] = {}
    vector_index_paths: list[str] = []
    database_path = None
    if include_db:
        database_path = _resolved_database_path_without_creating(resolved_project_dir)
        if database_path.exists():
            counts, vector_index_paths = _database_summary(resolved_project_dir)
            files["database.sqlite3"] = database_path.read_bytes()
        else:
            warnings.append("SQLite database was not found.")

    if include_vector_indexes:
        for index_path in vector_index_paths:
            path = Path(index_path).expanduser()
            if not path.is_absolute():
                path = resolved_project_dir / path
            try:
                resolved_index = path.resolve()
                resolved_index.relative_to(resolved_project_dir)
            except ValueError:
                warnings.append(f"Skipped vector index outside project: {index_path}")
                continue
            if resolved_index.exists() and resolved_index.is_file():
                archive_name = f"vector_indexes/{resolved_index.name}"
                files[archive_name] = resolved_index.read_bytes()

    manifest = {
        "format": "paper-galaxy-backup-v1",
        "paper_galaxy_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "created_at": _utc_now(),
        "project_dir_name": resolved_project_dir.name,
        "contains_database": "database.sqlite3" in files,
        "source_files_included": False,
        "vector_indexes_included": include_vector_indexes,
        "counts": counts,
        "warnings": warnings,
    }
    files["manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True).encode(
        "utf-8"
    )
    files["README_EXPORT.txt"] = _readme_export_text(manifest).encode("utf-8")

    checksums = _checksums(files)
    files["checksums.sha256"] = "\n".join(
        f"{digest}  {name}" for name, digest in sorted(checksums.items())
    ).encode("utf-8")

    with zipfile.ZipFile(resolved_output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in sorted(files.items()):
            zf.writestr(name, content)

    return {
        "output_path": str(resolved_output),
        "manifest": manifest,
        "files": sorted(files),
        "checksums": checksums,
    }


def inspect_backup(
    backup_path: Path, *, validate_checksums: bool = True
) -> dict[str, Any]:
    """Inspect a backup zip without writing any project files."""

    resolved_backup = backup_path.expanduser().resolve()
    if not resolved_backup.exists():
        raise FileNotFoundError(f"Backup does not exist: {resolved_backup}")
    with zipfile.ZipFile(resolved_backup) as zf:
        names = set(zf.namelist())
        if "manifest.json" not in names:
            raise ValueError("Backup is missing manifest.json.")
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        checksum_status = "skipped"
        if validate_checksums:
            checksum_status = _validate_zip_checksums(zf)
    return {
        "backup_path": str(resolved_backup),
        "manifest": manifest,
        "files": sorted(names),
        "checksum_status": checksum_status,
    }


def import_project(
    *,
    backup_path: Path,
    project_dir: Path,
    force: bool = False,
    dry_run: bool = False,
    validate_checksums: bool = True,
) -> dict[str, Any]:
    """Import a backup into a local project metadata directory."""

    inspection = inspect_backup(backup_path, validate_checksums=validate_checksums)
    resolved_project_dir = project_dir.expanduser().resolve()
    metadata_dir = resolved_project_dir / ".paper-galaxy"
    database_path = resolved_project_dir / DEFAULT_DATABASE_PATH
    writes = []
    files = set(inspection["files"])
    if "project.toml" in files:
        writes.append(str(metadata_dir / "project.toml"))
    if "database.sqlite3" in files:
        writes.append(str(database_path))
    summary = {
        "project_dir": str(resolved_project_dir),
        "backup_path": inspection["backup_path"],
        "dry_run": dry_run,
        "force": force,
        "writes": writes,
        "manifest": inspection["manifest"],
        "checksum_status": inspection["checksum_status"],
    }
    if dry_run:
        return summary
    if metadata_dir.exists() and not force:
        raise FileExistsError(
            f"{metadata_dir} already exists. Use --force to import over it."
        )

    metadata_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(backup_path.expanduser().resolve()) as zf:
        if "project.toml" in files:
            (metadata_dir / "project.toml").write_bytes(zf.read("project.toml"))
        if "database.sqlite3" in files:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            database_path.write_bytes(zf.read("database.sqlite3"))
    return summary


def _database_summary(project_dir: Path) -> tuple[dict[str, int], list[str]]:
    connection = connect_database(project_dir)
    try:
        initialize_database(connection)
        repository = Repository(
            connection, _resolved_database_path_without_creating(project_dir)
        )
        table_names = (
            "documents",
            "chunks",
            "scan_runs",
            "extraction_reports",
            "embedding_models",
            "vectors",
            "vector_indexes",
            "cluster_label_overrides",
            "map_runs",
        )
        counts = {name: repository.count_rows(name) for name in table_names}
        vector_stats = repository.vector_stats()
        indexes = vector_stats.get("vector_indexes")
        paths = (
            [
                str(row.get("index_path", ""))
                for row in indexes
                if isinstance(row, dict) and row.get("index_path")
            ]
            if isinstance(indexes, list)
            else []
        )
        return counts, paths
    finally:
        connection.close()


def _resolved_database_path_without_creating(project_dir: Path) -> Path:
    from paper_galaxy.storage.sqlite import resolve_database_path

    return resolve_database_path(project_dir)


def _checksums(files: dict[str, bytes]) -> dict[str, str]:
    return {
        name: hashlib.sha256(content).hexdigest()
        for name, content in files.items()
        if name != "checksums.sha256"
    }


def _validate_zip_checksums(zf: zipfile.ZipFile) -> str:
    names = set(zf.namelist())
    if "checksums.sha256" not in names:
        raise ValueError("Backup is missing checksums.sha256.")
    expected: dict[str, str] = {}
    for line in zf.read("checksums.sha256").decode("utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split(maxsplit=1)
        expected[name.strip()] = digest
    for name, digest in expected.items():
        if name not in names:
            raise ValueError(f"Checksum references missing file: {name}")
        actual = hashlib.sha256(zf.read(name)).hexdigest()
        if actual != digest:
            raise ValueError(f"Checksum mismatch for {name}.")
    return "ok"


def _readme_export_text(manifest: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Paper Galaxy backup bundle",
            "",
            "This archive contains local Paper Galaxy project metadata.",
            "It does not include source documents unless a future command adds "
            "that explicitly.",
            f"Created at: {manifest['created_at']}",
            f"Contains database: {manifest['contains_database']}",
            "",
        ]
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
