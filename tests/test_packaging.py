import tomllib
from pathlib import Path

import paper_galaxy


def test_package_version_matches_pyproject() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert paper_galaxy.__version__ == pyproject["project"]["version"]
    assert paper_galaxy.__version__ == "0.1.0"


def test_build_dependency_is_in_dev_extra() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dev = pyproject["project"]["optional-dependencies"]["dev"]

    assert any(requirement.startswith("build>=") for requirement in dev)
