"""Path helpers for local Paper Galaxy projects."""

from pathlib import Path

METADATA_DIR_NAME = ".paper-galaxy"
PROJECT_CONFIG_FILE_NAME = "project.toml"


def resolve_project_dir(project_dir: Path | str | None = None) -> Path:
    """Resolve a project directory, defaulting to the current working directory."""

    if project_dir is None:
        return Path.cwd().resolve()
    return Path(project_dir).expanduser().resolve()


def metadata_dir(project_dir: Path | str | None = None) -> Path:
    """Return the `.paper-galaxy` metadata directory for a project."""

    return resolve_project_dir(project_dir) / METADATA_DIR_NAME


def project_config_path(project_dir: Path | str | None = None) -> Path:
    """Return the local project metadata TOML path."""

    return metadata_dir(project_dir) / PROJECT_CONFIG_FILE_NAME
