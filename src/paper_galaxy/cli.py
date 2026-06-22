"""Command-line interface for Paper Galaxy."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from paper_galaxy import __version__
from paper_galaxy.logging import get_console
from paper_galaxy.paths import project_config_path

app = typer.Typer(
    help="Local-first research cartography tools.",
    no_args_is_help=True,
)

OPTIONAL_MODULES: tuple[tuple[str, str], ...] = (
    ("pypdf", "pypdf"),
    ("sklearn", "sklearn"),
    ("umap", "umap"),
    ("sentence_transformers", "sentence_transformers"),
    ("faiss", "faiss"),
    ("fastapi", "fastapi"),
    ("plotly", "plotly"),
)


def main() -> None:
    """Run the Paper Galaxy CLI."""

    app()


@app.command()
def doctor() -> None:
    """Print environment information and optional dependency status."""

    console = get_console()
    table = Table(title="Paper Galaxy Doctor")
    table.add_column("Check", style="bold")
    table.add_column("Value")

    table.add_row("Paper Galaxy version", __version__)
    table.add_row("Python version", sys.version.split()[0])
    table.add_row("Current working directory", str(Path.cwd()))

    for label, module_name in OPTIONAL_MODULES:
        status = "available" if _module_is_importable(module_name) else "missing"
        table.add_row(f"Optional module: {label}", status)

    console.print(table)
    console.print("Status: Phase 0 scaffold is ready.")


@app.command("init")
def init_project(
    project_dir: Annotated[
        Path,
        typer.Argument(
            help="Project directory for .paper-galaxy metadata.",
        ),
    ] = Path("."),
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Overwrite an existing .paper-galaxy/project.toml.",
        ),
    ] = False,
) -> None:
    """Create local project metadata without scanning user documents."""

    console = get_console()
    target_dir = project_dir.expanduser().resolve()
    config_path = project_config_path(target_dir)

    if config_path.exists() and not force:
        console.print(
            f"Project metadata already exists at {config_path}. "
            "Use --force to overwrite."
        )
        return

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_default_project_toml(target_dir), encoding="utf-8")
    console.print(f"Created project metadata at {config_path}.")


def _module_is_importable(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _default_project_toml(project_dir: Path) -> str:
    project_name = _escape_toml_string(project_dir.name or "Paper Galaxy Project")
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


def _escape_toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
