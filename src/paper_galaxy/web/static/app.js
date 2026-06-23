const API = {
  health: "/api/health",
  config: "/api/config",
  stats: "/api/stats",
  map: "/api/map",
  mapRuns: "/api/map-runs",
  search: "/api/search",
  documents: "/api/documents",
  clusters: "/api/clusters",
  explainPair: "/api/explain/pair"
};

const THEME_KEY = "paper-galaxy:theme";

const state = {
  health: null,
  config: null,
  stats: null,
  map: null,
  mapRuns: [],
  selectedRunId: "",
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
  mapRunSelect: document.querySelector("#map-run-select"),
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
  labelMode: document.querySelector("#label-mode"),
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
    await loadMapRuns();
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
    labelMode: els.labelMode,
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
  els.mapRunSelect.addEventListener("change", async () => {
    state.selectedRunId = els.mapRunSelect.value;
    state.selectedId = null;
    state.selectedDetail = null;
    await loadMap(state.selectedRunId);
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

async function loadMap(runId = "") {
  const url = runId
    ? `${API.map}?${new URLSearchParams({ run_id: runId }).toString()}`
    : API.map;
  const payload = await fetchJson(url);
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

async function loadMapRuns() {
  try {
    const payload = await fetchJson(API.mapRuns);
    state.mapRuns = payload.database_exists ? payload.map_runs || [] : [];
    renderMapRunSelect();
  } catch {
    state.mapRuns = [];
    renderMapRunSelect();
  }
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
    renderLegend(points, payload.cluster_labels || {}, payload.clusters || []);
    els.mapCaption.textContent = "0 active documents";
    if (state.graph) {
      state.graph.clear();
    }
    return;
  }

  els.mapEmpty.hidden = true;
  els.mapSvg.hidden = false;
  renderLegend(points, payload.cluster_labels || {}, payload.clusters || []);
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

function renderLegend(points, clusterLabels, clusters) {
  const counts = new Map();
  for (const point of points) {
    counts.set(point.cluster_id, (counts.get(point.cluster_id) || 0) + 1);
  }
  els.clusterLegend.replaceChildren();
  const clusterRows = clusters && clusters.length
    ? clusters.slice().sort((a, b) => Number(a.cluster_id) - Number(b.cluster_id))
    : Object.keys(clusterLabels)
      .sort((a, b) => Number(a) - Number(b))
      .map((id) => ({
        cluster_id: Number(id),
        display_label: clusterLabels[id],
        generated_label: clusterLabels[id],
        source: "generated",
        top_terms: [],
        cluster_signature: ""
      }));
  const ids = clusterRows.map((cluster) => String(cluster.cluster_id));
  if (!ids.length) {
    appendText(els.clusterLegend, "p", "No clusters yet.", "muted");
    return;
  }
  for (const cluster of clusterRows) {
    const id = String(cluster.cluster_id);
    const row = document.createElement("div");
    row.className = "legend-item";
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = window.PaperGalaxyGraph.clusterColor(Number(id));
    const body = document.createElement("div");
    body.className = "legend-body";
    const label = document.createElement("span");
    label.className = "legend-label";
    label.textContent = cluster.display_label || clusterLabels[id] || `Cluster ${id}`;
    const meta = document.createElement("span");
    meta.className = "legend-meta";
    meta.textContent = `${cluster.source || "generated"} - ${counts.get(Number(id)) || 0} docs`;
    body.append(label, meta);
    const terms = Array.isArray(cluster.top_terms)
      ? cluster.top_terms.map((item) => item.term).filter(Boolean).slice(0, 3)
      : [];
    if (terms.length) {
      const termRow = document.createElement("div");
      termRow.className = "chip-row";
      for (const term of terms) {
        appendText(termRow, "span", term, "chip");
      }
      body.append(termRow);
    }
    const actions = document.createElement("div");
    actions.className = "legend-actions";
    if (cluster.cluster_signature && !state.selectedRunId) {
      const rename = document.createElement("button");
      rename.type = "button";
      rename.className = "tiny-button";
      rename.textContent = "Rename";
      rename.addEventListener("click", () => renameCluster(cluster, row));
      actions.append(rename);
      if (cluster.source === "manual") {
        const reset = document.createElement("button");
        reset.type = "button";
        reset.className = "tiny-button";
        reset.textContent = "Reset";
        reset.addEventListener("click", () => resetClusterLabel(cluster.cluster_signature));
        actions.append(reset);
      }
    }
    row.append(swatch, body, actions);
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
  renderClusterInspector(point);

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
      const row = document.createElement("div");
      row.className = "neighbor-row";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "neighbor-button";
      button.addEventListener("click", () => selectDocument(neighbor.document_id));
      appendText(button, "span", neighbor.title, "neighbor-title");
      appendText(button, "span", `${neighbor.relative_path} - ${neighbor.score}`, "neighbor-path");
      const why = document.createElement("button");
      why.type = "button";
      why.className = "why-button";
      why.textContent = "Why?";
      why.addEventListener("click", () => loadPairExplanation(metadata.document_id, neighbor.document_id));
      row.append(button, why);
      neighborsSection.append(row);
    }
  } else {
    appendText(neighborsSection, "p", "No nearest neighbors available.", "muted");
  }
  els.inspector.append(neighborsSection);

  const pairSection = inspectorSection("Why nearby?");
  pairSection.id = "pair-explanation";
  appendText(pairSection, "p", "Choose Why? beside a neighbor.", "muted");
  els.inspector.append(pairSection);

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

function renderClusterInspector(point) {
  if (!point) {
    return;
  }
  const cluster = findCluster(point.cluster_signature);
  const section = inspectorSection("Cluster");
  const titleRow = document.createElement("div");
  titleRow.className = "cluster-title-row";
  appendText(titleRow, "strong", point.cluster_label || "Cluster");
  appendText(titleRow, "span", cluster ? cluster.source : "generated", "source-pill");
  section.append(titleRow);
  if (cluster && cluster.source === "manual" && cluster.generated_label) {
    appendText(section, "p", `Generated: ${cluster.generated_label}`, "muted");
  }
  appendText(section, "div", point.cluster_signature || "", "meta-row");
  const actions = document.createElement("div");
  actions.className = "cluster-actions";
  if (!state.selectedRunId) {
    const rename = document.createElement("button");
    rename.type = "button";
    rename.textContent = "Rename";
    rename.addEventListener("click", () => renameCluster(cluster || point, section));
    actions.append(rename);
    if (cluster && cluster.source === "manual") {
      const reset = document.createElement("button");
      reset.type = "button";
      reset.textContent = "Reset";
      reset.addEventListener("click", () => resetClusterLabel(cluster.cluster_signature));
      actions.append(reset);
    }
  }
  if (actions.children.length) {
    section.append(actions);
  }
  els.inspector.append(section);
}

function renameCluster(cluster, host) {
  const current = cluster.display_label || cluster.cluster_label || "";
  const signature = cluster.cluster_signature;
  if (!signature) {
    return;
  }
  const existing = host.querySelector(".cluster-editor");
  if (existing) {
    existing.remove();
  }
  const form = document.createElement("form");
  form.className = "cluster-editor";
  const input = document.createElement("input");
  input.type = "text";
  input.value = current;
  input.maxLength = 120;
  input.setAttribute("aria-label", "Cluster label");
  const error = document.createElement("p");
  error.className = "cluster-editor-error";
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = "Save";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.textContent = "Cancel";
  cancel.addEventListener("click", () => form.remove());
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const label = input.value.trim();
    if (!label || label.length > 120) {
      error.textContent = "Cluster label must be 1-120 characters.";
      return;
    }
    await saveClusterLabel(signature, label);
  });
  const actions = document.createElement("div");
  actions.className = "cluster-editor-actions";
  actions.append(save, cancel);
  form.append(input, actions, error);
  host.append(form);
  input.focus();
  input.select();
}

async function saveClusterLabel(signature, label) {
  await fetchJson(`${API.clusters}/${encodeURIComponent(signature)}/label`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ label })
  });
  await loadMap(state.selectedRunId);
  if (state.selectedId) {
    await selectDocument(state.selectedId);
  }
}

async function resetClusterLabel(clusterSignature) {
  await fetchJson(`${API.clusters}/${encodeURIComponent(clusterSignature)}/label`, {
    method: "DELETE"
  });
  await loadMap(state.selectedRunId);
  if (state.selectedId) {
    await selectDocument(state.selectedId);
  }
}

async function loadPairExplanation(sourceId, targetId) {
  const section = document.querySelector("#pair-explanation");
  if (!section) {
    return;
  }
  section.replaceChildren();
  appendText(section, "h3", "Why nearby?");
  appendText(section, "p", "Loading local explanation...", "muted");
  const params = new URLSearchParams({
    source: sourceId,
    target: targetId,
    term_limit: "8",
    chunk_limit: "3"
  });
  try {
    const payload = await fetchJson(`${API.explainPair}?${params.toString()}`);
    renderPairExplanation(section, payload.explanation);
  } catch (error) {
    section.replaceChildren();
    appendText(section, "h3", "Why nearby?");
    appendText(section, "p", `Explanation failed: ${error.message}`, "muted");
  }
}

function renderPairExplanation(section, explanation) {
  section.replaceChildren();
  appendText(section, "h3", "Why nearby?");
  appendText(
    section,
    "p",
    `${explanation.source.title} -> ${explanation.target.title}`,
    "muted"
  );
  appendText(section, "div", `Lexical score ${explanation.lexical_score}`, "meta-row");
  const terms = explanation.shared_terms || [];
  if (terms.length) {
    const chips = document.createElement("div");
    chips.className = "chip-row";
    for (const term of terms) {
      appendText(chips, "span", term.term, "chip");
    }
    section.append(chips);
  }
  for (const match of explanation.chunk_matches || []) {
    const block = document.createElement("div");
    block.className = "pair-match";
    appendText(
      block,
      "div",
      `Chunks ${match.source_chunk_index} -> ${match.target_chunk_index} - ${match.score}`,
      "chunk-meta"
    );
    appendText(block, "p", match.source_excerpt);
    appendText(block, "p", match.target_excerpt);
    section.append(block);
  }
  for (const warning of explanation.warnings || []) {
    appendText(section, "p", warning, "muted");
  }
}

function findCluster(clusterSignature) {
  const clusters = state.map && state.map.clusters ? state.map.clusters : [];
  return clusters.find((cluster) => cluster.cluster_signature === clusterSignature) || null;
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
  const runId = payload.map_run && payload.map_run.id ? payload.map_run.id : "live";
  const identity =
    config.database_path ||
    (state.health && state.health.database_path) ||
    (state.health && state.health.project_dir) ||
    "unknown";
  const points = payload.points || [];
  const seed = config.seed === null || config.seed === undefined ? "default" : config.seed;
  const limit = config.map_limit === null || config.map_limit === undefined ? "default" : config.map_limit;
  return `${identity}|run:${runId}|seed:${seed}|limit:${limit}|docs:${points.length}`;
}

function renderMapRunSelect() {
  els.mapRunSelect.replaceChildren();
  const liveOption = document.createElement("option");
  liveOption.value = "";
  liveOption.textContent = "Live map";
  els.mapRunSelect.append(liveOption);
  for (const run of state.mapRuns) {
    const option = document.createElement("option");
    option.value = run.id;
    option.textContent = `${run.name} (${run.document_count} docs)`;
    els.mapRunSelect.append(option);
  }
  els.mapRunSelect.value = state.selectedRunId;
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

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.headers || {})
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
