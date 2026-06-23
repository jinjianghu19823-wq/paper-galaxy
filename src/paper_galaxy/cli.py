"""Command-line interface for Paper Galaxy."""

import importlib.util
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from paper_galaxy import __version__
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
from paper_galaxy.paths import project_config_path
from paper_galaxy.pipeline import build_galaxy
from paper_galaxy.search import get_database_stats, search_index
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path
from paper_galaxy.web.map_builder import build_map_payload

app = typer.Typer(
    help="Local-first research cartography tools.",
    no_args_is_help=True,
)

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


def _print_embeddings_dependency_error() -> None:
    get_console().print(
        "Missing optional dependency for Phase 5 embeddings. Install with: "
        'python -m pip install -e ".[dev,ml,pdf,app,embeddings]"',
        markup=False,
    )


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
