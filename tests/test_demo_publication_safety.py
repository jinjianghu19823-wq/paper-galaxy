from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.build_demo_site as demo_builder

REPO_ROOT = Path(__file__).resolve().parents[1]
MARKER_NAME = ".paper-galaxy-demo-build.json"
MARKER_FORMAT = "paper-galaxy-demo-build"
MARKER_VERSION = 1


def test_publish_rejects_output_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    site, corpus = _copy_sources(tmp_path)
    target = tmp_path / "user-project"
    target.mkdir()
    sentinel = target / "keep.sqlite3"
    sentinel.write_bytes(b"private-user-data")
    output = tmp_path / "site_dist"
    output.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match=r"(?i)safety.*site_dist"):
        demo_builder.build_demo_site(
            site_dir=site,
            corpus_dir=corpus,
            output_dir=output,
        )

    assert output.is_symlink()
    assert sentinel.read_bytes() == b"private-user-data"


@pytest.mark.parametrize("dangling", [False, True])
def test_publish_rejects_symlink_path_components(
    tmp_path: Path, dangling: bool
) -> None:
    site, corpus = _copy_sources(tmp_path)
    real_parent = tmp_path / "real-parent"
    if not dangling:
        real_parent.mkdir()
    alias = tmp_path / "linked-parent"
    alias.symlink_to(real_parent, target_is_directory=True)
    output = alias if dangling else alias / "site_dist"

    with pytest.raises(ValueError, match=r"(?i)safety.*site_dist"):
        demo_builder.build_demo_site(
            site_dir=site,
            corpus_dir=corpus,
            output_dir=output,
        )

    assert alias.is_symlink()


def test_macos_system_tmp_alias_resolves_to_private_tmp(tmp_path: Path) -> None:
    if not Path("/tmp").is_symlink() or Path("/tmp").resolve() != Path("/private/tmp"):
        pytest.skip("This platform does not use the macOS /tmp alias.")
    site, corpus = _copy_sources(tmp_path)

    resolved = demo_builder._resolve_safe_output_target(
        Path("/tmp") / "paper-galaxy-safe-site-dist",
        site_dir=site.resolve(),
        corpus_dir=corpus.resolve(),
    )

    assert resolved == Path("/private/tmp/paper-galaxy-safe-site-dist")


def test_windows_reparse_point_is_treated_as_link_like() -> None:
    class FakeJunction:
        def is_symlink(self) -> bool:
            return False

        def lstat(self) -> SimpleNamespace:
            return SimpleNamespace(st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)

    assert demo_builder._is_link_like(FakeJunction()) is True  # type: ignore[arg-type]


def test_publish_rejects_symlinked_source_root(tmp_path: Path) -> None:
    site, corpus = _copy_sources(tmp_path)
    site_alias = tmp_path / "site-alias"
    site_alias.symlink_to(site, target_is_directory=True)

    with pytest.raises(ValueError, match=r"(?i)safety.*site_dist"):
        demo_builder.build_demo_site(
            site_dir=site_alias,
            corpus_dir=corpus,
            output_dir=tmp_path / "site_dist",
        )


def test_source_tree_reparse_point_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site, _ = _copy_sources(tmp_path)
    reparse_entry = site / "assets" / "demo.js"
    real_is_link_like = demo_builder._is_link_like
    monkeypatch.setattr(
        demo_builder,
        "_is_link_like",
        lambda path: path == reparse_entry or real_is_link_like(path),
    )

    with pytest.raises(ValueError, match=r"(?i)safety.*site_dist"):
        demo_builder._reject_source_symlinks(site, label="site source")


def test_output_reparse_point_is_rejected_during_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "site_dist"
    output.mkdir()
    monkeypatch.setattr(
        demo_builder,
        "_is_link_like",
        lambda path: path == output,
    )

    with pytest.raises(ValueError, match=r"(?i)safety.*site_dist"):
        demo_builder._classify_existing_output(output)


def test_publish_rejects_unowned_nonempty_directory_and_preserves_it(
    tmp_path: Path,
) -> None:
    site, corpus = _copy_sources(tmp_path)
    output = tmp_path / "user-output"
    output.mkdir()
    sentinel = output / "paper-galaxy-backup-user.zip"
    sentinel.write_bytes(b"user-backup")
    before = _tree_snapshot(output)

    with pytest.raises(ValueError, match=r"(?i)safety.*site_dist"):
        demo_builder.build_demo_site(
            site_dir=site,
            corpus_dir=corpus,
            output_dir=output,
        )

    assert _tree_snapshot(output) == before


def test_publish_claims_empty_directory_and_writes_supported_marker(
    tmp_path: Path,
) -> None:
    site, corpus = _copy_sources(tmp_path)
    output = tmp_path / "site_dist"
    output.mkdir()

    demo_builder.build_demo_site(
        site_dir=site,
        corpus_dir=corpus,
        output_dir=output,
    )

    marker = json.loads((output / MARKER_NAME).read_text(encoding="utf-8"))
    assert marker == {
        "format": MARKER_FORMAT,
        "state": "complete",
        "version": MARKER_VERSION,
    }


@pytest.mark.parametrize(
    "marker_kind",
    [
        "malformed",
        "unsupported",
        "boolean-version",
        "float-version",
        "wrong-format",
        "incomplete",
        "symlink",
    ],
)
def test_publish_rejects_invalid_or_unsupported_marker(
    tmp_path: Path, marker_kind: str
) -> None:
    site, corpus = _copy_sources(tmp_path)
    output = tmp_path / "site_dist"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_bytes(b"keep")
    marker = output / MARKER_NAME
    if marker_kind == "malformed":
        marker.write_text("{not-json", encoding="utf-8")
    elif marker_kind == "unsupported":
        _write_marker(output, version=MARKER_VERSION + 1)
    elif marker_kind == "boolean-version":
        _write_marker(output, version=True)
    elif marker_kind == "float-version":
        _write_marker(output, version=1.0)
    elif marker_kind == "wrong-format":
        _write_marker(output, marker_format="another-publisher")
    elif marker_kind == "incomplete":
        _write_marker(output, state="staging")
    else:
        marker_target = tmp_path / "marker-target.json"
        marker_target.write_text("{}", encoding="utf-8")
        marker.symlink_to(marker_target)
    before = _tree_snapshot(output)

    with pytest.raises(ValueError, match=r"(?i)safety.*site_dist"):
        demo_builder.build_demo_site(
            site_dir=site,
            corpus_dir=corpus,
            output_dir=output,
        )

    assert _tree_snapshot(output) == before


def test_publish_replaces_owned_build_and_preserves_parent_sentinel(
    tmp_path: Path,
) -> None:
    site, corpus = _copy_sources(tmp_path)
    output = tmp_path / "site_dist"
    output.mkdir()
    _write_marker(output)
    (output / "stale-build-file.txt").write_bytes(b"stale")
    parent_sentinel = tmp_path / "keep.sqlite3"
    parent_sentinel.write_bytes(b"outside-output")

    demo_builder.build_demo_site(
        site_dir=site,
        corpus_dir=corpus,
        output_dir=output,
    )
    first_payload = (output / "data" / "tiny-map.json").read_bytes()
    demo_builder.build_demo_site(
        site_dir=site,
        corpus_dir=corpus,
        output_dir=output,
    )

    assert not (output / "stale-build-file.txt").exists()
    assert (output / MARKER_NAME).is_file()
    assert (output / "data" / "tiny-map.json").read_bytes() == first_payload
    assert parent_sentinel.read_bytes() == b"outside-output"
    assert _publisher_residue(output) == []


@pytest.mark.parametrize("failure_kind", ["payload", "validation"])
def test_staging_failure_preserves_owned_output_byte_for_byte(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    site, corpus = _copy_sources(tmp_path)
    output = tmp_path / "site_dist"
    shutil.copytree(site, output)
    _write_marker(output)
    (output / "sentinel.txt").write_bytes(b"old-complete-build")
    before = _tree_snapshot(output)

    if failure_kind == "payload":

        def fail_payload(*args: object, **kwargs: object) -> dict[str, object]:
            del args, kwargs
            raise RuntimeError("injected payload failure")

        monkeypatch.setattr(demo_builder, "build_demo_payload", fail_payload)
    else:

        def fail_validation(*args: object, **kwargs: object) -> list[object]:
            del args, kwargs
            raise RuntimeError("injected validation failure")

        monkeypatch.setattr(
            demo_builder,
            "check_demo_site",
            fail_validation,
            raising=False,
        )

    with pytest.raises(RuntimeError, match=f"injected {failure_kind} failure"):
        demo_builder.build_demo_site(
            site_dir=site,
            corpus_dir=corpus,
            output_dir=output,
        )

    assert _tree_snapshot(output) == before
    assert _publisher_residue(output) == []


def test_failed_stage_replace_restores_previous_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site, corpus = _copy_sources(tmp_path)
    output = tmp_path / "site_dist"
    shutil.copytree(site, output)
    _write_marker(output)
    (output / "sentinel.txt").write_bytes(b"old-complete-build")
    before = _tree_snapshot(output)
    real_replace = os.replace

    def fail_stage_replace(
        source: str | bytes | Path,
        target: str | bytes | Path,
    ) -> None:
        source_path = Path(source)
        target_path = Path(target)
        if "paper-galaxy-demo-staging" in source_path.name and target_path == output:
            raise OSError("injected publish failure")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_stage_replace)

    with pytest.raises(OSError, match="injected publish failure"):
        demo_builder.build_demo_site(
            site_dir=site,
            corpus_dir=corpus,
            output_dir=output,
        )

    assert _tree_snapshot(output) == before
    assert _publisher_residue(output) == []


def test_output_changed_after_classification_is_revalidated_and_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site, corpus = _copy_sources(tmp_path)
    output = tmp_path / "site_dist"
    output.mkdir()
    real_replace = os.replace

    def inject_file_before_move(
        source: str | bytes | Path,
        target: str | bytes | Path,
    ) -> None:
        source_path = Path(source)
        target_path = Path(target)
        if source_path == output and target_path == demo_builder._backup_path(output):
            (output / "late-user-data.sqlite3").write_bytes(b"late-user-data")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", inject_file_before_move)

    with pytest.raises(ValueError, match=r"(?i)safety.*site_dist"):
        demo_builder.build_demo_site(
            site_dir=site,
            corpus_dir=corpus,
            output_dir=output,
        )

    assert (output / "late-user-data.sqlite3").read_bytes() == b"late-user-data"
    assert _publisher_residue(output) == []


def test_interrupted_publish_restores_backup_before_next_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site, corpus = _copy_sources(tmp_path)
    output = tmp_path / "site_dist"
    backup = tmp_path / ".site_dist.paper-galaxy-demo-backup"
    backup.mkdir()
    _write_marker(backup)
    (backup / "sentinel.txt").write_bytes(b"old-complete-build")
    before = _tree_snapshot(backup)
    staging_name = ".site_dist.paper-galaxy-demo-staging-interrupted"
    journal = tmp_path / ".site_dist.paper-galaxy-demo-publish.json"
    _write_journal(journal, output=output, staging_name=staging_name)

    def fail_payload(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        raise RuntimeError("stop after recovery")

    monkeypatch.setattr(demo_builder, "build_demo_payload", fail_payload)

    with pytest.raises(RuntimeError, match="stop after recovery"):
        demo_builder.build_demo_site(
            site_dir=site,
            corpus_dir=corpus,
            output_dir=output,
        )

    assert _tree_snapshot(output) == before
    assert not backup.exists()
    assert not journal.exists()
    assert _publisher_residue(output) == []


def test_interrupted_completed_publish_keeps_new_output_and_removes_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site, corpus = _copy_sources(tmp_path)
    output = tmp_path / "site_dist"
    output.mkdir()
    _write_marker(output)
    (output / "new-site.txt").write_bytes(b"complete-new-site")
    before = _tree_snapshot(output)
    backup = tmp_path / ".site_dist.paper-galaxy-demo-backup"
    backup.mkdir()
    _write_marker(backup)
    (backup / "old-site.txt").write_bytes(b"old-site")
    journal = tmp_path / ".site_dist.paper-galaxy-demo-publish.json"
    _write_journal(
        journal,
        output=output,
        staging_name=".site_dist.paper-galaxy-demo-staging-completed",
    )

    def fail_payload(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        raise RuntimeError("stop after completed recovery")

    monkeypatch.setattr(demo_builder, "build_demo_payload", fail_payload)

    with pytest.raises(RuntimeError, match="stop after completed recovery"):
        demo_builder.build_demo_site(
            site_dir=site,
            corpus_dir=corpus,
            output_dir=output,
        )

    assert _tree_snapshot(output) == before
    assert not backup.exists()
    assert not journal.exists()
    assert _publisher_residue(output) == []


def test_filesystem_root_and_source_overlap_guards_are_pure(tmp_path: Path) -> None:
    site, corpus = _copy_sources(tmp_path)
    dangerous_paths = (
        Path(tmp_path.anchor),
        tmp_path,
        site,
        site / "nested-output",
        corpus,
        corpus / "nested-output",
    )

    for dangerous in dangerous_paths:
        with pytest.raises(ValueError, match=r"(?i)safety.*site_dist"):
            demo_builder._resolve_safe_output_target(
                dangerous,
                site_dir=site.resolve(),
                corpus_dir=corpus.resolve(),
            )


def test_mounted_volume_root_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site, corpus = _copy_sources(tmp_path)
    mounted_root = tmp_path / "mounted-volume"
    mounted_root.mkdir()
    real_ismount = os.path.ismount
    monkeypatch.setattr(
        os.path,
        "ismount",
        lambda path: Path(path) == mounted_root or real_ismount(path),
    )

    with pytest.raises(ValueError, match=r"(?i)safety.*site_dist"):
        demo_builder._resolve_safe_output_target(
            mounted_root,
            site_dir=site.resolve(),
            corpus_dir=corpus.resolve(),
        )


def test_physical_alias_overlap_uses_samefile_equivalence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site, _ = _copy_sources(tmp_path)
    alias_root = tmp_path / "physical-alias"
    alias_root.mkdir()
    output = alias_root / "site_dist"
    real_same = demo_builder._same_existing_path

    def fake_same(first: Path, second: Path) -> bool:
        if {first, second} == {alias_root, site.resolve()}:
            return True
        return real_same(first, second)

    monkeypatch.setattr(demo_builder, "_same_existing_path", fake_same)

    assert demo_builder._paths_overlap_or_samefile(output, site.resolve()) is True


@pytest.mark.parametrize("version", [True, 1.0])
def test_publish_journal_requires_integer_version(
    tmp_path: Path, version: object
) -> None:
    output = tmp_path / "site_dist"
    journal = tmp_path / ".site_dist.paper-galaxy-demo-publish.json"
    _write_journal(
        journal,
        output=output,
        staging_name=".site_dist.paper-galaxy-demo-staging-version",
        version=version,
    )

    with pytest.raises(ValueError, match=r"(?i)safety.*site_dist"):
        demo_builder._read_publish_journal(journal, output_dir=output)


def test_dangerous_output_guards_never_call_recursive_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site, corpus = _copy_sources(tmp_path)
    fake_repo = tmp_path / "fake-repository"
    fake_repo.mkdir()
    fake_git = fake_repo / ".git"
    fake_git.mkdir()
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(demo_builder, "REPO_ROOT", fake_repo)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    def forbidden_delete(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("recursive delete reached a guarded path")

    monkeypatch.setattr(demo_builder.shutil, "rmtree", forbidden_delete)

    for dangerous in (fake_repo, fake_git, fake_home, site, corpus):
        with pytest.raises(ValueError, match=r"(?i)safety.*site_dist"):
            demo_builder.build_demo_site(
                site_dir=site,
                corpus_dir=corpus,
                output_dir=dangerous,
            )


def _copy_sources(tmp_path: Path) -> tuple[Path, Path]:
    site = tmp_path / "source-site"
    corpus = tmp_path / "source-corpus"
    shutil.copytree(REPO_ROOT / "site", site)
    shutil.copytree(REPO_ROOT / "examples" / "tiny_corpus", corpus)
    return site, corpus


def _write_marker(
    directory: Path,
    *,
    version: object = MARKER_VERSION,
    marker_format: str = MARKER_FORMAT,
    state: str = "complete",
) -> None:
    (directory / MARKER_NAME).write_text(
        json.dumps(
            {
                "format": marker_format,
                "state": state,
                "version": version,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_journal(
    path: Path,
    *,
    output: Path,
    staging_name: str,
    old_output: str = "owned",
    version: object = 1,
) -> None:
    path.write_text(
        json.dumps(
            {
                "format": "paper-galaxy-demo-publish",
                "old_output": old_output,
                "output_name": output.name,
                "staging_name": staging_name,
                "version": version,
            }
        ),
        encoding="utf-8",
    )


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes | str | None]]:
    snapshot: dict[str, tuple[str, bytes | str | None]] = {}
    if not root.exists() and not root.is_symlink():
        return snapshot
    snapshot["."] = (
        ("symlink", os.readlink(root)) if root.is_symlink() else ("dir", None)
    )
    if root.is_symlink():
        return snapshot
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", os.readlink(path))
        elif path.is_dir():
            snapshot[relative] = ("dir", None)
        else:
            snapshot[relative] = ("file", path.read_bytes())
    return snapshot


def _publisher_residue(output: Path) -> list[str]:
    prefix = f".{output.name}.paper-galaxy-demo-"
    return sorted(
        path.name for path in output.parent.iterdir() if path.name.startswith(prefix)
    )
