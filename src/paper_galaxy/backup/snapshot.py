"""Consistent, validated SQLite snapshots for project backup bundles.

The source database may be live and may have committed pages in a WAL.  A
snapshot is therefore always made with SQLite's online backup API; copying the
database file itself is not safe.  The completed snapshot is normalized to a
rollback journal so that it is a self-contained archive payload.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

from paper_galaxy.errors import (
    DatabaseCorruptError,
    FutureSchemaError,
    UnsupportedSchemaError,
)
from paper_galaxy.storage.migrations import (
    CURRENT_SCHEMA_VERSION,
    OLDEST_SUPPORTED_SCHEMA_VERSION,
    read_schema_version,
    validate_schema_capability,
)

_SQLITE_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")
_SQLITE_HEADER = b"SQLite format 3\x00"
_DEFAULT_BACKUP_PAGES = 256


def create_database_snapshot(
    source_path: Path | str,
    destination_path: Path | str,
    *,
    expected_schema_version: int | str | None = None,
    pages_per_step: int = _DEFAULT_BACKUP_PAGES,
) -> Path:
    """Create a self-contained, validated snapshot of a live SQLite database.

    ``destination_path`` must not already exist.  The function never follows a
    destination symlink and refuses aliases of the source database or any of
    its SQLite sidecars.  If backup, normalization, or validation fails, the
    partially created destination and its own sidecars are removed.

    ``Connection.backup`` copies a transactionally consistent view, including
    committed WAL pages.  Passing a finite ``pages_per_step`` keeps the copy in
    bounded SQLite page batches instead of reading the whole database into
    Python memory.
    """

    if pages_per_step <= 0:
        raise ValueError("pages_per_step must be greater than zero.")

    source = _resolve_source_database(source_path)
    destination = _absolute_path(destination_path)
    _prepare_destination(source, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _validate_destination(source, destination)

    created_destination = False
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        descriptor = os.open(
            destination,
            os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        created_destination = True
        try:
            file_status = os.fstat(descriptor)
            if not stat.S_ISREG(file_status.st_mode):
                raise ValueError(
                    f"SQLite snapshot destination is not a regular file: {destination}"
                )
        finally:
            os.close(descriptor)
        os.chmod(destination, 0o600)

        source_connection = sqlite3.connect(_source_uri(source), uri=True)
        source_connection.execute("PRAGMA query_only = ON")
        source_connection.execute("PRAGMA busy_timeout = 5000")

        destination_connection = sqlite3.connect(
            f"{destination.as_uri()}?mode=rw",
            uri=True,
        )
        destination_connection.execute("PRAGMA busy_timeout = 5000")
        destination_connection.execute("PRAGMA foreign_keys = ON")
        source_connection.backup(
            destination_connection,
            pages=pages_per_step,
            sleep=0.05,
        )

        journal_row = destination_connection.execute(
            "PRAGMA journal_mode = DELETE"
        ).fetchone()
        journal_mode = str(journal_row[0]).lower() if journal_row else ""
        if journal_mode != "delete":
            raise RuntimeError(
                "SQLite snapshot could not be normalized to rollback-journal mode."
            )
        destination_connection.execute("PRAGMA synchronous = FULL")
        synchronous_row = destination_connection.execute(
            "PRAGMA synchronous"
        ).fetchone()
        if synchronous_row is None or int(synchronous_row[0]) != 2:
            raise RuntimeError(
                "SQLite snapshot could not enable FULL synchronous durability."
            )

        _validate_connection(
            destination_connection,
            destination,
            expected_schema_version=expected_schema_version,
        )
        destination_connection.commit()
        destination_connection.close()
        destination_connection = None
        source_connection.close()
        source_connection = None

        os.chmod(destination, 0o600)
        _fsync_file(destination)
        validate_database_snapshot(
            destination,
            expected_schema_version=expected_schema_version,
        )
        _fsync_directory(destination.parent)
        return destination
    except BaseException:
        try:
            if destination_connection is not None:
                destination_connection.close()
        finally:
            try:
                if source_connection is not None:
                    source_connection.close()
            finally:
                if created_destination:
                    _remove_snapshot_files(destination)
        raise


def validate_database_snapshot(
    snapshot_path: Path | str,
    *,
    expected_schema_version: int | str | None = None,
) -> int:
    """Validate a self-contained Paper Galaxy SQLite snapshot without writes.

    Validation requires a rollback-journal database with no sidecar payloads,
    a successful ``quick_check`` and ``foreign_key_check``, and one of the
    schema identities explicitly supported by this build.  If supplied,
    ``expected_schema_version`` must match exactly.
    """

    snapshot = _absolute_path(snapshot_path)
    if _is_link_or_reparse_point(snapshot):
        raise ValueError(f"Refusing symbolic SQLite snapshot: {snapshot}")
    if not snapshot.is_file():
        raise FileNotFoundError(f"SQLite snapshot does not exist: {snapshot}")
    for sidecar in _sidecar_paths(snapshot):
        if sidecar.exists() or sidecar.is_symlink():
            raise ValueError(
                f"SQLite snapshot is not self-contained; unexpected sidecar: {sidecar}"
            )
    if _header_uses_wal(snapshot):
        raise ValueError("SQLite snapshot is not normalized to rollback-journal mode.")

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{snapshot.as_uri()}?mode=ro&immutable=1",
            uri=True,
        )
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        journal_row = connection.execute("PRAGMA journal_mode").fetchone()
        journal_mode = str(journal_row[0]).lower() if journal_row else ""
        if journal_mode != "delete":
            raise ValueError(
                "SQLite snapshot is not normalized to rollback-journal mode."
            )
        return _validate_connection(
            connection,
            snapshot,
            expected_schema_version=expected_schema_version,
        )
    except (FutureSchemaError, UnsupportedSchemaError, ValueError):
        raise
    except sqlite3.DatabaseError as exc:
        raise DatabaseCorruptError(
            snapshot,
            detail_message=f"SQLite snapshot at {snapshot} is unreadable: {exc}",
        ) from exc
    finally:
        if connection is not None:
            connection.close()


def _validate_connection(
    connection: sqlite3.Connection,
    database_path: Path,
    *,
    expected_schema_version: int | str | None,
) -> int:
    quick_rows = connection.execute("PRAGMA quick_check").fetchall()
    quick_messages = [str(row[0]) for row in quick_rows]
    if quick_messages != ["ok"]:
        detail = "; ".join(quick_messages[:5]) or "no result"
        raise DatabaseCorruptError(
            database_path,
            detail_message=(
                f"SQLite snapshot at {database_path} failed quick_check: {detail}"
            ),
        )

    foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_rows:
        raise DatabaseCorruptError(
            database_path,
            detail_message=(
                f"SQLite snapshot at {database_path} failed foreign_key_check "
                f"with {len(foreign_key_rows)} violation(s)."
            ),
        )

    version = read_schema_version(connection)
    if version > CURRENT_SCHEMA_VERSION:
        raise FutureSchemaError(
            database_path,
            found_version=version,
            current_version=CURRENT_SCHEMA_VERSION,
        )
    if version < OLDEST_SUPPORTED_SCHEMA_VERSION:
        raise UnsupportedSchemaError(
            database_path,
            detail_message=(
                f"SQLite snapshot at {database_path} uses unsupported schema "
                f"version {version}; the oldest supported version is "
                f"{OLDEST_SUPPORTED_SCHEMA_VERSION}."
            ),
        )
    validate_schema_capability(
        connection,
        version=version,
        database_path=database_path,
    )

    expected = _expected_version(expected_schema_version)
    if expected is not None and version != expected:
        raise UnsupportedSchemaError(
            database_path,
            detail_message=(
                f"SQLite snapshot at {database_path} has schema version "
                f"{version}, expected {expected}."
            ),
        )
    return version


def _resolve_source_database(path: Path | str) -> Path:
    source_input = Path(path).expanduser()
    try:
        source = source_input.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"SQLite source database does not exist: {source_input}"
        ) from exc
    if not source.is_file():
        raise ValueError(f"SQLite source database is not a file: {source}")
    for sidecar in _sidecar_paths(source):
        if _is_link_or_reparse_point(sidecar):
            raise ValueError(f"Refusing symbolic SQLite source sidecar: {sidecar}")
        if sidecar.exists() and not sidecar.is_file():
            raise ValueError(f"SQLite source sidecar is not a file: {sidecar}")
    wal_path = Path(f"{source}-wal")
    shm_path = Path(f"{source}-shm")
    if wal_path.exists() != shm_path.exists():
        raise ValueError(
            "SQLite source has an incomplete WAL/SHM sidecar pair; refusing an "
            "inconsistent snapshot."
        )
    return source


def _source_uri(source: Path) -> str:
    wal_path = Path(f"{source}-wal")
    shm_path = Path(f"{source}-shm")
    if _header_uses_wal(source) and not wal_path.exists() and not shm_path.exists():
        return f"{source.as_uri()}?mode=ro&immutable=1"
    return f"{source.as_uri()}?mode=ro"


def _prepare_destination(source: Path, destination: Path) -> None:
    _validate_destination_components(destination)
    _reject_source_alias(source, destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(
            f"Refusing to replace existing SQLite snapshot: {destination}"
        )
    _refuse_existing_destination_sidecars(destination)


def _validate_destination(source: Path, destination: Path) -> None:
    _validate_destination_components(destination)
    _reject_source_alias(source, destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(
            f"Refusing to replace existing SQLite snapshot: {destination}"
        )
    _refuse_existing_destination_sidecars(destination)


def _refuse_existing_destination_sidecars(destination: Path) -> None:
    for sidecar in _sidecar_paths(destination):
        if sidecar.exists() or sidecar.is_symlink():
            raise FileExistsError(
                "Refusing SQLite snapshot destination with an existing sidecar: "
                f"{sidecar}"
            )


def _validate_destination_components(destination: Path) -> None:
    if _is_link_or_reparse_point(destination):
        raise ValueError(f"Refusing symbolic SQLite snapshot: {destination}")
    for component in (destination.parent, *destination.parent.parents):
        if _is_link_or_reparse_point(component) and not (
            component.is_symlink() and _trusted_system_alias(component)
        ):
            raise ValueError(
                f"SQLite snapshot path contains a symbolic directory: {component}"
            )


def _reject_source_alias(source: Path, destination: Path) -> None:
    resolved_destination = destination.parent.resolve(strict=False) / destination.name
    protected = (source, *_sidecar_paths(source))
    for protected_path in protected:
        if resolved_destination == protected_path:
            raise ValueError(
                "SQLite snapshot destination aliases the live database or one "
                "of its sidecars."
            )
        if destination.exists() and protected_path.exists():
            try:
                if os.path.samefile(destination, protected_path):
                    raise ValueError(
                        "SQLite snapshot destination aliases the live database "
                        "or one of its sidecars."
                    )
            except FileNotFoundError:
                pass


def _expected_version(value: int | str | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("expected_schema_version must be an integer.")
    try:
        version = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("expected_schema_version must be an integer.") from exc
    if str(value).strip() != str(version):
        raise ValueError("expected_schema_version must be an integer.")
    return version


def _header_uses_wal(database_path: Path) -> bool:
    with database_path.open("rb") as handle:
        header = handle.read(20)
    return (
        len(header) >= 20
        and header.startswith(_SQLITE_HEADER)
        and (header[18] == 2 or header[19] == 2)
    )


def _sidecar_paths(database_path: Path) -> tuple[Path, ...]:
    return tuple(
        Path(f"{database_path}{suffix}") for suffix in _SQLITE_SIDECAR_SUFFIXES
    )


def _remove_snapshot_files(database_path: Path) -> None:
    failures: list[OSError] = []
    for path in (*_sidecar_paths(database_path), database_path):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            failures.append(exc)
    if failures:
        raise OSError(
            f"Could not remove incomplete SQLite snapshot at {database_path}."
        ) from failures[0]


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        # Some Windows/filesystem combinations do not support directory fsync.
        pass
    finally:
        os.close(descriptor)


def _absolute_path(path: Path | str) -> Path:
    return Path(os.path.abspath(Path(path).expanduser()))


def _trusted_system_alias(path: Path) -> bool:
    if path not in {Path("/tmp"), Path("/var")}:
        return False
    try:
        return path.lstat().st_uid == 0
    except OSError:
        return False


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
