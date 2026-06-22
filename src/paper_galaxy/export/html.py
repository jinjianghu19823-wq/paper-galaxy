"""Self-contained offline HTML export."""

# ruff: noqa: E501

from __future__ import annotations

import json
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from paper_galaxy.models import Document, GalaxyBuildResult, MapPoint, SkippedFile

PALETTE = [
    "#2563eb",
    "#dc2626",
    "#059669",
    "#9333ea",
    "#d97706",
    "#0891b2",
    "#be123c",
    "#4f46e5",
]


def write_html_export(result: GalaxyBuildResult, output_path: Path) -> None:
    """Write a self-contained static HTML galaxy map."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_render_html(result), encoding="utf-8")


def _render_html(result: GalaxyBuildResult) -> str:
    data = _html_payload(result)
    data_json = escape(json.dumps(data, sort_keys=True), quote=False)
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Paper Galaxy</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f7f4;
      --panel: #ffffff;
      --ink: #1f2937;
      --muted: #667085;
      --line: #d8d8d0;
      --focus: #111827;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--ink);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      padding: 20px 24px 12px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: 28px;
      letter-spacing: 0;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
    }}
    .summary strong {{ color: var(--ink); font-weight: 650; }}
    main {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      min-height: calc(100vh - 116px);
    }}
    .map-pane {{
      padding: 16px;
      min-width: 0;
    }}
    .toolbar {{
      display: flex;
      gap: 10px;
      align-items: center;
      margin-bottom: 12px;
    }}
    input[type="search"] {{
      width: min(420px, 100%);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 11px;
      font: inherit;
      background: #fff;
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      font-size: 13px;
      color: var(--muted);
    }}
    .legend-item {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 0;
    }}
    .swatch {{
      width: 12px;
      height: 12px;
      border-radius: 50%;
      display: inline-block;
    }}
    .map-shell {{
      position: relative;
      height: 620px;
      min-height: 420px;
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      overflow: hidden;
    }}
    svg {{ width: 100%; height: 100%; display: block; }}
    .point {{
      cursor: pointer;
      stroke: #ffffff;
      stroke-width: 2;
    }}
    .point.dimmed {{ opacity: 0.18; }}
    .point.selected {{
      stroke: var(--focus);
      stroke-width: 3;
    }}
    .axis-line {{
      stroke: #e8e7df;
      stroke-width: 1;
    }}
    .tooltip {{
      position: absolute;
      pointer-events: none;
      max-width: 280px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #ffffff;
      box-shadow: 0 8px 20px rgba(31, 41, 55, 0.12);
      font-size: 13px;
      display: none;
      z-index: 4;
    }}
    aside {{
      border-left: 1px solid var(--line);
      background: #ffffff;
      padding: 18px;
      min-width: 0;
    }}
    .inspector h2 {{
      margin: 0 0 6px;
      font-size: 20px;
      line-height: 1.25;
    }}
    .meta {{
      color: var(--muted);
      font-size: 13px;
      overflow-wrap: anywhere;
    }}
    .section-title {{
      margin: 18px 0 8px;
      font-size: 13px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .chip {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      color: var(--ink);
      background: #fafafa;
    }}
    .neighbor {{
      width: 100%;
      display: block;
      text-align: left;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      margin-bottom: 7px;
      background: #ffffff;
      color: var(--ink);
      cursor: pointer;
      font: inherit;
    }}
    .neighbor:hover, .neighbor:focus {{
      border-color: var(--focus);
      outline: none;
    }}
    details {{
      margin-top: 18px;
      border-top: 1px solid var(--line);
      padding-top: 12px;
      color: var(--muted);
      font-size: 13px;
    }}
    .skipped {{
      margin: 8px 0 0;
      padding-left: 18px;
      overflow-wrap: anywhere;
    }}
    @media (max-width: 900px) {{
      main {{ grid-template-columns: 1fr; }}
      aside {{ border-left: 0; border-top: 1px solid var(--line); }}
      .map-shell {{ height: 480px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Paper Galaxy</h1>
    <div class="summary">
      <div><strong>Corpus</strong><br>{escape(str(result.corpus_path))}</div>
      <div><strong>Documents</strong><br>{len(result.documents)}</div>
      <div><strong>Skipped</strong><br>{len(result.skipped_files)}</div>
      <div><strong>Clusters</strong><br>{len(result.cluster_labels)}</div>
      <div><strong>Generated</strong><br>{generated_at}</div>
    </div>
  </header>
  <main>
    <section class="map-pane" aria-label="Document map">
      <div class="toolbar">
        <input id="search" type="search" placeholder="Filter documents, paths, terms, clusters" aria-label="Filter documents">
      </div>
      <div id="legend" class="legend" aria-label="Cluster legend"></div>
      <div class="map-shell">
        <svg id="map" role="img" aria-label="2D document map"></svg>
        <div id="tooltip" class="tooltip"></div>
      </div>
    </section>
    <aside>
      <section id="inspector" class="inspector" aria-live="polite"></section>
      <details>
        <summary>Skipped files ({len(result.skipped_files)})</summary>
        <ul id="skipped" class="skipped"></ul>
      </details>
    </aside>
  </main>
  <script id="galaxy-data" type="application/json">{data_json}</script>
  <script>
    const data = JSON.parse(document.getElementById("galaxy-data").textContent);
    const svg = document.getElementById("map");
    const tooltip = document.getElementById("tooltip");
    const inspector = document.getElementById("inspector");
    const search = document.getElementById("search");
    const legend = document.getElementById("legend");
    const skippedList = document.getElementById("skipped");
    const documents = new Map(data.documents.map((doc) => [doc.id, doc]));
    const points = new Map(data.points.map((point) => [point.document_id, point]));
    let selectedId = data.points.length ? data.points[0].document_id : null;
    let filter = "";

    function escapeHtml(value) {{
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }}

    function pointMatches(point) {{
      if (!filter) return true;
      const doc = documents.get(point.document_id);
      const haystack = [
        doc.title,
        doc.relative_path,
        doc.file_type,
        point.cluster_label,
        point.top_terms.join(" ")
      ].join(" ").toLowerCase();
      return haystack.includes(filter);
    }}

    function colorFor(clusterId) {{
      return data.palette[Math.abs(clusterId) % data.palette.length];
    }}

    function renderLegend() {{
      legend.innerHTML = "";
      Object.entries(data.cluster_labels).forEach(([clusterId, label]) => {{
        const item = document.createElement("span");
        item.className = "legend-item";
        item.innerHTML = `<span class="swatch" style="background:${{colorFor(Number(clusterId))}}"></span>${{escapeHtml(label)}}`;
        legend.appendChild(item);
      }});
    }}

    function renderMap() {{
      const width = svg.clientWidth || 800;
      const height = svg.clientHeight || 560;
      const pad = 44;
      svg.setAttribute("viewBox", `0 0 ${{width}} ${{height}}`);
      svg.innerHTML = `
        <line class="axis-line" x1="${{pad}}" y1="${{height / 2}}" x2="${{width - pad}}" y2="${{height / 2}}"></line>
        <line class="axis-line" x1="${{width / 2}}" y1="${{pad}}" x2="${{width / 2}}" y2="${{height - pad}}"></line>
      `;
      data.points.forEach((point) => {{
        const doc = documents.get(point.document_id);
        const x = pad + ((point.x + 1) / 2) * (width - pad * 2);
        const y = height - pad - ((point.y + 1) / 2) * (height - pad * 2);
        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        circle.setAttribute("cx", x);
        circle.setAttribute("cy", y);
        circle.setAttribute("r", point.document_id === selectedId ? 8 : 6);
        circle.setAttribute("fill", colorFor(point.cluster_id));
        circle.setAttribute("class", `point${{pointMatches(point) ? "" : " dimmed"}}${{point.document_id === selectedId ? " selected" : ""}}`);
        circle.setAttribute("tabindex", "0");
        circle.setAttribute("aria-label", doc.title);
        circle.addEventListener("mouseenter", (event) => showTooltip(event, doc, point));
        circle.addEventListener("mousemove", (event) => moveTooltip(event));
        circle.addEventListener("mouseleave", hideTooltip);
        circle.addEventListener("click", () => selectDocument(point.document_id));
        circle.addEventListener("keydown", (event) => {{
          if (event.key === "Enter" || event.key === " ") {{
            event.preventDefault();
            selectDocument(point.document_id);
          }}
        }});
        svg.appendChild(circle);
      }});
    }}

    function showTooltip(event, doc, point) {{
      tooltip.innerHTML = `<strong>${{escapeHtml(doc.title)}}</strong><br>${{escapeHtml(doc.relative_path)}}<br>${{escapeHtml(point.cluster_label)}}`;
      tooltip.style.display = "block";
      moveTooltip(event);
    }}

    function moveTooltip(event) {{
      tooltip.style.left = `${{event.offsetX + 14}}px`;
      tooltip.style.top = `${{event.offsetY + 14}}px`;
    }}

    function hideTooltip() {{
      tooltip.style.display = "none";
    }}

    function selectDocument(documentId) {{
      selectedId = documentId;
      renderInspector();
      renderMap();
    }}

    function renderInspector() {{
      if (!selectedId) {{
        inspector.innerHTML = "<p>No documents were extracted.</p>";
        return;
      }}
      const doc = documents.get(selectedId);
      const point = points.get(selectedId);
      const terms = point.top_terms.map((term) => `<span class="chip">${{escapeHtml(term)}}</span>`).join("");
      const neighbors = point.nearest_neighbors.map((neighbor) => `
        <button class="neighbor" data-doc-id="${{escapeHtml(neighbor.document_id)}}">
          <strong>${{escapeHtml(neighbor.title)}}</strong><br>
          <span class="meta">${{escapeHtml(neighbor.relative_path)}} · score ${{neighbor.score.toFixed(3)}}</span>
        </button>
      `).join("") || "<p class='meta'>No neighbors available.</p>";
      inspector.innerHTML = `
        <h2>${{escapeHtml(doc.title)}}</h2>
        <div class="meta">${{escapeHtml(doc.relative_path)}} · ${{escapeHtml(doc.file_type)}} · ${{doc.char_count}} chars</div>
        <div class="section-title">Cluster</div>
        <div><span class="swatch" style="background:${{colorFor(point.cluster_id)}}"></span> ${{escapeHtml(point.cluster_label)}}</div>
        <div class="section-title">Top Terms</div>
        <div class="chips">${{terms || "<span class='meta'>No terms available.</span>"}}</div>
        <div class="section-title">Nearest Neighbors</div>
        ${{neighbors}}
      `;
      inspector.querySelectorAll(".neighbor").forEach((button) => {{
        button.addEventListener("click", () => selectDocument(button.dataset.docId));
      }});
    }}

    function renderSkipped() {{
      skippedList.innerHTML = data.skipped_files.map((file) =>
        `<li>${{escapeHtml(file.relative_path)}}: ${{escapeHtml(file.reason)}}</li>`
      ).join("");
    }}

    search.addEventListener("input", () => {{
      filter = search.value.trim().toLowerCase();
      renderMap();
    }});
    window.addEventListener("resize", renderMap);
    renderLegend();
    renderMap();
    renderInspector();
    renderSkipped();
  </script>
</body>
</html>
"""


def _html_payload(result: GalaxyBuildResult) -> dict[str, object]:
    return {
        "corpus_path": str(result.corpus_path),
        "files_found": result.files_found,
        "documents": [_document_payload(document) for document in result.documents],
        "points": [_point_payload(point) for point in result.points],
        "cluster_labels": {
            str(cluster_id): label
            for cluster_id, label in sorted(result.cluster_labels.items())
        },
        "skipped_files": [
            _skipped_payload(skipped) for skipped in result.skipped_files
        ],
        "palette": PALETTE,
    }


def _document_payload(document: Document) -> dict[str, object]:
    return {
        "id": document.id,
        "title": document.title,
        "relative_path": document.relative_path,
        "file_type": document.file_type,
        "char_count": document.char_count,
    }


def _point_payload(point: MapPoint) -> dict[str, object]:
    return {
        "document_id": point.document_id,
        "x": round(point.x, 6),
        "y": round(point.y, 6),
        "cluster_id": point.cluster_id,
        "cluster_label": point.cluster_label,
        "top_terms": point.top_terms,
        "nearest_neighbors": [
            {
                "document_id": neighbor.document_id,
                "title": neighbor.title,
                "relative_path": neighbor.relative_path,
                "score": neighbor.score,
            }
            for neighbor in point.nearest_neighbors
        ],
    }


def _skipped_payload(skipped: SkippedFile) -> dict[str, str]:
    return {
        "relative_path": skipped.relative_path,
        "reason": skipped.reason,
    }
