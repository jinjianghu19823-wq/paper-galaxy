"""Failure-atomic publication helpers for local backup artifacts."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import stat
import unicodedata
from collections.abc import Iterable
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import TypedDict

from paper_galaxy.storage.locking import (
    PROJECT_LOCK_MARKER,
    PROJECT_LOCK_RELATIVE_PATH,
    restore_transaction_path,
)

_RESTORE_TRANSACTION_FORMAT = "paper-galaxy-restore-transaction-v1"
_RESTORE_JOURNAL = "transaction.json"
_MAX_PORTABLE_PATH_BYTES = 1024
_MAX_PORTABLE_COMPONENT_BYTES = 255


class _TransactionEntry(TypedDict):
    path: str
    original_existed: bool
    original_sha256: str | None
    new_sha256: str
    created_parents: list[str]


class _TransactionJournal(TypedDict):
    format: str
    pid: int
    state: str
    target: str
    files: tuple[_TransactionEntry, ...]


def absolute_path_without_resolving(path: Path) -> Path:
    """Return an absolute path while preserving visible symbolic links."""

    return Path(os.path.abspath(path.expanduser()))


def validate_safe_destination(path: Path, *, kind: str) -> Path:
    """Reject destinations whose existing path components are symbolic links."""

    destination = absolute_path_without_resolving(path)
    for component in (destination, *destination.parents):
        if _untrusted_link_or_reparse(component):
            raise ValueError(
                f"Refusing {kind}: destination path contains a symbolic link "
                f"component ({component})."
            )
    if destination.exists() and not destination.is_file():
        raise ValueError(f"Refusing {kind}: destination is not a regular file.")
    return destination


def publish_file(staged_path: Path, destination: Path, *, kind: str) -> Path:
    """Atomically replace one file after it has been completely validated."""

    target = validate_safe_destination(destination, kind=kind)
    target.parent.mkdir(parents=True, exist_ok=True)
    validate_safe_destination(target, kind=kind)
    if staged_path.parent.stat().st_dev != target.parent.stat().st_dev:
        raise ValueError(f"Refusing {kind}: staging must use the target filesystem.")
    os.replace(staged_path, target)
    _fsync_directory(target.parent)
    return target


def publish_project_tree(
    *,
    staged_project: Path,
    project_dir: Path,
    relative_files: Iterable[str],
    remove_relative_files: Iterable[str] = (),
    force: bool,
    allow_maintenance_marker: bool = False,
) -> None:
    """Publish a validated staged project with in-process rollback.

    A new project is installed with one directory rename.  When the target
    directory already exists, every replaced or removed file is first renamed
    into a transaction-local rollback tree.  Any failure restores those same
    files before the exception is returned to the caller.
    """

    target, files, removals = _project_publish_plan(
        project_dir=project_dir,
        relative_files=relative_files,
        remove_relative_files=remove_relative_files,
        force=force,
        allow_maintenance_marker=allow_maintenance_marker,
    )

    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        _validate_project_root(target)
        os.replace(staged_project, target)
        _fsync_directory(target.parent)
        return
    entries = _transaction_entries(target, staged_project, files)
    transaction_root = _transaction_root(target)
    try:
        transaction_root.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise FileExistsError(
            "Another or interrupted restore transaction exists for this project. "
            "Retry the import to recover it before publishing new state."
        ) from exc
    try:
        _write_transaction_journal(
            transaction_root,
            target=target,
            state="prepared",
            entries=entries,
        )
        # Persist the sibling transaction-root directory entry before the first
        # target rename.  Without the parent fsync, a power loss could retain a
        # published file while losing the only rollback journal.
        _fsync_directory(target.parent)
    except BaseException:
        shutil.rmtree(transaction_root, ignore_errors=True)
        _fsync_directory(target.parent)
        raise
    preserve_transaction = False
    transaction_committed = False
    try:
        for relative in _publication_order(files):
            _refuse_database_sidecars(target, removals)
            destination = target / relative
            source = staged_project / relative
            if not source.is_file() or source.is_symlink():
                raise ValueError(
                    f"Staged restore file is missing or unsafe: {relative}"
                )
            _backup_existing(
                target=target,
                relative=relative,
                destination=destination,
                transaction_root=transaction_root,
            )
            _mkdir_private(destination.parent, target)
            os.replace(source, destination)
            _fsync_directory(destination.parent)

        _write_transaction_journal(
            transaction_root,
            target=target,
            state="committed",
            entries=entries,
        )
        transaction_committed = True
        shutil.rmtree(transaction_root)
        _fsync_directory(target.parent)
    except BaseException as original_error:
        if transaction_committed:
            try:
                _recover_transaction(
                    target,
                    transaction_root,
                    ignore_live_process=True,
                )
            except BaseException:
                preserve_transaction = True
                raise RuntimeError(
                    "Restore committed successfully but its transaction marker "
                    f"could not be cleaned at {transaction_root}."
                ) from original_error
            return
        try:
            _recover_transaction(target, transaction_root, ignore_live_process=True)
        except BaseException:
            preserve_transaction = True
            raise RuntimeError(
                "Restore failed and the original project could not be fully "
                f"rolled back. Recovery files remain at {transaction_root}."
            ) from original_error
        raise
    finally:
        if not preserve_transaction and transaction_root.exists():
            shutil.rmtree(transaction_root, ignore_errors=True)
            _fsync_directory(target.parent)


def preflight_project_tree(
    *,
    project_dir: Path,
    relative_files: Iterable[str],
    remove_relative_files: Iterable[str] = (),
    force: bool,
    allow_maintenance_marker: bool = False,
) -> None:
    """Run the read-only target checks used by real restore publication."""

    _project_publish_plan(
        project_dir=project_dir,
        relative_files=relative_files,
        remove_relative_files=remove_relative_files,
        force=force,
        allow_maintenance_marker=allow_maintenance_marker,
    )


def recover_interrupted_project_restore(
    project_dir: Path,
    *,
    dry_run: bool,
) -> bool:
    """Recover a durable prepared transaction, or report it during dry-run."""

    target = absolute_path_without_resolving(project_dir)
    _validate_project_root(target)
    transaction_root = _transaction_root(target)
    if not transaction_root.exists() and not transaction_root.is_symlink():
        return False
    if dry_run:
        raise FileExistsError(
            "An interrupted restore transaction requires recovery. Run the same "
            "import without --dry-run before validating another restore."
        )
    _recover_transaction(target, transaction_root, ignore_live_process=False)
    return True


def interrupted_project_restore_exists(project_dir: Path) -> bool:
    """Report a pending restore transaction without changing project state."""

    target = absolute_path_without_resolving(project_dir)
    _validate_project_root(target)
    transaction_root = _transaction_root(target)
    return transaction_root.exists() or transaction_root.is_symlink()


def _transaction_root(target: Path) -> Path:
    return restore_transaction_path(target)


def _transaction_entries(
    target: Path,
    staged_project: Path,
    files: tuple[Path, ...],
) -> tuple[_TransactionEntry, ...]:
    entries: list[_TransactionEntry] = []
    for relative in _publication_order(files):
        source = staged_project / relative
        if not source.is_file() or _is_link_or_reparse_point(source):
            raise ValueError(f"Staged restore file is missing or unsafe: {relative}")
        destination = target / relative
        existed = destination.exists()
        created_parents: list[str] = []
        parent = destination.parent
        while parent != target and not parent.exists():
            created_parents.append(parent.relative_to(target).as_posix())
            parent = parent.parent
        entries.append(
            {
                "path": relative.as_posix(),
                "original_existed": existed,
                "original_sha256": _sha256_file(destination) if existed else None,
                "new_sha256": _sha256_file(source),
                "created_parents": sorted(
                    created_parents,
                    key=lambda value: len(PurePosixPath(value).parts),
                    reverse=True,
                ),
            }
        )
    return tuple(entries)


def _write_transaction_journal(
    transaction_root: Path,
    *,
    target: Path,
    state: str,
    entries: tuple[_TransactionEntry, ...],
) -> None:
    if state not in {"prepared", "committed"}:
        raise ValueError("Restore transaction state is invalid.")
    content = json.dumps(
        {
            "format": _RESTORE_TRANSACTION_FORMAT,
            "pid": os.getpid(),
            "state": state,
            "target": str(target),
            "files": entries,
        },
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    temporary = transaction_root / ".transaction.json.tmp"
    descriptor = os.open(
        temporary,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, transaction_root / _RESTORE_JOURNAL)
        _fsync_directory(transaction_root)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _recover_transaction(
    target: Path,
    transaction_root: Path,
    *,
    ignore_live_process: bool,
) -> None:
    journal = _load_transaction_journal(target, transaction_root)
    pid = journal["pid"]
    if not ignore_live_process and _process_is_alive(pid):
        raise FileExistsError(
            f"Restore transaction is still owned by live process {pid}."
        )
    state = str(journal["state"])
    entries = journal["files"]
    if state == "prepared":
        _rollback_prepared_transaction(target, transaction_root, entries)
    elif state != "committed":
        raise ValueError("Restore transaction journal has an invalid state.")
    shutil.rmtree(transaction_root)
    _fsync_directory(target.parent)


def _load_transaction_journal(
    target: Path,
    transaction_root: Path,
) -> _TransactionJournal:
    if _is_link_or_reparse_point(transaction_root) or not transaction_root.is_dir():
        raise ValueError("Restore transaction directory is unsafe or malformed.")
    root_status = transaction_root.stat()
    getuid = getattr(os, "getuid", None)
    if callable(getuid) and root_status.st_uid != getuid():
        raise ValueError("Restore transaction directory has an unexpected owner.")
    if os.name == "posix" and stat.S_IMODE(root_status.st_mode) & 0o077:
        raise ValueError("Restore transaction directory permissions are too broad.")
    journal_path = transaction_root / _RESTORE_JOURNAL
    if _is_link_or_reparse_point(journal_path) or not journal_path.is_file():
        raise ValueError(
            "Restore transaction has no trusted journal; preserve it for manual "
            "inspection."
        )
    if journal_path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Restore transaction journal exceeds its size limit.")
    try:
        decoded = json.loads(journal_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Restore transaction journal is invalid JSON.") from exc
    if not isinstance(decoded, dict) or set(decoded) != {
        "format",
        "pid",
        "state",
        "target",
        "files",
    }:
        raise ValueError("Restore transaction journal has an invalid shape.")
    if decoded["format"] != _RESTORE_TRANSACTION_FORMAT:
        raise ValueError("Restore transaction journal format is unsupported.")
    if decoded["target"] != str(target):
        raise ValueError("Restore transaction journal targets another project.")
    if (
        not isinstance(decoded["pid"], int)
        or isinstance(decoded["pid"], bool)
        or int(decoded["pid"]) <= 0
    ):
        raise ValueError("Restore transaction journal has an invalid process id.")
    if decoded["state"] not in {"prepared", "committed"}:
        raise ValueError("Restore transaction journal has an invalid state.")
    raw_entries = decoded["files"]
    if not isinstance(raw_entries, list):
        raise ValueError("Restore transaction journal files must be a list.")
    if len(raw_entries) > 4096:
        raise ValueError("Restore transaction journal has too many file entries.")
    validated_entries = tuple(_validate_transaction_entry(item) for item in raw_entries)
    paths = tuple(
        Path(*PurePosixPath(str(item["path"])).parts) for item in validated_entries
    )
    if len(set(paths)) != len(paths):
        raise ValueError("Restore transaction journal has duplicate file paths.")
    _validate_prefix_free_paths(paths)
    return {
        "format": str(decoded["format"]),
        "pid": int(decoded["pid"]),
        "state": str(decoded["state"]),
        "target": str(decoded["target"]),
        "files": validated_entries,
    }


def _validate_transaction_entry(value: object) -> _TransactionEntry:
    if not isinstance(value, dict) or set(value) != {
        "path",
        "original_existed",
        "original_sha256",
        "new_sha256",
        "created_parents",
    }:
        raise ValueError("Restore transaction file entry has an invalid shape.")
    relative = _safe_relative_path(str(value["path"]))
    existed = value["original_existed"]
    original_digest = value["original_sha256"]
    new_digest = value["new_sha256"]
    if not isinstance(existed, bool):
        raise ValueError("Restore transaction existence flag must be boolean.")
    if (existed and not _valid_sha256(original_digest)) or (
        not existed and original_digest is not None
    ):
        raise ValueError("Restore transaction original digest is invalid.")
    if not _valid_sha256(new_digest):
        raise ValueError("Restore transaction new digest is invalid.")
    raw_parents = value["created_parents"]
    if not isinstance(raw_parents, list):
        raise ValueError("Restore transaction created parents must be a list.")
    parents: list[str] = []
    for raw_parent in raw_parents:
        parent = _safe_relative_path(str(raw_parent))
        if parent not in relative.parents:
            raise ValueError("Restore transaction created parent is unrelated.")
        parents.append(parent.as_posix())
    if len(set(parents)) != len(parents):
        raise ValueError("Restore transaction has duplicate created parents.")
    return {
        "path": relative.as_posix(),
        "original_existed": existed,
        "original_sha256": original_digest,
        "new_sha256": new_digest,
        "created_parents": parents,
    }


def _rollback_prepared_transaction(
    target: Path,
    transaction_root: Path,
    entries: tuple[_TransactionEntry, ...],
) -> None:
    for entry in reversed(entries):
        relative = _safe_relative_path(str(entry["path"]))
        destination = target / relative
        backup = transaction_root / "originals" / relative
        _validate_project_member(target, relative, destination)
        if bool(entry["original_existed"]):
            if backup.exists():
                if _is_link_or_reparse_point(backup) or not backup.is_file():
                    raise ValueError("Restore rollback backup is not a regular file.")
                if _sha256_file(backup) != entry["original_sha256"]:
                    raise ValueError("Restore rollback backup digest is invalid.")
                if destination.exists() and _sha256_file(destination) not in {
                    entry["original_sha256"],
                    entry["new_sha256"],
                }:
                    raise ValueError(
                        "Restore destination changed after interruption; refusing "
                        "automatic overwrite."
                    )
                _mkdir_private(destination.parent, target)
                os.replace(backup, destination)
                _fsync_directory(destination.parent)
            elif (
                not destination.is_file()
                or _sha256_file(destination) != entry["original_sha256"]
            ):
                raise ValueError("Restore rollback is missing the original file.")
        elif destination.exists():
            if (
                not destination.is_file()
                or _sha256_file(destination) != entry["new_sha256"]
            ):
                raise ValueError(
                    "Restore destination changed after interruption; refusing "
                    "automatic deletion."
                )
            destination.unlink()
            _fsync_directory(destination.parent)

    parents = {
        target / Path(*PurePosixPath(parent).parts)
        for entry in entries
        for parent in entry["created_parents"]
        if isinstance(parent, str)
    }
    for directory in sorted(parents, key=lambda path: len(path.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError as exc:
            if exc.errno not in {errno.ENOTEMPTY, errno.ENOENT}:
                raise
        else:
            _fsync_directory(directory.parent)


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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


def _project_publish_plan(
    *,
    project_dir: Path,
    relative_files: Iterable[str],
    remove_relative_files: Iterable[str],
    force: bool,
    allow_maintenance_marker: bool,
) -> tuple[Path, tuple[Path, ...], tuple[Path, ...]]:
    target = absolute_path_without_resolving(project_dir)
    _validate_project_root(target)
    files = tuple(_safe_relative_path(value) for value in relative_files)
    removals = tuple(_safe_relative_path(value) for value in remove_relative_files)
    if len(set(files)) != len(files):
        raise ValueError("Restore plan contains duplicate destination files.")
    if set(files) & set(removals):
        raise ValueError("Restore plan cannot both publish and remove the same file.")
    _validate_prefix_free_paths((*files, *removals))
    if not target.exists():
        return target, files, removals
    if not target.is_dir():
        raise ValueError("Restore target exists but is not a directory.")

    destinations = {relative: target / relative for relative in (*files, *removals)}
    for relative, destination in destinations.items():
        _validate_project_member(target, relative, destination)
    existing = [path for path in destinations.values() if path.exists()]
    metadata_dir = target / ".paper-galaxy"
    marker_only = allow_maintenance_marker and _is_lock_only_metadata(metadata_dir)
    if not force and ((metadata_dir.exists() and not marker_only) or existing):
        raise FileExistsError(
            f"{metadata_dir} already exists. Use --force to import over it."
        )
    _refuse_database_sidecars(target, removals)
    return target, files, removals


def _is_lock_only_metadata(metadata_dir: Path) -> bool:
    """Recognize the empty project skeleton owned by the held maintenance lock."""

    try:
        if _is_link_or_reparse_point(metadata_dir) or not metadata_dir.is_dir():
            return False
        entries = list(metadata_dir.iterdir())
        if len(entries) != 1:
            return False
        marker = entries[0]
        if marker.name != PROJECT_LOCK_RELATIVE_PATH.name:
            return False
        if _is_link_or_reparse_point(marker) or not marker.is_file():
            return False
        status = marker.stat()
        if os.name != "nt" and stat.S_IMODE(status.st_mode) != 0o600:
            return False
        with marker.open("rb") as handle:
            return handle.read(len(PROJECT_LOCK_MARKER) + 1) == PROJECT_LOCK_MARKER
    except OSError:
        return False


def _validate_project_root(project_dir: Path) -> None:
    for component in (project_dir, *project_dir.parents):
        if _untrusted_link_or_reparse(component):
            raise ValueError(
                "Refusing restore: project path contains a symbolic link component."
            )
    resolved = project_dir.resolve(strict=False)
    dangerous = {
        Path(resolved.anchor),
        Path.home().resolve(),
        Path("/tmp").resolve(),
        Path("/var").resolve(),
    }
    if resolved in dangerous:
        raise ValueError(
            "Refusing restore to a filesystem root, home directory, or shared "
            "system temporary root. Choose a dedicated project directory."
        )
    ancestor = project_dir
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if not ancestor.is_dir():
        raise ValueError("Restore target has a non-directory parent component.")


def _safe_relative_path(value: str) -> Path:
    if "\\" in value or "\x00" in value:
        raise ValueError("Restore path is not a safe POSIX relative path.")
    normalized_bytes = unicodedata.normalize("NFC", value).encode("utf-8")
    if len(normalized_bytes) > _MAX_PORTABLE_PATH_BYTES:
        raise ValueError("Restore path exceeds the portable length limit.")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
        or (pure.parts and pure.parts[0].endswith(":"))
    ):
        raise ValueError("Restore path is not a safe project-relative path.")
    if any(
        len(unicodedata.normalize("NFC", part).encode("utf-8"))
        > _MAX_PORTABLE_COMPONENT_BYTES
        for part in pure.parts
    ):
        raise ValueError("Restore path component exceeds the portable length limit.")
    return Path(*pure.parts)


def _validate_prefix_free_paths(paths: tuple[Path, ...]) -> None:
    keyed = sorted(
        (
            tuple(unicodedata.normalize("NFC", part).casefold() for part in path.parts),
            path,
        )
        for path in paths
    )
    for (parts, path), (other_parts, other_path) in pairwise(keyed):
        if len(other_parts) >= len(parts) and other_parts[: len(parts)] == parts:
            raise ValueError(
                "Restore destinations have an ancestor/descendant path "
                f"collision: {path} and {other_path}."
            )


def _validate_project_member(
    project_dir: Path,
    relative: Path,
    destination: Path,
) -> None:
    current = project_dir
    for part in relative.parts:
        current = current / part
        if _is_link_or_reparse_point(current):
            raise ValueError(
                f"Refusing restore: destination contains a symbolic link ({current})."
            )
    if destination.exists() and not destination.is_file():
        raise ValueError("Refusing restore over a non-file project entry.")


def _publication_order(files: tuple[Path, ...]) -> tuple[Path, ...]:
    """Preserve the validated plan order and publish configuration last."""

    config = Path(".paper-galaxy/project.toml")
    ordered = tuple(path for path in files if path != config)
    return ordered + ((config,) if config in files else ())


def _backup_existing(
    *,
    target: Path,
    relative: Path,
    destination: Path,
    transaction_root: Path,
) -> None:
    if not destination.exists():
        return
    _validate_project_member(target, relative, destination)
    backup = transaction_root / "originals" / relative
    _mkdir_private(backup.parent, transaction_root)
    os.replace(destination, backup)
    _fsync_directory(destination.parent)
    _fsync_directory(backup.parent)


def _mkdir_private(path: Path, root: Path) -> None:
    missing: list[Path] = []
    current = path
    while current != root and not current.exists():
        missing.append(current)
        current = current.parent
    if current != root and not current.is_dir():
        raise ValueError("Restore destination parent is not a directory.")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        _fsync_directory(directory.parent)


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


def _refuse_database_sidecars(
    project_dir: Path, relative_paths: tuple[Path, ...]
) -> None:
    unsafe = [
        project_dir / relative
        for relative in relative_paths
        if (project_dir / relative).exists() or (project_dir / relative).is_symlink()
    ]
    if unsafe:
        raise FileExistsError(
            "Restore target has active or uncheckpointed SQLite sidecars. Close "
            "Paper Galaxy, checkpoint the database, and retry; restore will not "
            "remove WAL/SHM/journal files behind an open connection."
        )


def _trusted_system_alias(path: Path) -> bool:
    if path not in {Path("/tmp"), Path("/var")}:
        return False
    try:
        return path.lstat().st_uid == 0
    except OSError:
        return False


def _untrusted_link_or_reparse(path: Path) -> bool:
    if not _is_link_or_reparse_point(path):
        return False
    return not (path.is_symlink() and _trusted_system_alias(path))


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
