"""Configuration models for Paper Galaxy."""

from pydantic import BaseModel, Field


class ProjectConfig(BaseModel):
    """Future-facing local project configuration."""

    project_name: str
    corpus_dirs: list[str] = Field(default_factory=list)
    database_path: str = ".paper-galaxy/paper_galaxy.sqlite3"
    map_seed: int = 42
    created_by: str = "paper-galaxy"
