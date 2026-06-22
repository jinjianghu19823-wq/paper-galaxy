"""Configuration models for Paper Galaxy."""

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

from paper_galaxy.paths import project_config_path


class ProjectConfig(BaseModel):
    """Future-facing local project configuration."""

    project_name: str
    corpus_dirs: list[str] = Field(default_factory=list)
    database_path: str = ".paper-galaxy/paper_galaxy.sqlite3"
    map_seed: int = 42
    created_by: str = "paper-galaxy"


def load_project_config(project_dir: Path | str) -> ProjectConfig | None:
    """Load `.paper-galaxy/project.toml` if it exists."""

    config_path = project_config_path(project_dir)
    if not config_path.exists():
        return None
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    return ProjectConfig.model_validate(data)
