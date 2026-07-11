"""Safe creation and opening of local Paper Galaxy projects."""

from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from paper_galaxy import __version__
from paper_galaxy.config import load_project_config, validate_project_config
from paper_galaxy.paths import metadata_dir, project_config_path
from paper_galaxy.storage.sqlite import ensure_database_ready, resolve_database_path


@dataclass(frozen=True)
class ProjectOpenResult:
    """Result of safely opening or creating one local project."""

    project_dir: Path
    config_path: Path
    database_path: Path
    created: bool


def open_or_initialize_project(
    project_dir: Path | str,
    *,
    initialize_database: bool = True,
) -> ProjectOpenResult:
    """Open a valid project or initialize its local metadata exactly once.

    The configuration is published from a sibling staging file with a
    no-clobber hard link.  A concurrent initializer therefore cannot replace
    an existing ``project.toml``.  Existing configuration bytes are only read
    and validated; they are never normalized or rewritten here.
    """

    project = _prepare_project_directory(project_dir)
    metadata = metadata_dir(project)
    _prepare_metadata_directory(metadata)
    config = project_config_path(project)
    _refuse_unsafe_config(config)

    created = False
    if config.exists():
        _load_existing_config(config, project)
    else:
        created = _publish_default_config(config, project)
        # Another safe initializer may have won the no-clobber publication
        # race.  In both cases, validate the exact bytes now on disk.
        _load_existing_config(config, project)

    database_path = (
        ensure_database_ready(project)
        if initialize_database
        else resolve_database_path(project)
    )
    return ProjectOpenResult(
        project_dir=project,
        config_path=config,
        database_path=database_path,
        created=created,
    )


def default_project_toml(project_dir: Path | str) -> str:
    """Return the deterministic default configuration for a new project."""

    project = Path(project_dir).expanduser().absolute()
    project_name = _escape_toml_string(project.name or "Paper Galaxy Project")
    return "\n".join(
        [
            f'project_name = "{project_name}"',
            f'created_by = "paper-galaxy {__version__}"',
            "map_seed = 42",
            "corpus_dirs = []",
            'database_path = ".paper-galaxy/paper_galaxy.sqlite3"',
            "",
        ]
    )


def _prepare_project_directory(project_dir: Path | str) -> Path:
    project = Path(project_dir).expanduser().absolute()
    if _is_link_or_reparse_point(project):
        raise ValueError("Project directory must not be a symbolic link.")
    if project.exists() and not project.is_dir():
        raise ValueError("Project path must be a directory.")
    project.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _is_link_or_reparse_point(project) or not project.is_dir():
        raise ValueError("Project directory became a symbolic link or non-directory.")
    return project.resolve()


def _prepare_metadata_directory(metadata: Path) -> None:
    if _is_link_or_reparse_point(metadata):
        raise ValueError(
            "Project .paper-galaxy metadata directory must not be a symbolic link."
        )
    if metadata.exists() and not metadata.is_dir():
        raise ValueError("Project .paper-galaxy metadata path must be a directory.")
    metadata.mkdir(mode=0o700, exist_ok=True)
    if _is_link_or_reparse_point(metadata) or not metadata.is_dir():
        raise ValueError(
            "Project .paper-galaxy metadata directory became unsafe during creation."
        )


def _refuse_unsafe_config(config: Path) -> None:
    if _is_link_or_reparse_point(config):
        raise ValueError("Project configuration must not be a symbolic link.")
    if config.exists() and not config.is_file():
        raise ValueError("Project configuration path must be a regular file.")


def _load_existing_config(config: Path, project: Path) -> None:
    _refuse_unsafe_config(config)
    try:
        loaded = load_project_config(project)
    except Exception as exc:
        raise ValueError(
            "Existing project.toml is invalid; fix or restore it before launch."
        ) from exc
    if loaded is None:
        raise RuntimeError("Project configuration disappeared during initialization.")


def _publish_default_config(config: Path, project: Path) -> bool:
    data = default_project_toml(project).encode("utf-8")
    # Validate before touching the destination so a packaging/config regression
    # cannot publish an unreadable project.
    import tomllib

    validate_project_config(tomllib.loads(data.decode("utf-8")))
    staging = config.with_name(f".{config.name}.{uuid4().hex}.staging")
    descriptor = -1
    published = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= int(getattr(os, "O_CLOEXEC", 0))
        flags |= int(getattr(os, "O_NOFOLLOW", 0))
        flags |= int(getattr(os, "O_BINARY", 0))
        descriptor = os.open(staging, flags, 0o600)
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        remaining = memoryview(data)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short write while staging project configuration")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            # Same-directory hard-link publication is atomic and, unlike
            # os.replace(), refuses to overwrite a concurrently created config.
            os.link(staging, config)
        except FileExistsError:
            return False
        published = True
        _fsync_directory(config.parent)
        return True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        staging.unlink(missing_ok=True)
        if published:
            _fsync_directory(config.parent)


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


def _escape_toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
