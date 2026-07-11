"""Owned private staging directories with conservative orphan recovery."""

from __future__ import annotations

import errno
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import TypedDict

_STAGING_FORMAT = "paper-galaxy-private-staging-v1"
_STAGING_MARKER = ".paper-galaxy-staging.json"
_MAX_MARKER_BYTES = 4096
_SUPPORTED_OPERATIONS = {"backup-export", "backup-inspect", "project-restore"}


class _StagingMarker(TypedDict):
    format: str
    operation: str
    pid: int
    target: str


def create_owned_staging(
    *,
    parent: Path,
    prefix: str,
    operation: str,
    target: Path,
) -> Path:
    """Clean dead owned siblings, then create and mark a private staging root."""

    parent.mkdir(parents=True, exist_ok=True)
    cleanup_orphaned_staging(
        parent=parent,
        prefix=prefix,
        operation=operation,
        target=target,
    )
    root = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    try:
        if os.name != "nt":
            root.chmod(0o700)
        _write_marker(root, operation=operation, target=target)
        _fsync_directory(parent)
        return root
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        _fsync_directory(parent)
        raise


def cleanup_orphaned_staging(
    *,
    parent: Path,
    prefix: str,
    operation: str,
    target: Path,
) -> int:
    """Delete only dead staging roots carrying an exact supported ownership marker."""

    if not parent.is_dir() or _is_link_or_reparse_point(parent):
        return 0
    expected_target = str(target.absolute())
    removed = 0
    for candidate in parent.iterdir():
        if not candidate.name.startswith(prefix):
            continue
        before = _safe_private_directory_status(candidate)
        if before is None:
            continue
        marker = _load_marker(candidate)
        if marker is None:
            continue
        if marker["operation"] != operation or marker["target"] != expected_target:
            continue
        if _process_is_alive(marker["pid"]):
            continue
        try:
            after = candidate.lstat()
        except OSError:
            continue
        if not _same_file(before, after) or _is_link_or_reparse_point(candidate):
            continue
        shutil.rmtree(candidate)
        _fsync_directory(parent)
        removed += 1
    return removed


def _write_marker(root: Path, *, operation: str, target: Path) -> None:
    if operation not in _SUPPORTED_OPERATIONS:
        raise ValueError("Private staging operation is unsupported.")
    content = json.dumps(
        {
            "format": _STAGING_FORMAT,
            "operation": operation,
            "pid": os.getpid(),
            "target": str(target.absolute()),
        },
        sort_keys=True,
    ).encode("utf-8")
    marker = root / _STAGING_MARKER
    descriptor = os.open(
        marker,
        os.O_CREAT
        | os.O_EXCL
        | os.O_WRONLY
        | int(getattr(os, "O_CLOEXEC", 0))
        | int(getattr(os, "O_NOFOLLOW", 0)),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(root)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _load_marker(root: Path) -> _StagingMarker | None:
    marker = root / _STAGING_MARKER
    descriptor = -1
    try:
        if _is_link_or_reparse_point(marker):
            return None
        before = marker.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_MARKER_BYTES:
            return None
        getuid = getattr(os, "getuid", None)
        if callable(getuid) and before.st_uid != getuid():
            return None
        if os.name != "nt" and stat.S_IMODE(before.st_mode) & 0o077:
            return None
        descriptor = os.open(
            marker,
            os.O_RDONLY
            | int(getattr(os, "O_CLOEXEC", 0))
            | int(getattr(os, "O_NOFOLLOW", 0)),
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not _same_file(before, opened):
            return None
        content = os.read(descriptor, _MAX_MARKER_BYTES + 1)
        if len(content) > _MAX_MARKER_BYTES:
            return None
        after = marker.lstat()
        if _is_link_or_reparse_point(marker) or not _same_file(opened, after):
            return None
        decoded = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(decoded, dict) or set(decoded) != {
        "format",
        "operation",
        "pid",
        "target",
    }:
        return None
    if decoded["format"] != _STAGING_FORMAT:
        return None
    operation = decoded["operation"]
    if not isinstance(operation, str) or operation not in _SUPPORTED_OPERATIONS:
        return None
    pid = decoded["pid"]
    target = decoded["target"]
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    if not isinstance(target, str) or not target:
        return None
    result: _StagingMarker = {
        "format": _STAGING_FORMAT,
        "operation": operation,
        "pid": pid,
        "target": target,
    }
    return result


def _safe_private_directory_status(path: Path) -> os.stat_result | None:
    try:
        if _is_link_or_reparse_point(path) or not path.is_dir():
            return None
        status = path.stat()
    except OSError:
        return None
    getuid = getattr(os, "getuid", None)
    if callable(getuid) and status.st_uid != getuid():
        return None
    if os.name != "nt" and stat.S_IMODE(status.st_mode) & 0o077:
        return None
    return status


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


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
