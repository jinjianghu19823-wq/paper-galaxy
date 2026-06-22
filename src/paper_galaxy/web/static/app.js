const API = {
  health: "/api/health",
  stats: "/api/stats",
  map: "/api/map",
  search: "/api/search",
  documents: "/api/documents"
};

const CLUSTER_COLORS = [
  "#0f766e",
  "#2563eb",
  "#d97706",
  "#be315b",
  "#7c3aed",
  "#0891b2",
  "#4d7c0f",
  "#c2410c"
];

const SVG_NS = "http:" + "//www.w3.org/2000/svg";

const state = {
  health: null,
  stats: null,
  map: null,
  selectedId: null,
  selectedDetail: null,
  filter: "",
  searchTimer: null
};

const els = {
  projectStatus: document.querySelector("#project-status"),
  activeCount: document.querySelector("#active-count"),
  missingCount: document.querySelector("#missing-count"),
  unindexedCount: document.querySelector("#unindexed-count"),
  lastScan: document.querySelector("#last-scan"),
  searchForm: document.querySelector("#search-form"),
  searchInput: document.querySelector("#search-input"),
  includeMissing: document.querySelector("#include-missing"),
  searchResults: document.querySelector("#search-results"),
  pointFilter: document.querySelector("#point-filter"),
  clusterLegend: document.querySelector("#cluster-legend"),
  mapCaption: document.querySelector("#map-caption"),
  mapEmpty: document.querySelector("#map-empty"),
  mapSvg: document.querySelector("#map-svg"),
  resetSelection: document.querySelector("#reset-selection"),
  inspector: document.querySelector("#inspector-content")
};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  bindEvents();
  setInspectorMessage("Select a document point or search result.");
  try {
    state.health = await fetchJson(API.health);
    updateHealth(state.health);
    await loadStats();
    await loadMap();
  } catch (error) {
    showErrorState("Unable to load Paper Galaxy.", error.message);
  }
}

function bindEvents() {
  els.searchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    runSearch();
  });
  els.searchInput.addEventListener("input", () => {
    window.clearTimeout(state.searchTimer);
    state.searchTimer = window.setTimeout(runSearch, 220);
  });
  els.includeMissing.addEventListener("change", runSearch);
  els.pointFilter.addEventListener("input", () => {
    state.filter = els.pointFilter.value.trim().toLowerCase();
    renderMap();
  });
  els.resetSelection.addEventListener("click", () => {
    state.selectedId = null;
    state.selectedDetail = null;
    renderMap();
    setInspectorMessage("Select a document point or search result.");
  });
  window.addEventListener("resize", renderMap);
}

async function loadStats() {
  const payload = await fetchJson(API.stats);
  if (!payload.database_exists) {
    state.stats = null;
    updateMissingDatabase(payload.error);
    return;
  }
  state.stats = payload.stats;
  updateStats(payload.stats);
}

async function loadMap() {
  const payload = await fetchJson(API.map);
  state.map = payload;
  if (!payload.database_exists) {
    updateMissingDatabase(payload.error);
    renderMap();
    return;
  }
  renderMap();
}

async function runSearch() {
  const query = els.searchInput.value.trim();
  if (!query) {
    clearSearchResults();
    return;
  }
  const params = new URLSearchParams({
    q: query,
    limit: "10",
    include_missing: String(els.includeMissing.checked)
  });
  try {
    const payload = await fetchJson(`${API.search}?${params.toString()}`);
    renderSearchResults(payload);
  } catch (error) {
    clearSearchResults();
    appendText(els.searchResults, "div", `Search failed: ${error.message}`, "muted");
  }
}

async function selectDocument(documentId) {
  state.selectedId = documentId;
  state.selectedDetail = null;
  renderMap();
  setInspectorMessage("Loading document...");
  try {
    const detail = await fetchJson(`${API.documents}/${encodeURIComponent(documentId)}`);
    state.selectedDetail = detail;
    renderInspector();
  } catch (error) {
    setInspectorMessage(`Document unavailable: ${error.message}`);
  }
}

function updateHealth(health) {
  const status = health.database_exists ? "local database connected" : "no database";
  els.projectStatus.textContent = `${status} - ${health.project_dir}`;
}

function updateStats(stats) {
  els.activeCount.textContent = String(stats.active_documents);
  els.missingCount.textContent = String(stats.missing_documents);
  els.unindexedCount.textContent = String(stats.unindexed_documents);
  els.lastScan.textContent = stats.last_scan_time || "none";
}

function updateMissingDatabase(error) {
  els.projectStatus.textContent = "No Paper Galaxy database found";
  els.activeCount.textContent = "0";
  els.missingCount.textContent = "0";
  els.unindexedCount.textContent = "0";
  els.lastScan.textContent = "none";
  showEmptyState(
    "No Paper Galaxy database found",
    "Run indexing from the command line before opening the map.",
    error ? error.command : "paper-galaxy index /path/to/corpus --project-dir /path/to/project"
  );
}

function renderMap() {
  const payload = state.map;
  els.mapSvg.replaceChildren();
  if (!payload || !payload.database_exists) {
    els.mapSvg.hidden = true;
    return;
  }
  const documents = payload.documents || [];
  const points = payload.points || [];
  if (!points.length) {
    els.mapSvg.hidden = true;
    const message = (payload.warnings && payload.warnings[0]) || "No active indexed documents found.";
    showEmptyState("No active documents", message, null);
    renderLegend(points, payload.cluster_labels || {});
    els.mapCaption.textContent = "0 active documents";
    return;
  }

  els.mapEmpty.hidden = true;
  els.mapSvg.hidden = false;
  const docsById = new Map(documents.map((doc) => [doc.document_id, doc]));
  const pointsById = new Map(points.map((point) => [point.document_id, point]));
  const visiblePoints = filteredPoints(points, docsById);
  els.mapCaption.textContent = `${visiblePoints.length} of ${points.length} active documents`;
  renderLegend(points, payload.cluster_labels || {});

  const width = 1000;
  const height = 640;
  const padding = 62;
  els.mapSvg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  els.mapSvg.setAttribute("preserveAspectRatio", "xMidYMid meet");

  const lineLayer = svgEl("g");
  const pointLayer = svgEl("g");
  els.mapSvg.append(lineLayer, pointLayer);

  const visibleIds = new Set(visiblePoints.map((point) => point.document_id));
  const drawnLinks = new Set();
  for (const point of visiblePoints) {
    const from = projectPoint(point, width, height, padding);
    for (const neighbor of point.nearest_neighbors || []) {
      if (!visibleIds.has(neighbor.document_id)) {
        continue;
      }
      const key = [point.document_id, neighbor.document_id].sort().join(":");
      if (drawnLinks.has(key)) {
        continue;
      }
      drawnLinks.add(key);
      const neighborPoint = pointsById.get(neighbor.document_id);
      if (!neighborPoint) {
        continue;
      }
      const to = projectPoint(neighborPoint, width, height, padding);
      const line = svgEl("line", {
        x1: from.x,
        y1: from.y,
        x2: to.x,
        y2: to.y,
        class: linkClass(point.document_id, neighbor.document_id)
      });
      lineLayer.append(line);
    }
  }

  for (const point of visiblePoints) {
    const doc = docsById.get(point.document_id);
    const projected = projectPoint(point, width, height, padding);
    const circle = svgEl("circle", {
      cx: projected.x,
      cy: projected.y,
      r: state.selectedId === point.document_id ? 9 : 7,
      fill: clusterColor(point.cluster_id),
      class: state.selectedId === point.document_id ? "map-point selected" : "map-point",
      tabindex: "0"
    });
    const title = svgEl("title");
    title.textContent = `${doc ? doc.title : point.document_id}\n${point.cluster_label}`;
    circle.append(title);
    circle.addEventListener("click", () => selectDocument(point.document_id));
    circle.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectDocument(point.document_id);
      }
    });
    pointLayer.append(circle);

    if (state.selectedId === point.document_id && doc) {
      const label = svgEl("text", {
        x: projected.x + 12,
        y: projected.y - 12,
        class: "map-label"
      });
      label.textContent = doc.title;
      pointLayer.append(label);
    }
  }
}

function renderLegend(points, clusterLabels) {
  const counts = new Map();
  for (const point of points) {
    counts.set(point.cluster_id, (counts.get(point.cluster_id) || 0) + 1);
  }
  els.clusterLegend.replaceChildren();
  const ids = Object.keys(clusterLabels).sort((a, b) => Number(a) - Number(b));
  if (!ids.length) {
    appendText(els.clusterLegend, "p", "No clusters yet.", "muted");
    return;
  }
  for (const id of ids) {
    const row = document.createElement("div");
    row.className = "legend-item";
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = clusterColor(Number(id));
    const label = document.createElement("span");
    label.textContent = clusterLabels[id];
    const count = document.createElement("span");
    count.className = "legend-count";
    count.textContent = String(counts.get(Number(id)) || 0);
    row.append(swatch, label, count);
    els.clusterLegend.append(row);
  }
}

function renderSearchResults(payload) {
  clearSearchResults();
  if (!payload.database_exists) {
    appendText(els.searchResults, "div", "No Paper Galaxy database found.", "muted");
    return;
  }
  const results = payload.results || [];
  if (!results.length) {
    appendText(els.searchResults, "div", "No matching indexed documents found.", "muted");
    return;
  }
  for (const result of results) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "result-item";
    button.addEventListener("click", () => selectDocument(result.document_id));
    appendText(button, "span", result.title, "result-title");
    appendText(button, "span", result.relative_path, "result-path");
    if (result.snippet) {
      appendText(button, "div", result.snippet.replaceAll("[", "").replaceAll("]", ""), "result-snippet");
    }
    els.searchResults.append(button);
  }
}

function renderInspector() {
  const detail = state.selectedDetail;
  if (!detail || !detail.metadata) {
    setInspectorMessage("Select a document point or search result.");
    return;
  }
  const metadata = detail.metadata;
  const point = state.map && state.map.points
    ? state.map.points.find((candidate) => candidate.document_id === metadata.document_id)
    : null;

  els.inspector.replaceChildren();
  const title = document.createElement("h3");
  title.className = "document-title";
  title.textContent = metadata.title;
  els.inspector.append(title);
  appendText(els.inspector, "div", metadata.relative_path, "meta-row");
  appendText(
    els.inspector,
    "div",
    `${metadata.file_type || "file"} - ${metadata.status} - ${metadata.char_count} chars`,
    "meta-row"
  );
  if (metadata.local_path) {
    appendText(els.inspector, "div", metadata.local_path, "meta-row");
  }

  const terms = point ? point.top_terms || [] : [];
  const termsSection = inspectorSection("Top terms");
  if (terms.length) {
    const list = document.createElement("ul");
    list.className = "term-list";
    for (const term of terms) {
      appendText(list, "li", term);
    }
    termsSection.append(list);
  } else {
    appendText(termsSection, "p", "No terms available for this document.", "muted");
  }
  els.inspector.append(termsSection);

  const neighborsSection = inspectorSection("Nearest neighbors");
  const neighbors = point ? point.nearest_neighbors || [] : [];
  if (neighbors.length) {
    for (const neighbor of neighbors) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "neighbor-button";
      button.addEventListener("click", () => selectDocument(neighbor.document_id));
      appendText(button, "span", neighbor.title, "neighbor-title");
      appendText(button, "span", `${neighbor.relative_path} - ${neighbor.score}`, "neighbor-path");
      neighborsSection.append(button);
    }
  } else {
    appendText(neighborsSection, "p", "No nearest neighbors available.", "muted");
  }
  els.inspector.append(neighborsSection);

  const chunksSection = inspectorSection(`Chunks (${detail.chunk_count})`);
  const chunks = detail.chunks || [];
  if (chunks.length) {
    for (const chunk of chunks) {
      const block = document.createElement("div");
      block.className = "chunk";
      appendText(block, "div", `Chunk ${chunk.chunk_index} - ${chunk.char_count} chars`, "chunk-meta");
      appendText(block, "p", chunk.text);
      chunksSection.append(block);
    }
  } else if (detail.text_preview) {
    appendText(chunksSection, "p", detail.text_preview, "muted");
  } else {
    appendText(chunksSection, "p", "No chunk preview available.", "muted");
  }
  els.inspector.append(chunksSection);
}

function filteredPoints(points, docsById) {
  if (!state.filter) {
    return points;
  }
  return points.filter((point) => {
    const doc = docsById.get(point.document_id);
    const haystack = [
      doc ? doc.title : "",
      doc ? doc.relative_path : "",
      point.cluster_label,
      ...(point.top_terms || [])
    ].join(" ").toLowerCase();
    return haystack.includes(state.filter);
  });
}

function linkClass(a, b) {
  return state.selectedId === a || state.selectedId === b
    ? "map-link selected"
    : "map-link";
}

function projectPoint(point, width, height, padding) {
  return {
    x: padding + ((point.x + 1) / 2) * (width - padding * 2),
    y: padding + ((1 - (point.y + 1) / 2)) * (height - padding * 2)
  };
}

function clusterColor(clusterId) {
  return CLUSTER_COLORS[Math.abs(Number(clusterId) || 0) % CLUSTER_COLORS.length];
}

function showEmptyState(title, body, command) {
  els.mapSvg.hidden = true;
  els.mapEmpty.hidden = false;
  els.mapEmpty.replaceChildren();
  appendText(els.mapEmpty, "strong", title);
  appendText(els.mapEmpty, "div", body);
  if (command) {
    appendText(els.mapEmpty, "code", command);
  }
}

function showErrorState(title, message) {
  els.projectStatus.textContent = title;
  showEmptyState(title, message, null);
  setInspectorMessage(message);
}

function setInspectorMessage(message) {
  els.inspector.replaceChildren();
  appendText(els.inspector, "p", message, "muted");
}

function clearSearchResults() {
  els.searchResults.replaceChildren();
}

function inspectorSection(title) {
  const section = document.createElement("section");
  section.className = "inspector-section";
  appendText(section, "h3", title);
  return section;
}

function appendText(parent, tagName, text, className) {
  const child = document.createElement(tagName);
  if (className) {
    child.className = className;
  }
  child.textContent = text;
  parent.append(child);
  return child;
}

function svgEl(tagName, attributes = {}) {
  const element = document.createElementNS(SVG_NS, tagName);
  for (const [key, value] of Object.entries(attributes)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

async function fetchJson(url) {
  const response = await fetch(url, {
    headers: {
      Accept: "application/json"
    }
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const message = payload && payload.error
      ? payload.error.message
      : `${response.status} ${response.statusText}`;
    throw new Error(message);
  }
  return payload;
}
