import json
from pathlib import Path

from paper_galaxy.indexer import index_corpus
from paper_galaxy.validation import (
    validate_project,
    validation_exit_code,
    write_validation_report,
)
from tests.test_indexer import copy_tiny_corpus


def test_validate_project_reports_missing_database_without_traceback(
    tmp_path: Path,
) -> None:
    report = validate_project(tmp_path)

    assert report["status"] == "ERRORS"
    assert report["database_exists"] is False
    assert any(issue["code"] == "database_missing" for issue in report["issues"])
    assert validation_exit_code(report) == 1


def test_validate_indexed_project_and_json_report(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    report = validate_project(tmp_path)
    output = write_validation_report(report, tmp_path / "validation.json")
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert report["database_exists"] is True
    assert report["counts"]["documents"] == 8
    assert report["tables"]["map_runs"] is True
    assert payload["counts"]["chunks"] >= 8
    assert "text_preview" not in output.read_text(encoding="utf-8")
