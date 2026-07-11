"""Consistent, portable, and failure-atomic project backup bundles."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import tomllib
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paper_galaxy import __version__
from paper_galaxy.backup.archive import (
    DEFAULT_ARCHIVE_LIMITS,
    ArchiveInspection,
    ArchiveLimits,
    DatabaseMapping,
    VectorIndexMapping,
    configured_database_path,
    database_mapping,
    inspect_archive,
    schema_version,
    stream_extract_member,
    validate_database_schema_version,
    validate_relative_path,
    vector_index_mappings,
)
from paper_galaxy.backup.publish import (
    absolute_path_without_resolving,
    interrupted_project_restore_exists,
    preflight_project_tree,
    publish_file,
    publish_project_tree,
    recover_interrupted_project_restore,
    validate_safe_destination,
)
from paper_galaxy.backup.snapshot import (
    create_database_snapshot,
    validate_database_snapshot,
)
from paper_galaxy.backup.staging import create_owned_staging
from paper_galaxy.config import ProjectConfig, validate_project_config
from paper_galaxy.errors import DatabaseError
from paper_galaxy.paths import project_config_path
from paper_galaxy.storage.locking import (
    PROJECT_LOCK_RELATIVE_PATH,
    acquire_shared_project_locks,
    exclusive_project_maintenance_lock,
)
from paper_galaxy.storage.migrations import CURRENT_SCHEMA_VERSION, SCHEMA_VERSION
from paper_galaxy.storage.sqlite import DEFAULT_DATABASE_PATH, resolve_database_path

BACKUP_FORMAT = "paper-galaxy-backup-v2"
DATABASE_ARCHIVE_PATH = "database.sqlite3"
PROJECT_CONFIG_ARCHIVE_PATH = "project.toml"
PROJECT_CONFIG_RELATIVE_PATH = ".paper-galaxy/project.toml"
README_ARCHIVE_PATH = "README_EXPORT.txt"
MANIFEST_ARCHIVE_PATH = "manifest.json"
CHECKSUM_ARCHIVE_PATH = "checksums.sha256"
_DATABASE_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")
MAX_PROJECT_CONFIG_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class _Payload:
    content: bytes | Path


def export_project(
    *,
    project_dir: Path,
    output_path: Path,
    include_db: bool = True,
    include_vector_indexes: bool = False,
    include_source_files: bool = False,
    yes: bool = False,
) -> dict[str, Any]:
    """Hold a project read lock while producing one consistent backup bundle."""

    source_project = project_dir.expanduser().resolve()
    if not source_project.is_dir():
        raise FileNotFoundError(f"Project directory does not exist: {source_project}")
    initial_database = resolve_database_path(source_project)
    locks = acquire_shared_project_locks(
        source_project,
        initial_database,
        create_marker=False,
    )
    try:
        if resolve_database_path(source_project) != initial_database:
            raise ValueError(
                "Project database configuration changed during backup lock "
                "acquisition; retry the export."
            )
        return _export_project_under_lock(
            project_dir=source_project,
            output_path=output_path,
            include_db=include_db,
            include_vector_indexes=include_vector_indexes,
            include_source_files=include_source_files,
            yes=yes,
        )
    finally:
        locks.close()


def _export_project_under_lock(
    *,
    project_dir: Path,
    output_path: Path,
    include_db: bool,
    include_vector_indexes: bool,
    include_source_files: bool,
    yes: bool,
) -> dict[str, Any]:
    """Export a validated backup and atomically publish the completed ZIP."""

    if include_source_files:
        raise ValueError(
            "Source file export is intentionally unsupported; source documents "
            "remain outside Paper Galaxy backups."
        )
    if include_db and not yes:
        raise PermissionError(
            "Use --yes to confirm exporting the local SQLite database."
        )
    if include_vector_indexes and not include_db:
        raise ValueError("Vector indexes can only be exported with the database.")

    source_project = project_dir.expanduser().resolve()
    if not source_project.is_dir():
        raise FileNotFoundError(f"Project directory does not exist: {source_project}")
    metadata_dir = source_project / ".paper-galaxy"
    if _is_link_or_reparse_point(metadata_dir):
        raise ValueError(
            "Project metadata directory must not be a symbolic link or reparse point."
        )
    destination = validate_safe_destination(output_path, kind="backup export")
    config_path = project_config_path(source_project)
    source_database = resolve_database_path(source_project)
    protected_inputs = [
        source_database,
        config_path,
        source_project / PROJECT_LOCK_RELATIVE_PATH,
    ]
    _reject_output_alias(destination, protected_inputs)
    _require_owned_backup_destination(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = validate_safe_destination(destination, kind="backup export")
    _reject_output_alias(destination, protected_inputs)

    warnings: list[str] = []
    portable_database_path = _portable_database_path(
        source_project,
        source_database,
        warnings,
    )
    config_bytes = _portable_project_config(
        config_path,
        database_path=portable_database_path,
        warnings=warnings,
    )

    staging_root = create_owned_staging(
        parent=destination.parent,
        prefix=f".{destination.name}.paper-galaxy-export-",
        operation="backup-export",
        target=destination,
    )
    try:
        payloads: dict[str, _Payload] = {}
        if config_bytes is not None:
            payloads[PROJECT_CONFIG_ARCHIVE_PATH] = _Payload(config_bytes)

        snapshot_path = staging_root / "database.sqlite3"
        database_schema_version = CURRENT_SCHEMA_VERSION
        counts: dict[str, int] = {}
        vector_manifest: list[dict[str, str]] = []
        if include_db:
            if source_database.is_file():
                create_database_snapshot(source_database, snapshot_path)
                database_schema_version = validate_database_snapshot(snapshot_path)
                index_rows = _snapshot_vector_index_rows(snapshot_path)
                vector_manifest, vector_payloads, vector_sources = (
                    _prepare_vector_indexes(
                        snapshot_path=snapshot_path,
                        project_dir=source_project,
                        rows=index_rows,
                        include_files=include_vector_indexes,
                        warnings=warnings,
                    )
                )
                protected_inputs.extend(vector_sources)
                payloads.update(vector_payloads)
                validate_database_snapshot(
                    snapshot_path,
                    expected_schema_version=database_schema_version,
                )
                counts = _snapshot_counts(snapshot_path)
                payloads[DATABASE_ARCHIVE_PATH] = _Payload(snapshot_path)
            else:
                warnings.append("SQLite database was not found; no database was added.")

        _reject_output_alias(destination, protected_inputs)
        contains_database = DATABASE_ARCHIVE_PATH in payloads
        manifest: dict[str, Any] = {
            "format": BACKUP_FORMAT,
            "paper_galaxy_version": __version__,
            "schema_version": str(
                database_schema_version if contains_database else SCHEMA_VERSION
            ),
            "created_at": _utc_now(),
            "project_dir_name": source_project.name,
            "contains_database": contains_database,
            "configured_database_path": portable_database_path,
            "database": (
                {
                    "archive_path": DATABASE_ARCHIVE_PATH,
                    "project_path": portable_database_path,
                }
                if contains_database
                else None
            ),
            "source_files_included": False,
            "vector_indexes_included": bool(vector_manifest),
            "vector_indexes": vector_manifest,
            "counts": counts,
            "warnings": warnings,
        }
        if not contains_database:
            manifest.pop("database")
        payloads[MANIFEST_ARCHIVE_PATH] = _Payload(
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        )
        payloads[README_ARCHIVE_PATH] = _Payload(
            _readme_export_text(manifest).encode("utf-8")
        )
        checksums = {
            name: _payload_sha256(payload.content)
            for name, payload in sorted(payloads.items())
        }
        payloads[CHECKSUM_ARCHIVE_PATH] = _Payload(
            "\n".join(
                f"{digest}  {name}" for name, digest in sorted(checksums.items())
            ).encode("utf-8")
        )

        staged_archive = staging_root / "backup.zip"
        _write_archive(staged_archive, payloads)
        _inspect_backup_archive(staged_archive, limits=DEFAULT_ARCHIVE_LIMITS)
        os.chmod(staged_archive, 0o600)
        _fsync_file(staged_archive)
        publish_file(staged_archive, destination, kind="backup export")
        return {
            "output_path": str(destination),
            "manifest": manifest,
            "files": sorted(payloads),
            "checksums": checksums,
        }
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def inspect_backup(
    backup_path: Path,
    *,
    validate_checksums: bool = True,
    limits: ArchiveLimits | None = None,
) -> dict[str, Any]:
    """Deeply inspect a backup without writing any project files."""

    if not validate_checksums:
        raise ValueError("Backup checksum validation cannot be disabled.")
    inspection = _inspect_backup_archive(
        backup_path,
        limits=limits or DEFAULT_ARCHIVE_LIMITS,
    )
    return {
        "backup_path": str(inspection.path),
        "manifest": inspection.manifest,
        "files": list(inspection.names),
        "checksum_status": inspection.checksum_status,
    }


def import_project(
    *,
    backup_path: Path,
    project_dir: Path,
    force: bool = False,
    dry_run: bool = False,
    validate_checksums: bool = True,
    limits: ArchiveLimits | None = None,
) -> dict[str, Any]:
    """Validate into staging, then failure-atomically restore project state."""

    if not validate_checksums:
        raise ValueError("Backup checksum validation cannot be disabled.")
    archive_limits = limits or DEFAULT_ARCHIVE_LIMITS
    inspection = _inspect_backup_archive(backup_path, limits=archive_limits)
    manifest = inspection.manifest
    database = database_mapping(manifest)
    configured_path = configured_database_path(manifest)
    vector_mappings = vector_index_mappings(manifest)
    if vector_mappings and database is None:
        raise ValueError("A backup cannot restore vector indexes without a database.")

    target_project = absolute_path_without_resolving(project_dir)
    relative_files: list[str] = []
    relative_files.extend(mapping.project_path for mapping in vector_mappings)
    if database is not None:
        relative_files.append(database.project_path)
    if PROJECT_CONFIG_ARCHIVE_PATH in inspection.names or database is not None:
        relative_files.append(PROJECT_CONFIG_RELATIVE_PATH)
    writes = [str(target_project / relative) for relative in relative_files]
    summary: dict[str, Any] = {
        "project_dir": str(target_project),
        "backup_path": str(inspection.path),
        "dry_run": dry_run,
        "force": force,
        "writes": writes,
        "manifest": manifest,
        "checksum_status": inspection.checksum_status,
        "warnings": list(manifest.get("warnings", [])),
    }
    if manifest.get("format") == "paper-galaxy-backup-v1":
        summary["warnings"].append(
            "Legacy v1 vector index metadata was omitted because v1 did not "
            "preserve portable logical paths; rebuild indexes after restore."
        )
    remove_paths: list[str] = []
    if PROJECT_CONFIG_ARCHIVE_PATH in inspection.names or database is not None:
        remove_paths.extend(
            f"{configured_path}{suffix}" for suffix in _DATABASE_SIDECAR_SUFFIXES
        )
    if dry_run:
        recover_interrupted_project_restore(target_project, dry_run=True)
        _preflight_restore_destination(
            inspection=inspection,
            project_dir=target_project,
            relative_files=relative_files,
            remove_paths=remove_paths,
            configured_database_path=configured_path,
            force=force,
        )
        return summary

    # An optimistic read-only pass reports active SQLite sidecars before lock
    # acquisition.  The complete preflight is repeated under the maintenance
    # lock after any interrupted transaction is recovered, so this does not
    # weaken the publication boundary or introduce a TOCTOU window.
    if not interrupted_project_restore_exists(target_project):
        _preflight_restore_destination(
            inspection=inspection,
            project_dir=target_project,
            relative_files=relative_files,
            remove_paths=remove_paths,
            configured_database_path=configured_path,
            force=force,
        )
    with exclusive_project_maintenance_lock(target_project):
        recover_interrupted_project_restore(target_project, dry_run=False)
        _preflight_restore_destination(
            inspection=inspection,
            project_dir=target_project,
            relative_files=relative_files,
            remove_paths=remove_paths,
            configured_database_path=configured_path,
            force=force,
            allow_maintenance_marker=True,
        )
        _restore_validated_project(
            inspection=inspection,
            database=database,
            vector_mappings=vector_mappings,
            configured_database_path=configured_path,
            relative_files=relative_files,
            remove_paths=remove_paths,
            project_dir=target_project,
            force=force,
            limits=archive_limits,
        )
    return summary


def _preflight_restore_destination(
    *,
    inspection: ArchiveInspection,
    project_dir: Path,
    relative_files: list[str],
    remove_paths: list[str],
    configured_database_path: str,
    force: bool,
    allow_maintenance_marker: bool = False,
) -> None:
    preflight_project_tree(
        project_dir=project_dir,
        relative_files=relative_files,
        remove_relative_files=remove_paths,
        force=force,
        allow_maintenance_marker=allow_maintenance_marker,
    )
    _reject_restore_input_alias(
        inspection.path,
        project_dir=project_dir,
        relative_files=(*relative_files, *remove_paths),
    )
    _preflight_existing_database_destination(
        project_dir,
        database_project_path=configured_database_path,
    )


def _restore_validated_project(
    *,
    inspection: ArchiveInspection,
    database: DatabaseMapping | None,
    vector_mappings: tuple[VectorIndexMapping, ...],
    configured_database_path: str,
    relative_files: list[str],
    remove_paths: list[str],
    project_dir: Path,
    force: bool,
    limits: ArchiveLimits,
) -> None:
    """Extract and validate staging before publishing under maintenance lock."""

    manifest = inspection.manifest
    placeholder = project_dir.parent / ".paper-galaxy-restore-placeholder"
    validate_safe_destination(placeholder, kind="project restore")
    project_dir.parent.mkdir(parents=True, exist_ok=True)
    validate_safe_destination(placeholder, kind="project restore")
    staging_root = create_owned_staging(
        parent=project_dir.parent,
        prefix=f".{project_dir.name}.paper-galaxy-restore-",
        operation="project-restore",
        target=project_dir,
    )
    staged_project = staging_root / "project"
    staged_project.mkdir(mode=0o700)
    try:
        with zipfile.ZipFile(inspection.path) as archive:
            if PROJECT_CONFIG_ARCHIVE_PATH in inspection.names:
                _extract(
                    archive,
                    inspection,
                    PROJECT_CONFIG_ARCHIVE_PATH,
                    staged_project / PROJECT_CONFIG_RELATIVE_PATH,
                    limits=limits,
                )
            if database is not None:
                _extract(
                    archive,
                    inspection,
                    database.archive_path,
                    staged_project / database.project_path,
                    limits=limits,
                )
            for mapping in vector_mappings:
                _extract(
                    archive,
                    inspection,
                    mapping.archive_path,
                    staged_project / mapping.project_path,
                    limits=limits,
                )

        portable_database_path = configured_database_path
        staged_config = staged_project / PROJECT_CONFIG_RELATIVE_PATH
        if staged_config.exists():
            _rewrite_staged_config(staged_config, portable_database_path)
        elif database is not None:
            staged_config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            staged_config.write_bytes(
                _minimal_project_config(
                    str(manifest.get("project_dir_name", "Restored Paper Galaxy")),
                    portable_database_path,
                )
            )
            os.chmod(staged_config, 0o600)
            _fsync_file(staged_config)

        if database is not None:
            staged_database = staged_project / database.project_path
            restored_schema = _validate_or_normalize_database_payload(
                staged_database,
                manifest=manifest,
            )
            validate_database_schema_version(manifest, restored_schema)
            _validate_restored_vector_mappings(
                staged_database,
                manifest_format=str(manifest["format"]),
                expected_paths={mapping.project_path for mapping in vector_mappings},
            )

        publish_project_tree(
            staged_project=staged_project,
            project_dir=project_dir,
            relative_files=relative_files,
            remove_relative_files=remove_paths,
            force=force,
            allow_maintenance_marker=True,
        )
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def _inspect_backup_archive(
    backup_path: Path,
    *,
    limits: ArchiveLimits,
) -> ArchiveInspection:
    inspection = inspect_archive(backup_path, limits=limits)
    database = database_mapping(inspection.manifest)
    configured_path = configured_database_path(inspection.manifest)
    vectors = vector_index_mappings(inspection.manifest)
    if vectors and database is None:
        raise ValueError("A backup cannot contain vector indexes without a database.")
    temporary_root = create_owned_staging(
        parent=Path(tempfile.gettempdir()),
        prefix="paper-galaxy-backup-inspect-",
        operation="backup-inspect",
        target=backup_path.expanduser().absolute(),
    )
    try:
        snapshot = temporary_root / "database.sqlite3"
        config_path = temporary_root / "project.toml"
        with zipfile.ZipFile(inspection.path) as archive:
            if PROJECT_CONFIG_ARCHIVE_PATH in inspection.names:
                _extract(
                    archive,
                    inspection,
                    PROJECT_CONFIG_ARCHIVE_PATH,
                    config_path,
                    limits=limits,
                )
            if database is not None:
                _extract(
                    archive,
                    inspection,
                    database.archive_path,
                    snapshot,
                    limits=limits,
                )
        if config_path.exists():
            _rewrite_staged_config(config_path, configured_path)
        if database is not None:
            try:
                actual_schema = _validate_or_normalize_database_payload(
                    snapshot,
                    manifest=inspection.manifest,
                )
                validate_database_schema_version(inspection.manifest, actual_schema)
                _validate_restored_vector_mappings(
                    snapshot,
                    manifest_format=str(inspection.manifest["format"]),
                    expected_paths={mapping.project_path for mapping in vectors},
                )
            except DatabaseError as exc:
                raise ValueError(
                    "Backup database failed integrity or schema validation."
                ) from exc
        return inspection
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def _extract(
    archive: zipfile.ZipFile,
    inspection: ArchiveInspection,
    member: str,
    destination: Path,
    *,
    limits: ArchiveLimits,
) -> None:
    expected = inspection.checksums.get(member)
    if expected is None:
        raise ValueError(f"Archive member is not covered by checksums: {member}")
    if (
        member == PROJECT_CONFIG_ARCHIVE_PATH
        and archive.getinfo(member).file_size > MAX_PROJECT_CONFIG_BYTES
    ):
        raise ValueError("Backup project configuration exceeds the 1 MiB limit.")
    stream_extract_member(
        archive,
        member,
        destination,
        expected_sha256=expected,
        limits=limits,
    )


def _validate_or_normalize_database_payload(
    database_path: Path,
    *,
    manifest: dict[str, Any],
) -> int:
    expected = schema_version(manifest)
    if manifest.get("format") != "paper-galaxy-backup-v1":
        return validate_database_snapshot(
            database_path,
            expected_schema_version=expected,
        )

    normalized = database_path.with_name(f".{database_path.name}.v1-normalized")
    try:
        create_database_snapshot(
            database_path,
            normalized,
            expected_schema_version=expected,
        )
        os.replace(normalized, database_path)
    finally:
        normalized.unlink(missing_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("DELETE FROM vector_indexes")
        connection.commit()
    finally:
        connection.close()
    os.chmod(database_path, 0o600)
    _fsync_file(database_path)
    return validate_database_snapshot(
        database_path,
        expected_schema_version=expected,
    )


def _portable_database_path(
    project_dir: Path,
    database_path: Path,
    warnings: list[str],
) -> str:
    try:
        relative = database_path.resolve(strict=False).relative_to(project_dir)
        portable = relative.as_posix()
        validate_relative_path(portable, purpose="database project")
        return portable
    except (ValueError, OSError):
        warnings.append(
            "The configured database was outside the project and was safely "
            f"mapped to {DEFAULT_DATABASE_PATH} for restore."
        )
        return DEFAULT_DATABASE_PATH


def _portable_project_config(
    config_path: Path,
    *,
    database_path: str,
    warnings: list[str],
) -> bytes | None:
    if not config_path.exists():
        warnings.append(".paper-galaxy/project.toml was not found.")
        return None
    if (
        _is_link_or_reparse_point(config_path.parent)
        or _is_link_or_reparse_point(config_path)
        or not config_path.is_file()
        or config_path.stat().st_nlink != 1
    ):
        raise ValueError("Project configuration must be a regular, non-symbolic file.")
    if config_path.stat().st_size > MAX_PROJECT_CONFIG_BYTES:
        raise ValueError("Project configuration exceeds the 1 MiB backup limit.")
    try:
        text = config_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Project configuration must be valid UTF-8 TOML.") from exc
    config = _decode_project_config(text)
    return _canonical_project_config(config, database_path=database_path)


def _rewrite_staged_config(config_path: Path, database_path: str) -> None:
    if config_path.stat().st_size > MAX_PROJECT_CONFIG_BYTES:
        raise ValueError("Backup project configuration exceeds the 1 MiB limit.")
    try:
        text = config_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Backup project configuration is not valid UTF-8.") from exc
    config = _decode_project_config(text)
    content = _canonical_project_config(config, database_path=database_path)
    config_path.write_bytes(content)
    os.chmod(config_path, 0o600)
    _fsync_file(config_path)


def _decode_project_config(text: str) -> ProjectConfig:
    try:
        decoded = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Project configuration is invalid TOML: {exc}") from exc
    supported_keys = {
        "project_name",
        "corpus_dirs",
        "database_path",
        "map_seed",
        "created_by",
    }
    unknown_keys = set(decoded) - supported_keys
    if unknown_keys:
        raise ValueError(
            "Project configuration contains unsupported top-level fields: "
            + ", ".join(sorted(unknown_keys))
        )
    strict_fields: tuple[tuple[str, type[object]], ...] = (
        ("project_name", str),
        ("database_path", str),
        ("created_by", str),
        ("map_seed", int),
    )
    for field, expected_type in strict_fields:
        if field in decoded and (
            not isinstance(decoded[field], expected_type)
            or (field == "map_seed" and isinstance(decoded[field], bool))
        ):
            raise ValueError(f"Project configuration field {field} has invalid type.")
    corpus_dirs = decoded.get("corpus_dirs")
    if corpus_dirs is not None and (
        not isinstance(corpus_dirs, list)
        or any(not isinstance(item, str) for item in corpus_dirs)
    ):
        raise ValueError("Project configuration field corpus_dirs has invalid type.")
    try:
        return validate_project_config(decoded)
    except ValueError as exc:
        raise ValueError("Project configuration fields are invalid.") from exc


def _canonical_project_config(config: ProjectConfig, *, database_path: str) -> bytes:
    text = "\n".join(
        [
            f"project_name = {json.dumps(config.project_name, ensure_ascii=False)}",
            f"created_by = {json.dumps(config.created_by, ensure_ascii=False)}",
            f"map_seed = {config.map_seed}",
            "corpus_dirs = "
            + json.dumps(config.corpus_dirs, ensure_ascii=False, separators=(",", ":")),
            f"database_path = {json.dumps(database_path, ensure_ascii=False)}",
            "",
        ]
    )
    validated = _decode_project_config(text)
    if validated.database_path != database_path:
        raise ValueError("Project configuration database path is not portable.")
    return text.encode("utf-8")


def _minimal_project_config(project_name: str, database_path: str) -> bytes:
    config = validate_project_config(
        {
            "project_name": project_name,
            "created_by": "paper-galaxy restore",
            "map_seed": 42,
            "corpus_dirs": [],
            "database_path": database_path,
        }
    )
    return _canonical_project_config(config, database_path=database_path)


def _build_owned_vector_source(project_dir: Path, candidate: Path) -> Path:
    build_root = project_dir / ".paper-galaxy" / "vector_indexes"
    lexical = absolute_path_without_resolving(candidate)
    relative = lexical.relative_to(build_root)
    current = project_dir / ".paper-galaxy"
    if _is_link_or_reparse_point(current):
        raise ValueError("Vector index metadata directory is a link or reparse point.")
    for part in ("vector_indexes", *relative.parts):
        current /= part
        if _is_link_or_reparse_point(current):
            raise ValueError("Vector index source contains a link or reparse point.")
    resolved_root = build_root.resolve(strict=True)
    resolved = lexical.resolve(strict=True)
    resolved.relative_to(resolved_root)
    if resolved.stat().st_nlink != 1:
        raise ValueError("Vector index source has multiple hard links.")
    return resolved


def _is_link_or_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        status = path.lstat()
    except OSError:
        return False
    attributes = int(getattr(status, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return bool(reparse_flag and attributes & reparse_flag)


def _snapshot_vector_index_rows(snapshot_path: Path) -> list[dict[str, str]]:
    connection = sqlite3.connect(snapshot_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT id, index_path FROM vector_indexes ORDER BY id"
        ).fetchall()
        return [
            {"id": str(row["id"]), "index_path": str(row["index_path"])} for row in rows
        ]
    finally:
        connection.close()


def _prepare_vector_indexes(
    *,
    snapshot_path: Path,
    project_dir: Path,
    rows: list[dict[str, str]],
    include_files: bool,
    warnings: list[str],
) -> tuple[list[dict[str, str]], dict[str, _Payload], list[Path]]:
    mappings: list[dict[str, str]] = []
    payloads: dict[str, _Payload] = {}
    protected_sources: list[Path] = []
    row_paths: dict[str, str] = {}
    files_by_project_path: dict[str, Path] = {}
    kept_ids: set[str] = set()

    if include_files:
        for row in rows:
            configured = Path(row["index_path"]).expanduser()
            candidate = (
                configured if configured.is_absolute() else project_dir / configured
            )
            try:
                resolved = _build_owned_vector_source(project_dir, candidate)
                project_path = resolved.relative_to(project_dir).as_posix()
                validate_relative_path(project_path, purpose="vector index project")
            except (FileNotFoundError, ValueError, OSError):
                warnings.append(
                    "Skipped unavailable, symbolic, or non-build-owned vector "
                    f"index id {row['id']}."
                )
                continue
            if not resolved.is_file():
                warnings.append(f"Skipped non-file vector index id {row['id']}.")
                continue
            kept_ids.add(row["id"])
            row_paths[row["id"]] = project_path
            files_by_project_path.setdefault(project_path, resolved)

        for project_path, source in sorted(files_by_project_path.items()):
            digest = hashlib.sha256(project_path.encode("utf-8")).hexdigest()
            archive_path = f"vector_indexes/{digest[:24]}/{source.name}"
            mapping_id = f"file-{digest}"
            mappings.append(
                {
                    "id": mapping_id,
                    "archive_path": archive_path,
                    "project_path": project_path,
                }
            )
            payloads[archive_path] = _Payload(source)
            protected_sources.append(source)
    elif rows:
        warnings.append(
            "Vector index files were not requested; portable index metadata was "
            "removed from the backup snapshot and can be rebuilt locally."
        )

    connection = sqlite3.connect(snapshot_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        if kept_ids:
            placeholders = ", ".join("?" for _ in kept_ids)
            connection.execute(
                f"DELETE FROM vector_indexes WHERE id NOT IN ({placeholders})",
                tuple(sorted(kept_ids)),
            )
            connection.executemany(
                "UPDATE vector_indexes SET index_path = ? WHERE id = ?",
                [(path, identifier) for identifier, path in sorted(row_paths.items())],
            )
        else:
            connection.execute("DELETE FROM vector_indexes")
        connection.commit()
    finally:
        connection.close()
    return mappings, payloads, protected_sources


def _snapshot_counts(snapshot_path: Path) -> dict[str, int]:
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
    connection = sqlite3.connect(
        f"{snapshot_path.as_uri()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        return {
            name: int(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            for name in table_names
        }
    finally:
        connection.close()


def _validate_restored_vector_mappings(
    database_path: Path,
    *,
    manifest_format: str,
    expected_paths: set[str],
) -> None:
    if manifest_format != BACKUP_FORMAT:
        return
    connection = sqlite3.connect(
        f"{database_path.as_uri()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        actual_paths = {
            str(row[0])
            for row in connection.execute("SELECT index_path FROM vector_indexes")
        }
    finally:
        connection.close()
    if actual_paths != expected_paths:
        raise ValueError(
            "Backup vector index mappings do not match the embedded database."
        )


def _write_archive(path: Path, payloads: dict[str, _Payload]) -> None:
    with zipfile.ZipFile(
        path,
        "x",
        compression=zipfile.ZIP_DEFLATED,
        allowZip64=True,
    ) as archive:
        for name, payload in sorted(payloads.items()):
            if isinstance(payload.content, bytes):
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, payload.content)
            else:
                archive.write(
                    payload.content,
                    arcname=name,
                    compress_type=zipfile.ZIP_DEFLATED,
                )


def _payload_sha256(content: bytes | Path) -> str:
    digest = hashlib.sha256()
    if isinstance(content, bytes):
        digest.update(content)
        return digest.hexdigest()
    with content.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_restore_input_alias(
    backup_path: Path,
    *,
    project_dir: Path,
    relative_files: tuple[str, ...],
) -> None:
    source = backup_path.resolve(strict=True)
    for relative in relative_files:
        destination = project_dir / Path(*relative.split("/"))
        resolved_destination = destination.resolve(strict=False)
        if resolved_destination == source:
            raise ValueError("Restore cannot replace its own input backup archive.")
        if destination.exists():
            try:
                if os.path.samefile(destination, source):
                    raise ValueError(
                        "Restore cannot replace an alias of its input backup archive."
                    )
            except FileNotFoundError:
                continue


def _preflight_existing_database_destination(
    project_dir: Path,
    *,
    database_project_path: str | None,
) -> None:
    if database_project_path is None or not project_dir.is_dir():
        return
    destination = project_dir / Path(*database_project_path.split("/"))
    if not destination.exists():
        return
    config_path = project_dir / PROJECT_CONFIG_RELATIVE_PATH
    if config_path.exists() or config_path.is_symlink():
        if not config_path.is_file() or config_path.is_symlink():
            raise FileExistsError(
                "Refusing to replace an existing database path because the "
                "target project configuration is unsafe."
            )
        try:
            config = _decode_project_config(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ValueError(
                "The existing target configuration cannot safely authorize "
                "database replacement."
            ) from exc
        configured = Path(config.database_path).expanduser()
        if not configured.is_absolute():
            configured = project_dir / configured
        if configured.resolve(strict=False) != destination.resolve(strict=False):
            raise FileExistsError(
                "Refusing to replace an existing file that is not the database "
                "declared by the target project."
            )
        return

    if database_project_path != DEFAULT_DATABASE_PATH:
        raise FileExistsError(
            "Refusing to replace an existing database path that is not declared "
            "by the target project's configuration."
        )
    try:
        validate_database_snapshot(destination)
    except (DatabaseError, OSError, ValueError) as exc:
        raise FileExistsError(
            "Refusing to replace an undeclared default-path file that is not a "
            "verifiable Paper Galaxy database."
        ) from exc


def _reject_output_alias(destination: Path, inputs: list[Path]) -> None:
    resolved_destination = destination.resolve(strict=False)
    for input_path in inputs:
        resolved_input = input_path.expanduser().resolve(strict=False)
        protected = {
            resolved_input,
            *(
                Path(f"{resolved_input}{suffix}")
                for suffix in _DATABASE_SIDECAR_SUFFIXES
            ),
        }
        if any(
            _portable_paths_overlap(resolved_destination, protected_path)
            for protected_path in protected
        ):
            raise ValueError(
                "Backup output cannot overlap a project data or control path."
            )
        if destination.exists():
            for protected_path in protected:
                if not protected_path.exists():
                    continue
                try:
                    if os.path.samefile(destination, protected_path):
                        raise ValueError(
                            "Backup output cannot alias a project data file."
                        )
                except FileNotFoundError:
                    continue


def _portable_paths_overlap(first: Path, second: Path) -> bool:
    first_parts = tuple(
        unicodedata.normalize("NFC", part).casefold() for part in first.parts
    )
    second_parts = tuple(
        unicodedata.normalize("NFC", part).casefold() for part in second.parts
    )
    shared = min(len(first_parts), len(second_parts))
    return first_parts[:shared] == second_parts[:shared]


def _require_owned_backup_destination(destination: Path) -> None:
    if not destination.exists():
        return
    try:
        _inspect_backup_archive(destination, limits=DEFAULT_ARCHIVE_LIMITS)
    except (DatabaseError, OSError, ValueError) as exc:
        raise FileExistsError(
            "Refusing to replace an existing file that is not a fully validated "
            "Paper Galaxy backup. Choose a new --out path."
        ) from exc


def _readme_export_text(manifest: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Paper Galaxy backup bundle",
            "",
            "This archive contains local Paper Galaxy project metadata.",
            "It does not include source documents.",
            f"Created at: {manifest['created_at']}",
            f"Contains database: {manifest['contains_database']}",
            "Restore validates every payload, SQLite integrity, and schema "
            "before write.",
            "",
        ]
    )


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
