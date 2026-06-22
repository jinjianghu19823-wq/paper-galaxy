from pathlib import Path

from typer.testing import CliRunner

from paper_galaxy.cli import app


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
