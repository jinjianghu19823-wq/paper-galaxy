"""Strict, streaming validation helpers for Paper Galaxy backup archives.

This module deliberately owns the untrusted-ZIP boundary.  Callers should
inspect an archive successfully before using any member, and should extract
only the named members described by the returned manifest.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import unicodedata
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, NoReturn

from paper_galaxy.storage.migrations import (
    CURRENT_SCHEMA_VERSION,
    OLDEST_SUPPORTED_SCHEMA_VERSION,
)

CHECKSUM_MEMBER = "checksums.sha256"
MANIFEST_MEMBER = "manifest.json"
DATABASE_MEMBER_V1 = "database.sqlite3"
DEFAULT_PROJECT_DATABASE_PATH = ".paper-galaxy/paper_galaxy.sqlite3"
SUPPORTED_BACKUP_FORMATS = frozenset(
    {"paper-galaxy-backup-v1", "paper-galaxy-backup-v2"}
)

_CHECKSUM_LINE = re.compile(r"^([0-9a-fA-F]{64})[ \t]+(.+)$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "aux",
        "con",
        "nul",
        "prn",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
    }
)
_COPY_CHUNK_BYTES = 1024 * 1024
_MAX_MANIFEST_BYTES = 1024 * 1024
_MAX_CHECKSUM_BYTES = 4 * 1024 * 1024
_MAX_PORTABLE_PATH_BYTES = 1024
_MAX_PORTABLE_COMPONENT_BYTES = 255
_ALLOWED_COMPRESSION_METHODS = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    """Resource limits applied before and while reading a backup archive."""

    max_entries: int = 1024
    max_archive_bytes: int = 8 * 1024 * 1024 * 1024
    max_entry_uncompressed_bytes: int = 4 * 1024 * 1024 * 1024
    max_total_uncompressed_bytes: int = 8 * 1024 * 1024 * 1024
    max_compression_ratio: float = 200.0
    min_free_bytes_after_extract: int = 256 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.max_entries <= 0:
            raise ValueError("Archive entry limit must be greater than zero.")
        if self.max_archive_bytes <= 0:
            raise ValueError("Archive file-size limit must be greater than zero.")
        if self.max_entry_uncompressed_bytes <= 0:
            raise ValueError(
                "Archive per-entry uncompressed size limit must be greater than zero."
            )
        if self.max_total_uncompressed_bytes <= 0:
            raise ValueError(
                "Archive total uncompressed size limit must be greater than zero."
            )
        if not math.isfinite(self.max_compression_ratio):
            raise ValueError("Archive compression ratio limit must be finite.")
        if self.max_compression_ratio <= 0:
            raise ValueError(
                "Archive compression ratio limit must be greater than zero."
            )
        if self.min_free_bytes_after_extract < 0:
            raise ValueError("Archive free-space reserve cannot be negative.")


DEFAULT_ARCHIVE_LIMITS = ArchiveLimits()


@dataclass(frozen=True, slots=True)
class ArchiveInspection:
    """Validated metadata for a backup whose members are safe to address."""

    path: Path
    manifest: dict[str, Any]
    names: tuple[str, ...]
    checksums: dict[str, str]
    checksum_status: str = "ok"


@dataclass(frozen=True, slots=True)
class DatabaseMapping:
    """A v2 database member and its project-relative restore destination."""

    archive_path: str
    project_path: str


@dataclass(frozen=True, slots=True)
class VectorIndexMapping:
    """A v2 vector-index member and its project-relative destination."""

    id: str
    archive_path: str
    project_path: str


def inspect_archive(
    path: Path, *, limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS
) -> ArchiveInspection:
    """Validate archive topology, manifest, resources, and all checksums.

    Validation is mandatory: the checksum manifest must list every payload
    member exactly once, and every listed digest is computed by streaming the
    decompressed member rather than loading the archive into memory.
    """

    resolved_path = path.expanduser().resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"Backup does not exist: {resolved_path}")
    if not resolved_path.is_file():
        raise ValueError(f"Backup path is not a regular file: {resolved_path}")
    if resolved_path.stat().st_size > limits.max_archive_bytes:
        raise ValueError("Backup archive file size exceeds the configured limit.")

    try:
        with zipfile.ZipFile(resolved_path) as archive:
            infos = validate_archive_topology(archive, limits=limits)
            by_name = {info.filename: info for info in infos}
            if MANIFEST_MEMBER not in by_name:
                raise ValueError("Backup is missing manifest.json.")
            if CHECKSUM_MEMBER not in by_name:
                raise ValueError("Backup is missing checksums.sha256.")

            manifest_content = read_member_bytes(
                archive,
                by_name[MANIFEST_MEMBER],
                limits=limits,
                max_bytes=_MAX_MANIFEST_BYTES,
            )
            manifest = load_manifest(manifest_content)
            names = tuple(sorted(by_name))

            checksum_content = read_member_bytes(
                archive,
                by_name[CHECKSUM_MEMBER],
                limits=limits,
                max_bytes=_MAX_CHECKSUM_BYTES,
            )
            checksums = parse_checksums(checksum_content)
            validate_checksum_coverage(checksums, names)
            validate_manifest_payload(manifest, names)
            for name, expected_digest in sorted(checksums.items()):
                actual_digest = digest_member(
                    archive,
                    by_name[name],
                    limits=limits,
                )
                if actual_digest != expected_digest:
                    raise ValueError(f"Checksum mismatch for archive member: {name}")
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as exc:
        raise ValueError(f"Invalid or unsupported backup ZIP archive: {exc}") from exc

    return ArchiveInspection(
        path=resolved_path,
        manifest=manifest,
        names=names,
        checksums=checksums,
    )


def validate_archive_topology(
    archive: zipfile.ZipFile, *, limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS
) -> tuple[zipfile.ZipInfo, ...]:
    """Reject duplicate, unsafe, special, encrypted, or excessive members."""

    infos = tuple(archive.infolist())
    if len(infos) > limits.max_entries:
        raise ValueError(
            "Backup contains too many archive entries: "
            f"{len(infos)} exceeds the limit of {limits.max_entries}."
        )

    exact_names: set[str] = set()
    portable_names: dict[str, str] = {}
    total_size = 0
    for info in infos:
        name = validate_relative_path(info.filename, purpose="archive member")
        if name in exact_names:
            raise ValueError(f"Backup contains a duplicate ZIP entry: {name}")
        exact_names.add(name)

        portable_name = _portable_path_key(name)
        previous = portable_names.get(portable_name)
        if previous is not None:
            raise ValueError(
                "Backup contains archive member names that collide on a "
                f"portable filesystem: {previous!r} and {name!r}."
            )
        portable_names[portable_name] = name

        _validate_regular_zip_member(info)
        if info.flag_bits & 0x1:
            raise ValueError(f"Encrypted archive members are unsupported: {name}")
        if info.compress_type not in _ALLOWED_COMPRESSION_METHODS:
            raise ValueError(
                f"Unsupported compression method for archive member: {name}"
            )
        if info.file_size < 0 or info.compress_size < 0:
            raise ValueError(f"Invalid negative size for archive member: {name}")
        if info.file_size > limits.max_entry_uncompressed_bytes:
            raise ValueError(
                "Archive member uncompressed size exceeds the limit: "
                f"{name} ({info.file_size} bytes)."
            )

        total_size += info.file_size
        if total_size > limits.max_total_uncompressed_bytes:
            raise ValueError(
                "Archive total uncompressed size exceeds the limit: "
                f"{total_size} bytes."
            )
        ratio = _compression_ratio(info)
        if ratio > limits.max_compression_ratio:
            raise ValueError(
                "Archive member compression ratio exceeds the limit: "
                f"{name} ({ratio:.1f}:1)."
            )
    _validate_prefix_free_paths(exact_names, purpose="archive members")
    return infos


def validate_relative_path(value: object, *, purpose: str) -> str:
    """Return a safe portable POSIX-relative path or raise ``ValueError``."""

    if not isinstance(value, str) or not value:
        raise ValueError(f"{purpose.capitalize()} path must be a non-empty string.")
    if "\x00" in value:
        raise ValueError(f"Unsafe NUL byte in {purpose} path.")
    if "\\" in value:
        raise ValueError(f"Unsafe backslash in {purpose} path: {value!r}")
    if value.startswith(("/", "//")) or _WINDOWS_DRIVE.match(value):
        raise ValueError(f"Absolute {purpose} path is unsafe: {value!r}")
    normalized_path = unicodedata.normalize("NFC", value)
    if len(normalized_path.encode("utf-8")) > _MAX_PORTABLE_PATH_BYTES:
        raise ValueError(f"Overlong {purpose} path: {value!r}")

    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Unsafe path traversal in {purpose}: {value!r}")
    for part in parts:
        normalized_part = unicodedata.normalize("NFC", part)
        if len(normalized_part.encode("utf-8")) > _MAX_PORTABLE_COMPONENT_BYTES:
            raise ValueError(f"Overlong component in {purpose} path: {value!r}")
        if part != part.strip() or part.endswith("."):
            raise ValueError(
                f"Non-portable whitespace or trailing dot in {purpose}: {value!r}"
            )
        if ":" in part:
            raise ValueError(f"Unsafe colon in {purpose} path: {value!r}")
        if any(ord(character) < 32 or ord(character) == 127 for character in part):
            raise ValueError(f"Unsafe control character in {purpose}: {value!r}")
        windows_stem = part.split(".", maxsplit=1)[0].casefold()
        if windows_stem in _WINDOWS_RESERVED_NAMES:
            raise ValueError(
                f"Reserved Windows device name in {purpose} path: {value!r}"
            )
    return value


def parse_checksums(content: bytes) -> dict[str, str]:
    """Parse a strict, duplicate-free SHA-256 manifest."""

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Checksum manifest must be valid UTF-8.") from exc

    checksums: dict[str, str] = {}
    portable_names: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        match = _CHECKSUM_LINE.fullmatch(line)
        if match is None:
            raise ValueError(
                f"Malformed checksum manifest line {line_number}; expected SHA-256."
            )
        digest, raw_name = match.groups()
        name = validate_relative_path(raw_name, purpose="checksum member")
        portable_name = _portable_path_key(name)
        if name in checksums or portable_name in portable_names:
            raise ValueError(f"Duplicate checksum entry for archive member: {name}")
        if name == CHECKSUM_MEMBER:
            raise ValueError("Checksum manifest may not checksum itself.")
        checksums[name] = digest.lower()
        portable_names.add(portable_name)
    if not checksums:
        raise ValueError("Checksum manifest is empty.")
    return checksums


def validate_checksum_coverage(
    checksums: Mapping[str, str], names: Sequence[str]
) -> None:
    """Require exact set equality between checksums and payload members."""

    payload_names = set(names) - {CHECKSUM_MEMBER}
    checksum_names = set(checksums)
    missing_payloads = checksum_names - payload_names
    unlisted_payloads = payload_names - checksum_names
    if missing_payloads:
        joined = ", ".join(sorted(missing_payloads))
        raise ValueError(f"Checksum references missing archive payload: {joined}")
    if unlisted_payloads:
        joined = ", ".join(sorted(unlisted_payloads))
        raise ValueError(f"Archive payload is not listed by checksums: {joined}")


def load_manifest(content: bytes) -> dict[str, Any]:
    """Load and validate the public backup manifest without duplicate JSON keys."""

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Backup manifest must be valid UTF-8.") from exc
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_json_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"Backup manifest is not valid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise ValueError("Backup manifest must be a JSON object.")

    manifest = decoded
    backup_format = _required_string(manifest, "format")
    if backup_format not in SUPPORTED_BACKUP_FORMATS:
        raise ValueError(f"Unsupported backup format: {backup_format}")
    schema_version(manifest)
    _required_string(manifest, "paper_galaxy_version")
    _required_string(manifest, "created_at")
    _required_string(manifest, "project_dir_name")
    _required_bool(manifest, "contains_database")
    source_files_included = _required_bool(manifest, "source_files_included")
    if source_files_included:
        raise ValueError("Backups containing source files are unsupported.")
    _required_bool(manifest, "vector_indexes_included")
    _required_object(manifest, "counts")
    _required_string_list(manifest, "warnings")

    if backup_format == "paper-galaxy-backup-v2":
        _validate_v2_manifest_structures(manifest)
    return manifest


def schema_version(manifest: Mapping[str, Any]) -> int:
    """Return a supported integer schema version from a manifest."""

    raw_version = manifest.get("schema_version")
    if isinstance(raw_version, bool):
        raise ValueError("Backup manifest schema version must be an integer.")
    if isinstance(raw_version, int):
        version = raw_version
    elif (
        isinstance(raw_version, str) and raw_version.isascii() and raw_version.isdigit()
    ):
        version = int(raw_version)
    else:
        raise ValueError("Backup manifest schema version must be an integer.")
    if version > CURRENT_SCHEMA_VERSION:
        raise ValueError(
            "Backup manifest uses a future schema version "
            f"({version}); this build supports {CURRENT_SCHEMA_VERSION}."
        )
    if version < OLDEST_SUPPORTED_SCHEMA_VERSION:
        raise ValueError(
            "Backup manifest schema version is unsupported: "
            f"{version}; oldest supported is {OLDEST_SUPPORTED_SCHEMA_VERSION}."
        )
    return version


def database_mapping(manifest: Mapping[str, Any]) -> DatabaseMapping | None:
    """Return the declared database mapping for a supported manifest."""

    if not _required_bool(manifest, "contains_database"):
        return None
    if manifest.get("format") == "paper-galaxy-backup-v1":
        return DatabaseMapping(
            archive_path=DATABASE_MEMBER_V1,
            project_path=".paper-galaxy/paper_galaxy.sqlite3",
        )
    value = manifest.get("database")
    if not isinstance(value, dict):
        raise ValueError("Backup v2 manifest database mapping is missing.")
    return DatabaseMapping(
        archive_path=validate_relative_path(
            value.get("archive_path"), purpose="database archive"
        ),
        project_path=validate_relative_path(
            value.get("project_path"), purpose="database project"
        ),
    )


def configured_database_path(manifest: Mapping[str, Any]) -> str:
    """Return the safe database path retained by database or config-only backups."""

    mapping = database_mapping(manifest)
    if mapping is not None:
        return mapping.project_path
    if manifest.get("format") == "paper-galaxy-backup-v1":
        return DEFAULT_PROJECT_DATABASE_PATH
    raw_path = manifest.get("configured_database_path", DEFAULT_PROJECT_DATABASE_PATH)
    return validate_relative_path(raw_path, purpose="configured database project")


def vector_index_mappings(
    manifest: Mapping[str, Any],
) -> tuple[VectorIndexMapping, ...]:
    """Return strict v2 vector mappings (v1 has no portable mapping table)."""

    if manifest.get("format") == "paper-galaxy-backup-v1":
        return ()
    raw_mappings = manifest.get("vector_indexes", [])
    if not isinstance(raw_mappings, list):
        raise ValueError("Backup v2 vector_indexes must be a list.")

    mappings: list[VectorIndexMapping] = []
    identifiers: set[str] = set()
    archive_paths: set[str] = set()
    project_paths: set[str] = set()
    for position, value in enumerate(raw_mappings):
        if not isinstance(value, dict):
            raise ValueError(
                f"Backup v2 vector index mapping {position} must be an object."
            )
        if set(value) != {"id", "archive_path", "project_path"}:
            raise ValueError(
                "Backup v2 vector index mappings require exactly id, "
                "archive_path, and project_path."
            )
        identifier = value.get("id")
        if (
            not isinstance(identifier, str)
            or not identifier.strip()
            or identifier != identifier.strip()
            or any(
                ord(character) < 32 or ord(character) == 127 for character in identifier
            )
        ):
            raise ValueError("Backup v2 vector index id must be a non-empty string.")
        archive_path = validate_relative_path(
            value.get("archive_path"), purpose="vector index archive"
        )
        project_path = validate_relative_path(
            value.get("project_path"), purpose="vector index project"
        )
        if not archive_path.startswith("vector_indexes/"):
            raise ValueError(
                "Backup v2 vector index archive path must be below vector_indexes/."
            )
        if not project_path.startswith(".paper-galaxy/vector_indexes/"):
            raise ValueError(
                "Backup v2 vector index project path must be below the "
                "build-owned .paper-galaxy/vector_indexes/ directory."
            )
        identifier_key = unicodedata.normalize("NFC", identifier).casefold()
        archive_key = _portable_path_key(archive_path)
        project_key = _portable_path_key(project_path)
        if identifier_key in identifiers:
            raise ValueError(f"Duplicate backup vector index id: {identifier}")
        if archive_key in archive_paths:
            raise ValueError(
                f"Duplicate backup vector index archive path: {archive_path}"
            )
        if project_key in project_paths:
            raise ValueError(
                f"Duplicate backup vector index project path: {project_path}"
            )
        identifiers.add(identifier_key)
        archive_paths.add(archive_key)
        project_paths.add(project_key)
        mappings.append(
            VectorIndexMapping(
                id=identifier,
                archive_path=archive_path,
                project_path=project_path,
            )
        )
    return tuple(mappings)


def validate_manifest_payload(
    manifest: Mapping[str, Any], names: Sequence[str]
) -> None:
    """Require database/vector presence flags to match declared payloads."""

    name_set = set(names)
    mapping = database_mapping(manifest)
    contains_database = mapping is not None
    if mapping is not None and mapping.archive_path not in name_set:
        raise ValueError(
            "Backup manifest contains_database is true but its database payload "
            "is missing."
        )
    if mapping is None:
        unexpected_database = DATABASE_MEMBER_V1 in name_set
        if manifest.get("format") == "paper-galaxy-backup-v2":
            raw_database = manifest.get("database")
            if isinstance(raw_database, dict):
                archive_path = raw_database.get("archive_path")
                unexpected_database = (
                    isinstance(archive_path, str) and archive_path in name_set
                )
        if unexpected_database:
            raise ValueError(
                "Backup contains a database payload but contains_database is false."
            )
    elif not contains_database:
        raise AssertionError("unreachable database manifest state")

    actual_vectors = {name for name in name_set if name.startswith("vector_indexes/")}
    vector_flag = _required_bool(manifest, "vector_indexes_included")
    if manifest.get("format") == "paper-galaxy-backup-v2":
        declared_vectors = {
            item.archive_path for item in vector_index_mappings(manifest)
        }
        if declared_vectors != actual_vectors:
            raise ValueError(
                "Backup v2 vector index manifest does not match archive payloads."
            )
        if vector_flag != bool(declared_vectors):
            raise ValueError(
                "Backup vector_indexes_included flag does not match its mappings."
            )
    elif vector_flag != bool(actual_vectors):
        raise ValueError(
            "Backup vector_indexes_included flag does not match archive payloads."
        )

    allowed_names = {
        CHECKSUM_MEMBER,
        MANIFEST_MEMBER,
        "README_EXPORT.txt",
        "project.toml",
    }
    if mapping is not None:
        allowed_names.add(mapping.archive_path)
    if manifest.get("format") == "paper-galaxy-backup-v2":
        allowed_names.update(
            item.archive_path for item in vector_index_mappings(manifest)
        )
    else:
        allowed_names.update(actual_vectors)
    unexpected_names = name_set - allowed_names
    if unexpected_names:
        joined = ", ".join(sorted(unexpected_names))
        raise ValueError(
            "Backup contains unexpected payloads that are unsupported by its "
            f"manifest format: {joined}"
        )
    project_paths: set[str] = set()
    if mapping is not None:
        project_paths.add(mapping.project_path)
        project_paths.update(
            f"{mapping.project_path}{suffix}" for suffix in ("-journal", "-wal", "-shm")
        )
        project_paths.add(".paper-galaxy/project.toml")
    elif "project.toml" in name_set:
        project_paths.add(".paper-galaxy/project.toml")
    project_paths.update(item.project_path for item in vector_index_mappings(manifest))
    _validate_prefix_free_paths(project_paths, purpose="restore destinations")


def validate_database_schema_version(
    manifest: Mapping[str, Any], database_schema_version: int
) -> None:
    """Require an extracted SQLite schema version to match its manifest."""

    declared_version = schema_version(manifest)
    if database_schema_version != declared_version:
        raise ValueError(
            "Backup database schema version does not match manifest: "
            f"database={database_schema_version}, manifest={declared_version}."
        )


def read_member_bytes(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo | str,
    *,
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
    max_bytes: int | None = None,
) -> bytes:
    """Read a small member through the same bounded streaming path."""

    chunks: list[bytes] = []
    total = 0

    def collect(chunk: bytes) -> None:
        nonlocal total
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:
            raise ValueError("Archive metadata member exceeds its size limit.")
        chunks.append(chunk)

    _consume_member(archive, member, collect, limits=limits)
    return b"".join(chunks)


def digest_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo | str,
    *,
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
) -> str:
    """Return a member SHA-256 digest using bounded streaming reads."""

    digest = hashlib.sha256()
    _consume_member(archive, member, digest.update, limits=limits)
    return digest.hexdigest()


def stream_extract_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo | str,
    destination: Path,
    *,
    expected_sha256: str | None = None,
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
) -> str:
    """Extract one validated member to a new regular file and return its digest.

    The destination must not already exist.  On any read, CRC, size, digest, or
    write failure, the partial destination is removed.
    """

    info = archive.getinfo(member) if isinstance(member, str) else member
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    available = shutil.disk_usage(destination.parent).free
    required = info.file_size + limits.min_free_bytes_after_extract
    if required > available:
        raise OSError(
            "Insufficient free space to extract this backup member while "
            "preserving the configured safety reserve."
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags | no_follow, 0o600)
    digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1

            def write(chunk: bytes) -> None:
                output.write(chunk)
                digest.update(chunk)

            _consume_member(archive, info, write, limits=limits)
            output.flush()
            os.fsync(output.fileno())
        actual_digest = digest.hexdigest()
        if expected_sha256 is not None:
            expected = _validated_digest(expected_sha256)
            if actual_digest != expected:
                raise ValueError(
                    "Checksum mismatch while extracting archive member: "
                    f"{info.filename}"
                )
        return actual_digest
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        destination.unlink(missing_ok=True)
        raise


def _consume_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo | str,
    consume: Callable[[bytes], object],
    *,
    limits: ArchiveLimits,
) -> int:
    info = archive.getinfo(member) if isinstance(member, str) else member
    validate_relative_path(info.filename, purpose="archive member")
    _validate_regular_zip_member(info)
    if info.flag_bits & 0x1:
        raise ValueError(f"Encrypted archive members are unsupported: {info.filename}")
    if info.compress_type not in _ALLOWED_COMPRESSION_METHODS:
        raise ValueError(
            f"Unsupported compression method for archive member: {info.filename}"
        )
    if _compression_ratio(info) > limits.max_compression_ratio:
        raise ValueError(
            f"Archive member compression ratio exceeds the limit: {info.filename}"
        )
    if info.file_size > limits.max_entry_uncompressed_bytes:
        raise ValueError(
            f"Archive member uncompressed size exceeds the limit: {info.filename}"
        )
    total = 0
    with archive.open(info, "r") as source:
        while True:
            chunk = source.read(_COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > info.file_size:
                raise ValueError(
                    f"Archive member expanded beyond its declared size: {info.filename}"
                )
            if total > limits.max_entry_uncompressed_bytes:
                raise ValueError(
                    "Archive member expanded beyond the uncompressed size limit: "
                    f"{info.filename}"
                )
            consume(chunk)
    if total != info.file_size:
        raise ValueError(
            f"Archive member size does not match its ZIP metadata: {info.filename}"
        )
    return total


def _validate_regular_zip_member(info: zipfile.ZipInfo) -> None:
    if info.is_dir() or info.filename.endswith("/"):
        raise ValueError(f"Archive directories are unsupported: {info.filename}")
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(unix_mode)
    if file_type == stat.S_IFLNK:
        raise ValueError(f"Archive member may not be a symbolic link: {info.filename}")
    if file_type not in {0, stat.S_IFREG}:
        raise ValueError(f"Archive member must be a regular file: {info.filename}")
    if info.external_attr & 0x10:
        raise ValueError(f"Archive member may not be a directory: {info.filename}")


def _compression_ratio(info: zipfile.ZipInfo) -> float:
    if info.file_size == 0:
        return 0.0
    if info.compress_size == 0:
        return math.inf
    return info.file_size / info.compress_size


def _portable_path_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def _validate_prefix_free_paths(
    paths: Sequence[str] | set[str], *, purpose: str
) -> None:
    keyed = sorted(
        (
            tuple(
                unicodedata.normalize("NFC", part).casefold()
                for part in path.split("/")
            ),
            path,
        )
        for path in paths
    )
    for (parts, path), (other_parts, other_path) in pairwise(keyed):
        if len(other_parts) >= len(parts) and other_parts[: len(parts)] == parts:
            raise ValueError(
                f"Backup {purpose} have an ancestor/descendant path collision: "
                f"{path!r} and {other_path!r}."
            )


def _validated_digest(value: str) -> str:
    if re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise ValueError("Expected checksum must be a 64-character SHA-256 digest.")
    return value.lower()


def _json_object_without_duplicates(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key in backup manifest: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"Non-finite JSON value is forbidden in backup manifest: {value}")


def _required_string(manifest: Mapping[str, Any], key: str) -> str:
    value = manifest.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Backup manifest {key} must be a non-empty string.")
    return value


def _required_bool(manifest: Mapping[str, Any], key: str) -> bool:
    value = manifest.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"Backup manifest {key} must be a boolean.")
    return value


def _required_object(manifest: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = manifest.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Backup manifest {key} must be an object.")
    return value


def _required_string_list(manifest: Mapping[str, Any], key: str) -> list[str]:
    value = manifest.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"Backup manifest {key} must be a list of strings.")
    return value


def _validate_v2_manifest_structures(manifest: Mapping[str, Any]) -> None:
    raw_database = manifest.get("database")
    contains_database = _required_bool(manifest, "contains_database")
    if contains_database:
        if not isinstance(raw_database, dict):
            raise ValueError("Backup v2 manifest database mapping is required.")
        if set(raw_database) != {"archive_path", "project_path"}:
            raise ValueError(
                "Backup v2 database mapping requires exactly archive_path and "
                "project_path."
            )
        database_archive_path = validate_relative_path(
            raw_database.get("archive_path"), purpose="database archive"
        )
        database_project_path = validate_relative_path(
            raw_database.get("project_path"), purpose="database project"
        )
        if database_archive_path in {
            CHECKSUM_MEMBER,
            MANIFEST_MEMBER,
            "README_EXPORT.txt",
            "project.toml",
        } or database_archive_path.startswith("vector_indexes/"):
            raise ValueError(
                "Backup v2 database archive path collides with a reserved payload."
            )
    elif raw_database is not None:
        raise ValueError(
            "Backup v2 database mapping must be absent when contains_database is false."
        )

    configured_path = validate_relative_path(
        manifest.get(
            "configured_database_path",
            database_project_path
            if contains_database
            else DEFAULT_PROJECT_DATABASE_PATH,
        ),
        purpose="configured database project",
    )
    _validate_database_project_destination(
        configured_path,
        purpose="configured database project",
    )
    if contains_database and configured_path != database_project_path:
        raise ValueError(
            "Backup v2 configured database path must match its database mapping."
        )

    mappings = vector_index_mappings(manifest)
    if contains_database:
        _validate_database_project_destination(
            database_project_path,
            purpose="database project",
        )
        database_archive_key = _portable_path_key(database_archive_path)
        database_project_key = _portable_path_key(database_project_path)
        reserved_project_keys = {
            _portable_path_key(".paper-galaxy/project.toml"),
            *(
                _portable_path_key(f"{database_project_path}{suffix}")
                for suffix in ("-journal", "-wal", "-shm")
            ),
        }
        if (
            database_project_key in reserved_project_keys
            or database_project_key.startswith(
                _portable_path_key(".paper-galaxy/vector_indexes/")
            )
        ):
            raise ValueError(
                "Backup v2 database project path collides with reserved project "
                "metadata."
            )
        if any(
            _portable_path_key(item.archive_path) == database_archive_key
            or _portable_path_key(item.project_path)
            in {database_project_key, *reserved_project_keys}
            for item in mappings
        ):
            raise ValueError(
                "Backup v2 database and vector index mappings must not collide."
            )
    vector_flag = _required_bool(manifest, "vector_indexes_included")
    if vector_flag != bool(mappings):
        raise ValueError(
            "Backup v2 vector_indexes_included flag does not match its mappings."
        )


def _validate_database_project_destination(path: str, *, purpose: str) -> None:
    """Keep restored databases out of control and build-owned namespaces."""

    key = _portable_path_key(path)
    reserved_exact = {
        _portable_path_key(".paper-galaxy/project.toml"),
        _portable_path_key(".paper-galaxy/project.lock"),
        _portable_path_key(".paper-galaxy/vector_indexes"),
    }
    vector_root = _portable_path_key(".paper-galaxy/vector_indexes/")
    git_component = unicodedata.normalize("NFC", ".git").casefold()
    components = {
        unicodedata.normalize("NFC", component).casefold()
        for component in path.split("/")
    }
    if key in reserved_exact or key.startswith(vector_root):
        raise ValueError(
            f"Backup v2 {purpose} path collides with reserved project metadata."
        )
    if git_component in components:
        raise ValueError(f"Backup v2 {purpose} path may not enter Git metadata.")
