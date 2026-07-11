from __future__ import annotations

import hashlib
import json
import random
import stat
import warnings
import zipfile
from pathlib import Path
from typing import Any

import pytest

from paper_galaxy.backup import inspect_backup
from paper_galaxy.backup.bundle import MAX_PROJECT_CONFIG_BYTES
from paper_galaxy.storage.migrations import (
    CURRENT_SCHEMA_VERSION,
    SCHEMA_VERSION,
)
from paper_galaxy.storage.sqlite import ensure_database_ready


def _manifest(**overrides: Any) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "format": "paper-galaxy-backup-v1",
        "paper_galaxy_version": "test",
        "schema_version": SCHEMA_VERSION,
        "created_at": "2026-01-01T00:00:00+00:00",
        "project_dir_name": "synthetic-project",
        "contains_database": False,
        "source_files_included": False,
        "vector_indexes_included": False,
        "counts": {},
        "warnings": [],
    }
    manifest.update(overrides)
    return manifest


def _write_archive(
    path: Path,
    *,
    files: dict[str, bytes] | None = None,
    manifest_overrides: dict[str, Any] | None = None,
    checksum_names: list[str] | None = None,
    checksum_lines: list[str] | None = None,
    unchecksummed_entries: list[tuple[str, bytes]] | None = None,
    duplicate_entries: list[tuple[str, bytes]] | None = None,
    special_entries: list[tuple[zipfile.ZipInfo, bytes]] | None = None,
) -> Path:
    manifest = _manifest(**(manifest_overrides or {}))
    payloads = {
        "manifest.json": json.dumps(manifest, sort_keys=True).encode("utf-8"),
        "README_EXPORT.txt": b"Synthetic Paper Galaxy backup\n",
        **(files or {}),
    }
    special_entries = special_entries or []
    payloads.update({info.filename: content for info, content in special_entries})
    if checksum_lines is None:
        names = checksum_names if checksum_names is not None else sorted(payloads)
        checksum_lines = [
            f"{hashlib.sha256(payloads[name]).hexdigest()}  {name}" for name in names
        ]

    special_names = {info.filename for info, _ in special_entries}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(payloads.items()):
            if name not in special_names:
                archive.writestr(name, content)
        for info, content in special_entries:
            archive.writestr(info, content)
        archive.writestr("checksums.sha256", "\n".join(checksum_lines).encode())
        for name, content in unchecksummed_entries or []:
            archive.writestr(name, content)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            for name, content in duplicate_entries or []:
                archive.writestr(name, content)
    return path


def _current_database_bytes(tmp_path: Path) -> bytes:
    project_dir = tmp_path / "database-project"
    database_path = ensure_database_ready(project_dir)
    return database_path.read_bytes()


def test_checksum_manifest_must_cover_every_archive_payload(tmp_path: Path) -> None:
    archive = _write_archive(
        tmp_path / "unlisted-extra.zip",
        unchecksummed_entries=[("unexpected/private.txt", b"not checksummed")],
    )

    with pytest.raises(ValueError, match=r"(?i)checksum|listed|unexpected|extra"):
        inspect_backup(archive)


def test_checksum_manifest_may_not_reference_a_missing_payload(tmp_path: Path) -> None:
    missing_content = b"not in the archive"
    missing_line = f"{hashlib.sha256(missing_content).hexdigest()}  database.sqlite3"
    archive = _write_archive(
        tmp_path / "missing-payload.zip",
        checksum_lines=[
            *[
                f"{hashlib.sha256(content).hexdigest()}  {name}"
                for name, content in {
                    "manifest.json": json.dumps(_manifest(), sort_keys=True).encode(
                        "utf-8"
                    ),
                    "README_EXPORT.txt": b"Synthetic Paper Galaxy backup\n",
                }.items()
            ],
            missing_line,
        ],
    )

    with pytest.raises(ValueError, match=r"(?i)missing"):
        inspect_backup(archive)


def test_duplicate_checksum_names_are_rejected(tmp_path: Path) -> None:
    content = b"Synthetic Paper Galaxy backup\n"
    digest = hashlib.sha256(content).hexdigest()
    manifest_content = json.dumps(_manifest(), sort_keys=True).encode("utf-8")
    archive = _write_archive(
        tmp_path / "duplicate-checksum.zip",
        checksum_lines=[
            f"{hashlib.sha256(manifest_content).hexdigest()}  manifest.json",
            f"{digest}  README_EXPORT.txt",
            f"{digest}  README_EXPORT.txt",
        ],
    )

    with pytest.raises(
        ValueError,
        match=r"(?i)duplicate.*checksum|checksum.*duplicate",
    ):
        inspect_backup(archive)


def test_duplicate_zip_entries_are_rejected(tmp_path: Path) -> None:
    content = b"Synthetic Paper Galaxy backup\n"
    archive = _write_archive(
        tmp_path / "duplicate-entry.zip",
        duplicate_entries=[("README_EXPORT.txt", content)],
    )

    with pytest.raises(ValueError, match=r"(?i)duplicate"):
        inspect_backup(archive)


def test_sorted_path_neighbors_without_component_prefix_are_allowed(
    tmp_path: Path,
) -> None:
    from paper_galaxy.backup.archive import validate_archive_topology

    archive_path = tmp_path / "non-colliding-prefixes.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("models/model.index", b"one")
        archive.writestr("models/model-extra/child.index", b"two")

    with zipfile.ZipFile(archive_path) as archive:
        infos = validate_archive_topology(archive)

    assert [info.filename for info in infos] == [
        "models/model.index",
        "models/model-extra/child.index",
    ]


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "/absolute.txt",
        "../escape.txt",
        "nested/../../escape.txt",
        "C:/Users/example/private.txt",
        r"..\escape.txt",
        "a" * 256,
        ("a/" * 512) + "z",
    ],
)
def test_unsafe_archive_member_paths_are_rejected(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    archive = _write_archive(
        tmp_path / "unsafe-path.zip",
        files={unsafe_name: b"must not be extracted"},
    )

    with pytest.raises(
        ValueError,
        match=r"(?i)path|absolute|traversal|unsafe|member",
    ):
        inspect_backup(archive)


def test_symlink_like_zip_entry_is_rejected(tmp_path: Path) -> None:
    symlink = zipfile.ZipInfo("vector_indexes/current.index")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    symlink.compress_type = zipfile.ZIP_DEFLATED
    archive = _write_archive(
        tmp_path / "symlink-entry.zip",
        manifest_overrides={"vector_indexes_included": True},
        special_entries=[(symlink, b"../../private.index")],
    )

    with pytest.raises(
        ValueError,
        match=r"(?i)symbolic|symlink|file type|regular",
    ):
        inspect_backup(archive)


def test_archive_entry_count_limit_is_enforced(tmp_path: Path) -> None:
    from paper_galaxy.backup.archive import ArchiveLimits

    archive = _write_archive(
        tmp_path / "too-many-entries.zip",
        manifest_overrides={"vector_indexes_included": True},
        files={
            "vector_indexes/one.index": b"one",
            "vector_indexes/two.index": b"two",
        },
    )

    with pytest.raises(
        ValueError,
        match=r"(?i)entr|member|file.*limit|too many",
    ):
        inspect_backup(archive, limits=ArchiveLimits(max_entries=4))


def test_archive_entry_uncompressed_size_limit_is_enforced(tmp_path: Path) -> None:
    from paper_galaxy.backup.archive import ArchiveLimits

    archive = _write_archive(
        tmp_path / "oversize-entry.zip",
        manifest_overrides={"vector_indexes_included": True},
        files={"vector_indexes/large.index": b"x" * 2048},
    )

    with pytest.raises(
        ValueError,
        match=r"(?i)uncompressed|size|large|limit",
    ):
        inspect_backup(
            archive,
            limits=ArchiveLimits(max_entry_uncompressed_bytes=1024),
        )


def test_archive_total_uncompressed_size_limit_is_enforced(tmp_path: Path) -> None:
    from paper_galaxy.backup.archive import ArchiveLimits

    archive = _write_archive(tmp_path / "oversize-total.zip")
    with zipfile.ZipFile(archive) as opened:
        total_size = sum(info.file_size for info in opened.infolist())

    with pytest.raises(
        ValueError,
        match=r"(?i)uncompressed|total|size|limit",
    ):
        inspect_backup(
            archive,
            limits=ArchiveLimits(max_total_uncompressed_bytes=total_size - 1),
        )


def test_archive_file_size_limit_is_enforced_before_opening_zip(tmp_path: Path) -> None:
    from paper_galaxy.backup.archive import ArchiveLimits

    archive = _write_archive(tmp_path / "oversize-archive.zip")

    with pytest.raises(ValueError, match=r"(?i)archive.*size|file.*limit"):
        inspect_backup(
            archive,
            limits=ArchiveLimits(max_archive_bytes=archive.stat().st_size - 1),
        )


def test_archive_compression_ratio_limit_is_enforced(tmp_path: Path) -> None:
    from paper_galaxy.backup.archive import ArchiveLimits

    archive = _write_archive(
        tmp_path / "suspicious-ratio.zip",
        manifest_overrides={"vector_indexes_included": True},
        files={"vector_indexes/compressed.index": b"A" * 4096},
    )

    with pytest.raises(
        ValueError,
        match=r"(?i)compression|ratio|compressed",
    ):
        inspect_backup(archive, limits=ArchiveLimits(max_compression_ratio=10.0))


def test_project_config_has_a_dedicated_memory_limit(tmp_path: Path) -> None:
    oversized_config = random.Random(42).randbytes(MAX_PROJECT_CONFIG_BYTES + 1)
    archive = _write_archive(
        tmp_path / "oversize-project-config.zip",
        manifest_overrides={
            "format": "paper-galaxy-backup-v2",
            "configured_database_path": ".paper-galaxy/paper_galaxy.sqlite3",
            "vector_indexes": [],
        },
        files={"project.toml": oversized_config},
    )

    with pytest.raises(ValueError, match=r"(?i)configuration.*1 MiB|config.*limit"):
        inspect_backup(archive)


def test_extraction_refuses_to_consume_the_free_space_reserve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_galaxy.backup import archive as archive_module

    archive = _write_archive(
        tmp_path / "free-space-guard.zip",
        manifest_overrides={
            "format": "paper-galaxy-backup-v2",
            "configured_database_path": ".paper-galaxy/paper_galaxy.sqlite3",
            "vector_indexes": [],
        },
        files={"project.toml": b'project_name = "Synthetic"\n'},
    )
    no_space = type("DiskUsage", (), {"free": 0})()
    monkeypatch.setattr(archive_module.shutil, "disk_usage", lambda _path: no_space)

    with pytest.raises(OSError, match=r"(?i)free space|safety reserve"):
        inspect_backup(archive)


def test_unsupported_backup_format_is_rejected(tmp_path: Path) -> None:
    archive = _write_archive(
        tmp_path / "future-format.zip",
        manifest_overrides={"format": "paper-galaxy-backup-v999"},
    )

    with pytest.raises(ValueError, match=r"(?i)format|unsupported"):
        inspect_backup(archive)


def test_v2_vector_restore_path_must_be_build_owned(tmp_path: Path) -> None:
    archive = _write_archive(
        tmp_path / "user-file-vector-target.zip",
        manifest_overrides={
            "format": "paper-galaxy-backup-v2",
            "contains_database": False,
            "vector_indexes_included": True,
            "vector_indexes": [
                {
                    "id": "malicious-index",
                    "archive_path": "vector_indexes/malicious.index",
                    "project_path": "papers/user-notes.txt",
                }
            ],
        },
        files={"vector_indexes/malicious.index": b"overwrite-attempt"},
    )

    with pytest.raises(ValueError, match=r"(?i)build-owned|vector.*project"):
        inspect_backup(archive)


def test_v2_restore_destinations_must_be_prefix_free(tmp_path: Path) -> None:
    archive = _write_archive(
        tmp_path / "colliding-vector-targets.zip",
        manifest_overrides={
            "format": "paper-galaxy-backup-v2",
            "contains_database": False,
            "vector_indexes_included": True,
            "vector_indexes": [
                {
                    "id": "parent",
                    "archive_path": "vector_indexes/parent.index",
                    "project_path": ".paper-galaxy/vector_indexes/model-a",
                },
                {
                    "id": "child",
                    "archive_path": "vector_indexes/child.index",
                    "project_path": ".paper-galaxy/vector_indexes/model-a/child.index",
                },
            ],
        },
        files={
            "vector_indexes/parent.index": b"parent",
            "vector_indexes/child.index": b"child",
        },
    )

    with pytest.raises(ValueError, match=r"(?i)ancestor|descendant|collision"):
        inspect_backup(archive)


def test_v2_database_cannot_be_ancestor_of_generated_config(tmp_path: Path) -> None:
    archive = _write_archive(
        tmp_path / "database-config-collision.zip",
        manifest_overrides={
            "format": "paper-galaxy-backup-v2",
            "contains_database": True,
            "database": {
                "archive_path": "database.sqlite3",
                "project_path": ".paper-galaxy",
            },
            "vector_indexes": [],
        },
        files={"database.sqlite3": _current_database_bytes(tmp_path)},
    )

    with pytest.raises(ValueError, match=r"(?i)reserved|ancestor|collision"):
        inspect_backup(archive)


@pytest.mark.parametrize(
    "database_project_path",
    [
        ".paper-galaxy/project.lock",
        ".paper-galaxy/vector_indexes",
        ".git/objects/paper-galaxy.sqlite3",
        "nested/.GIT/paper-galaxy.sqlite3",
    ],
)
def test_v2_database_cannot_target_reserved_project_namespaces(
    tmp_path: Path,
    database_project_path: str,
) -> None:
    archive = _write_archive(
        tmp_path / "reserved-database-target.zip",
        manifest_overrides={
            "format": "paper-galaxy-backup-v2",
            "contains_database": True,
            "database": {
                "archive_path": "database.sqlite3",
                "project_path": database_project_path,
            },
            "configured_database_path": database_project_path,
            "vector_indexes": [],
        },
        files={"database.sqlite3": _current_database_bytes(tmp_path)},
    )

    with pytest.raises(ValueError, match=r"(?i)reserved|metadata|git"):
        inspect_backup(archive)


@pytest.mark.parametrize(
    "configured_path",
    [
        ".paper-galaxy/project.lock",
        ".paper-galaxy/vector_indexes",
        ".git/paper-galaxy.sqlite3",
    ],
)
def test_v2_config_only_backup_cannot_reserve_control_paths(
    tmp_path: Path,
    configured_path: str,
) -> None:
    archive = _write_archive(
        tmp_path / "reserved-configured-target.zip",
        manifest_overrides={
            "format": "paper-galaxy-backup-v2",
            "configured_database_path": configured_path,
            "vector_indexes": [],
        },
    )

    with pytest.raises(ValueError, match=r"(?i)reserved|metadata|git"):
        inspect_backup(archive)


def test_future_manifest_schema_is_rejected(tmp_path: Path) -> None:
    archive = _write_archive(
        tmp_path / "future-schema.zip",
        manifest_overrides={"schema_version": str(CURRENT_SCHEMA_VERSION + 1)},
    )

    with pytest.raises(ValueError, match=r"(?i)schema|version|future"):
        inspect_backup(archive)


@pytest.mark.parametrize(
    ("declared_database", "include_database"),
    [(True, False), (False, True)],
)
def test_contains_database_must_match_archive_payload(
    tmp_path: Path,
    declared_database: bool,
    include_database: bool,
) -> None:
    files = (
        {"database.sqlite3": _current_database_bytes(tmp_path)}
        if include_database
        else None
    )
    archive = _write_archive(
        tmp_path / "database-flag-mismatch.zip",
        manifest_overrides={"contains_database": declared_database},
        files=files,
    )

    with pytest.raises(ValueError, match=r"(?i)database|manifest|contains"):
        inspect_backup(archive)


def test_manifest_schema_must_match_embedded_database(tmp_path: Path) -> None:
    archive = _write_archive(
        tmp_path / "database-schema-mismatch.zip",
        manifest_overrides={
            "contains_database": True,
            "schema_version": str(CURRENT_SCHEMA_VERSION - 1),
        },
        files={"database.sqlite3": _current_database_bytes(tmp_path)},
    )

    with pytest.raises(
        ValueError,
        match=r"(?i)schema|version|database|manifest",
    ):
        inspect_backup(archive)
