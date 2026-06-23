"""Audit Paper Galaxy for public repository readiness."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".cfg",
    ".cff",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".sql",
    ".svg",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
}
SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "site_dist",
    "dist",
    "build",
}
FORBIDDEN_ARTIFACT_NAMES = {
    ".paper-galaxy",
    "galaxy.html",
    "galaxy.json",
    "extraction-report.json",
    "validation.json",
}
FORBIDDEN_ARTIFACT_SUFFIXES = (".sqlite3", ".faiss", ".index")
FORBIDDEN_ARTIFACT_PATTERNS = (
    re.compile(r"map-run.*\.json$"),
    re.compile(r"paper-galaxy-backup.*\.zip$"),
)
MODEL_FILENAMES = {
    "pytorch_model.bin",
    "model.safetensors",
    "tokenizer.json",
    "sentence_bert_config.json",
}
REQUIRED_FILES = (
    "LICENSE",
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "CITATION.cff",
)
COMMUNITY_FILES = (
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/pull_request_template.md",
)
README_TOKENS = (
    "quickstart",
    "live demo",
    "privacy",
    "local-first",
    "python -m pip install -e",
    "screenshot",
    "contributing",
)
SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(openai_api_key|github_token|aws_[a-z_]*|access_token)\b"
        r"\s*[:=]\s*['\"]?[a-z0-9_\-./=]{12,}"
    ),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)?PRIVATE KEY-----"),
)


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    status: str
    detail: str

    @property
    def is_blocker(self) -> bool:
        return self.status == "FAIL"


def run_public_readiness(
    *,
    root: Path = REPO_ROOT,
    strict: bool = True,
    max_binary_bytes: int = 5_000_000,
) -> dict[str, Any]:
    """Run all public-readiness checks and return a JSON-safe report."""

    root = root.resolve()
    checks: list[ReadinessCheck] = []
    checks.extend(_check_generated_artifacts(root))
    checks.extend(_check_secrets(root))
    checks.extend(_check_model_files(root))
    checks.extend(_check_large_binaries(root, max_binary_bytes=max_binary_bytes))
    checks.extend(_check_required_files(root))
    checks.extend(_check_community_files(root))
    checks.extend(_check_site(root))
    checks.extend(_check_readme(root))
    checks.extend(_check_pyproject(root))
    blocker_count = sum(check.is_blocker for check in checks)
    status = "PASS" if blocker_count == 0 else "FAIL"
    return {
        "status": status,
        "strict": strict,
        "blocker_count": blocker_count,
        "checks": [asdict(check) for check in checks],
    }


def _check_generated_artifacts(root: Path) -> list[ReadinessCheck]:
    blockers: list[str] = []
    for path in _walk_repo(root, include_dirs=True):
        relative = _relative(path, root)
        name = path.name
        if name in FORBIDDEN_ARTIFACT_NAMES:
            blockers.append(relative)
        elif path.is_file() and path.suffix in FORBIDDEN_ARTIFACT_SUFFIXES:
            blockers.append(relative)
        elif path.is_file() and any(
            pattern.match(name) for pattern in FORBIDDEN_ARTIFACT_PATTERNS
        ):
            blockers.append(relative)
    return [
        _result(
            "generated-local-artifacts",
            blockers,
            "No generated local databases, backups, or map exports found.",
        )
    ]


def _check_secrets(root: Path) -> list[ReadinessCheck]:
    findings: list[str] = []
    for path in _walk_repo(root):
        if path.name == ".env" or path.suffix == ".env":
            findings.append(_relative(path, root))
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > 500_000:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(lines, start=1):
            if _is_safe_example_line(line):
                continue
            if any(pattern.search(line) for pattern in SECRET_PATTERNS):
                findings.append(f"{_relative(path, root)}:{line_number}")
                break
    return [
        _result(
            "likely-secrets",
            findings,
            "No likely API keys, tokens, private keys, or .env files found.",
        )
    ]


def _check_model_files(root: Path) -> list[ReadinessCheck]:
    findings = [
        _relative(path, root)
        for path in _walk_repo(root)
        if path.is_file() and path.name in MODEL_FILENAMES
    ]
    return [
        _result(
            "downloaded-model-files",
            findings,
            "No downloaded model/tokenizer files found.",
        )
    ]


def _check_large_binaries(root: Path, *, max_binary_bytes: int) -> list[ReadinessCheck]:
    findings: list[str] = []
    for path in _walk_repo(root):
        if not path.is_file() or path.suffix.lower() in TEXT_SUFFIXES:
            continue
        if path.stat().st_size > max_binary_bytes:
            findings.append(f"{_relative(path, root)} ({path.stat().st_size} bytes)")
    return [
        _result(
            "large-binary-files",
            findings,
            f"No non-text files above {max_binary_bytes} bytes found.",
        )
    ]


def _check_required_files(root: Path) -> list[ReadinessCheck]:
    missing = [
        relative for relative in REQUIRED_FILES if not (root / relative).exists()
    ]
    return [
        _result(
            "required-project-files",
            missing,
            "License, security, citation, conduct, changelog, and "
            "contribution files exist.",
        )
    ]


def _check_community_files(root: Path) -> list[ReadinessCheck]:
    missing = [
        relative for relative in COMMUNITY_FILES if not (root / relative).exists()
    ]
    return [
        _result(
            "github-community-files",
            missing,
            "Issue templates and pull request template exist.",
        )
    ]


def _check_site(root: Path) -> list[ReadinessCheck]:
    try:
        from scripts.check_demo_site import check_demo_site
    except ModuleNotFoundError:
        from check_demo_site import check_demo_site

    blockers: list[str] = []
    required = [
        "site/index.html",
        "site/demo/index.html",
        "site/zh-cn/index.html",
        "site/assets/styles.css",
        "site/assets/demo.js",
        "site/assets/graph-demo.js",
        "site/data/tiny-map.json",
        "site_dist/index.html",
        "site_dist/demo/index.html",
        ".github/workflows/pages.yml",
    ]
    for relative in required:
        if not (root / relative).exists():
            blockers.append(relative)
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    if "site_dist/" not in gitignore:
        blockers.append(".gitignore missing site_dist/")
    workflow_path = root / ".github" / "workflows" / "pages.yml"
    if workflow_path.exists():
        workflow = workflow_path.read_text(encoding="utf-8")
        required_workflow_tokens = (
            "github.repository_visibility != 'public'",
            "github.repository_visibility == 'public'",
            "enablement: true",
        )
        for token in required_workflow_tokens:
            if token not in workflow:
                blockers.append(f"pages workflow missing {token}")
    if not blockers and (root / "site_dist").exists():
        blockers.extend(
            f"demo-site:{issue.code}:{issue.message}"
            for issue in check_demo_site(dist_dir=root / "site_dist")
        )
    return [
        _result(
            "static-demo-site",
            blockers,
            "Static demo source/output, Pages workflow, and asset policy are ready.",
        )
    ]


def _check_readme(root: Path) -> list[ReadinessCheck]:
    readme = root / "README.md"
    if not readme.exists():
        return [ReadinessCheck("readme-content", "FAIL", "README.md missing")]
    lower = readme.read_text(encoding="utf-8").lower()
    missing = [token for token in README_TOKENS if token not in lower]
    return [
        _result(
            "readme-content",
            missing,
            "README includes quickstart, demo, privacy, install, screenshot, "
            "and contribution sections.",
        )
    ]


def _check_pyproject(root: Path) -> list[ReadinessCheck]:
    blockers: list[str] = []
    pyproject_path = root / "pyproject.toml"
    version_path = root / "src" / "paper_galaxy" / "__init__.py"
    if not pyproject_path.exists():
        blockers.append("pyproject.toml missing")
    else:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        project_version = str(pyproject.get("project", {}).get("version", ""))
        package_data = (
            pyproject.get("tool", {}).get("setuptools", {}).get("package-data", {})
        )
        storage_data = package_data.get("paper_galaxy.storage", [])
        web_data = package_data.get("paper_galaxy.web", [])
        if "schema.sql" not in storage_data:
            blockers.append("package data missing storage schema")
        if not any(str(item).startswith("static/") for item in web_data):
            blockers.append("package data missing web static assets")
        if version_path.exists():
            match = re.search(
                r'__version__\s*=\s*"([^"]+)"',
                version_path.read_text(encoding="utf-8"),
            )
            package_version = match.group(1) if match else ""
            if project_version != package_version:
                blockers.append(
                    "version mismatch "
                    f"pyproject={project_version} package={package_version}"
                )
        else:
            blockers.append("src/paper_galaxy/__init__.py missing")
    return [
        _result(
            "pyproject-metadata",
            blockers,
            "Version and package data metadata are sane.",
        )
    ]


def _result(name: str, blockers: list[str], success: str) -> ReadinessCheck:
    if blockers:
        return ReadinessCheck(name, "FAIL", "; ".join(blockers))
    return ReadinessCheck(name, "PASS", success)


def _walk_repo(root: Path, *, include_dirs: bool = False) -> list[Path]:
    result: list[Path] = []
    for path in root.rglob("*"):
        relative_parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in relative_parts):
            continue
        if path.is_dir():
            if include_dirs:
                result.append(path)
            continue
        result.append(path)
    return result


def _relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


def _is_safe_example_line(line: str) -> bool:
    lower = line.lower()
    return any(word in lower for word in ("fake", "example", "placeholder"))


def _print_table(report: dict[str, Any]) -> None:
    rows = report["checks"]
    widths = {
        "check": max(len("Check"), *(len(row["name"]) for row in rows)),
        "status": len("Status"),
    }
    print(f"Public readiness: {report['status']}")
    print(f"{'Check'.ljust(widths['check'])}  Status  Detail")
    print(f"{'-' * widths['check']}  ------  ------")
    for row in rows:
        name = row["name"].ljust(widths["check"])
        print(f"{name}  {row['status']}    {row['detail']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--strict", dest="strict", action="store_true", default=True)
    parser.add_argument("--no-strict", dest="strict", action="store_false")
    parser.add_argument("--max-binary-bytes", type=int, default=5_000_000)
    args = parser.parse_args()

    report = run_public_readiness(
        root=args.root,
        strict=args.strict,
        max_binary_bytes=args.max_binary_bytes,
    )
    _print_table(report)
    if args.json_out:
        args.json_out.write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if args.strict and report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
