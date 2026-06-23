import json
from pathlib import Path

import click
from typer.main import get_command
from typer.testing import CliRunner

from paper_galaxy.cli import app
from paper_galaxy.errors import MissingDependencyError
from paper_galaxy.web.map_builder import build_map_payload


def test_doctor_exits_successfully() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "Paper Galaxy version" in result.output
    assert "Status: Phase 0 scaffold is ready." in result.output
    assert "Serve command: Phase 3 local web app is available." in result.output
    assert "Professionalization commands: Phase 7" in result.output
    assert "Zotero commands: local read-only import" in result.output


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


def test_index_help_lists_phase_four_extraction_options() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["index", "--help"])
    command = get_command(app)
    index_command = command.get_command(click.Context(command), "index")
    assert index_command is not None
    option_names = {
        option
        for param in index_command.params
        for option in [*param.opts, *param.secondary_opts]
    }

    assert result.exit_code == 0
    assert "--include-images" in option_names
    assert "--ocr" in option_names
    assert "--ocr-language" in option_names
    assert "--extraction-report-json" in option_names


def test_index_command_writes_extraction_report_json(tmp_path: Path) -> None:
    runner = CliRunner()
    report_path = tmp_path / "report.json"

    result = runner.invoke(
        app,
        [
            "index",
            "examples/tiny_corpus",
            "--project-dir",
            str(tmp_path),
            "--min-chars",
            "40",
            "--extraction-report-json",
            str(report_path),
        ],
    )
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert result.exit_code == 0
    assert report_path.exists()
    assert "Extraction report JSON" in result.output
    assert payload["counts"]["extracted_count"] == 8


def test_extract_preview_command_reports_metadata() -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "extract-preview",
            "examples/tiny_corpus/neural_operators/fourier_neural_operator.md",
            "--include-metadata",
            "--max-chars",
            "120",
        ],
    )

    assert result.exit_code == 0
    assert "Paper Galaxy Extraction Preview" in result.output
    assert "markdown" in result.output
    assert "frontmatter_keys" in result.output


def test_search_missing_database_prints_clear_message(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["search", "neural", "--project-dir", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "No Paper Galaxy database found" in result.output
    assert "Run paper-galaxy index CORPUS_DIR first" in result.output


def test_serve_help_exits_successfully() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["serve", "--help"])

    assert result.exit_code == 0
    assert "Serve the local Phase 3 browser app." in result.output


def test_serve_command_calls_server_startup(
    monkeypatch: object, tmp_path: Path
) -> None:
    runner = CliRunner()
    calls: dict[str, object] = {}

    def fake_serve_app(**kwargs: object) -> None:
        calls.update(kwargs)

    monkeypatch.setattr("paper_galaxy.web.server.serve_app", fake_serve_app)

    result = runner.invoke(
        app,
        [
            "serve",
            "--project-dir",
            str(tmp_path),
            "--host",
            "127.0.0.1",
            "--port",
            "9876",
            "--seed",
            "17",
            "--clusters",
            "3",
            "--neighbors",
            "4",
            "--limit",
            "25",
        ],
    )

    assert result.exit_code == 0
    assert calls["project_dir"] == tmp_path.resolve()
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 9876
    assert calls["seed"] == 17
    assert calls["clusters"] == 3
    assert calls["neighbors"] == 4
    assert calls["map_limit"] == 25


def test_serve_reports_missing_app_dependency(monkeypatch: object) -> None:
    runner = CliRunner()

    def raise_missing_dependency(**kwargs: object) -> None:
        del kwargs
        raise MissingDependencyError("uvicorn")

    monkeypatch.setattr(
        "paper_galaxy.web.server.serve_app",
        raise_missing_dependency,
    )

    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 1
    assert "Missing optional dependency for Phase 3 app" in result.output
    assert 'python -m pip install -e ".[dev,ml,pdf,app]"' in result.output


def test_phase_five_command_help_exits_successfully() -> None:
    runner = CliRunner()

    for command in ("embed", "semantic-search", "compare-neighbors", "vector-stats"):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0


def test_phase_six_command_help_exits_successfully() -> None:
    runner = CliRunner()

    for command in (
        "clusters",
        "explain-pair",
        "rename-cluster",
        "reset-cluster-label",
    ):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0


def test_phase_seven_command_help_exits_successfully() -> None:
    runner = CliRunner()

    for command in (
        "validate-project",
        "build-map-run",
        "map-runs",
        "show-map-run",
        "delete-map-run",
        "export-map-run",
        "export-project",
        "import-project",
        "plugins",
    ):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0


def test_embed_rejects_remote_model_names_by_default(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "embed",
            "--project-dir",
            str(tmp_path),
            "--model",
            "sentence-transformers/all-MiniLM-L6-v2",
        ],
    )

    assert result.exit_code == 1
    assert "hidden downloads" in result.output
    assert "--allow-model-download" in result.output


def test_vector_stats_command_handles_empty_project(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["vector-stats", "--project-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "Paper Galaxy Vector Stats" in result.output
    assert "Embedding models" in result.output


def test_cluster_and_pair_cli_commands_on_tiny_corpus(tmp_path: Path) -> None:
    runner = CliRunner()
    index = runner.invoke(
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
    payload = build_map_payload(project_dir=tmp_path)
    signature = payload["clusters"][0]["cluster_signature"]

    clusters = runner.invoke(app, ["clusters", "--project-dir", str(tmp_path)])
    rename = runner.invoke(
        app,
        [
            "rename-cluster",
            str(signature),
            "Neural Operators",
            "--project-dir",
            str(tmp_path),
        ],
    )
    renamed_clusters = runner.invoke(
        app,
        ["clusters", "--project-dir", str(tmp_path)],
    )
    renamed_payload = build_map_payload(project_dir=tmp_path)
    reset = runner.invoke(
        app,
        ["reset-cluster-label", str(signature), "--project-dir", str(tmp_path)],
    )
    explanation = runner.invoke(
        app,
        [
            "explain-pair",
            "neural_operators/fourier_neural_operator.md",
            "neural_operators/deep_operator_network.txt",
            "--project-dir",
            str(tmp_path),
            "--term-limit",
            "4",
            "--chunk-limit",
            "1",
        ],
    )

    assert index.exit_code == 0
    assert clusters.exit_code == 0
    assert "Paper Galaxy Clusters" in clusters.output
    assert rename.exit_code == 0
    assert renamed_clusters.exit_code == 0
    assert any(
        cluster["cluster_signature"] == signature
        and cluster["display_label"] == "Neural Operators"
        and cluster["source"] == "manual"
        for cluster in renamed_payload["clusters"]
    )
    assert reset.exit_code == 0
    assert explanation.exit_code == 0
    assert "Lexical score" in explanation.output
    assert "Shared Terms" in explanation.output


def test_phase_seven_cli_commands_on_tiny_corpus(tmp_path: Path) -> None:
    runner = CliRunner()
    index = runner.invoke(
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
    validate = runner.invoke(
        app,
        [
            "validate-project",
            "--project-dir",
            str(tmp_path),
            "--json-out",
            str(tmp_path / "validation.json"),
        ],
    )
    build = runner.invoke(
        app,
        [
            "build-map-run",
            "--project-dir",
            str(tmp_path),
            "--name",
            "CLI run",
            "--json-out",
            str(tmp_path / "map-run.json"),
        ],
    )
    runs = runner.invoke(app, ["map-runs", "--project-dir", str(tmp_path), "--json"])
    run_payload = json.loads(runs.output)
    run_id = run_payload["map_runs"][0]["id"]
    show = runner.invoke(
        app,
        ["show-map-run", run_id, "--project-dir", str(tmp_path)],
    )
    export_run = runner.invoke(
        app,
        [
            "export-map-run",
            run_id,
            "--project-dir",
            str(tmp_path),
            "--out",
            str(tmp_path / "exported-map-run.json"),
        ],
    )
    export_project = runner.invoke(
        app,
        [
            "export-project",
            "--project-dir",
            str(tmp_path),
            "--out",
            str(tmp_path / "backup.zip"),
            "--yes",
        ],
    )
    plugins = runner.invoke(app, ["plugins", "--json"])

    assert index.exit_code == 0
    assert validate.exit_code == 0
    assert (tmp_path / "validation.json").exists()
    assert build.exit_code == 0
    assert (tmp_path / "map-run.json").exists()
    assert runs.exit_code == 0
    assert run_payload["map_runs"][0]["name"] == "CLI run"
    assert show.exit_code == 0
    assert "Documents: 8" in show.output
    assert export_run.exit_code == 0
    assert (tmp_path / "exported-map-run.json").exists()
    assert export_project.exit_code == 0
    assert (tmp_path / "backup.zip").exists()
    assert plugins.exit_code == 0
    assert "extractor.markdown" in plugins.output
