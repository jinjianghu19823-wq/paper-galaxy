from __future__ import annotations

from pathlib import Path

import pytest

from paper_galaxy.services.jobs import enqueue_job, request_job_cancel
from paper_galaxy.services.sources import (
    SOURCE_KIND_ZOTERO,
    get_source,
    list_sources,
    public_source_payload,
    register_corpus_source,
    register_zotero_source,
    remove_source,
    validate_source_locator_for_use,
)
from paper_galaxy.storage.sqlite import (
    connect_read_write,
    ensure_database_ready,
)


def _insert_zotero_source(
    project_dir: Path,
    *,
    source_id: str = "zotero_source_synthetic",
    api_url: str = "http://127.0.0.1:23119/api/",
    data_dir: str | None = None,
    source_type: str = "local_api",
) -> None:
    ensure_database_ready(project_dir)
    connection = connect_read_write(project_dir)
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO zotero_sources(
                  id, source_type, local_api_url, data_dir, library_id,
                  library_type, name, last_version, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, '0', 'user', 'Synthetic Zotero', NULL,
                        '2026-01-01', '2026-01-01')
                """,
                (source_id, source_type, api_url, data_dir),
            )
    finally:
        connection.close()


def test_zotero_registration_reads_existing_source_and_canonicalizes_profile(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    data_dir = tmp_path / "zotero" / "nested" / ".."
    (tmp_path / "zotero").mkdir()
    _insert_zotero_source(project, data_dir=str(data_dir))

    first, first_created = register_zotero_source(
        project,
        "zotero_source_synthetic",
        filters={
            "tags": ["operator learning", "read", "read"],
            "collections": ["Methods"],
            "include_status": "to_read",
        },
    )
    second, second_created = register_zotero_source(
        project,
        "zotero_source_synthetic",
        filters={
            "include_status": "to-read",
            "collections": ["Methods"],
            "tags": ["read", "operator learning"],
        },
    )
    public = public_source_payload(first)

    assert first_created is True
    assert second_created is False
    assert first == second
    assert first.kind == SOURCE_KIND_ZOTERO
    assert first.root_path is None
    assert first.zotero_source_id == "zotero_source_synthetic"
    assert first.config == {
        "data_dir": str((tmp_path / "zotero").resolve()),
        "filters": {
            "collections": ["Methods"],
            "include_status": "to_read",
            "tags": ["operator learning", "read"],
        },
        "library_id": "0",
        "library_type": "user",
        "local_api_url": "http://127.0.0.1:23119/api/",
    }
    assert set(public) == {
        "id",
        "kind",
        "display_name",
        "status",
        "last_success_at",
        "last_error",
        "created_at",
        "updated_at",
    }
    assert str(tmp_path) not in str(public)
    assert "127.0.0.1" not in str(public)
    assert "config" not in public


def test_zotero_profile_rejects_multiple_collections_until_union_sync_exists(
    tmp_path: Path,
) -> None:
    _insert_zotero_source(tmp_path)

    with pytest.raises(ValueError, match=r"at most one collection"):
        register_zotero_source(
            tmp_path,
            "zotero_source_synthetic",
            filters={"collections": ["Methods", "Archive"]},
        )

    assert list_sources(tmp_path, kind=SOURCE_KIND_ZOTERO) == []


def test_zotero_filter_profiles_have_independent_stable_ids(tmp_path: Path) -> None:
    _insert_zotero_source(tmp_path)

    unfiltered, _ = register_zotero_source(tmp_path, "zotero_source_synthetic")
    filtered, _ = register_zotero_source(
        tmp_path,
        "zotero_source_synthetic",
        filters={"tags": ["todo"]},
    )

    assert unfiltered.id != filtered.id
    assert unfiltered.profile_signature != filtered.profile_signature
    assert len(list_sources(tmp_path, kind=SOURCE_KIND_ZOTERO)) == 2


def test_source_registration_never_places_project_database_inside_corpus(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    sentinel = corpus / "keep.txt"
    sentinel.write_bytes(b"private source bytes")
    config = project / ".paper-galaxy" / "project.toml"
    config.parent.mkdir(parents=True)
    database = corpus / "paper-galaxy.sqlite3"
    config.write_text(
        "\n".join(
            [
                'project_name = "Synthetic"',
                'created_by = "test"',
                "map_seed = 42",
                "corpus_dirs = []",
                f'database_path = "{database.as_posix()}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"database cannot be stored inside"):
        register_corpus_source(project, corpus)

    assert sentinel.read_bytes() == b"private source bytes"
    assert not database.exists()


def test_source_registration_rejects_project_metadata_as_source(
    tmp_path: Path,
) -> None:
    ensure_database_ready(tmp_path)
    metadata = tmp_path / ".paper-galaxy"
    before = {
        path.name: path.read_bytes() for path in metadata.iterdir() if path.is_file()
    }

    with pytest.raises(ValueError, match=r"metadata or database cannot be stored"):
        register_corpus_source(tmp_path, metadata)

    after = {
        path.name: path.read_bytes() for path in metadata.iterdir() if path.is_file()
    }
    assert after == before


def test_repeated_registration_preserves_custom_display_name(tmp_path: Path) -> None:
    project = tmp_path / "project"
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    ensure_database_ready(project)

    first, created = register_corpus_source(
        project,
        corpus,
        display_name="My Papers",
    )
    repeated, repeated_created = register_corpus_source(project, corpus)

    assert created is True
    assert repeated_created is False
    assert first.id == repeated.id
    assert repeated.display_name == "My Papers"


def test_project_backup_directory_cannot_be_registered_as_source(
    tmp_path: Path,
) -> None:
    ensure_database_ready(tmp_path)
    backups = tmp_path / ".paper-galaxy" / "backups"
    backups.mkdir()

    with pytest.raises(ValueError, match=r"project metadata or database"):
        register_corpus_source(tmp_path, backups)

    assert list_sources(tmp_path) == []


def test_zotero_registration_rejects_project_metadata_inside_data_dir(
    tmp_path: Path,
) -> None:
    ensure_database_ready(tmp_path)
    metadata = tmp_path / ".paper-galaxy"
    _insert_zotero_source(tmp_path, data_dir=str(metadata))
    before = {
        path.name: path.read_bytes() for path in metadata.iterdir() if path.is_file()
    }

    with pytest.raises(ValueError, match=r"Zotero data directory"):
        register_zotero_source(tmp_path, "zotero_source_synthetic")

    after = {
        path.name: path.read_bytes() for path in metadata.iterdir() if path.is_file()
    }
    assert after == before


@pytest.mark.parametrize(
    "api_url",
    [
        "https://127.0.0.1:23119/api",
        "http://example.invalid:23119/api",
        "http://127.0.0.1:23119@evil.invalid/api",
        "http://user:password@127.0.0.1:23119/api",
        "http://127.0.0.1:23119/api?target=https://example.invalid",
        "http://127.0.0.1:23119/api#private",
    ],
)
def test_zotero_registration_rejects_non_loopback_or_ambiguous_urls(
    tmp_path: Path,
    api_url: str,
) -> None:
    _insert_zotero_source(tmp_path, api_url=api_url)

    with pytest.raises(ValueError, match=r"Zotero local API|loopback"):
        register_zotero_source(tmp_path, "zotero_source_synthetic")

    assert list_sources(tmp_path) == []


def test_zotero_registration_rejects_unknown_sources_and_untrusted_input(
    tmp_path: Path,
) -> None:
    ensure_database_ready(tmp_path)

    with pytest.raises(ValueError, match="was not found"):
        register_zotero_source(tmp_path, "zotero_source_missing")
    with pytest.raises(ValueError, match="safe characters"):
        register_zotero_source(tmp_path, "../../zotero")

    _insert_zotero_source(tmp_path, source_id="zotero_source_synthetic")
    with pytest.raises(ValueError, match="Unsupported Zotero source filters"):
        register_zotero_source(
            tmp_path,
            "zotero_source_synthetic",
            filters={"raw_url": "https://example.invalid/private"},
        )
    with pytest.raises(ValueError, match="control characters"):
        register_zotero_source(
            tmp_path,
            "zotero_source_synthetic",
            display_name="Unsafe\nname",
        )
    with pytest.raises(ValueError, match="1 to 120"):
        register_zotero_source(
            tmp_path,
            "zotero_source_synthetic",
            display_name="",
        )


def test_remove_source_is_soft_idempotent_and_registration_restores_it(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source, created = register_corpus_source(tmp_path / "project", corpus)
    assert created is True

    removed = remove_source(tmp_path / "project", source.id)
    removed_again = remove_source(tmp_path / "project", source.id)

    assert removed.removed_at is not None
    assert removed_again == removed
    assert list_sources(tmp_path / "project") == []
    assert list_sources(tmp_path / "project", include_removed=True) == [removed]
    assert get_source(tmp_path / "project", source.id) is None
    assert get_source(tmp_path / "project", source.id, include_removed=True) == removed
    restored, restored_created = register_corpus_source(tmp_path / "project", corpus)
    assert restored_created is False
    assert restored.id == source.id
    assert restored.removed_at is None
    assert len(list_sources(tmp_path / "project")) == 1


def test_remove_source_refuses_active_jobs_and_preserves_the_source(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source, _ = register_corpus_source(project, corpus)
    job, _ = enqueue_job(
        project,
        kind="index_corpus",
        source_id=source.id,
        params={"min_chars": 1},
    )

    with pytest.raises(ValueError, match="active work"):
        remove_source(project, source.id)

    assert get_source(project, source.id) == source
    request_job_cancel(project, job.id)
    removed = remove_source(project, source.id)
    assert removed.removed_at is not None
    connection = connect_read_write(project)
    try:
        assert (
            connection.execute(
                "SELECT status FROM jobs WHERE id = ?", (job.id,)
            ).fetchone()[0]
            == "cancelled"
        )
    finally:
        connection.close()


def test_corpus_locator_is_revalidated_after_swap_to_symlink(tmp_path: Path) -> None:
    project = tmp_path / "project"
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "sentinel.txt").write_text("original", encoding="utf-8")
    source, _ = register_corpus_source(project, corpus)
    assert validate_source_locator_for_use(project, source) == source

    moved = tmp_path / "moved-corpus"
    corpus.rename(moved)
    replacement = tmp_path / "untrusted"
    replacement.mkdir()
    sentinel = replacement / "keep.txt"
    sentinel.write_text("never touched", encoding="utf-8")
    corpus.symlink_to(replacement, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic link"):
        validate_source_locator_for_use(project, source)

    assert sentinel.read_text(encoding="utf-8") == "never touched"
    assert (moved / "sentinel.txt").read_text(encoding="utf-8") == "original"


def test_zotero_locator_is_revalidated_before_use(tmp_path: Path) -> None:
    _insert_zotero_source(tmp_path)
    source, _ = register_zotero_source(tmp_path, "zotero_source_synthetic")
    connection = connect_read_write(tmp_path)
    try:
        with connection:
            connection.execute(
                """
                UPDATE registered_sources
                SET config_json = '{"data_dir":null,"filters":{},"library_id":"0",
                    "library_type":"user","local_api_url":"http://10.0.0.1/api"}'
                WHERE id = ?
                """,
                (source.id,),
            )
    finally:
        connection.close()
    tampered = get_source(tmp_path, source.id)
    assert tampered is not None

    with pytest.raises(ValueError, match="loopback"):
        validate_source_locator_for_use(tmp_path, tampered)


def test_public_source_error_never_returns_private_error_text(tmp_path: Path) -> None:
    corpus = tmp_path / "private" / "papers"
    corpus.mkdir(parents=True)
    source, _ = register_corpus_source(tmp_path / "project", corpus)
    connection = connect_read_write(tmp_path / "project")
    try:
        with connection:
            connection.execute(
                """
                UPDATE registered_sources
                SET last_error_code = 'source_failed', last_error_message = ?
                WHERE id = ?
                """,
                (f"Cannot open {corpus}/private.pdf", source.id),
            )
    finally:
        connection.close()
    failed = get_source(tmp_path / "project", source.id)
    assert failed is not None

    payload = public_source_payload(failed)
    assert str(tmp_path) not in str(payload)
    assert "private.pdf" not in str(payload)
