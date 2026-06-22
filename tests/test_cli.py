from pathlib import Path

from typer.testing import CliRunner

from paper_galaxy.cli import app
from paper_galaxy.errors import MissingDependencyError


def test_doctor_exits_successfully() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "Paper Galaxy version" in result.output
    assert "Status: Phase 0 scaffold is ready." in result.output


def test_init_creates_project_metadata(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["init", str(tmp_path)])

    config_path = tmp_path / ".paper-galaxy" / "project.toml"
    assert result.exit_code == 0
    assert config_path.exists()
    assert "project_name" in config_path.read_text(encoding="utf-8")
    assert "corpus_dirs = []" in config_path.read_text(encoding="utf-8")


def test_init_without_force_does_not_overwrite(tmp_path: Path) -> None:
    runner = CliRunner()
    config_path = tmp_path / ".paper-galaxy" / "project.toml"
    config_path.parent.mkdir()
    config_path.write_text("sentinel = true\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 0
    assert "already exists" in result.output
    assert config_path.read_text(encoding="utf-8") == "sentinel = true\n"


def test_scan_command_writes_html_and_json(tmp_path: Path) -> None:
    runner = CliRunner()
    output = tmp_path / "galaxy.html"
    json_output = tmp_path / "galaxy.json"

    result = runner.invoke(
        app,
        [
            "scan",
            "examples/tiny_corpus",
            "--out",
            str(output),
            "--json-out",
            str(json_output),
            "--force",
            "--min-chars",
            "40",
        ],
    )

    assert result.exit_code == 0
    assert output.exists()
    assert json_output.exists()
    assert "Documents extracted" in result.output


def test_scan_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    runner = CliRunner()
    output = tmp_path / "galaxy.html"
    output.write_text("existing", encoding="utf-8")

    result = runner.invoke(
        app,
        ["scan", "examples/tiny_corpus", "--out", str(output), "--min-chars", "40"],
    )

    assert result.exit_code == 1
    assert "Use --force to overwrite" in result.output
    assert output.read_text(encoding="utf-8") == "existing"


def test_scan_reports_missing_ml_dependency(
    monkeypatch: object, tmp_path: Path
) -> None:
    runner = CliRunner()

    def raise_missing_dependency(*args: object, **kwargs: object) -> None:
        raise MissingDependencyError("scikit-learn")

    monkeypatch.setattr("paper_galaxy.cli.build_galaxy", raise_missing_dependency)

    result = runner.invoke(
        app,
        [
            "scan",
            "examples/tiny_corpus",
            "--out",
            str(tmp_path / "galaxy.html"),
            "--force",
        ],
    )

    assert result.exit_code == 1
    assert "Missing optional dependency" in result.output
    assert 'python -m pip install -e ".[dev,ml,pdf]"' in result.output


def test_index_search_and_db_stats_commands(tmp_path: Path) -> None:
    runner = CliRunner()

    first = runner.invoke(
        app,
        [
            "index",
            "examples/tiny_corpus",
            "--project-dir",
            str(tmp_path),
            "--min-chars",
            "40",
        ],
    )
    second = runner.invoke(
        app,
        [
            "index",
            "examples/tiny_corpus",
            "--project-dir",
            str(tmp_path),
            "--min-chars",
            "40",
        ],
    )
    search = runner.invoke(
        app,
        ["search", "neural", "--project-dir", str(tmp_path)],
    )
    stats = runner.invoke(
        app,
        ["db-stats", "--project-dir", str(tmp_path)],
    )

    assert first.exit_code == 0
    assert "Documents inserted" in first.output
    assert second.exit_code == 0
    assert "Documents unchanged" in second.output
    assert search.exit_code == 0
    assert "neural_operators" in search.output
    assert stats.exit_code == 0
    assert "Active documents" in stats.output
    assert "Unindexed documents" in stats.output


def test_search_missing_database_prints_clear_message(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["search", "neural", "--project-dir", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "No Paper Galaxy database found" in result.output
    assert "Run paper-galaxy index CORPUS_DIR first" in result.output
