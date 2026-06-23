from pathlib import Path

from paper_galaxy.backup import export_project, import_project, inspect_backup
from paper_galaxy.indexer import index_corpus
from tests.test_indexer import copy_tiny_corpus


def test_export_project_requires_confirmation_for_database(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)

    try:
        export_project(
            project_dir=tmp_path,
            output_path=tmp_path / "backup.zip",
        )
    except PermissionError as exc:
        assert "--yes" in str(exc)
    else:
        raise AssertionError("Expected database export to require confirmation.")


def test_export_inspect_and_import_project_backup(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    output = tmp_path / "paper-galaxy-backup.zip"

    exported = export_project(
        project_dir=tmp_path,
        output_path=output,
        yes=True,
    )
    inspected = inspect_backup(output)
    target = tmp_path / "restored"
    dry_run = import_project(backup_path=output, project_dir=target, dry_run=True)
    imported = import_project(backup_path=output, project_dir=target)

    assert output.exists()
    assert "database.sqlite3" in exported["files"]
    assert inspected["checksum_status"] == "ok"
    assert inspected["manifest"]["source_files_included"] is False
    assert dry_run["dry_run"] is True
    assert imported["dry_run"] is False
    assert (target / ".paper-galaxy" / "paper_galaxy.sqlite3").exists()


def test_import_refuses_existing_metadata_without_force(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    output = tmp_path / "backup.zip"
    export_project(project_dir=tmp_path, output_path=output, yes=True)
    target = tmp_path / "target"
    (target / ".paper-galaxy").mkdir(parents=True)

    try:
        import_project(backup_path=output, project_dir=target)
    except FileExistsError as exc:
        assert "--force" in str(exc)
    else:
        raise AssertionError("Expected import to refuse existing metadata.")
