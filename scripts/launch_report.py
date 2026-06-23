"""Generate a concise Paper Galaxy public-launch report."""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/jinjianghu19823-wq/paper-galaxy"
PAGES_URL = "https://jinjianghu19823-wq.github.io/paper-galaxy/"

try:
    from scripts.check_demo_site import check_demo_site
    from scripts.public_readiness_check import run_public_readiness
except ModuleNotFoundError:
    from check_demo_site import check_demo_site
    from public_readiness_check import run_public_readiness


def build_launch_report(
    *,
    root: Path = REPO_ROOT,
    require_site_dist: bool = False,
) -> dict[str, Any]:
    """Return a deterministic JSON-safe launch report."""

    root = root.resolve()
    readiness = run_public_readiness(
        root=root,
        require_site_dist=require_site_dist,
    )
    demo = _demo_status(root)
    package_files = _package_files(root)
    return {
        "version": _version(root),
        "repo_url": REPO_URL,
        "pages_url": PAGES_URL,
        "public_readiness": {
            "status": readiness["status"],
            "blocker_count": readiness["blocker_count"],
            "warning_count": readiness["warning_count"],
            "require_site_dist": readiness["require_site_dist"],
        },
        "demo_site": demo,
        "package_files": package_files,
        "test_command_summary": [
            "python -m ruff check .",
            "python -m ruff format . --check",
            "python -m mypy src",
            "python -m pytest",
            "python -m build",
            "python scripts/build_demo_site.py --out site_dist",
            "python scripts/check_demo_site.py --dist site_dist --serve",
            "python scripts/public_readiness_check.py --strict --require-site-dist",
        ],
        "known_manual_steps": [
            "Confirm GitHub Pages source is GitHub Actions.",
            "Verify the live Pages URL after deployment.",
            "Create the v0.1.0 GitHub Release only after all checks pass.",
            "Do not publish to PyPI unless explicitly requested.",
        ],
        "next_recommended_action": (
            "Publish v0.1.0 release notes after live-site verification, then triage "
            "early feedback with the public issue templates."
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render a launch report as Markdown."""

    package_files = report["package_files"] or ["No dist files found."]
    lines = [
        "# Paper Galaxy Launch Report",
        "",
        f"- Version: `{report['version']}`",
        f"- Repository: {report['repo_url']}",
        f"- Expected Pages URL: {report['pages_url']}",
        (
            "- Public readiness: "
            f"{report['public_readiness']['status']} "
            f"({report['public_readiness']['blocker_count']} blockers, "
            f"{report['public_readiness']['warning_count']} warnings)"
        ),
        f"- Demo site: {report['demo_site']['status']}",
        "",
        "## Package Files",
        "",
        *[f"- `{item}`" for item in package_files],
        "",
        "## Check Commands",
        "",
        *[f"- `{item}`" for item in report["test_command_summary"]],
        "",
        "## Manual Steps",
        "",
        *[f"- {item}" for item in report["known_manual_steps"]],
        "",
        "## Next Recommended Action",
        "",
        report["next_recommended_action"],
        "",
    ]
    if report["demo_site"].get("issues"):
        lines.extend(
            [
                "## Demo Site Issues",
                "",
                *[f"- {item}" for item in report["demo_site"]["issues"]],
                "",
            ]
        )
    return "\n".join(lines)


def _version(root: Path) -> str:
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(pyproject.get("project", {}).get("version", "unknown"))


def _demo_status(root: Path) -> dict[str, Any]:
    dist = root / "site_dist"
    if not dist.exists():
        return {"status": "NOT_BUILT", "issues": ["site_dist does not exist"]}
    issues = check_demo_site(dist_dir=dist)
    return {
        "status": "PASS" if not issues else "FAIL",
        "issues": [f"{issue.code}: {issue.message}" for issue in issues],
    }


def _package_files(root: Path) -> list[str]:
    dist = root / "dist"
    if not dist.exists():
        return []
    return sorted(str(path.relative_to(root)) for path in dist.glob("*"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--require-site-dist", action="store_true")
    args = parser.parse_args()

    report = build_launch_report(
        root=args.root,
        require_site_dist=args.require_site_dist,
    )
    markdown = render_markdown(report)
    if args.out:
        args.out.write_text(markdown, encoding="utf-8")
    else:
        print(markdown)
    if args.json_out:
        args.json_out.write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
