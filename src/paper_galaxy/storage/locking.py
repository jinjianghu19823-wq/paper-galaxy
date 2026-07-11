"""Cross-process advisory locks for Paper Galaxy project maintenance."""

from __future__ import annotations

import contextlib
import errno
import os
import stat
from collections.abc import Iterator
from pathlib import Path

from paper_galaxy.errors import DatabaseLockedError

PROJECT_LOCK_RELATIVE_PATH = Path(".paper-galaxy/project.lock")
PROJECT_LOCK_MARKER = b"paper-galaxy-project-lock-v1\n"
RESTORE_TRANSACTION_SUFFIX = ".paper-galaxy-restore-transaction"


class ProjectLockSet:
    """One or more advisory locks released together in reverse order."""

    def __init__(self, locks: list[_AdvisoryLock]) -> None:
        self._locks = locks

    def close(self) -> None:
        locks, self._locks = self._locks, []
        for lock in reversed(locks):
            lock.close()


class _AdvisoryLock:
    def __init__(self, descriptor: int, *, exclusive: bool) -> None:
        self._descriptor = descriptor
        self._exclusive = exclusive

    def close(self) -> None:
        descriptor, self._descriptor = self._descriptor, -1
        if descriptor < 0:
            return
        try:
            _unlock_descriptor(descriptor, exclusive=self._exclusive)
        finally:
            os.close(descriptor)


def acquire_shared_project_locks(
    project_dir: Path | str,
    database_path: Path,
    *,
    create_marker: bool,
) -> ProjectLockSet:
    """Acquire the shared locks held by one operational project connection.

    Current writers and migration/bootstrap connections create the build-owned
    marker.  A read-only connection never creates it.  Legacy projects without
    a marker fall back to locking the existing database file.  We do not lock
    both during normal access because whole-file locks can conflict with
    SQLite's byte-range locks on macOS.
    """

    resolved_project = Path(project_dir).expanduser().resolve()
    _refuse_pending_restore(resolved_project, database_path=database_path)
    marker_path = resolved_project / PROJECT_LOCK_RELATIVE_PATH
    _validate_existing_metadata_dir(
        marker_path.parent,
        database_path=database_path,
    )
    if create_marker:
        _ensure_project_lock_marker(marker_path, database_path=database_path)

    if marker_path.exists() or marker_path.is_symlink():
        return _validated_operational_locks(
            resolved_project,
            _acquire_lock_set(
                [(marker_path, True)],
                database_path=database_path,
                exclusive=False,
            ),
            database_path=database_path,
        )
    if not (database_path.exists() or database_path.is_symlink()):
        return _validated_operational_locks(
            resolved_project,
            ProjectLockSet([]),
            database_path=database_path,
        )

    legacy_lock = _acquire_lock_set(
        [(database_path, False)],
        database_path=database_path,
        exclusive=False,
    )
    if not (marker_path.exists() or marker_path.is_symlink()):
        return _validated_operational_locks(
            resolved_project,
            legacy_lock,
            database_path=database_path,
        )

    # Maintenance may have claimed the marker after this connector observed a
    # legacy project.  Switching to the stable marker closes the post-rename
    # race: a connector can never continue on a replacement DB inode while an
    # exclusive marker owner is publishing it.
    legacy_lock.close()
    return _validated_operational_locks(
        resolved_project,
        _acquire_lock_set(
            [(marker_path, True)],
            database_path=database_path,
            exclusive=False,
        ),
        database_path=database_path,
    )


def restore_transaction_path(project_dir: Path | str) -> Path:
    """Return the sibling durable-journal directory for a project."""

    project = Path(project_dir).expanduser().absolute()
    return project.parent / f".{project.name}{RESTORE_TRANSACTION_SUFFIX}"


def _validated_operational_locks(
    project_dir: Path,
    locks: ProjectLockSet,
    *,
    database_path: Path,
) -> ProjectLockSet:
    try:
        _refuse_pending_restore(project_dir, database_path=database_path)
    except BaseException:
        locks.close()
        raise
    return locks


def _refuse_pending_restore(project_dir: Path, *, database_path: Path) -> None:
    transaction = restore_transaction_path(project_dir)
    if transaction.exists() or transaction.is_symlink():
        raise DatabaseLockedError(
            database_path,
            safe_message=(
                "An interrupted project restore requires recovery. Retry the "
                "same import before opening or modifying this project."
            ),
            detail_message=(
                "Normal project access is blocked because an interrupted durable "
                "restore requires recovery; retry the same import first."
            ),
        )


@contextlib.contextmanager
def exclusive_project_maintenance_lock(
    project_dir: Path | str,
) -> Iterator[None]:
    """Refuse concurrent project connections during destructive maintenance.

    Acquisition is non-blocking: callers receive ``DatabaseLockedError`` and
    can leave the existing database and user files byte-for-byte unchanged.
    Existing legacy project state is claimed with the build-owned marker so
    connections opened after a database rename still see the maintenance gate.
    Callers must invoke this context only for real maintenance, never dry-run.
    """

    # The local import avoids a module cycle: sqlite connections consume the
    # shared-lock primitive defined above.
    from paper_galaxy.storage.sqlite import (
        DEFAULT_DATABASE_PATH,
        resolve_database_path,
    )

    resolved_project = Path(project_dir).expanduser().resolve()
    try:
        database_path = resolve_database_path(resolved_project)
    except Exception:
        # Disaster recovery must still serialize replacement of the standard
        # database when project.toml is malformed or unreadable.  The restore
        # layer validates/rebuilds configuration separately before publishing.
        database_path = (resolved_project / DEFAULT_DATABASE_PATH).resolve()
    marker_path = resolved_project / PROJECT_LOCK_RELATIVE_PATH
    project_preexisted = resolved_project.exists()
    metadata_preexisted = marker_path.parent.exists() or marker_path.parent.is_symlink()
    marker_preexisted = marker_path.exists() or marker_path.is_symlink()
    database_preexisted = database_path.exists() or database_path.is_symlink()
    _validate_existing_metadata_dir(
        marker_path.parent,
        database_path=database_path,
    )
    # Real maintenance always claims the stable marker, including for a target
    # that does not exist yet.  Otherwise a concurrent initializer could enter
    # after restore preflight and keep using the inode that publication replaces.
    _ensure_project_lock_marker(marker_path, database_path=database_path)
    locks = _acquire_lock_set(
        [(marker_path, True)],
        database_path=database_path,
        exclusive=True,
    )
    completed = False
    try:
        if database_path.exists() or database_path.is_symlink():
            # Drain pre-marker legacy connections, then release this whole-file
            # lock before publication.  Retaining it can prevent os.replace on
            # Windows; the stable exclusive marker blocks all new connectors.
            legacy_drain = _acquire_lock_set(
                [(database_path, False)],
                database_path=database_path,
                exclusive=True,
            )
            legacy_drain.close()
        yield
        completed = True
    finally:
        remove_failed_claim = (
            not completed
            and not marker_preexisted
            and not metadata_preexisted
            and not database_preexisted
        )
        if remove_failed_claim and os.name != "nt":
            _remove_failed_new_project_claim(
                resolved_project,
                marker_path,
                project_preexisted=project_preexisted,
            )
        locks.close()
        if remove_failed_claim and os.name == "nt":
            # Windows does not permit unlinking the open locked marker.  Normal
            # connectors recreate/lock it atomically if they win this narrow
            # post-close race, causing cleanup to retain their gate.
            _remove_failed_new_project_claim(
                resolved_project,
                marker_path,
                project_preexisted=project_preexisted,
            )


def _ensure_project_lock_marker(
    marker_path: Path,
    *,
    database_path: Path,
) -> None:
    metadata_dir = marker_path.parent
    _validate_existing_metadata_dir(metadata_dir, database_path=database_path)
    metadata_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _is_link_or_reparse_point(metadata_dir) or not metadata_dir.is_dir():
        raise _unsafe_lock_error(
            database_path,
            "Project metadata directory became a symbolic link or reparse point.",
        )

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= int(getattr(os, "O_CLOEXEC", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    flags |= int(getattr(os, "O_BINARY", 0))
    try:
        descriptor = os.open(marker_path, flags, 0o600)
    except FileExistsError:
        return
    except OSError as exc:
        raise _unsafe_lock_error(
            database_path,
            f"Project lock marker could not be created safely: {exc}",
        ) from exc
    created = os.fstat(descriptor)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        remaining = memoryview(PROJECT_LOCK_MARKER)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short write while creating project lock marker")
            remaining = remaining[written:]
        os.fsync(descriptor)
    except BaseException:
        try:
            current = marker_path.lstat()
            if not _is_link_or_reparse_point(marker_path) and _same_file(
                created, current
            ):
                marker_path.unlink()
        except OSError:
            pass
        raise
    finally:
        os.close(descriptor)


def _validate_existing_metadata_dir(
    metadata_dir: Path,
    *,
    database_path: Path,
) -> None:
    if _is_link_or_reparse_point(metadata_dir):
        raise _unsafe_lock_error(
            database_path,
            "Project metadata directory is a symbolic link or reparse point.",
        )
    if metadata_dir.exists() and not metadata_dir.is_dir():
        raise _unsafe_lock_error(
            database_path,
            "Project metadata path is not a directory.",
        )


def _remove_failed_new_project_claim(
    project_dir: Path,
    marker_path: Path,
    *,
    project_preexisted: bool,
) -> None:
    """Remove only the empty marker skeleton created by failed maintenance."""

    try:
        metadata_dir = marker_path.parent
        metadata_entries = list(metadata_dir.iterdir())
        if metadata_entries != [marker_path]:
            return
        if not project_preexisted and list(project_dir.iterdir()) != [metadata_dir]:
            return
        if _is_link_or_reparse_point(marker_path) or not marker_path.is_file():
            return
        with marker_path.open("rb") as handle:
            if handle.read(len(PROJECT_LOCK_MARKER) + 1) != PROJECT_LOCK_MARKER:
                return
        marker_path.unlink()
        marker_path.parent.rmdir()
        if not project_preexisted:
            project_dir.rmdir()
    except OSError:
        # Concurrent or unrelated files are never removed.  A supported marker
        # can safely remain as a recoverable maintenance claim.
        return


def _acquire_lock_set(
    paths: list[tuple[Path, bool]],
    *,
    database_path: Path,
    exclusive: bool,
) -> ProjectLockSet:
    acquired: list[_AdvisoryLock] = []
    try:
        for path, is_marker in paths:
            acquired.append(
                _acquire_path_lock(
                    path,
                    database_path=database_path,
                    exclusive=exclusive,
                    is_marker=is_marker,
                )
            )
    except BaseException:
        ProjectLockSet(acquired).close()
        raise
    return ProjectLockSet(acquired)


def _acquire_path_lock(
    path: Path,
    *,
    database_path: Path,
    exclusive: bool,
    is_marker: bool,
) -> _AdvisoryLock:
    if _is_link_or_reparse_point(path):
        raise _unsafe_lock_error(
            database_path,
            f"Project lock path is a symbolic link or reparse point: {path}",
        )
    try:
        before = path.lstat()
    except OSError as exc:
        raise _unsafe_lock_error(
            database_path,
            f"Project lock path could not be inspected safely: {path}",
        ) from exc
    if not stat.S_ISREG(before.st_mode):
        raise _unsafe_lock_error(
            database_path,
            f"Project lock path is not a regular file: {path}",
        )

    flags = os.O_RDWR if exclusive else os.O_RDONLY
    flags |= int(getattr(os, "O_CLOEXEC", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    flags |= int(getattr(os, "O_BINARY", 0))
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _unsafe_lock_error(
            database_path,
            f"Project lock path could not be opened safely: {path}",
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not _same_file(before, opened):
            raise _unsafe_lock_error(
                database_path,
                f"Project lock path changed while it was being opened: {path}",
            )
        if is_marker:
            _validate_marker_descriptor(
                descriptor,
                opened,
                database_path=database_path,
            )
        _lock_descriptor(
            descriptor,
            exclusive=exclusive,
            database_path=database_path,
        )
        after = path.lstat()
        if _is_link_or_reparse_point(path) or not _same_file(opened, after):
            raise _unsafe_lock_error(
                database_path,
                f"Project lock path changed during lock acquisition: {path}",
            )
        return _AdvisoryLock(descriptor, exclusive=exclusive)
    except BaseException:
        os.close(descriptor)
        raise


def _validate_marker_descriptor(
    descriptor: int,
    status: os.stat_result,
    *,
    database_path: Path,
) -> None:
    if os.name != "nt" and stat.S_IMODE(status.st_mode) != 0o600:
        raise _unsafe_lock_error(
            database_path,
            "Project lock marker must have owner-only mode 0600.",
        )
    os.lseek(descriptor, 0, os.SEEK_SET)
    content = os.read(descriptor, len(PROJECT_LOCK_MARKER) + 1)
    os.lseek(descriptor, 0, os.SEEK_SET)
    if content != PROJECT_LOCK_MARKER:
        raise _unsafe_lock_error(
            database_path,
            "Project lock marker is not owned by a supported Paper Galaxy build.",
        )


def _lock_descriptor(
    descriptor: int,
    *,
    exclusive: bool,
    database_path: Path,
) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            mode_name = "LK_NBLCK" if exclusive else "LK_NBRLCK"
            mode = int(getattr(msvcrt, mode_name))
            msvcrt.locking(descriptor, mode, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            raise
        if exclusive:
            safe_message = (
                "Another Paper Galaxy process is using this project. Close it "
                "and retry the maintenance operation."
            )
            detail_message = (
                "Project maintenance lock could not be acquired because another "
                "Paper Galaxy connection is active."
            )
        else:
            safe_message = (
                "Paper Galaxy project maintenance is active. Wait for it to "
                "finish and try again."
            )
            detail_message = (
                "Normal project access was blocked because project maintenance "
                "holds the exclusive project lock."
            )
        raise DatabaseLockedError(
            database_path,
            safe_message=safe_message,
            detail_message=detail_message,
        ) from exc


def _unlock_descriptor(descriptor: int, *, exclusive: bool) -> None:
    del exclusive
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            mode = msvcrt.LK_UNLCK  # type: ignore[attr-defined]
            msvcrt.locking(descriptor, mode, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        # Closing the descriptor also releases the operating-system lock.  The
        # close must not mask the SQLite exception a caller may already hold.
        pass


def _unsafe_lock_error(database_path: Path, detail: str) -> DatabaseLockedError:
    return DatabaseLockedError(
        database_path,
        safe_message=(
            "The Paper Galaxy project lock is unsafe or unavailable. Restore the "
            "build-owned lock path or open the project with a writer and retry."
        ),
        detail_message=detail,
    )


def _same_file(first: os.stat_result, second: os.stat_result) -> bool:
    return first.st_dev == second.st_dev and first.st_ino == second.st_ino


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
