from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from paper_galaxy.backup import export_project, import_project, inspect_backup
from paper_galaxy.cli import app
from paper_galaxy.indexer import index_corpus
from paper_galaxy.storage.sqlite import resolve_database_path
from tests.test_indexer import copy_tiny_corpus


def _indexed_project(root: Path) -> Path:
    project_dir = root / "project"
    project_dir.mkdir(parents=True)
    corpus = copy_tiny_corpus(project_dir)
    index_corpus(corpus, project_dir=project_dir, min_chars=40)
    return project_dir


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _write_config(project_dir: Path, *, database_path: str) -> Path:
    config = project_dir / ".paper-galaxy" / "project.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "\n".join(
            [
                'project_name = "Restore target"',
                f'database_path = "{database_path}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config


def test_export_refuses_to_replace_the_live_database(tmp_path: Path) -> None:
    project_dir = _indexed_project(tmp_path)
    database_path = resolve_database_path(project_dir)
    before = hashlib.sha256(database_path.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match=r"(?i)project data|alias|replace"):
        export_project(
            project_dir=project_dir,
            output_path=database_path,
            yes=True,
        )

    assert hashlib.sha256(database_path.read_bytes()).hexdigest() == before


def test_export_refuses_symlink_destination_without_touching_target(
    tmp_path: Path,
) -> None:
    project_dir = _indexed_project(tmp_path / "source")
    sentinel = tmp_path / "user-data.zip"
    sentinel.write_bytes(b"must-survive")
    output = tmp_path / "backup.zip"
    output.symlink_to(sentinel)

    with pytest.raises(ValueError, match=r"(?i)symbolic link"):
        export_project(project_dir=project_dir, output_path=output, yes=True)

    assert output.is_symlink()
    assert sentinel.read_bytes() == b"must-survive"


def test_export_refuses_to_replace_unowned_regular_file(tmp_path: Path) -> None:
    project_dir = _indexed_project(tmp_path / "source")
    output = tmp_path / "paper.pdf"
    sentinel = b"SYNTHETIC-PAPER-BYTES-MUST-SURVIVE"
    output.write_bytes(sentinel)

    with pytest.raises(FileExistsError, match=r"(?i)not.*backup|new --out"):
        export_project(project_dir=project_dir, output_path=output, yes=True)

    assert output.read_bytes() == sentinel


def test_restore_refuses_symlink_project_without_touching_target(
    tmp_path: Path,
) -> None:
    project_dir = _indexed_project(tmp_path / "source")
    archive = tmp_path / "backup.zip"
    export_project(project_dir=project_dir, output_path=archive, yes=True)
    real_target = tmp_path / "user-project"
    real_target.mkdir()
    sentinel = real_target / "keep.txt"
    sentinel.write_bytes(b"keep")
    alias = tmp_path / "restore-alias"
    alias.symlink_to(real_target, target_is_directory=True)

    with pytest.raises(ValueError, match=r"(?i)symbolic link"):
        import_project(backup_path=archive, project_dir=alias, force=True)

    assert sentinel.read_bytes() == b"keep"
    assert _tree_bytes(real_target) == {"keep.txt": b"keep"}


def test_force_restore_refuses_active_target_wal(tmp_path: Path) -> None:
    source = _indexed_project(tmp_path / "source")
    archive = tmp_path / "backup.zip"
    export_project(project_dir=source, output_path=archive, yes=True)
    target = _indexed_project(tmp_path / "target")
    _write_config(
        target,
        database_path=".paper-galaxy/paper_galaxy.sqlite3",
    )
    target_database = resolve_database_path(target)
    writer = sqlite3.connect(target_database)
    try:
        assert writer.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute("CREATE TABLE active_writer_probe(value TEXT NOT NULL)")
        writer.execute("INSERT INTO active_writer_probe VALUES ('preserved')")
        writer.commit()
        assert Path(f"{target_database}-wal").exists()

        with pytest.raises(
            FileExistsError,
            match=r"(?i)active|sidecar|WAL|checkpoint",
        ):
            import_project(
                backup_path=archive,
                project_dir=target,
                force=True,
            )
        assert writer.execute("SELECT value FROM active_writer_probe").fetchone() == (
            "preserved",
        )
    finally:
        writer.close()
    with sqlite3.connect(target_database) as verification:
        assert verification.execute(
            "SELECT value FROM active_writer_probe"
        ).fetchone() == ("preserved",)


def test_force_restore_cannot_overwrite_undeclared_existing_file(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_config(source, database_path="papers/user.sqlite3")
    corpus = copy_tiny_corpus(source)
    index_corpus(corpus, project_dir=source, min_chars=40)
    archive = tmp_path / "backup.zip"
    export_project(project_dir=source, output_path=archive, yes=True)

    target = tmp_path / "target"
    _write_config(
        target,
        database_path=".paper-galaxy/paper_galaxy.sqlite3",
    )
    user_file = target / "papers" / "user.sqlite3"
    user_file.parent.mkdir()
    sentinel = b"USER-SQLITE-NAMED-FILE-MUST-SURVIVE"
    user_file.write_bytes(sentinel)

    with pytest.raises(FileExistsError, match=r"(?i)not the database|declared"):
        import_project(backup_path=archive, project_dir=target, force=True)

    assert user_file.read_bytes() == sentinel


def test_force_restore_cannot_overwrite_undeclared_default_database_path(
    tmp_path: Path,
) -> None:
    source = _indexed_project(tmp_path / "source")
    archive = tmp_path / "backup.zip"
    export_project(project_dir=source, output_path=archive, yes=True)

    target = tmp_path / "target"
    target.mkdir()
    _write_config(target, database_path="state/current.sqlite3")
    corpus = copy_tiny_corpus(target)
    index_corpus(corpus, project_dir=target, min_chars=40)
    configured_database = resolve_database_path(target)
    configured_before = configured_database.read_bytes()
    undeclared_default = target / ".paper-galaxy/paper_galaxy.sqlite3"
    sentinel = b"UNDECLARED-DEFAULT-PATH-USER-FILE"
    undeclared_default.write_bytes(sentinel)

    with pytest.raises(FileExistsError, match=r"(?i)not the database|declared"):
        import_project(backup_path=archive, project_dir=target, force=True)

    assert undeclared_default.read_bytes() == sentinel
    assert configured_database.read_bytes() == configured_before


def test_restore_cannot_replace_its_input_archive(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_config(source, database_path="backup.zip")
    corpus = copy_tiny_corpus(source)
    index_corpus(corpus, project_dir=source, min_chars=40)
    exported = tmp_path / "exported.zip"
    export_project(project_dir=source, output_path=exported, yes=True)

    target = tmp_path / "target"
    target.mkdir()
    input_archive = target / "backup.zip"
    input_archive.write_bytes(exported.read_bytes())
    before = input_archive.read_bytes()

    with pytest.raises(ValueError, match=r"(?i)own input|input backup|alias"):
        import_project(
            backup_path=input_archive,
            project_dir=target,
            force=True,
        )

    assert input_archive.read_bytes() == before
    assert zipfile.is_zipfile(input_archive)


def test_checksum_validation_cannot_be_disabled(tmp_path: Path) -> None:
    project_dir = _indexed_project(tmp_path / "source")
    archive = tmp_path / "backup.zip"
    export_project(project_dir=project_dir, output_path=archive, yes=True)
    target = tmp_path / "target"

    with pytest.raises(ValueError, match=r"(?i)checksum.*cannot be disabled"):
        inspect_backup(archive, validate_checksums=False)
    with pytest.raises(ValueError, match=r"(?i)checksum.*cannot be disabled"):
        import_project(
            backup_path=archive,
            project_dir=target,
            validate_checksums=False,
        )

    assert not target.exists()


def test_dry_run_enforces_target_preflight_without_modifying_target(
    tmp_path: Path,
) -> None:
    source = _indexed_project(tmp_path / "source")
    archive = tmp_path / "backup.zip"
    export_project(project_dir=source, output_path=archive, yes=True)
    target = tmp_path / "target"
    metadata = target / ".paper-galaxy"
    metadata.mkdir(parents=True)
    sentinel = metadata / "keep.txt"
    sentinel.write_bytes(b"keep")
    before = _tree_bytes(target)

    with pytest.raises(FileExistsError, match=r"(?i)--force"):
        import_project(
            backup_path=archive,
            project_dir=target,
            dry_run=True,
        )

    assert _tree_bytes(target) == before


def test_import_cli_has_no_checksum_bypass() -> None:
    result = CliRunner().invoke(app, ["import-project", "--help"])

    assert result.exit_code == 0
    assert "--no-validate-checksums" not in result.output


def test_tampered_database_is_rejected_before_target_creation(tmp_path: Path) -> None:
    project_dir = _indexed_project(tmp_path / "source")
    original = tmp_path / "backup.zip"
    export_project(project_dir=project_dir, output_path=original, yes=True)
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(tampered, "x") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "database.sqlite3":
                content += b"tampered"
            target.writestr(info, content)

    restore_target = tmp_path / "restored"
    with pytest.raises(ValueError, match=r"(?i)checksum"):
        import_project(backup_path=tampered, project_dir=restore_target)

    assert not restore_target.exists()


def test_invalid_project_config_is_rejected_by_inspect_and_dry_run(
    tmp_path: Path,
) -> None:
    project_dir = _indexed_project(tmp_path / "source")
    archive = tmp_path / "backup.zip"
    export_project(project_dir=project_dir, output_path=archive, yes=True)
    contents: dict[str, bytes] = {}
    with zipfile.ZipFile(archive) as source:
        for name in source.namelist():
            contents[name] = source.read(name)
    contents["project.toml"] = b'project_name = 42\ndatabase_path = "bad"\n'
    checksums = {
        name: hashlib.sha256(content).hexdigest()
        for name, content in contents.items()
        if name != "checksums.sha256"
    }
    contents["checksums.sha256"] = "\n".join(
        f"{digest}  {name}" for name, digest in sorted(checksums.items())
    ).encode()
    invalid = tmp_path / "invalid-config.zip"
    with zipfile.ZipFile(invalid, "x", compression=zipfile.ZIP_DEFLATED) as target:
        for name, content in sorted(contents.items()):
            target.writestr(name, content)

    with pytest.raises(ValueError, match=r"(?i)configuration.*invalid|fields"):
        inspect_backup(invalid)
    with pytest.raises(ValueError, match=r"(?i)configuration.*invalid|fields"):
        import_project(
            backup_path=invalid,
            project_dir=tmp_path / "restore",
            dry_run=True,
        )


def test_export_refuses_unknown_multiline_config_without_mutating_it(
    tmp_path: Path,
) -> None:
    project_dir = _indexed_project(tmp_path / "source")
    config_path = _write_config(
        project_dir,
        database_path=".paper-galaxy/paper_galaxy.sqlite3",
    )
    original = (
        config_path.read_text(encoding="utf-8")
        + 'description = """\ndatabase_path = "documentation only"\n"""\n'
    )
    config_path.write_text(original, encoding="utf-8")
    archive = tmp_path / "backup.zip"

    with pytest.raises(ValueError, match=r"(?i)unsupported.*fields|description"):
        export_project(project_dir=project_dir, output_path=archive, yes=True)

    assert config_path.read_text(encoding="utf-8") == original
    assert not archive.exists()


def test_legacy_v1_clean_wal_database_is_normalized_and_restored(
    tmp_path: Path,
) -> None:
    project_dir = _indexed_project(tmp_path / "source")
    database_path = resolve_database_path(project_dir)
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
        connection.execute("CREATE TABLE legacy_probe(value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_probe VALUES ('preserved')")
        connection.commit()
    finally:
        connection.close()
    assert not Path(f"{database_path}-wal").exists()
    assert not Path(f"{database_path}-shm").exists()

    manifest = {
        "format": "paper-galaxy-backup-v1",
        "paper_galaxy_version": "legacy-test",
        "schema_version": "7",
        "created_at": "2026-01-01T00:00:00+00:00",
        "project_dir_name": "legacy-project",
        "contains_database": True,
        "source_files_included": False,
        "vector_indexes_included": False,
        "counts": {},
        "warnings": [],
    }
    payloads = {
        "database.sqlite3": database_path.read_bytes(),
        "manifest.json": json.dumps(manifest, sort_keys=True).encode(),
        "README_EXPORT.txt": b"Legacy backup\n",
    }
    checksums = {
        name: hashlib.sha256(content).hexdigest() for name, content in payloads.items()
    }
    payloads["checksums.sha256"] = "\n".join(
        f"{digest}  {name}" for name, digest in sorted(checksums.items())
    ).encode()
    archive = tmp_path / "legacy-v1.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as target:
        for name, content in sorted(payloads.items()):
            target.writestr(name, content)

    assert inspect_backup(archive)["checksum_status"] == "ok"
    restored = tmp_path / "restored"
    result = import_project(backup_path=archive, project_dir=restored)
    restored_database = resolve_database_path(restored)
    with sqlite3.connect(restored_database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert connection.execute("SELECT value FROM legacy_probe").fetchone() == (
            "preserved",
        )
        assert connection.execute("SELECT COUNT(*) FROM vector_indexes").fetchone() == (
            0,
        )
    assert any("Legacy v1" in warning for warning in result["warnings"])


def test_export_and_restore_files_are_owner_only_on_posix(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX permission bits are not available on this platform.")
    project_dir = _indexed_project(tmp_path / "source")
    archive = tmp_path / "backup.zip"
    export_project(project_dir=project_dir, output_path=archive, yes=True)
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600

    target = tmp_path / "target"
    import_project(backup_path=archive, project_dir=target)
    config_path = target / ".paper-galaxy" / "project.toml"
    database_path = resolve_database_path(target)
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
