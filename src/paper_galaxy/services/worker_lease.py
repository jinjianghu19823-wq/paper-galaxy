"""Cross-process lease for the single local background job worker."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from uuid import uuid4

WORKER_LOCK_RELATIVE_PATH = Path(".paper-galaxy/job-worker.lock")
WORKER_LOCK_MARKER = b"paper-galaxy-job-worker-lock-v1\n"


class JobWorkerLeaseError(RuntimeError):
    """Raised when a project cannot safely start one exclusive worker."""


class JobWorkerLease:
    """Exclusive advisory lease released when its owning process stops."""

    def __init__(
        self,
        descriptor: int,
        *,
        path: Path,
        metadata_identity: os.stat_result,
    ) -> None:
        self._descriptor = descriptor
        self._path = path
        self._metadata_identity = metadata_identity

    def is_current(self) -> bool:
        """Return false if the lock path or its metadata directory was replaced."""

        if self._descriptor < 0:
            return False
        try:
            opened = os.fstat(self._descriptor)
            path_now = self._path.lstat()
            metadata_now = self._path.parent.lstat()
        except OSError:
            return False
        return (
            not _is_link_or_reparse_point(self._path)
            and not _is_link_or_reparse_point(self._path.parent)
            and os.path.samestat(opened, path_now)
            and os.path.samestat(self._metadata_identity, metadata_now)
        )

    def close(self) -> None:
        descriptor, self._descriptor = self._descriptor, -1
        if descriptor < 0:
            return
        try:
            _unlock_descriptor(descriptor)
        finally:
            os.close(descriptor)


def acquire_job_worker_lease(project_dir: Path | str) -> JobWorkerLease:
    """Acquire the one background-worker lease for a project without waiting."""

    project = Path(project_dir).expanduser().resolve()
    metadata = project / ".paper-galaxy"
    if _is_link_or_reparse_point(metadata) or not metadata.is_dir():
        raise JobWorkerLeaseError("Project metadata is unavailable or unsafe for jobs.")
    metadata_identity = metadata.lstat()
    path = project / WORKER_LOCK_RELATIVE_PATH
    _ensure_worker_marker(path)
    if _is_link_or_reparse_point(path):
        raise JobWorkerLeaseError("Job worker lock must not be a symbolic link.")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise JobWorkerLeaseError("Job worker lock must be a regular file.")
    if os.name != "nt" and stat.S_IMODE(before.st_mode) != 0o600:
        raise JobWorkerLeaseError("Job worker lock must have owner-only mode 0600.")

    flags = os.O_RDWR
    flags |= int(getattr(os, "O_CLOEXEC", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    flags |= int(getattr(os, "O_BINARY", 0))
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
            raise JobWorkerLeaseError("Job worker lock changed while it was opened.")
        os.lseek(descriptor, 0, os.SEEK_SET)
        content = os.read(descriptor, len(WORKER_LOCK_MARKER) + 1)
        if content != WORKER_LOCK_MARKER:
            raise JobWorkerLeaseError("Job worker lock has an unsupported marker.")
        _lock_descriptor(descriptor)
        after = path.lstat()
        if _is_link_or_reparse_point(path) or not os.path.samestat(opened, after):
            raise JobWorkerLeaseError(
                "Job worker lock changed during lease acquisition."
            )
        metadata_after = metadata.lstat()
        if _is_link_or_reparse_point(metadata) or not os.path.samestat(
            metadata_identity, metadata_after
        ):
            raise JobWorkerLeaseError(
                "Project metadata changed during worker lease acquisition."
            )
        return JobWorkerLease(
            descriptor,
            path=path,
            metadata_identity=metadata_identity,
        )
    except BaseException:
        os.close(descriptor)
        raise


def _ensure_worker_marker(path: Path) -> None:
    if path.exists() or path.is_symlink():
        return
    staging = path.with_name(f".{path.name}.{uuid4().hex}.staging")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= int(getattr(os, "O_CLOEXEC", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    flags |= int(getattr(os, "O_BINARY", 0))
    try:
        descriptor = os.open(staging, flags, 0o600)
        try:
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            remaining = memoryview(WORKER_LOCK_MARKER)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("short write while creating job worker lock")
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(staging, path)
        except FileExistsError:
            return
        _fsync_directory(path.parent)
    finally:
        staging.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EBADF, errno.EINVAL, errno.ENOTSUP}:
            return
        raise
    try:
        os.fsync(descriptor)
    except OSError as exc:
        if exc.errno not in {
            errno.EACCES,
            errno.EBADF,
            errno.EINVAL,
            errno.ENOTSUP,
        }:
            raise
    finally:
        os.close(descriptor)


def _lock_descriptor(descriptor: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            raise JobWorkerLeaseError(
                "A background job worker is already active for this project."
            ) from exc
        raise


def _unlock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _is_link_or_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
    except OSError:
        return False
    reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return bool(reparse and attributes & reparse)
