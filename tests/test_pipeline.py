import math
from pathlib import Path

from paper_galaxy.pipeline import build_galaxy


def test_tiny_corpus_build_produces_points_and_labels(tmp_path: Path) -> None:
    output = tmp_path / "galaxy.html"

    result = build_galaxy(
        Path("examples/tiny_corpus"),
        output,
        json_output_path=tmp_path / "galaxy.json",
        min_chars=40,
        neighbor_count=3,
        seed=42,
    )

    assert output.exists()
    assert len(result.documents) == 8
    assert len(result.points) == len(result.documents)
    assert result.cluster_labels
    assert all(label for label in result.cluster_labels.values())
    assert all(
        math.isfinite(point.x) and math.isfinite(point.y) for point in result.points
    )
    assert all(point.nearest_neighbors for point in result.points)


def test_html_export_is_self_contained(tmp_path: Path) -> None:
    output = tmp_path / "galaxy.html"

    build_galaxy(Path("examples/tiny_corpus"), output, min_chars=40)

    html = output.read_text(encoding="utf-8")
    assert "Paper Galaxy" in html
    assert "galaxy-data" in html
    assert "documents" in html
    assert "https://" not in html
