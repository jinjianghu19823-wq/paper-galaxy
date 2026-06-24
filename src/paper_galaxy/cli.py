"""Command-line interface for Paper Galaxy."""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from paper_galaxy import __version__
from paper_galaxy.backup import export_project, import_project
from paper_galaxy.embeddings.builder import build_embeddings
from paper_galaxy.embeddings.models import NeighborResult
from paper_galaxy.embeddings.search import (
    NoVectorsFoundError,
    semantic_search,
    vector_stats,
)
from paper_galaxy.embeddings.sentence_transformers import ModelDownloadDisabledError
from paper_galaxy.embeddings.similarity import compare_neighbors
from paper_galaxy.errors import (
    DatabaseNotFoundError,
    FTSUnavailableError,
    MissingDependencyError,
)
from paper_galaxy.explain.labels import validate_manual_label
from paper_galaxy.explain.pairs import explain_pair
from paper_galaxy.extract import extract_file
from paper_galaxy.indexer import index_corpus
from paper_galaxy.logging import get_console
from paper_galaxy.maps import (
    build_and_store_map_run,
    export_map_run,
    persisted_map_payload,
)
from paper_galaxy.paths import project_config_path
from paper_galaxy.pipeline import build_galaxy
from paper_galaxy.plugins import get_plugin_registry
from paper_galaxy.search import get_database_stats, search_index
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path
from paper_galaxy.validation import (
    validate_project,
    validation_exit_code,
    write_validation_report,
)
from paper_galaxy.web.map_builder import build_map_payload
from paper_galaxy.zotero.detect import detect_zotero, detection_payload
from paper_galaxy.zotero.doctor import ZoteroDoctorReport, validate_local_zotero
from paper_galaxy.zotero.filters import (
    ZoteroFilterError,
    collection_paths,
    filter_items,
    normalize_local_library,
    normalize_reading_status,
    resolve_collection,
    validate_non_empty_values,
)
from paper_galaxy.zotero.importers import import_from_zotero
from paper_galaxy.zotero.local_api import (
    DEFAULT_LOCAL_API_URL,
    LocalZoteroAPIClient,
    ZoteroAPIError,
)
from paper_galaxy.zotero.models import ZoteroImportRunSummary
from paper_galaxy.zotero.normalize import normalize_collection, normalize_item
from paper_galaxy.zotero.reading import build_and_store_zotero_reading_map

app = typer.Typer(
    help="Local-first research cartography tools.",
    no_args_is_help=True,
)
zotero_app = typer.Typer(
    help="Read-only local Zotero import and reading graph tools.",
    no_args_is_help=True,
)
app.add_typer(zotero_app, name="zotero")

OPTIONAL_MODULES: tuple[tuple[str, str], ...] = (
    ("pypdf", "pypdf"),
    ("Pillow", "PIL"),
    ("pytesseract", "pytesseract"),
    ("sklearn", "sklearn"),
    ("umap", "umap"),
    ("sentence_transformers", "sentence_transformers"),
    ("faiss", "faiss"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
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
    console.print("Serve command: Phase 3 local web app is available.")
    console.print("Extraction reports: Phase 4 extraction diagnostics are available.")
    console.print("Embedding commands: Phase 5 optional local vectors are available.")
    console.print(
        "Explainability commands: Phase 6 labels and pair evidence are available."
    )
    console.print(
        "Professionalization commands: Phase 7 validation, map runs, backups, "
        "and plugins are available."
    )
    console.print(
        "Zotero commands: local read-only import and reading graph are available."
    )


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
        typer.Option(
            "--force",
            "-f",
            help="Overwrite an existing output file.",
        ),
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
    include_images: Annotated[
        bool,
        typer.Option(
            "--include-images/--no-include-images",
            help="Discover image files for optional local OCR.",
        ),
    ] = False,
    ocr: Annotated[
        bool,
        typer.Option(
            "--ocr/--no-ocr",
            help="Run optional local OCR for image files when available.",
        ),
    ] = False,
    ocr_language: Annotated[
        str,
        typer.Option("--ocr-language", help="Tesseract OCR language code."),
    ] = "eng",
    json_out: Annotated[
        Path | None,
        typer.Option("--json-out", help="Optional sidecar JSON summary."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Print more extraction details.",
        ),
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
            include_images=include_images,
            ocr=ocr,
            ocr_language=ocr_language,
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
    include_images: Annotated[
        bool,
        typer.Option(
            "--include-images/--no-include-images",
            help="Discover image files for optional local OCR.",
        ),
    ] = False,
    ocr: Annotated[
        bool,
        typer.Option(
            "--ocr/--no-ocr",
            help="Run optional local OCR for image files when available.",
        ),
    ] = False,
    ocr_language: Annotated[
        str,
        typer.Option("--ocr-language", help="Tesseract OCR language code."),
    ] = "eng",
    extraction_report_json: Annotated[
        Path | None,
        typer.Option(
            "--extraction-report-json",
            help="Optional local JSON sidecar with extraction diagnostics.",
        ),
    ] = None,
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
        typer.Option(
            "--verbose",
            help="Reserved for more detailed indexing output.",
        ),
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
            include_images=include_images,
            ocr=ocr,
            ocr_language=ocr_language,
            extraction_report_json=extraction_report_json,
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
    table.add_row("Files extracted", str(summary.extracted_count))
    table.add_row("Extraction warnings", str(summary.warning_count))
    table.add_row("OCR files extracted", str(summary.ocr_count))
    table.add_row("Image files seen", str(summary.image_files_seen))
    table.add_row("Low-text files", str(summary.low_text_count))
    table.add_row("Scanned PDF candidates", str(summary.scanned_pdf_candidates))
    if summary.extraction_report_json is not None:
        table.add_row("Extraction report JSON", str(summary.extraction_report_json))
    table.add_row("Scan run id", summary.scan_run_id)
    console.print(table)


@app.command("extract-preview")
def extract_preview_command(
    path: Annotated[
        Path,
        typer.Argument(help="Local file to extract without writing to the database."),
    ],
    ocr: Annotated[
        bool,
        typer.Option(
            "--ocr/--no-ocr",
            help="Run optional local OCR for image files.",
        ),
    ] = False,
    ocr_language: Annotated[
        str,
        typer.Option("--ocr-language", help="Tesseract OCR language code."),
    ] = "eng",
    include_metadata: Annotated[
        bool,
        typer.Option(
            "--include-metadata",
            help="Print extractor metadata.",
        ),
    ] = False,
    max_chars: Annotated[
        int,
        typer.Option("--max-chars", help="Maximum preview characters to print."),
    ] = 1200,
) -> None:
    """Preview local extraction output for one file without touching SQLite."""

    console = get_console()
    source_path = path.expanduser().resolve()
    if not source_path.exists() or not source_path.is_file():
        console.print(f"File does not exist: {source_path}")
        raise typer.Exit(1)
    if max_chars < 0:
        console.print("--max-chars must be non-negative.")
        raise typer.Exit(1)

    extracted, reason = extract_file(
        source_path,
        include_pdf=True,
        include_images=True,
        ocr=ocr,
        ocr_language=ocr_language,
    )
    if reason is not None or extracted is None:
        console.print(f"Extraction skipped: {reason or 'unknown'}")
        raise typer.Exit(1)

    table = Table(title="Paper Galaxy Extraction Preview")
    table.add_column("Field", style="bold")
    table.add_column("Value", overflow="fold")
    table.add_row("Path", str(source_path))
    table.add_row("Title", extracted.title)
    table.add_row("Method", extracted.method)
    table.add_row("Characters", str(len(extracted.text)))
    table.add_row("Warnings", "; ".join(extracted.warnings) or "none")
    if extracted.sections:
        table.add_row("Sections", ", ".join(extracted.sections[:8]))
    if extracted.links:
        table.add_row("Links", ", ".join(extracted.links[:8]))
    console.print(table)
    if include_metadata:
        console.print_json(data=extracted.metadata)
    preview = extracted.text[:max_chars]
    if len(extracted.text) > max_chars:
        preview += "..."
    console.print(preview or "[no extracted text]")


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
    table.add_row("Unindexed documents", str(stats.unindexed_documents))
    table.add_row("Chunks", str(stats.chunks))
    table.add_row("Scan runs", str(stats.scan_runs))
    table.add_row("Last scan time", stats.last_scan_time or "none")
    table.add_row("Total indexed characters", str(stats.total_indexed_characters))
    console.print(table)


@app.command("embed")
def embed_command(
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
    model: Annotated[
        str,
        typer.Option(
            "--model",
            help="Local Sentence Transformer model path, or remote name with opt-in.",
        ),
    ] = "",
    allow_model_download: Annotated[
        bool,
        typer.Option(
            "--allow-model-download/--no-allow-model-download",
            help="Allow Sentence Transformers to resolve or download a model name.",
        ),
    ] = False,
    object_type: Annotated[
        str,
        typer.Option("--object-type", help="document, chunk, or both."),
    ] = "both",
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Optional cap per selected object type."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Recompute vectors even if text is unchanged.",
        ),
    ] = False,
    batch_size: Annotated[
        int,
        typer.Option("--batch-size", help="Embedding batch size."),
    ] = 32,
    max_document_chars: Annotated[
        int,
        typer.Option("--max-document-chars", help="Document text cap for embeddings."),
    ] = 8000,
    max_chunk_chars: Annotated[
        int,
        typer.Option("--max-chunk-chars", help="Chunk text cap for embeddings."),
    ] = 2000,
    normalize: Annotated[
        bool,
        typer.Option(
            "--normalize/--no-normalize",
            help="Store normalized vectors for cosine similarity.",
        ),
    ] = True,
) -> None:
    """Generate optional local document/chunk embeddings."""

    console = get_console()
    if not model.strip():
        console.print("--model is required.")
        raise typer.Exit(1)
    if object_type not in {"document", "chunk", "both"}:
        console.print("--object-type must be document, chunk, or both.")
        raise typer.Exit(1)
    if batch_size <= 0:
        console.print("--batch-size must be positive.")
        raise typer.Exit(1)
    try:
        summary = build_embeddings(
            project_dir=project_dir.expanduser().resolve(),
            model=model,
            allow_model_download=allow_model_download,
            object_type=object_type,
            limit=limit,
            force=force,
            batch_size=batch_size,
            max_document_chars=max_document_chars,
            max_chunk_chars=max_chunk_chars,
            normalize=normalize,
        )
    except ModelDownloadDisabledError as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(1) from exc
    except MissingDependencyError as exc:
        _print_embeddings_dependency_error()
        raise typer.Exit(1) from exc

    table = Table(title="Paper Galaxy Embedding Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Value", overflow="fold")
    table.add_row("Model ID", summary.model_id)
    table.add_row("Model name/path", summary.model_name)
    table.add_row("Provider", summary.provider)
    table.add_row("Dimension", str(summary.dimension))
    table.add_row("Database path", str(summary.database_path))
    table.add_row("Documents seen", str(summary.documents_seen))
    table.add_row("Documents embedded", str(summary.documents_embedded))
    table.add_row("Documents unchanged", str(summary.documents_unchanged))
    table.add_row("Chunks seen", str(summary.chunks_seen))
    table.add_row("Chunks embedded", str(summary.chunks_embedded))
    table.add_row("Chunks unchanged", str(summary.chunks_unchanged))
    table.add_row("Errors", str(summary.errors))
    console.print(table)


@app.command("semantic-search")
def semantic_search_command(
    query: Annotated[
        str,
        typer.Argument(help="Semantic query for stored local vectors."),
    ],
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
    model: Annotated[
        str,
        typer.Option(
            "--model",
            help="Local Sentence Transformer model path, or remote name with opt-in.",
        ),
    ] = "",
    allow_model_download: Annotated[
        bool,
        typer.Option(
            "--allow-model-download/--no-allow-model-download",
            help="Allow Sentence Transformers to resolve or download a model name.",
        ),
    ] = False,
    object_type: Annotated[
        str,
        typer.Option("--object-type", help="document or chunk."),
    ] = "document",
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum results."),
    ] = 10,
    include_missing: Annotated[
        bool,
        typer.Option(
            "--include-missing/--no-include-missing",
            help="Include missing documents for document search.",
        ),
    ] = False,
    show_chunks: Annotated[
        bool,
        typer.Option(
            "--show-chunks",
            help="Show chunk indexes when available.",
        ),
    ] = False,
    normalize: Annotated[
        bool,
        typer.Option(
            "--normalize/--no-normalize",
            help="Use the same vector normalization setting as paper-galaxy embed.",
        ),
    ] = True,
) -> None:
    """Search stored local vectors with a local query embedding."""

    console = get_console()
    if not model.strip():
        console.print("--model is required.")
        raise typer.Exit(1)
    if object_type not in {"document", "chunk"}:
        console.print("--object-type must be document or chunk.")
        raise typer.Exit(1)
    try:
        results = semantic_search(
            query,
            project_dir=project_dir.expanduser().resolve(),
            model=model,
            allow_model_download=allow_model_download,
            object_type=object_type,
            limit=limit,
            include_missing=include_missing,
            normalize=normalize,
        )
    except ModelDownloadDisabledError as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(1) from exc
    except MissingDependencyError as exc:
        _print_embeddings_dependency_error()
        raise typer.Exit(1) from exc
    except NoVectorsFoundError as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc

    table = Table(title=f"Paper Galaxy Semantic Search: {query}")
    table.add_column("#", justify="right")
    table.add_column("Title")
    table.add_column("Relative path", overflow="fold")
    if object_type == "chunk" or show_chunks:
        table.add_column("Chunk", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Snippet", overflow="fold")
    for result in results:
        row = [
            str(result.rank),
            result.title,
            result.relative_path,
        ]
        if object_type == "chunk" or show_chunks:
            row.append("" if result.chunk_index is None else str(result.chunk_index))
        row.extend([f"{result.score:.4f}", result.snippet])
        table.add_row(*row)
    console.print(table)
    if not results:
        console.print("No semantic matches found.")


@app.command("compare-neighbors")
def compare_neighbors_command(
    document_id_or_path: Annotated[
        str,
        typer.Argument(help="Document ID or corpus-relative path."),
    ],
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
    model: Annotated[
        str,
        typer.Option(
            "--model",
            help="Local Sentence Transformer model path, or remote name with opt-in.",
        ),
    ] = "",
    allow_model_download: Annotated[
        bool,
        typer.Option(
            "--allow-model-download/--no-allow-model-download",
            help="Allow Sentence Transformers to resolve or download a model name.",
        ),
    ] = False,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum neighbors per ranking."),
    ] = 10,
    dense_weight: Annotated[
        float,
        typer.Option("--dense-weight", help="Dense cosine weight for hybrid scores."),
    ] = 0.65,
    tfidf_weight: Annotated[
        float,
        typer.Option("--tfidf-weight", help="TF-IDF cosine weight for hybrid scores."),
    ] = 0.35,
    normalize: Annotated[
        bool,
        typer.Option(
            "--normalize/--no-normalize",
            help="Use the same vector normalization setting as paper-galaxy embed.",
        ),
    ] = True,
) -> None:
    """Compare TF-IDF, dense, and hybrid nearest neighbors."""

    console = get_console()
    if not model.strip():
        console.print("--model is required.")
        raise typer.Exit(1)
    try:
        comparison = compare_neighbors(
            document_id_or_path,
            project_dir=project_dir.expanduser().resolve(),
            model=model,
            allow_model_download=allow_model_download,
            limit=limit,
            dense_weight=dense_weight,
            tfidf_weight=tfidf_weight,
            normalize=normalize,
        )
    except ModelDownloadDisabledError as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(1) from exc
    except MissingDependencyError as exc:
        if exc.dependency == "sentence-transformers":
            _print_embeddings_dependency_error()
        else:
            console.print(str(exc))
        raise typer.Exit(1) from exc
    except NoVectorsFoundError as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc
    except ValueError as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc

    console.print(f"Target: {comparison.target.title}")
    console.print(f"Path: {comparison.target.relative_path}")
    console.print(f"Hybrid weights: dense={dense_weight:.2f}, tfidf={tfidf_weight:.2f}")
    console.print(_neighbors_table("TF-IDF Neighbors", comparison.tfidf_neighbors))
    console.print(_neighbors_table("Dense Neighbors", comparison.dense_neighbors))
    console.print(_neighbors_table("Hybrid Neighbors", comparison.hybrid_neighbors))


@app.command("vector-stats")
def vector_stats_command(
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
) -> None:
    """Show local embedding model and vector statistics."""

    stats = vector_stats(project_dir.expanduser().resolve())
    models = stats.get("models")
    vector_counts = stats.get("vector_counts")
    model_rows = models if isinstance(models, list) else []
    vector_count_rows = vector_counts if isinstance(vector_counts, list) else []
    table = Table(title="Paper Galaxy Vector Stats")
    table.add_column("Metric", style="bold")
    table.add_column("Value", overflow="fold")
    table.add_row("Database path", str(stats["database_path"]))
    table.add_row("Embedding models", str(len(model_rows)))
    table.add_row("Vector groups", str(len(vector_count_rows)))
    last_run = stats.get("last_run")
    if isinstance(last_run, dict):
        table.add_row("Last embedding run", str(last_run["id"]))
        table.add_row("Last run status", str(last_run["status"]))
    else:
        table.add_row("Last embedding run", "none")
    get_console().print(table)

    counts_table = Table(title="Vectors By Model/Object")
    counts_table.add_column("Model")
    counts_table.add_column("Object type")
    counts_table.add_column("Dimension", justify="right")
    counts_table.add_column("Vectors", justify="right")
    counts_table.add_column("Updated")
    for row in vector_count_rows:
        if isinstance(row, dict):
            counts_table.add_row(
                str(row["model_name"]),
                str(row["object_type"]),
                str(row["dimension"]),
                str(row["vector_count"]),
                str(row["last_vector_at"]),
            )
    get_console().print(counts_table)


@app.command("clusters")
def clusters_command(
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
    seed: Annotated[
        int,
        typer.Option("--seed", help="Random seed for deterministic cluster layout."),
    ] = 42,
    clusters: Annotated[
        int | None,
        typer.Option("--clusters", help="Optional cluster count."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum active documents to explain."),
    ] = 1000,
) -> None:
    """List generated and manual cluster labels."""

    console = get_console()
    try:
        payload = build_map_payload(
            project_dir=project_dir.expanduser().resolve(),
            seed=seed,
            clusters=clusters,
            limit=limit,
        )
    except MissingDependencyError as exc:
        console.print(f"Missing optional dependency: {exc.dependency}.")
        raise typer.Exit(1) from exc

    table = Table(title="Paper Galaxy Clusters")
    table.add_column("Cluster", justify="right")
    table.add_column("Signature", overflow="fold")
    table.add_column("Label", overflow="fold")
    table.add_column("Source")
    table.add_column("Size", justify="right")
    table.add_column("Top terms", overflow="fold")
    cluster_value = payload.get("clusters", [])
    cluster_rows = cluster_value if isinstance(cluster_value, list) else []
    for cluster in cluster_rows:
        if not isinstance(cluster, dict):
            continue
        terms = cluster.get("top_terms")
        term_text = (
            ", ".join(
                str(term.get("term", "")) for term in terms if isinstance(term, dict)
            )
            if isinstance(terms, list)
            else ""
        )
        table.add_row(
            str(cluster.get("cluster_id", "")),
            str(cluster.get("cluster_signature", "")),
            str(cluster.get("display_label", "")),
            str(cluster.get("source", "")),
            str(cluster.get("size", "")),
            term_text,
        )
    console.print(table)


@app.command("rename-cluster")
def rename_cluster_command(
    cluster_signature: Annotated[
        str,
        typer.Argument(help="Stable cluster signature from paper-galaxy clusters."),
    ],
    label: Annotated[
        str,
        typer.Argument(help="Manual local display label, 1-120 characters."),
    ],
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
) -> None:
    """Store a local manual label override for a cluster."""

    console = get_console()
    try:
        cleaned_label = validate_manual_label(label)
    except ValueError as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc
    repository = _open_repository(project_dir.expanduser().resolve())
    try:
        with repository.connection:
            repository.upsert_cluster_label_override(
                cluster_signature=cluster_signature,
                label=cleaned_label,
            )
    finally:
        repository.connection.close()
    console.print(f"Renamed {cluster_signature} to {cleaned_label}.")


@app.command("reset-cluster-label")
def reset_cluster_label_command(
    cluster_signature: Annotated[
        str,
        typer.Argument(help="Stable cluster signature from paper-galaxy clusters."),
    ],
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
) -> None:
    """Remove a local manual label override for a cluster."""

    repository = _open_repository(project_dir.expanduser().resolve())
    try:
        with repository.connection:
            deleted = repository.delete_cluster_label_override(cluster_signature)
    finally:
        repository.connection.close()
    status = "Removed" if deleted else "No override found for"
    get_console().print(f"{status} {cluster_signature}.")


@app.command("validate-project")
def validate_project_command(
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
    json_out: Annotated[
        Path | None,
        typer.Option("--json-out", help="Optional JSON validation report path."),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Exit nonzero on warnings as well as errors."),
    ] = False,
) -> None:
    """Validate local project metadata, database, indexes, and map runs."""

    console = get_console()
    report = validate_project(project_dir.expanduser().resolve())
    table = Table(title="Paper Galaxy Project Validation")
    table.add_column("Check", style="bold")
    table.add_column("Value", overflow="fold")
    table.add_row("Status", str(report["status"]))
    table.add_row("Project dir", str(report["project_dir"]))
    table.add_row("Database", str(report["database_path"]))
    table.add_row("Schema", str(report.get("schema_version") or "missing"))
    counts = report.get("counts")
    if isinstance(counts, dict):
        for key in ("documents", "chunks", "extraction_reports", "vectors", "map_runs"):
            table.add_row(key.replace("_", " ").title(), str(counts.get(key, 0)))
    console.print(table)

    issues = report.get("issues")
    issue_rows = issues if isinstance(issues, list) else []
    if issue_rows:
        issue_table = Table(title="Validation Issues")
        issue_table.add_column("Severity")
        issue_table.add_column("Code")
        issue_table.add_column("Message", overflow="fold")
        for issue in issue_rows:
            if isinstance(issue, dict):
                issue_table.add_row(
                    str(issue.get("severity", "")),
                    str(issue.get("code", "")),
                    str(issue.get("message", "")),
                )
        console.print(issue_table)
    if json_out is not None:
        path = write_validation_report(report, json_out)
        console.print(f"Wrote validation JSON to {path}.")
    raise typer.Exit(validation_exit_code(report, strict=strict))


@app.command("build-map-run")
def build_map_run_command(
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
    name: Annotated[
        str | None,
        typer.Option("--name", help="Human-readable saved map name."),
    ] = None,
    seed: Annotated[
        int,
        typer.Option("--seed", help="Random seed for deterministic layout."),
    ] = 42,
    clusters: Annotated[
        int | None,
        typer.Option("--clusters", help="Optional cluster count."),
    ] = None,
    neighbors: Annotated[
        int,
        typer.Option("--neighbors", help="Nearest neighbors per document."),
    ] = 5,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum active documents to map."),
    ] = 1000,
    similarity_mode: Annotated[
        str,
        typer.Option("--similarity-mode", help="Saved map similarity mode."),
    ] = "tfidf",
    model_id: Annotated[
        str | None,
        typer.Option("--model-id", help="Reserved for future dense map runs."),
    ] = None,
    force_name: Annotated[
        bool,
        typer.Option(
            "--force-name",
            help="Reserved for future duplicate-name enforcement.",
        ),
    ] = False,
    json_out: Annotated[
        Path | None,
        typer.Option("--json-out", help="Optional saved map run JSON export."),
    ] = None,
) -> None:
    """Build and persist a saved local map run."""

    console = get_console()
    del force_name
    try:
        payload = build_and_store_map_run(
            project_dir=project_dir.expanduser().resolve(),
            name=name,
            seed=seed,
            clusters=clusters,
            neighbors=neighbors,
            limit=limit,
            similarity_mode=similarity_mode,
            model_id=model_id,
        )
    except (MissingDependencyError, ValueError) as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(1) from exc
    run = payload["map_run"]
    if isinstance(run, dict):
        table = _map_run_table([run], title="Saved Map Run")
        console.print(table)
        if json_out is not None:
            output = export_map_run(
                project_dir=project_dir.expanduser().resolve(),
                run_id=str(run["id"]),
                output_path=json_out,
            )
            console.print(f"Wrote saved map run JSON to {output}.")


@app.command("map-runs")
def map_runs_command(
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print JSON instead of a table."),
    ] = False,
) -> None:
    """List saved local map runs."""

    repository = _open_repository(project_dir.expanduser().resolve())
    try:
        runs = repository.list_map_runs()
    finally:
        repository.connection.close()
    if json_output:
        get_console().print_json(data={"map_runs": runs})
    else:
        get_console().print(_map_run_table(runs))


@app.command("show-map-run")
def show_map_run_command(
    run_id: Annotated[str, typer.Argument(help="Saved map run id.")],
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print JSON instead of a table."),
    ] = False,
) -> None:
    """Show saved map run metadata and counts."""

    try:
        payload = persisted_map_payload(
            project_dir=project_dir.expanduser().resolve(), run_id=run_id
        )
    except ValueError as exc:
        get_console().print(str(exc))
        raise typer.Exit(1) from exc
    if json_output:
        get_console().print_json(data=payload)
        return
    run = payload.get("map_run")
    table = _map_run_table([run] if isinstance(run, dict) else [], title="Map Run")
    get_console().print(table)
    documents = _object_list(payload.get("documents"))
    points = _object_list(payload.get("points"))
    clusters = _object_list(payload.get("clusters"))
    get_console().print(
        f"Documents: {len(documents)}; Points: {len(points)}; Clusters: {len(clusters)}"
    )


@app.command("delete-map-run")
def delete_map_run_command(
    run_id: Annotated[str, typer.Argument(help="Saved map run id.")],
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Delete without an interactive prompt."),
    ] = False,
) -> None:
    """Delete a saved map run."""

    if not yes and not typer.confirm(f"Delete saved map run {run_id}?"):
        get_console().print("Cancelled.")
        return
    repository = _open_repository(project_dir.expanduser().resolve())
    try:
        with repository.connection:
            deleted = repository.delete_map_run(run_id)
    finally:
        repository.connection.close()
    get_console().print("Deleted." if deleted else "No saved map run found.")


@app.command("export-map-run")
def export_map_run_command(
    run_id: Annotated[str, typer.Argument(help="Saved map run id.")],
    out: Annotated[
        Path,
        typer.Option("--out", help="Output JSON path for the saved map run."),
    ] = Path("map-run.json"),
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
) -> None:
    """Export a saved map run as JSON without document text."""

    try:
        output = export_map_run(
            project_dir=project_dir.expanduser().resolve(),
            run_id=run_id,
            output_path=out,
        )
    except ValueError as exc:
        get_console().print(str(exc))
        raise typer.Exit(1) from exc
    get_console().print(f"Wrote saved map run JSON to {output}.")


@app.command("export-project")
def export_project_command(
    out: Annotated[
        Path,
        typer.Option("--out", help="Output zip backup path."),
    ] = Path("paper-galaxy-backup.zip"),
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
    include_db: Annotated[
        bool,
        typer.Option("--include-db/--no-include-db", help="Include the SQLite DB."),
    ] = True,
    include_vector_indexes: Annotated[
        bool,
        typer.Option("--include-vector-indexes", help="Include local vector indexes."),
    ] = False,
    include_source_files: Annotated[
        bool,
        typer.Option("--include-source-files", help="Reserved; not available yet."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Confirm exporting the local SQLite DB."),
    ] = False,
) -> None:
    """Export local project metadata and database into a zip backup."""

    try:
        result = export_project(
            project_dir=project_dir.expanduser().resolve(),
            output_path=out,
            include_db=include_db,
            include_vector_indexes=include_vector_indexes,
            include_source_files=include_source_files,
            yes=yes,
        )
    except (PermissionError, ValueError) as exc:
        get_console().print(str(exc))
        raise typer.Exit(1) from exc
    get_console().print(f"Wrote project backup to {result['output_path']}.")


@app.command("import-project")
def import_project_command(
    backup: Annotated[Path, typer.Argument(help="Paper Galaxy backup zip.")],
    project_dir: Annotated[
        Path,
        typer.Option("--project-dir", help="Target project directory."),
    ] = Path("."),
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Overwrite .paper-galaxy if present."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Inspect planned writes without importing."),
    ] = False,
    validate_checksums: Annotated[
        bool,
        typer.Option(
            "--validate-checksums/--no-validate-checksums",
            help="Validate bundle checksums before import.",
        ),
    ] = True,
) -> None:
    """Import a local project backup into a target project directory."""

    try:
        result = import_project(
            backup_path=backup,
            project_dir=project_dir.expanduser().resolve(),
            force=force,
            dry_run=dry_run,
            validate_checksums=validate_checksums,
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        get_console().print(str(exc))
        raise typer.Exit(1) from exc
    action = "Would write" if dry_run else "Imported"
    get_console().print(f"{action}: {', '.join(result['writes']) or 'no files'}.")


@app.command("plugins")
def plugins_command(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print JSON instead of a table."),
    ] = False,
) -> None:
    """List built-in local plugin boundaries."""

    plugins = get_plugin_registry().list_payloads()
    if json_output:
        get_console().print_json(data={"plugins": plugins})
        return
    table = Table(title="Paper Galaxy Built-in Plugins")
    table.add_column("ID")
    table.add_column("Kind")
    table.add_column("Default")
    table.add_column("Local")
    table.add_column("Extensions", overflow="fold")
    for plugin in plugins:
        extensions = _object_list(plugin.get("file_extensions"))
        table.add_row(
            str(plugin["id"]),
            str(plugin["kind"]),
            "yes" if plugin["enabled_by_default"] else "no",
            "yes" if plugin["local_only"] else "no",
            ", ".join(str(item) for item in extensions),
        )
    get_console().print(table)


@app.command("explain-pair")
def explain_pair_command(
    source: Annotated[
        str,
        typer.Argument(help="Source document ID or corpus-relative path."),
    ],
    target: Annotated[
        str,
        typer.Argument(help="Target document ID or corpus-relative path."),
    ],
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
    term_limit: Annotated[
        int,
        typer.Option("--term-limit", help="Maximum shared terms to show."),
    ] = 8,
    chunk_limit: Annotated[
        int,
        typer.Option("--chunk-limit", help="Maximum matching chunk pairs to show."),
    ] = 3,
    model_id: Annotated[
        str | None,
        typer.Option("--model-id", help="Optional dense model id for future evidence."),
    ] = None,
    model: Annotated[
        str,
        typer.Option("--model", help="Reserved local model path for dense evidence."),
    ] = "",
    allow_model_download: Annotated[
        bool,
        typer.Option(
            "--allow-model-download/--no-allow-model-download",
            help="Reserved for future dense pair evidence.",
        ),
    ] = False,
    dense: Annotated[
        bool,
        typer.Option(
            "--dense/--no-dense",
            help="Request optional dense evidence when available.",
        ),
    ] = False,
) -> None:
    """Explain why two local documents are nearby."""

    del model, allow_model_download
    console = get_console()
    repository = _open_repository(project_dir.expanduser().resolve())
    try:
        explanation = explain_pair(
            repository,
            source,
            target,
            term_limit=term_limit,
            chunk_limit=chunk_limit,
            dense=dense,
            model_id=model_id,
        )
    except ValueError as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc
    finally:
        repository.connection.close()

    console.print(f"Source: {explanation.source.title}")
    console.print(f"Target: {explanation.target.title}")
    console.print(f"Lexical score: {explanation.lexical_score:.4f}")
    if explanation.warnings:
        for warning in explanation.warnings:
            console.print(f"Warning: {warning}")

    terms_table = Table(title="Shared Terms")
    terms_table.add_column("Term")
    terms_table.add_column("Score", justify="right")
    for term in explanation.shared_terms:
        terms_table.add_row(term.term, f"{term.score:.4f}")
    console.print(terms_table)

    chunk_table = Table(title="Chunk Matches")
    chunk_table.add_column("Source chunk", justify="right")
    chunk_table.add_column("Target chunk", justify="right")
    chunk_table.add_column("Score", justify="right")
    chunk_table.add_column("Shared terms", overflow="fold")
    for match in explanation.chunk_matches:
        chunk_table.add_row(
            str(match.source_chunk_index),
            str(match.target_chunk_index),
            f"{match.score:.4f}",
            ", ".join(match.shared_terms),
        )
    console.print(chunk_table)


@zotero_app.command("detect")
def zotero_detect_command(
    data_dir: Annotated[
        Path | None,
        typer.Option("--data-dir", help="Explicit Zotero data directory."),
    ] = None,
    api_url: Annotated[
        str,
        typer.Option("--api-url", help="Zotero local API base URL."),
    ] = DEFAULT_LOCAL_API_URL,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Local API timeout in seconds."),
    ] = 2.0,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print JSON instead of a table."),
    ] = False,
) -> None:
    """Detect local Zotero API and data directory state."""

    console = get_console()
    payload = detection_payload(
        detect_zotero(api_url=api_url, data_dir=data_dir, timeout=timeout)
    )
    if json_output:
        console.print_json(json.dumps(payload, sort_keys=True))
        return
    table = Table(title="Paper Galaxy Zotero Detection")
    table.add_column("Check", style="bold")
    table.add_column("Value", overflow="fold")
    table.add_row("Local API reachable", str(payload["api_reachable"]))
    table.add_row("API URL", str(payload["api_url"]))
    table.add_row("Data dir", str(payload["data_dir"]))
    table.add_row("zotero.sqlite exists", str(payload["database_exists"]))
    table.add_row("storage/ exists", str(payload["storage_exists"]))
    if payload.get("api_error"):
        table.add_row("API error", str(payload["api_error"]))
    table.add_row("Recommended next command", str(payload["recommended_next_command"]))
    console.print(table)
    console.print(str(payload["note"]))


@zotero_app.command("status")
def zotero_status_command(
    api_url: Annotated[
        str,
        typer.Option("--api-url", help="Zotero local API base URL."),
    ] = DEFAULT_LOCAL_API_URL,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Local API timeout in seconds."),
    ] = 2.0,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print JSON instead of a table."),
    ] = False,
) -> None:
    """Check whether Zotero Desktop local API appears enabled."""

    console = get_console()
    client = LocalZoteroAPIClient(api_url, timeout=timeout)
    try:
        root = client.root()
        top_items = client.top_items(limit=1)
        collections = client.collections(limit=1)
        tags = client.tags(limit=1)
    except ZoteroAPIError as exc:
        next_steps = [
            "Open Zotero Desktop.",
            "Make sure the local API is enabled.",
            "Use Zotero Settings -> Advanced -> Files and Folders -> "
            "Show Data Directory.",
            "Retry: paper-galaxy zotero status",
        ]
        payload = {
            "reachable": False,
            "api_url": api_url,
            "error": str(exc),
            "next_steps": next_steps,
        }
        if json_output:
            console.print_json(json.dumps(payload, sort_keys=True))
        else:
            console.print(payload["error"])
            for step in next_steps:
                console.print(f"- {step}")
        raise typer.Exit(1) from exc
    payload = {
        "reachable": True,
        "api_url": api_url,
        "root": root,
        "top_items_probe_count": len(top_items),
        "collections_probe_count": len(collections),
        "tags_probe_count": len(tags),
    }
    if json_output:
        console.print_json(json.dumps(payload, sort_keys=True))
        return
    table = Table(title="Paper Galaxy Zotero Status")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Local API reachable", "true")
    table.add_row("API URL", api_url)
    table.add_row("Top items probe", str(len(top_items)))
    table.add_row("Collections probe", str(len(collections)))
    table.add_row("Tags probe", str(len(tags)))
    console.print(table)


@zotero_app.command("doctor")
def zotero_doctor_command(
    project_dir: Annotated[Path, typer.Option("--project-dir")] = Path("."),
    api_url: Annotated[
        str,
        typer.Option("--api-url", help="Zotero local API base URL."),
    ] = DEFAULT_LOCAL_API_URL,
    data_dir: Annotated[
        Path | None,
        typer.Option("--data-dir", help="Explicit Zotero data directory."),
    ] = None,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Local API timeout in seconds."),
    ] = 2.0,
    limit: Annotated[int, typer.Option("--limit")] = 20,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print JSON instead of a table."),
    ] = False,
    json_out: Annotated[Path | None, typer.Option("--json-out")] = None,
) -> None:
    """Run a no-write real-machine Zotero readiness check."""

    _print_zotero_doctor(
        project_dir=project_dir,
        api_url=api_url,
        data_dir=data_dir,
        timeout=timeout,
        limit=limit,
        verbose=verbose,
        json_output=json_output,
        json_out=json_out,
    )


@zotero_app.command("validate-local")
def zotero_validate_local_command(
    project_dir: Annotated[Path, typer.Option("--project-dir")] = Path("."),
    api_url: Annotated[
        str,
        typer.Option("--api-url", help="Zotero local API base URL."),
    ] = DEFAULT_LOCAL_API_URL,
    data_dir: Annotated[
        Path | None,
        typer.Option("--data-dir", help="Explicit Zotero data directory."),
    ] = None,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Local API timeout in seconds."),
    ] = 2.0,
    limit: Annotated[int, typer.Option("--limit")] = 20,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    json_out: Annotated[Path | None, typer.Option("--json-out")] = None,
) -> None:
    """Alias for `paper-galaxy zotero doctor`."""

    _print_zotero_doctor(
        project_dir=project_dir,
        api_url=api_url,
        data_dir=data_dir,
        timeout=timeout,
        limit=limit,
        verbose=False,
        json_output=json_output,
        json_out=json_out,
    )


@zotero_app.command("collections")
def zotero_collections_command(
    api_url: Annotated[str, typer.Option("--api-url")] = DEFAULT_LOCAL_API_URL,
    project_dir: Annotated[Path, typer.Option("--project-dir")] = Path("."),
    limit: Annotated[int, typer.Option("--limit")] = 50,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List Zotero collections from the read-only local API."""

    del project_dir
    console = get_console()
    try:
        rows = [
            normalize_collection(row)
            for row in LocalZoteroAPIClient(api_url).collections(limit=limit)
        ]
    except ZoteroAPIError as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc
    paths = collection_paths(rows)
    payload = [
        {
            "key": row.key,
            "name": row.name,
            "path": paths.get(row.key),
            "parent_key": row.parent_key,
            "version": row.version,
        }
        for row in rows
    ]
    if json_output:
        console.print_json(json.dumps(payload, sort_keys=True))
        return
    table = Table(title="Zotero Collections")
    table.add_column("Key")
    table.add_column("Name")
    table.add_column("Path", overflow="fold")
    table.add_column("Parent")
    for row in payload:
        table.add_row(
            str(row["key"]),
            str(row["name"]),
            str(row["path"] or ""),
            str(row["parent_key"] or ""),
        )
    console.print(table)


@zotero_app.command("items")
def zotero_items_command(
    api_url: Annotated[str, typer.Option("--api-url")] = DEFAULT_LOCAL_API_URL,
    limit: Annotated[int, typer.Option("--limit")] = 20,
    collection: Annotated[str | None, typer.Option("--collection")] = None,
    tag: Annotated[str | None, typer.Option("--tag")] = None,
    item_type: Annotated[str | None, typer.Option("--item-type")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Preview Zotero items from the read-only local API without importing."""

    console = get_console()
    client = LocalZoteroAPIClient(api_url)
    try:
        collections = [normalize_collection(row) for row in client.collections()]
        selected_collection = (
            resolve_collection(collections, collection) if collection else None
        )
        raw_rows = (
            client.collection_items(selected_collection.key, limit=limit)
            if selected_collection
            else client.top_items(limit=limit)
        )
        rows = [normalize_item(row) for row in raw_rows]
        if tag:
            validate_non_empty_values((tag,), option_name="--tag")
        if item_type:
            validate_non_empty_values((item_type,), option_name="--item-type")
        rows = filter_items(
            rows,
            collection_key=selected_collection.key if selected_collection else None,
            tags=(tag,) if tag else (),
            item_types=(item_type,) if item_type else (),
        )
    except (ZoteroAPIError, ZoteroFilterError) as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc
    payload = [
        {
            "key": row.key,
            "title": row.title,
            "year": row.year,
            "creators": [creator.display_name for creator in row.creators],
            "item_type": row.item_type,
            "tags": [tag_row.tag for tag_row in row.tags],
            "collections": list(row.collections),
        }
        for row in rows
    ]
    if json_output:
        console.print_json(json.dumps(payload, sort_keys=True))
        return
    table = Table(title="Zotero Item Preview")
    table.add_column("Key")
    table.add_column("Title", overflow="fold")
    table.add_column("Year")
    table.add_column("Type")
    table.add_column("Tags", overflow="fold")
    for row in rows:
        table.add_row(
            row.key,
            row.title,
            str(row.year or ""),
            row.item_type,
            ", ".join(tag_value.tag for tag_value in row.tags),
        )
    console.print(table)


@zotero_app.command("import")
def zotero_import_command(
    project_dir: Annotated[Path, typer.Option("--project-dir")] = Path("."),
    api_url: Annotated[str, typer.Option("--api-url")] = DEFAULT_LOCAL_API_URL,
    data_dir: Annotated[Path | None, typer.Option("--data-dir")] = None,
    library: Annotated[
        str,
        typer.Option(
            "--library",
            help=(
                "Beta supports only Zotero Desktop local user library aliases: "
                "local, user, users/0, /users/0."
            ),
        ),
    ] = "local",
    collection: Annotated[str | None, typer.Option("--collection")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
    item_type: Annotated[list[str] | None, typer.Option("--item-type")] = None,
    include_pdfs: Annotated[
        bool,
        typer.Option("--include-pdfs/--no-include-pdfs"),
    ] = True,
    include_notes: Annotated[
        bool,
        typer.Option("--include-notes/--no-include-notes"),
    ] = True,
    include_attachments: Annotated[
        bool,
        typer.Option("--include-attachments/--no-include-attachments"),
    ] = True,
    include_metadata_only: Annotated[
        bool,
        typer.Option("--include-metadata-only/--no-include-metadata-only"),
    ] = True,
    pdf_policy: Annotated[
        str,
        typer.Option(
            "--pdf-policy",
            help="PDF handling policy: extract, metadata, or skip-missing.",
        ),
    ] = "extract",
    read_tag: Annotated[list[str] | None, typer.Option("--read-tag")] = None,
    reading_tag: Annotated[list[str] | None, typer.Option("--reading-tag")] = None,
    to_read_tag: Annotated[list[str] | None, typer.Option("--to-read-tag")] = None,
    include_status: Annotated[str, typer.Option("--include-status")] = "all",
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    since_version: Annotated[int | None, typer.Option("--since-version")] = None,
    force: Annotated[bool, typer.Option("--force")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    build_reading_map: Annotated[
        bool,
        typer.Option("--build-reading-map/--no-build-reading-map"),
    ] = True,
    map_name: Annotated[str, typer.Option("--map-name")] = "Zotero Reading Graph",
    min_chars: Annotated[int, typer.Option("--min-chars")] = 40,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
    json_out: Annotated[Path | None, typer.Option("--json-out")] = None,
) -> None:
    """Import Zotero items into Paper Galaxy local SQLite state."""

    console = get_console()
    try:
        normalize_local_library(library)
        summary = import_from_zotero(
            project_dir=project_dir,
            api_url=api_url,
            data_dir=data_dir,
            collection=collection,
            tags=tuple(tag or ()),
            item_types=tuple(item_type or ()),
            include_pdfs=include_pdfs,
            include_notes=include_notes,
            include_attachments=include_attachments,
            include_metadata_only=include_metadata_only,
            pdf_policy=pdf_policy,
            read_tags=tuple(read_tag or ("read", "Read", "finished")),
            reading_tags=tuple(reading_tag or ("reading", "Reading", "current")),
            to_read_tags=tuple(
                to_read_tag or ("to read", "To Read", "unread", "queue")
            ),
            include_status=include_status,
            limit=limit,
            since_version=since_version,
            force=force,
            dry_run=dry_run,
            build_reading_map=build_reading_map,
            map_name=map_name,
            min_chars=min_chars,
            verbose=verbose,
        )
    except (ZoteroAPIError, ZoteroFilterError) as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc
    payload = _zotero_import_summary_payload(summary)
    if json_out is not None:
        json_out.expanduser().resolve().write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    table = Table(title="Paper Galaxy Zotero Import Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Value", overflow="fold")
    for key, value in payload.items():
        if key in {"warnings", "reading_status_counts"}:
            continue
        table.add_row(key.replace("_", " ").title(), str(value))
    reading_status_counts = payload["reading_status_counts"]
    warnings = payload["warnings"]
    table.add_row("Reading statuses", json.dumps(reading_status_counts))
    if isinstance(warnings, list) and warnings:
        table.add_row("Warnings", "; ".join(str(w) for w in warnings))
    if json_out is not None:
        table.add_row("JSON output", str(json_out.expanduser().resolve()))
    console.print(table)


@zotero_app.command("graph")
def zotero_graph_command(
    project_dir: Annotated[Path, typer.Option("--project-dir")] = Path("."),
    status: Annotated[str, typer.Option("--status")] = "all",
    collection: Annotated[str | None, typer.Option("--collection")] = None,
    tag: Annotated[str | None, typer.Option("--tag")] = None,
    seed: Annotated[int, typer.Option("--seed")] = 42,
    clusters: Annotated[int | None, typer.Option("--clusters")] = None,
    neighbors: Annotated[int, typer.Option("--neighbors")] = 5,
    limit: Annotated[int, typer.Option("--limit")] = 1000,
    name: Annotated[str, typer.Option("--name")] = "Zotero Reading Graph",
    show: Annotated[bool, typer.Option("--show")] = False,
) -> None:
    """Build a saved Zotero reading graph map run from imported documents."""

    del show
    console = get_console()
    try:
        status_selection = normalize_reading_status(status, option_name="--status")
    except ZoteroFilterError as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc
    if status_selection.warning:
        console.print(f"Warning: {status_selection.warning}")
    saved = build_and_store_zotero_reading_map(
        project_dir=project_dir,
        name=name,
        status=status_selection.value,
        collection=collection,
        tag=tag,
        seed=seed,
        clusters=clusters,
        neighbors=neighbors,
        limit=limit,
    )
    run_value = saved.get("map_run", {})
    run = run_value if isinstance(run_value, dict) else {}
    console.print(f"Saved Zotero reading graph: {run.get('id', 'unknown')}")
    console.print(f"Documents: {len(_object_list(saved.get('documents')))}")
    console.print(f"Clusters: {len(_object_list(saved.get('clusters')))}")


@zotero_app.command("imported")
def zotero_imported_command(
    project_dir: Annotated[Path, typer.Option("--project-dir")] = Path("."),
    limit: Annotated[int, typer.Option("--limit")] = 50,
    status: Annotated[str, typer.Option("--status")] = "all",
    collection: Annotated[str | None, typer.Option("--collection")] = None,
    tag: Annotated[str | None, typer.Option("--tag")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List Zotero items already imported into Paper Galaxy."""

    console = get_console()
    try:
        status_selection = normalize_reading_status(status, option_name="--status")
    except ZoteroFilterError as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc
    if status_selection.warning:
        console.print(f"Warning: {status_selection.warning}")
    repository = _open_repository(project_dir.expanduser().resolve())
    try:
        items = repository.list_zotero_items(
            limit=limit,
            status=status_selection.value,
            collection=collection,
            tag=tag,
        )
    finally:
        repository.connection.close()
    if json_output:
        console.print_json(json.dumps(items, sort_keys=True))
        return
    table = Table(title="Imported Zotero Items")
    table.add_column("ID", overflow="fold")
    table.add_column("Title", overflow="fold")
    table.add_column("Status")
    table.add_column("Year")
    table.add_column("Tags", overflow="fold")
    for item in items:
        tag_rows = item.get("tags")
        tag_labels: list[str] = []
        if isinstance(tag_rows, list):
            tag_labels = [
                str(tag_row.get("tag", ""))
                for tag_row in tag_rows
                if isinstance(tag_row, dict)
            ]
        table.add_row(
            str(item["id"]),
            str(item["title"]),
            str(item["reading_status"]),
            str(item.get("year") or ""),
            ", ".join(tag_labels),
        )
    console.print(table)


@zotero_app.command("validate")
def zotero_validate_command(
    project_dir: Annotated[Path, typer.Option("--project-dir")] = Path("."),
    data_dir: Annotated[Path | None, typer.Option("--data-dir")] = None,
    json_out: Annotated[Path | None, typer.Option("--json-out")] = None,
) -> None:
    """Validate imported Zotero state inside a Paper Galaxy project."""

    del data_dir
    console = get_console()
    repository = _open_repository(project_dir.expanduser().resolve())
    try:
        payload = {
            "stats": repository.zotero_stats(),
            "dangling_counts": repository.zotero_dangling_counts(),
        }
    finally:
        repository.connection.close()
    if json_out is not None:
        json_out.expanduser().resolve().write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    console.print_json(json.dumps(payload, sort_keys=True))


@zotero_app.command("smoke-test")
def zotero_smoke_test_command(
    project_dir: Annotated[Path, typer.Option("--project-dir")] = Path("."),
    api_url: Annotated[str, typer.Option("--api-url")] = DEFAULT_LOCAL_API_URL,
    data_dir: Annotated[Path | None, typer.Option("--data-dir")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 10,
    no_write: Annotated[bool, typer.Option("--no-write")] = True,
) -> None:
    """Fetch a small Zotero sample and report what would be imported."""

    del no_write
    console = get_console()
    try:
        summary = import_from_zotero(
            project_dir=project_dir,
            api_url=api_url,
            data_dir=data_dir,
            limit=limit,
            dry_run=True,
            build_reading_map=False,
        )
    except ZoteroAPIError as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc
    payload = _zotero_import_summary_payload(summary)
    console.print_json(json.dumps(payload, sort_keys=True))


@app.command("serve")
def serve_command(
    project_dir: Annotated[
        Path,
        typer.Option(
            "--project-dir", help="Project directory containing .paper-galaxy."
        ),
    ] = Path("."),
    host: Annotated[
        str,
        typer.Option("--host", help="Host interface for the local app."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", help="Port for the local app."),
    ] = 8765,
    reload: Annotated[
        bool,
        typer.Option(
            "--reload",
            help="Enable Uvicorn reload mode for development.",
        ),
    ] = False,
    open_browser: Annotated[
        bool,
        typer.Option(
            "--open/--no-open",
            help="Open the local app in the default browser after startup.",
        ),
    ] = False,
    seed: Annotated[
        int,
        typer.Option("--seed", help="Random seed for deterministic map layout."),
    ] = 42,
    clusters: Annotated[
        int | None,
        typer.Option("--clusters", help="Optional cluster count for the map."),
    ] = None,
    neighbors: Annotated[
        int,
        typer.Option("--neighbors", help="Nearest neighbors per document."),
    ] = 5,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum active documents to map."),
    ] = 1000,
) -> None:
    """Serve the local Phase 3 browser app."""

    console = get_console()
    try:
        from paper_galaxy.web.server import serve_app

        serve_app(
            project_dir=project_dir.expanduser().resolve(),
            host=host,
            port=port,
            reload=reload,
            open_browser=open_browser,
            seed=seed,
            clusters=clusters,
            neighbors=neighbors,
            map_limit=limit,
        )
    except MissingDependencyError as exc:
        del exc
        console.print(
            "Missing optional dependency for Phase 3 app. Install with: "
            'python -m pip install -e ".[dev,ml,pdf,app]"',
            markup=False,
        )
        raise typer.Exit(1) from None


def _print_zotero_doctor(
    *,
    project_dir: Path,
    api_url: str,
    data_dir: Path | None,
    timeout: float,
    limit: int,
    verbose: bool,
    json_output: bool,
    json_out: Path | None,
) -> None:
    console = get_console()
    report = validate_local_zotero(
        project_dir=project_dir,
        api_url=api_url,
        data_dir=data_dir,
        timeout=timeout,
        limit=limit,
        verbose=verbose,
    )
    payload = report.payload()
    if json_out is not None:
        json_out.expanduser().resolve().write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if json_output:
        console.print_json(json.dumps(payload, sort_keys=True))
        return
    table = _zotero_doctor_table(report)
    console.print(table)
    if report.next_steps:
        console.print("Next steps:")
        for step in report.next_steps:
            console.print(f"- {step}")
    if json_out is not None:
        console.print(f"JSON output: {json_out.expanduser().resolve()}")


def _zotero_doctor_table(report: ZoteroDoctorReport) -> Table:
    table = Table(title=f"Paper Galaxy Zotero Doctor: {report.readiness}")
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Severity")
    table.add_column("Message", overflow="fold")
    for check in report.checks:
        table.add_row(check.name, check.status, check.severity, check.message)
    table.add_row("API URL", "info", "info", report.api_url)
    table.add_row("Project DB", "info", "info", str(report.database_path))
    return table


def _print_embeddings_dependency_error() -> None:
    get_console().print(
        "Missing optional dependency for Phase 5 embeddings. Install with: "
        'python -m pip install -e ".[dev,ml,pdf,app,embeddings]"',
        markup=False,
    )


def _zotero_import_summary_payload(
    summary: ZoteroImportRunSummary,
) -> dict[str, object]:
    return {
        "run_id": str(summary.run_id),
        "source_id": str(summary.source_id),
        "project_dir": str(summary.project_dir),
        "database_path": str(summary.database_path),
        "dry_run": bool(summary.dry_run),
        "items_seen": int(summary.items_seen),
        "items_fetched": int(summary.items_fetched),
        "items_selected": int(summary.items_selected),
        "items_filtered_out": int(summary.items_filtered_out),
        "items_imported": int(summary.items_imported),
        "items_updated": int(summary.items_updated),
        "items_unchanged": int(summary.items_unchanged),
        "attachments_seen": int(summary.attachments_seen),
        "attachments_resolved": int(summary.attachments_resolved),
        "attachment_status_counts": dict(summary.attachment_status_counts),
        "stored_attachments": int(summary.stored_attachments),
        "linked_attachments": int(summary.linked_attachments),
        "pdfs_seen": int(summary.pdfs_seen),
        "pdfs_extracted": int(summary.pdfs_extracted),
        "pdfs_missing": int(summary.pdfs_missing),
        "pdfs_extraction_failed": int(summary.pdfs_extraction_failed),
        "notes_imported": int(summary.notes_imported),
        "annotations_imported": int(summary.annotations_imported),
        "metadata_only_documents": int(summary.metadata_only_documents),
        "skipped": int(summary.skipped),
        "filters": dict(summary.filters),
        "selected_collection": summary.selected_collection,
        "include_status": str(summary.include_status),
        "since_version": summary.since_version,
        "last_version_before": summary.last_version_before,
        "last_version_after": summary.last_version_after,
        "warnings": list(summary.warnings),
        "reading_status_counts": dict(summary.reading_status_counts),
        "map_run_id": summary.map_run_id,
    }


def _neighbors_table(title: str, neighbors: list[NeighborResult]) -> Table:
    table = Table(title=title)
    table.add_column("#", justify="right")
    table.add_column("Title")
    table.add_column("Relative path", overflow="fold")
    table.add_column("Score", justify="right")
    for neighbor in neighbors:
        table.add_row(
            str(neighbor.rank),
            neighbor.title,
            neighbor.relative_path,
            f"{neighbor.score:.4f}",
        )
    return table


def _map_run_table(
    runs: list[dict[str, object]], *, title: str = "Paper Galaxy Saved Map Runs"
) -> Table:
    table = Table(title=title)
    table.add_column("ID", overflow="fold")
    table.add_column("Name", overflow="fold")
    table.add_column("Created")
    table.add_column("Mode")
    table.add_column("Docs", justify="right")
    table.add_column("Clusters", justify="right")
    for run in runs:
        table.add_row(
            str(run.get("id", "")),
            str(run.get("name", "")),
            str(run.get("created_at", "")),
            str(run.get("similarity_mode", "")),
            str(run.get("document_count", "")),
            str(run.get("cluster_count", "")),
        )
    return table


def _object_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _open_repository(project_dir: Path) -> Repository:
    connection = connect_database(project_dir)
    initialize_database(connection)
    return Repository(connection, resolve_database_path(project_dir))


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
