"""Command-line interface for Paper Galaxy."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from paper_galaxy import __version__
from paper_galaxy.errors import (
    DatabaseNotFoundError,
    FTSUnavailableError,
    MissingDependencyError,
)
from paper_galaxy.indexer import index_corpus
from paper_galaxy.logging import get_console
from paper_galaxy.paths import project_config_path
from paper_galaxy.pipeline import build_galaxy
from paper_galaxy.search import get_database_stats, search_index

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
    console.print("Index/search commands: Phase 2 local database is available.")


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


@app.command("index")
def index_command(
    corpus_dir: Annotated[
        Path,
        typer.Argument(help="Local corpus directory to index into SQLite."),
    ],
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
    min_chars: Annotated[
        int,
        typer.Option("--min-chars", help="Minimum extracted text length."),
    ] = 80,
    include_pdf: Annotated[
        bool,
        typer.Option(
            "--include-pdf/--no-include-pdf",
            help="Include PDFs when optional pypdf support is installed.",
        ),
    ] = True,
    force_reextract: Annotated[
        bool,
        typer.Option(
            "--force-reextract",
            help="Re-extract files even when their SHA-256 hash is unchanged.",
        ),
    ] = False,
    chunk_size: Annotated[
        int,
        typer.Option(
            "--chunk-size", help="Approximate target chunk size in characters."
        ),
    ] = 2000,
    chunk_overlap: Annotated[
        int,
        typer.Option(
            "--chunk-overlap", help="Approximate chunk overlap in characters."
        ),
    ] = 200,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Reserved for more detailed indexing output."),
    ] = False,
) -> None:
    """Index a local corpus into the project's SQLite database."""

    console = get_console()
    corpus_path = corpus_dir.expanduser().resolve()
    resolved_project_dir = project_dir.expanduser().resolve()
    if not corpus_path.exists() or not corpus_path.is_dir():
        console.print(f"Corpus directory does not exist: {corpus_path}")
        raise typer.Exit(1)
    if chunk_size <= 0:
        console.print("--chunk-size must be positive.")
        raise typer.Exit(1)
    if chunk_overlap >= chunk_size:
        console.print("--chunk-overlap must be smaller than --chunk-size.")
        raise typer.Exit(1)

    try:
        summary = index_corpus(
            corpus_path,
            project_dir=resolved_project_dir,
            min_chars=min_chars,
            include_pdf=include_pdf,
            force_reextract=force_reextract,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            verbose=verbose,
        )
    except FTSUnavailableError as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc

    table = Table(title="Paper Galaxy Index Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    table.add_row("Corpus path", str(summary.corpus_path))
    table.add_row("Project dir", str(summary.project_dir))
    table.add_row("Database path", str(summary.database_path))
    table.add_row("Files found", str(summary.files_found))
    table.add_row("Documents inserted", str(summary.documents_inserted))
    table.add_row("Documents updated", str(summary.documents_updated))
    table.add_row("Documents unchanged", str(summary.documents_unchanged))
    table.add_row("Documents marked missing", str(summary.documents_missing))
    table.add_row("Skipped files", str(summary.skipped_files))
    table.add_row("Chunks written", str(summary.chunks_written))
    table.add_row("Scan run id", summary.scan_run_id)
    console.print(table)


@app.command("search")
def search_command(
    query: Annotated[
        str,
        typer.Argument(help="Full-text query for indexed documents."),
    ],
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum number of search results."),
    ] = 10,
    include_missing: Annotated[
        bool,
        typer.Option(
            "--include-missing/--no-include-missing",
            help="Include documents marked missing.",
        ),
    ] = False,
) -> None:
    """Search indexed documents with local SQLite FTS."""

    console = get_console()
    try:
        results = search_index(
            query,
            project_dir=project_dir.expanduser().resolve(),
            limit=limit,
            include_missing=include_missing,
        )
    except DatabaseNotFoundError as exc:
        console.print(
            "No Paper Galaxy database found. Run paper-galaxy index CORPUS_DIR first."
        )
        console.print(f"Expected database path: {exc.database_path}")
        raise typer.Exit(1) from exc

    table = Table(title=f"Paper Galaxy Search: {query}")
    table.add_column("#", justify="right")
    table.add_column("Title")
    table.add_column("Relative path", overflow="fold")
    table.add_column("Type")
    table.add_column("Chars", justify="right")
    table.add_column("Updated")
    table.add_column("Snippet", overflow="fold")
    for result in results:
        table.add_row(
            str(result.rank),
            result.title,
            result.relative_path,
            result.file_type,
            str(result.char_count),
            result.updated_at,
            result.snippet,
        )
    console.print(table)
    if results:
        console.print("Matched paths:")
        for result in results:
            console.print(f"{result.rank}. {result.relative_path}")
    else:
        console.print("No matching indexed documents found.")


@app.command("db-stats")
def db_stats_command(
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
) -> None:
    """Show local Paper Galaxy SQLite database statistics."""

    console = get_console()
    try:
        stats = get_database_stats(project_dir=project_dir.expanduser().resolve())
    except DatabaseNotFoundError as exc:
        console.print(
            "No Paper Galaxy database found. Run paper-galaxy index CORPUS_DIR first."
        )
        console.print(f"Expected database path: {exc.database_path}")
        raise typer.Exit(1) from exc

    table = Table(title="Paper Galaxy Database Stats")
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    table.add_row("Database path", str(stats.database_path))
    table.add_row("Documents", str(stats.documents))
    table.add_row("Active documents", str(stats.active_documents))
    table.add_row("Missing documents", str(stats.missing_documents))
    table.add_row("Chunks", str(stats.chunks))
    table.add_row("Scan runs", str(stats.scan_runs))
    table.add_row("Last scan time", stats.last_scan_time or "none")
    table.add_row("Total indexed characters", str(stats.total_indexed_characters))
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
