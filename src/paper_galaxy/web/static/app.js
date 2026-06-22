const API = {
  health: "/api/health",
  config: "/api/config",
  stats: "/api/stats",
  map: "/api/map",
  search: "/api/search",
  documents: "/api/documents"
};

const THEME_KEY = "paper-galaxy:theme";

const state = {
  health: null,
  config: null,
  stats: null,
  map: null,
  graph: null,
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
  themeToggle: document.querySelector("#theme-toggle"),
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
  resetView: document.querySelector("#reset-view"),
  resetLayout: document.querySelector("#reset-layout"),
  pauseGraph: document.querySelector("#pause-graph"),
  animateToggle: document.querySelector("#animate-toggle"),
  arrowsToggle: document.querySelector("#arrows-toggle"),
  centerForce: document.querySelector("#center-force"),
  repelForce: document.querySelector("#repel-force"),
  linkForce: document.querySelector("#link-force"),
  linkDistance: document.querySelector("#link-distance"),
  nodeSize: document.querySelector("#node-size"),
  linkThickness: document.querySelector("#link-thickness"),
  labelThreshold: document.querySelector("#label-threshold"),
  inspector: document.querySelector("#inspector-content")
};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  initTheme();
  initGraph();
  bindEvents();
  setInspectorMessage("Select a document point or search result.");
  try {
    state.health = await fetchJson(API.health);
    state.config = await fetchJson(API.config);
    updateHealth(state.health);
    await loadStats();
    await loadMap();
  } catch (error) {
    showErrorState("Unable to load Paper Galaxy.", error.message);
  }
}

function initGraph() {
  state.graph = new window.PaperGalaxyGraph.ForceGraph(els.mapSvg, {
    onSelect: selectDocument,
    onVisibleCountChange: updateMapCaption,
    onPinChange: () => {
      if (state.selectedDetail) {
        renderInspector();
      }
    },
    onLayoutReset: () => {
      if (state.selectedDetail) {
        renderInspector();
      }
    }
  });
  state.graph.bindControls({
    animate: els.animateToggle,
    showArrows: els.arrowsToggle,
    centerForce: els.centerForce,
    repelForce: els.repelForce,
    linkForce: els.linkForce,
    linkDistance: els.linkDistance,
    nodeSize: els.nodeSize,
    linkThickness: els.linkThickness,
    labelThreshold: els.labelThreshold,
    resetView: els.resetView,
    resetLayout: els.resetLayout,
    pause: els.pauseGraph
  });
}

function bindEvents() {
  els.themeToggle.addEventListener("click", toggleTheme);
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
    if (state.graph) {
      state.graph.setFilter(state.filter);
    }
  });
  els.resetSelection.addEventListener("click", () => {
    state.selectedId = null;
    state.selectedDetail = null;
    if (state.graph) {
      state.graph.setSelected(null);
    }
    setInspectorMessage("Select a document point or search result.");
  });
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
    if (state.graph) {
      state.graph.clear();
    }
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
  if (state.graph) {
    state.graph.setSelected(documentId);
  }
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
    "Run indexing from the command line before opening the graph.",
    error ? error.command : "paper-galaxy index /path/to/corpus --project-dir /path/to/project"
  );
}

function renderMap() {
  const payload = state.map;
  if (!payload || !payload.database_exists) {
    els.mapSvg.hidden = true;
    return;
  }
  const points = payload.points || [];
  if (!points.length) {
    els.mapSvg.hidden = true;
    const message = (payload.warnings && payload.warnings[0]) || "No active indexed documents found.";
    showEmptyState("No active documents", message, null);
    renderLegend(points, payload.cluster_labels || {});
    els.mapCaption.textContent = "0 active documents";
    if (state.graph) {
      state.graph.clear();
    }
    return;
  }

  els.mapEmpty.hidden = true;
  els.mapSvg.hidden = false;
  renderLegend(points, payload.cluster_labels || {});
  state.graph.setData(payload, { layoutKey: graphLayoutKey(payload) });
  state.graph.setFilter(state.filter);
  state.graph.setSelected(state.selectedId);
}

function updateMapCaption(visible, total) {
  if (!state.map || !state.map.database_exists) {
    return;
  }
  const warnings = state.map.warnings || [];
  const mode = state.graph && state.graph.settings.animate ? "animated" : "paused";
  const base = `${visible} of ${total} active documents - semantic TF-IDF links - ${mode}`;
  els.mapCaption.textContent = warnings.length ? `${base} - ${warnings[0]}` : base;
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
    swatch.style.background = window.PaperGalaxyGraph.clusterColor(Number(id));
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
  renderPinControl(metadata.document_id);

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

function renderPinControl(documentId) {
  if (!state.graph || !state.graph.nodeById.has(documentId)) {
    return;
  }
  const pinned = state.graph.isPinned(documentId);
  const row = document.createElement("div");
  row.className = "pin-row";
  const status = document.createElement("span");
  status.textContent = pinned ? "Pinned manual position" : "Free force layout";
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = pinned ? "Unpin" : "Pin";
  button.addEventListener("click", () => {
    state.graph.togglePin(documentId);
    renderInspector();
  });
  row.append(status, button);
  els.inspector.append(row);
}

function graphLayoutKey(payload) {
  const config = state.config || {};
  const identity =
    config.database_path ||
    (state.health && state.health.database_path) ||
    (state.health && state.health.project_dir) ||
    "unknown";
  const points = payload.points || [];
  const seed = config.seed === null || config.seed === undefined ? "default" : config.seed;
  const limit = config.map_limit === null || config.map_limit === undefined ? "default" : config.map_limit;
  return `${identity}|seed:${seed}|limit:${limit}|docs:${points.length}`;
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

function initTheme() {
  const theme = localStorage.getItem(THEME_KEY) || "dark";
  document.documentElement.dataset.theme = theme;
  updateThemeToggle(theme);
}

function toggleTheme() {
  const current = document.documentElement.dataset.theme || "dark";
  const next = current === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem(THEME_KEY, next);
  updateThemeToggle(next);
}

function updateThemeToggle(theme) {
  els.themeToggle.setAttribute(
    "aria-label",
    theme === "dark" ? "Switch to light theme" : "Switch to dark theme"
  );
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
