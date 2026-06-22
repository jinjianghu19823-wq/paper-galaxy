"""Command-line interface for Paper Galaxy."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from paper_galaxy import __version__
from paper_galaxy.errors import MissingDependencyError
from paper_galaxy.logging import get_console
from paper_galaxy.paths import project_config_path
from paper_galaxy.pipeline import build_galaxy

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
    console.print("Scan command: Phase 1 static CLI MVP is available.")


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


@app.command()
def scan(
    corpus_dir: Annotated[
        Path,
        typer.Argument(help="Local corpus directory to scan."),
    ],
    out: Annotated[
        Path,
        typer.Option("--out", help="Output static HTML file."),
    ] = Path("galaxy.html"),
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Overwrite an existing output file."),
    ] = False,
    max_documents: Annotated[
        int | None,
        typer.Option("--max-documents", help="Optional cap for debugging."),
    ] = None,
    min_chars: Annotated[
        int,
        typer.Option("--min-chars", help="Minimum extracted text length."),
    ] = 80,
    neighbors: Annotated[
        int,
        typer.Option("--neighbors", help="Nearest neighbors per document."),
    ] = 5,
    clusters: Annotated[
        int | None,
        typer.Option("--clusters", help="Number of k-means clusters."),
    ] = None,
    seed: Annotated[
        int,
        typer.Option("--seed", help="Random seed for deterministic map steps."),
    ] = 42,
    include_pdf: Annotated[
        bool,
        typer.Option(
            "--include-pdf/--no-include-pdf",
            help="Include PDFs when optional pypdf support is installed.",
        ),
    ] = True,
    json_out: Annotated[
        Path | None,
        typer.Option("--json-out", help="Optional sidecar JSON summary."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Print more extraction details."),
    ] = False,
) -> None:
    """Build a self-contained static HTML galaxy from a local corpus."""

    console = get_console()
    corpus_path = corpus_dir.expanduser().resolve()
    output_path = out.expanduser().resolve()

    if not corpus_path.exists() or not corpus_path.is_dir():
        console.print(f"Corpus directory does not exist: {corpus_path}")
        raise typer.Exit(1)

    if output_path.exists() and not force:
        console.print(
            f"Output already exists: {output_path}. Use --force to overwrite."
        )
        raise typer.Exit(1)

    try:
        result = build_galaxy(
            corpus_path,
            output_path,
            json_output_path=json_out,
            max_documents=max_documents,
            min_chars=min_chars,
            neighbor_count=neighbors,
            cluster_count=clusters,
            seed=seed,
            include_pdf=include_pdf,
            verbose=verbose,
        )
    except MissingDependencyError as exc:
        console.print(
            "Missing optional dependency for Phase 1 scan: "
            f"{exc.dependency}. Install with: "
            'python -m pip install -e ".[dev,ml,pdf]"',
            markup=False,
        )
        raise typer.Exit(1) from exc

    table = Table(title="Paper Galaxy Scan Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    table.add_row("Files found", str(result.files_found))
    table.add_row("Documents extracted", str(len(result.documents)))
    table.add_row("Skipped files", str(len(result.skipped_files)))
    table.add_row("Clusters", str(len(result.cluster_labels)))
    table.add_row("Output HTML", str(result.output_path))
    if json_out is not None:
        table.add_row("Output JSON", str(json_out.expanduser().resolve()))
    console.print(table)


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
