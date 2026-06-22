"""Phase 1 static local galaxy pipeline."""

from __future__ import annotations

import hashlib
from pathlib import Path

from paper_galaxy.export.html import write_html_export
from paper_galaxy.export.json import write_json_export
from paper_galaxy.extract import extract_file
from paper_galaxy.ingest.scanner import discover_files, relative_path
from paper_galaxy.ml.cluster import compute_clusters
from paper_galaxy.ml.labels import label_clusters
from paper_galaxy.ml.layout import compute_layout
from paper_galaxy.ml.neighbors import compute_neighbors
from paper_galaxy.ml.tfidf import compute_tfidf, top_terms_for_documents
from paper_galaxy.models import Document, GalaxyBuildResult, MapPoint, SkippedFile


def build_galaxy(
    corpus_dir: Path,
    output_path: Path,
    *,
    json_output_path: Path | None = None,
    max_documents: int | None = None,
    min_chars: int = 80,
    neighbor_count: int = 5,
    cluster_count: int | None = None,
    seed: int = 42,
    include_pdf: bool = True,
    verbose: bool = False,
) -> GalaxyBuildResult:
    """Build a static local galaxy HTML file from a corpus directory."""

    corpus_path = corpus_dir.expanduser().resolve()
    discovered_files = discover_files(corpus_path, include_pdf=include_pdf)
    files_to_process = (
        discovered_files[:max_documents] if max_documents else discovered_files
    )

    documents: list[Document] = []
    skipped_files: list[SkippedFile] = []
    for path in files_to_process:
        rel_path = relative_path(path, corpus_path)
        extracted, skip_reason = extract_file(path, include_pdf=include_pdf)
        if skip_reason is not None or extracted is None:
            skipped_files.append(
                SkippedFile(
                    path=path, relative_path=rel_path, reason=skip_reason or "unknown"
                )
            )
            continue
        if len(extracted.text) < min_chars:
            skipped_files.append(
                SkippedFile(
                    path=path,
                    relative_path=rel_path,
                    reason=f"extracted text shorter than {min_chars} characters",
                )
            )
            continue
        documents.append(
            Document(
                id=_document_id(rel_path, extracted.text),
                path=path,
                relative_path=rel_path,
                file_type=path.suffix.lower().lstrip("."),
                title=extracted.title,
                text=extracted.text,
                char_count=len(extracted.text),
            )
        )

    if not documents:
        result = GalaxyBuildResult(
            corpus_path=corpus_path,
            files_found=len(discovered_files),
            documents=[],
            skipped_files=skipped_files,
            points=[],
            cluster_labels={},
            output_path=output_path.expanduser().resolve(),
        )
        write_html_export(result, result.output_path)
        if json_output_path is not None:
            write_json_export(result, json_output_path.expanduser().resolve())
        return result

    _, matrix, terms = compute_tfidf([document.text for document in documents])
    coordinates = compute_layout(matrix, seed=seed)
    labels = compute_clusters(matrix, requested=cluster_count, seed=seed)
    cluster_labels = label_clusters(matrix, labels, terms)
    document_neighbors = compute_neighbors(
        matrix,
        documents,
        neighbor_count=neighbor_count,
    )
    document_terms = top_terms_for_documents(matrix, terms)
    points = [
        MapPoint(
            document_id=document.id,
            x=coordinates[index][0],
            y=coordinates[index][1],
            cluster_id=labels[index],
            cluster_label=cluster_labels[labels[index]],
            nearest_neighbors=document_neighbors[document.id],
            top_terms=document_terms[index],
        )
        for index, document in enumerate(documents)
    ]
    result = GalaxyBuildResult(
        corpus_path=corpus_path,
        files_found=len(discovered_files),
        documents=documents,
        skipped_files=skipped_files,
        points=points,
        cluster_labels=cluster_labels,
        output_path=output_path.expanduser().resolve(),
    )
    write_html_export(result, result.output_path)
    if json_output_path is not None:
        write_json_export(result, json_output_path.expanduser().resolve())
    return result


def _document_id(relative_document_path: str, text: str) -> str:
    digest = hashlib.sha256()
    digest.update(relative_document_path.encode("utf-8"))
    digest.update(b"\0")
    digest.update(text.encode("utf-8"))
    return digest.hexdigest()[:16]
