"""Configuration models for Paper Galaxy."""

import tomllib
from pathlib import Path
from typing import Any, TypeVar, cast

from pydantic import BaseModel, Field

from paper_galaxy.paths import project_config_path

ModelT = TypeVar("ModelT")


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
    return _validate_model(ProjectConfig, data)


def _validate_model(model_type: type[ModelT], data: dict[str, Any]) -> ModelT:
    """Validate config data with either Pydantic v2 or v1 style APIs."""

    model_validate = getattr(model_type, "model_validate", None)
    if callable(model_validate):
        return cast(ModelT, model_validate(data))
    parse_obj = getattr(model_type, "parse_obj", None)
    if callable(parse_obj):
        return cast(ModelT, parse_obj(data))
    constructor = cast(Any, model_type)
    return cast(ModelT, constructor(**data))
