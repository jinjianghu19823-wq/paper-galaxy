from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = REPO_ROOT / "Makefile"

USER_DATA_SENTINELS = (
    Path(".paper-galaxy/keep.txt"),
    Path("nested/user.sqlite3"),
    Path("nested/zotero.sqlite"),
    Path("nested/model.faiss"),
    Path("nested/model.index"),
    Path("paper-galaxy-backup-user.zip"),
)

BUILD_ARTIFACTS = (
    Path("dist"),
    Path("build"),
    Path("site_dist"),
    Path("src/example.egg-info"),
    Path(".pytest_cache"),
    Path(".ruff_cache"),
    Path(".mypy_cache"),
)

FORBIDDEN_CLEANUP_COMMANDS = (
    "rm -rf .paper-galaxy",
    'find . -name "*.sqlite3" -delete',
    'find . -name "zotero.sqlite" -delete',
    'find . -name "*.faiss" -delete',
    'find . -name "*.index" -delete',
)


def test_clean_artifacts_only_removes_known_build_outputs(tmp_path: Path) -> None:
    for relative_path in USER_DATA_SENTINELS:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("keep\n", encoding="utf-8")

    for relative_path in BUILD_ARTIFACTS:
        path = tmp_path / relative_path
        path.mkdir(parents=True, exist_ok=True)
        (path / "generated.txt").write_text("remove\n", encoding="utf-8")

    subprocess.run(
        ["make", "--no-print-directory", "-f", str(MAKEFILE), "clean-artifacts"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert all((tmp_path / path).is_file() for path in USER_DATA_SENTINELS)
    assert all(not (tmp_path / path).exists() for path in BUILD_ARTIFACTS)


@pytest.mark.parametrize("target", ["release-check", "launch-check"])
def test_release_targets_never_dry_run_user_data_deletion(
    tmp_path: Path, target: str
) -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "-f", str(MAKEFILE), target],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr

    for forbidden in FORBIDDEN_CLEANUP_COMMANDS:
        assert forbidden not in output
