"""SQLite connection and project database path helpers."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from paper_galaxy.config import load_project_config
from paper_galaxy.errors import (
    DatabaseCorruptError,
    DatabaseError,
    DatabaseLockedError,
    DatabaseNeedsMigrationError,
    DatabaseNeedsWriterNormalizationError,
    DatabaseNotFoundError,
    FutureSchemaError,
    UnsupportedSchemaError,
)
from paper_galaxy.storage.locking import (
    ProjectLockSet,
    acquire_shared_project_locks,
)
from paper_galaxy.storage.migrations import (
    CURRENT_SCHEMA_VERSION,
    OLDEST_SUPPORTED_SCHEMA_VERSION,
    initialize_database,
    read_schema_version,
    validate_schema_capability,
)

DEFAULT_DATABASE_PATH = ".paper-galaxy/paper_galaxy.sqlite3"


class _ProjectConnection(sqlite3.Connection):
    """SQLite connection that owns project advisory locks until close."""

    _paper_galaxy_project_locks: ProjectLockSet | None = None

    def attach_project_locks(self, locks: ProjectLockSet) -> None:
        self._paper_galaxy_project_locks = locks

    def close(self) -> None:
        locks = self._paper_galaxy_project_locks
        try:
            super().close()
        finally:
            self._paper_galaxy_project_locks = None
            if locks is not None:
                locks.close()

    def __del__(self) -> None:
        try:
            self.close()
        except BaseException:
            pass


def resolve_database_path(project_dir: Path | str) -> Path:
    """Resolve the SQLite path from project config or the Phase 2 default."""

    resolved_project_dir = Path(project_dir).expanduser().resolve()
    config = load_project_config(resolved_project_dir)
    configured_path = (
        config.database_path if config is not None else DEFAULT_DATABASE_PATH
    )
    database_path = Path(configured_path).expanduser()
    if not database_path.is_absolute():
        database_path = resolved_project_dir / database_path
    return database_path.resolve()


def connect_read_only(project_dir: Path | str) -> sqlite3.Connection:
    """Open an existing current-schema database without write capability."""

    return _connect_project_read_only(project_dir, require_operational_schema=True)


def connect_diagnostic_read_only(project_dir: Path | str) -> sqlite3.Connection:
    """Open a project read-only so validation can inspect an incomplete schema.

    This is not an operational connection: it checks the declared version and
    refuses future databases, but leaves current-schema capability reporting to
    the caller.
    """

    return _connect_project_read_only(project_dir, require_operational_schema=False)


def _connect_project_read_only(
    project_dir: Path | str,
    *,
    require_operational_schema: bool,
) -> sqlite3.Connection:
    database_path = resolve_database_path(project_dir)
    _require_existing_database(database_path)
    _guard_read_only_sidecars(database_path)
    locks = acquire_shared_project_locks(
        project_dir,
        database_path,
        create_marker=False,
    )
    connection = _connect_uri(database_path, mode="ro", project_locks=locks)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        if require_operational_schema:
            _validate_operational_schema(connection, database_path)
        else:
            _validate_diagnostic_schema_version(connection, database_path)
        return connection
    except DatabaseError:
        connection.close()
        raise
    except sqlite3.Error as exc:
        connection.close()
        raise _translate_sqlite_error(database_path, exc) from exc
    except BaseException:
        connection.close()
        raise


def connect_external_read_only(database_path: Path | str) -> sqlite3.Connection:
    """Open an external SQLite database without creating files or project schema."""

    resolved = Path(database_path).expanduser().resolve()
    _require_existing_database(resolved)
    _guard_read_only_sidecars(resolved, external=True)
    connection = _connect_uri(resolved, mode="ro")
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone()
        return connection
    except DatabaseError:
        connection.close()
        raise
    except sqlite3.Error as exc:
        connection.close()
        raise _translate_sqlite_error(resolved, exc) from exc
    except BaseException:
        connection.close()
        raise


def connect_read_write(project_dir: Path | str) -> sqlite3.Connection:
    """Open an existing current-schema database for short writer transactions.

    Rollback-journal ``DELETE`` mode lets true read-only connections avoid
    creating WAL/SHM files. ``synchronous=FULL`` favors local research-data
    durability; short single-writer transactions keep lock intervals bounded.
    Backups still use SQLite's online backup API rather than copying live files.
    """

    database_path = resolve_database_path(project_dir)
    _require_existing_database(database_path)
    locks = acquire_shared_project_locks(
        project_dir,
        database_path,
        create_marker=True,
    )
    connection = _connect_uri(database_path, mode="rw", project_locks=locks)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        _validate_operational_schema(connection, database_path)
        _configure_writer_journal(connection, database_path)
        return connection
    except DatabaseError:
        connection.close()
        raise
    except sqlite3.Error as exc:
        connection.close()
        raise _translate_sqlite_error(database_path, exc) from exc
    except BaseException:
        connection.close()
        raise


def connect_migration(project_dir: Path | str) -> sqlite3.Connection:
    """Open the sole connection type allowed to create/bootstrap a database."""

    database_path = resolve_database_path(project_dir)
    locks = acquire_shared_project_locks(
        project_dir,
        database_path,
        create_marker=True,
    )
    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            database_path,
            os.O_CREAT | os.O_EXCL | os.O_RDWR,
            0o600,
        )
    except FileExistsError:
        pass
    except BaseException:
        locks.close()
        raise
    else:
        os.close(descriptor)
    try:
        connection = sqlite3.connect(database_path, factory=_ProjectConnection)
    except sqlite3.OperationalError as exc:
        locks.close()
        raise _translate_sqlite_error(database_path, exc) from exc
    connection.attach_project_locks(locks)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        _preflight_migration_schema(connection, database_path)
        return connection
    except DatabaseError:
        connection.close()
        raise
    except sqlite3.Error as exc:
        connection.close()
        raise _translate_sqlite_error(database_path, exc) from exc


def connect_database(project_dir: Path | str) -> sqlite3.Connection:
    """Compatibility alias for the explicit migration/bootstrap connection."""

    return connect_migration(project_dir)


def ensure_database_ready(
    project_dir: Path | str,
    *,
    create: bool = True,
) -> Path:
    """Bootstrap or migrate a project database, then close the migration handle."""

    database_path = resolve_database_path(project_dir)
    if not create:
        _require_existing_database(database_path)
    connection = connect_migration(project_dir)
    try:
        try:
            initialize_database(connection)
            _configure_writer_journal(connection, database_path)
        except DatabaseError:
            raise
        except sqlite3.Error as exc:
            raise _translate_sqlite_error(database_path, exc) from exc
    finally:
        connection.close()
    return database_path


def _connect_uri(
    database_path: Path,
    *,
    mode: str,
    project_locks: ProjectLockSet | None = None,
) -> _ProjectConnection:
    uri = f"{database_path.as_uri()}?mode={mode}"
    try:
        connection = sqlite3.connect(
            uri,
            uri=True,
            factory=_ProjectConnection,
        )
    except sqlite3.OperationalError as exc:
        if project_locks is not None:
            project_locks.close()
        raise _translate_sqlite_error(database_path, exc) from exc
    if project_locks is not None:
        connection.attach_project_locks(project_locks)
    connection.row_factory = sqlite3.Row
    return connection


def _require_existing_database(database_path: Path) -> None:
    if not database_path.is_file():
        raise DatabaseNotFoundError(database_path)


def _guard_read_only_sidecars(
    database_path: Path,
    *,
    external: bool = False,
) -> None:
    wal_path = Path(f"{database_path}-wal")
    shm_path = Path(f"{database_path}-shm")
    if wal_path.is_symlink() or shm_path.is_symlink():
        raise DatabaseCorruptError(
            database_path,
            detail_message=(
                f"Refusing symbolic SQLite sidecar files for database at "
                f"{database_path}."
            ),
        )
    wal_exists = wal_path.exists()
    shm_exists = shm_path.exists()
    if wal_exists != shm_exists or (
        wal_exists and (not wal_path.is_file() or not shm_path.is_file())
    ):
        raise DatabaseLockedError(
            database_path,
            safe_message=(
                "Open Zotero Desktop and retry diagnostics; Paper Galaxy will not "
                "modify this database."
                if external
                else "The database has an incomplete WAL snapshot. Open the "
                "original project or restore it with SQLite's backup API."
            ),
            detail_message=(
                f"Database at {database_path} has an incomplete WAL/SHM sidecar "
                "pair; refusing a read that would create files."
            ),
        )
    if not wal_exists and _database_header_uses_wal(database_path):
        raise DatabaseNeedsWriterNormalizationError(
            database_path,
            safe_message=(
                "Open Zotero Desktop and retry diagnostics; Paper Galaxy will not "
                "modify this database."
                if external
                else None
            ),
            detail_message=(
                f"Database at {database_path} is in WAL mode without owned WAL/SHM "
                "sidecars; refusing a read that would create them."
            ),
        )


def _database_header_uses_wal(database_path: Path) -> bool:
    with database_path.open("rb") as handle:
        header = handle.read(20)
    return (
        len(header) >= 20
        and header.startswith(b"SQLite format 3\x00")
        and (header[18] == 2 or header[19] == 2)
    )


def _configure_writer_journal(
    connection: sqlite3.Connection,
    database_path: Path,
) -> None:
    row = connection.execute("PRAGMA journal_mode = DELETE").fetchone()
    if row is None or str(row[0]).lower() != "delete":
        raise DatabaseLockedError(
            database_path,
            detail_message=(
                f"Database at {database_path} could not be normalized to the "
                "rollback journal while another connection is active."
            ),
        )
    connection.execute("PRAGMA synchronous = FULL")


def _preflight_migration_schema(
    connection: sqlite3.Connection,
    database_path: Path,
) -> None:
    tables = connection.execute(
        """
        SELECT name
        FROM sqlite_schema
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        """
    ).fetchall()
    if not tables:
        return
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
                f"Database at {database_path} uses unsupported schema version "
                f"{version}; the oldest supported version is "
                f"{OLDEST_SUPPORTED_SCHEMA_VERSION}."
            ),
        )
    validate_schema_capability(
        connection,
        version=version,
        database_path=database_path,
    )


def _validate_operational_schema(
    connection: sqlite3.Connection,
    database_path: Path,
) -> None:
    try:
        version = read_schema_version(connection)
    except UnsupportedSchemaError:
        raise
    except sqlite3.Error as exc:
        raise _translate_sqlite_error(database_path, exc) from exc
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
                f"Database at {database_path} uses unsupported schema version "
                f"{version}; the oldest supported version is "
                f"{OLDEST_SUPPORTED_SCHEMA_VERSION}."
            ),
        )
    if version < CURRENT_SCHEMA_VERSION:
        raise DatabaseNeedsMigrationError(
            database_path,
            found_version=version,
            current_version=CURRENT_SCHEMA_VERSION,
        )
    validate_schema_capability(
        connection,
        version=version,
        database_path=database_path,
    )


def _validate_diagnostic_schema_version(
    connection: sqlite3.Connection,
    database_path: Path,
) -> None:
    try:
        version = read_schema_version(connection)
    except UnsupportedSchemaError:
        raise
    except sqlite3.Error as exc:
        raise _translate_sqlite_error(database_path, exc) from exc
    if version > CURRENT_SCHEMA_VERSION:
        raise FutureSchemaError(
            database_path,
            found_version=version,
            current_version=CURRENT_SCHEMA_VERSION,
        )


def _translate_sqlite_error(
    database_path: Path,
    exc: sqlite3.Error,
) -> Exception:
    message = str(exc).lower()
    if "locked" in message or "busy" in message:
        return DatabaseLockedError(
            database_path,
            detail_message=f"Database at {database_path} is locked: {exc}",
        )
    if "not a database" in message or "malformed" in message or "corrupt" in message:
        return DatabaseCorruptError(
            database_path,
            detail_message=f"Database at {database_path} is unreadable: {exc}",
        )
    return DatabaseCorruptError(
        database_path,
        detail_message=f"SQLite could not open database at {database_path}: {exc}",
    )
