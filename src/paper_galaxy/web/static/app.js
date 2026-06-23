const API = {
  health: "/api/health",
  config: "/api/config",
  stats: "/api/stats",
  map: "/api/map",
  mapRuns: "/api/map-runs",
  zoteroStatus: "/api/zotero/status",
  zoteroReadingMap: "/api/zotero/reading-map",
  search: "/api/search",
  documents: "/api/documents",
  clusters: "/api/clusters",
  explainPair: "/api/explain/pair"
};

const THEME_KEY = "paper-galaxy:theme";
const i18n = window.PaperGalaxyI18n.createI18n();

const state = {
  health: null,
  config: null,
  stats: null,
  map: null,
  mapRuns: [],
  graphSource: "documents",
  zoteroStatus: null,
  selectedRunId: "",
  graph: null,
  selectedId: null,
  selectedDetail: null,
  filter: "",
  searchTimer: null,
  lastSearchPayload: null
};

const els = {
  projectStatus: document.querySelector("#project-status"),
  activeCount: document.querySelector("#active-count"),
  missingCount: document.querySelector("#missing-count"),
  unindexedCount: document.querySelector("#unindexed-count"),
  lastScan: document.querySelector("#last-scan"),
  languageToggle: document.querySelector("#language-toggle"),
  themeToggle: document.querySelector("#theme-toggle"),
  searchForm: document.querySelector("#search-form"),
  searchInput: document.querySelector("#search-input"),
  includeMissing: document.querySelector("#include-missing"),
  searchResults: document.querySelector("#search-results"),
  pointFilter: document.querySelector("#point-filter"),
  clusterLegend: document.querySelector("#cluster-legend"),
  graphSourceSelect: document.querySelector("#graph-source-select"),
  zoteroStatus: document.querySelector("#zotero-status"),
  zoteroStatusFilter: document.querySelector("#zotero-status-filter"),
  zoteroTagFilter: document.querySelector("#zotero-tag-filter"),
  zoteroCollectionFilter: document.querySelector("#zotero-collection-filter"),
  zoteroEmptyHelp: document.querySelector("#zotero-empty-help"),
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
  i18n.apply();
  updateLanguageToggle();
  initTheme();
  initGraph();
  bindEvents();
  setInspectorMessage(t("inspector.default"));
  try {
    state.health = await fetchJson(API.health);
    state.config = await fetchJson(API.config);
    updateHealth(state.health);
    await loadStats();
    await loadZoteroStatus();
    await loadMapRuns();
    await loadMap();
  } catch (error) {
    showErrorState(t("map.loadFailed"), error.message);
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
  els.languageToggle.addEventListener("click", toggleLanguage);
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
  els.graphSourceSelect.addEventListener("change", async () => {
    state.graphSource = els.graphSourceSelect.value;
    state.selectedRunId = "";
    state.selectedId = null;
    state.selectedDetail = null;
    renderMapRunSelect();
    await loadMap();
    setInspectorMessage(t("inspector.default"));
  });
  for (const input of [
    els.zoteroStatusFilter,
    els.zoteroTagFilter,
    els.zoteroCollectionFilter
  ]) {
    input.addEventListener("change", reloadZoteroMap);
    input.addEventListener("input", () => {
      if (input.tagName === "INPUT") {
        window.clearTimeout(state.searchTimer);
        state.searchTimer = window.setTimeout(reloadZoteroMap, 220);
      }
    });
  }
  els.resetSelection.addEventListener("click", () => {
    state.selectedId = null;
    state.selectedDetail = null;
    if (state.graph) {
      state.graph.setSelected(null);
    }
    setInspectorMessage(t("inspector.default"));
  });
  els.mapRunSelect.addEventListener("change", async () => {
    state.selectedRunId = els.mapRunSelect.value;
    state.selectedId = null;
    state.selectedDetail = null;
    await loadMap(state.selectedRunId);
    setInspectorMessage(t("inspector.default"));
  });
}

function toggleLanguage() {
  i18n.toggle();
  i18n.apply();
  updateLanguageToggle();
  updateThemeToggle(document.documentElement.dataset.theme || "dark");
  if (state.health) {
    updateHealth(state.health);
  }
  if (state.stats) {
    updateStats(state.stats);
  }
  renderZoteroStatus();
  renderMapRunSelect();
  if (state.map) {
    renderMap();
  }
  if (state.lastSearchPayload) {
    renderSearchResults(state.lastSearchPayload, { remember: false });
  }
  if (state.selectedDetail) {
    renderInspector();
  } else {
    setInspectorMessage(t("inspector.default"));
  }
}

function updateLanguageToggle() {
  els.languageToggle.textContent = t("language.toggle");
  els.languageToggle.setAttribute("aria-label", t("language.aria"));
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

async function loadZoteroStatus() {
  try {
    const payload = await fetchJson(API.zoteroStatus);
    state.zoteroStatus = payload.zotero || null;
  } catch {
    state.zoteroStatus = null;
  }
  renderZoteroStatus();
}

async function loadMap(runId = "") {
  const base = state.graphSource === "zotero" ? API.zoteroReadingMap : API.map;
  const params = state.graphSource === "zotero" ? zoteroMapParams() : new URLSearchParams();
  if (runId) {
    params.set("run_id", runId);
  }
  const url = params.toString()
    ? `${base}?${params.toString()}`
    : base;
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

async function reloadZoteroMap() {
  if (state.graphSource !== "zotero") {
    return;
  }
  state.selectedId = null;
  state.selectedDetail = null;
  await loadMap();
  setInspectorMessage(t("inspector.default"));
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
    state.lastSearchPayload = null;
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
    state.lastSearchPayload = null;
    clearSearchResults();
    appendText(els.searchResults, "div", t("search.failed", { message: error.message }), "muted");
  }
}

async function selectDocument(documentId) {
  state.selectedId = documentId;
  state.selectedDetail = null;
  if (state.graph) {
    state.graph.setSelected(documentId);
  }
  setInspectorMessage(t("inspector.loading"));
  try {
    const detail = await fetchJson(`${API.documents}/${encodeURIComponent(documentId)}`);
    state.selectedDetail = detail;
    renderInspector();
  } catch (error) {
    setInspectorMessage(t("inspector.unavailable", { message: error.message }));
  }
}

function updateHealth(health) {
  const status = health.database_exists ? t("health.connected") : t("health.noDatabase");
  els.projectStatus.textContent = `${status} - ${health.project_dir}`;
}

function updateStats(stats) {
  els.activeCount.textContent = String(stats.active_documents);
  els.missingCount.textContent = String(stats.missing_documents);
  els.unindexedCount.textContent = String(stats.unindexed_documents);
  els.lastScan.textContent = stats.last_scan_time || t("health.none");
}

function renderZoteroStatus() {
  els.zoteroStatus.replaceChildren();
  const status = state.zoteroStatus;
  if (!status || !status.imported_item_count) {
    appendText(els.zoteroStatus, "div", t("zotero.noImports"));
    renderZoteroHelp(true);
    return;
  }
  appendText(
    els.zoteroStatus,
    "div",
    t("zotero.status", {
      items: status.imported_item_count,
      attachments: status.attachment_count
    })
  );
  const counts = status.reading_status_counts || {};
  appendText(
    els.zoteroStatus,
    "div",
    Object.keys(counts).map((key) => `${key}: ${counts[key]}`).join(" · "),
    "meta-row"
  );
  renderZoteroHelp(false);
}

function renderZoteroHelp(show) {
  els.zoteroEmptyHelp.hidden = !show;
  els.zoteroEmptyHelp.replaceChildren();
  if (!show) {
    return;
  }
  appendText(els.zoteroEmptyHelp, "div", t("zotero.instructions"));
  appendText(
    els.zoteroEmptyHelp,
    "code",
    "paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map"
  );
}

function updateMissingDatabase(error) {
  els.projectStatus.textContent = t("missing.title");
  els.activeCount.textContent = "0";
  els.missingCount.textContent = "0";
  els.unindexedCount.textContent = "0";
  els.lastScan.textContent = t("health.none");
  showEmptyState(
    t("missing.title"),
    t("missing.body"),
    error ? error.command : t("missing.command")
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
    const isZotero = state.graphSource === "zotero";
    const message = (payload.warnings && payload.warnings[0]) || t("map.noActiveMessage");
    showEmptyState(
      isZotero ? t("zotero.noImports") : t("map.noActiveTitle"),
      isZotero ? t("zotero.instructions") : message,
      isZotero
        ? "paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map"
        : null
    );
    renderLegend(points, payload.cluster_labels || {}, payload.clusters || []);
    els.mapCaption.textContent = t("graph.zeroActive");
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
  const mode = state.graph && state.graph.settings.animate
    ? t("graph.modeAnimated")
    : t("graph.modePaused");
  const base = t("graph.caption", { visible, total, mode });
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
    appendText(els.clusterLegend, "p", t("clusters.empty"), "muted");
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
    label.textContent = cluster.display_label || clusterLabels[id] || t("clusters.fallback", { id });
    const meta = document.createElement("span");
    meta.className = "legend-meta";
    meta.textContent = t("clusters.meta", {
      source: sourceLabel(cluster.source || "generated"),
      count: counts.get(Number(id)) || 0
    });
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
      rename.textContent = t("cluster.rename");
      rename.addEventListener("click", () => renameCluster(cluster, row));
      actions.append(rename);
      if (cluster.source === "manual") {
        const reset = document.createElement("button");
        reset.type = "button";
        reset.className = "tiny-button";
        reset.textContent = t("cluster.reset");
        reset.addEventListener("click", () => resetClusterLabel(cluster.cluster_signature));
        actions.append(reset);
      }
    }
    row.append(swatch, body, actions);
    els.clusterLegend.append(row);
  }
}

function renderSearchResults(payload, options = {}) {
  clearSearchResults();
  if (options.remember !== false) {
    state.lastSearchPayload = payload;
  }
  if (!payload.database_exists) {
    appendText(els.searchResults, "div", t("search.noDatabase"), "muted");
    return;
  }
  const results = payload.results || [];
  if (!results.length) {
    appendText(els.searchResults, "div", t("search.noResults"), "muted");
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
    setInspectorMessage(t("inspector.default"));
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
    t("inspector.documentMeta", {
      type: metadata.file_type || t("inspector.fileFallback"),
      status: metadata.status,
      chars: metadata.char_count
    }),
    "meta-row"
  );
  if (metadata.local_path) {
    appendText(els.inspector, "div", metadata.local_path, "meta-row");
  }
  renderPinControl(metadata.document_id);
  renderClusterInspector(point);
  renderZoteroInspector(metadata.document_id);

  const terms = point ? point.top_terms || [] : [];
  const termsSection = inspectorSection(t("inspector.topTerms"));
  if (terms.length) {
    const list = document.createElement("ul");
    list.className = "term-list";
    for (const term of terms) {
      appendText(list, "li", term);
    }
    termsSection.append(list);
  } else {
    appendText(termsSection, "p", t("inspector.noTerms"), "muted");
  }
  els.inspector.append(termsSection);

  const neighborsSection = inspectorSection(t("inspector.neighbors"));
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
      why.textContent = t("inspector.whyButton");
      why.addEventListener("click", () => loadPairExplanation(metadata.document_id, neighbor.document_id));
      row.append(button, why);
      neighborsSection.append(row);
    }
  } else {
    appendText(neighborsSection, "p", t("inspector.noNeighbors"), "muted");
  }
  els.inspector.append(neighborsSection);

  const pairSection = inspectorSection(t("inspector.why"));
  pairSection.id = "pair-explanation";
  appendText(pairSection, "p", t("inspector.whyPrompt"), "muted");
  els.inspector.append(pairSection);

  const chunksSection = inspectorSection(t("inspector.chunks", { count: detail.chunk_count }));
  const chunks = detail.chunks || [];
  if (chunks.length) {
    for (const chunk of chunks) {
      const block = document.createElement("div");
      block.className = "chunk";
      appendText(
        block,
        "div",
        t("inspector.chunkMeta", { index: chunk.chunk_index, chars: chunk.char_count }),
        "chunk-meta"
      );
      appendText(block, "p", chunk.text);
      chunksSection.append(block);
    }
  } else if (detail.text_preview) {
    appendText(chunksSection, "p", detail.text_preview, "muted");
  } else {
    appendText(chunksSection, "p", t("inspector.noChunk"), "muted");
  }
  els.inspector.append(chunksSection);
}

function renderZoteroInspector(documentId) {
  const documents = state.map && state.map.documents ? state.map.documents : [];
  const row = documents.find((document) => document.document_id === documentId);
  if (!row || !row.zotero) {
    return;
  }
  const zotero = row.zotero;
  const section = inspectorSection(t("zotero.metadata"));
  appendText(section, "div", `Key: ${zotero.zotero_key || ""}`, "meta-row");
  appendText(section, "div", `${t("zotero.statusFilter")}: ${zotero.reading_status || "unknown"}`, "meta-row");
  appendText(section, "div", `${t("zotero.creators")}: ${zotero.creators || ""}`, "meta-row");
  appendText(section, "div", `${t("zotero.publication")}: ${zotero.publication || ""}`, "meta-row");
  appendText(section, "div", `${t("zotero.tags")}: ${zotero.tags || ""}`, "meta-row");
  appendText(section, "div", `${t("zotero.collections")}: ${zotero.collections || ""}`, "meta-row");
  appendText(section, "div", `${t("zotero.attachmentStatus")}: ${zotero.attachment_status || ""}`, "meta-row");
  if (zotero.zotero_key) {
    appendText(section, "code", `zotero://select/items/${zotero.zotero_key}`);
  }
  els.inspector.append(section);
}

function renderClusterInspector(point) {
  if (!point) {
    return;
  }
  const cluster = findCluster(point.cluster_signature);
  const section = inspectorSection(t("inspector.cluster"));
  const titleRow = document.createElement("div");
  titleRow.className = "cluster-title-row";
  appendText(titleRow, "strong", point.cluster_label || "Cluster");
  appendText(titleRow, "span", sourceLabel(cluster ? cluster.source : "generated"), "source-pill");
  section.append(titleRow);
  if (cluster && cluster.source === "manual" && cluster.generated_label) {
    appendText(
      section,
      "p",
      t("inspector.generatedLabel", { label: cluster.generated_label }),
      "muted"
    );
  }
  appendText(section, "div", point.cluster_signature || "", "meta-row");
  const actions = document.createElement("div");
  actions.className = "cluster-actions";
  if (!state.selectedRunId) {
    const rename = document.createElement("button");
    rename.type = "button";
    rename.textContent = t("cluster.rename");
    rename.addEventListener("click", () => renameCluster(cluster || point, section));
    actions.append(rename);
    if (cluster && cluster.source === "manual") {
      const reset = document.createElement("button");
      reset.type = "button";
      reset.textContent = t("cluster.reset");
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
  input.setAttribute("aria-label", t("cluster.labelAria"));
  const error = document.createElement("p");
  error.className = "cluster-editor-error";
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = t("cluster.save");
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.textContent = t("cluster.cancel");
  cancel.addEventListener("click", () => form.remove());
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const label = input.value.trim();
    if (!label || label.length > 120) {
      error.textContent = t("cluster.labelError");
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
  appendText(section, "h3", t("inspector.why"));
  appendText(section, "p", t("pair.loading"), "muted");
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
    appendText(section, "h3", t("inspector.why"));
    appendText(section, "p", t("pair.failed", { message: error.message }), "muted");
  }
}

function renderPairExplanation(section, explanation) {
  section.replaceChildren();
  appendText(section, "h3", t("inspector.why"));
  appendText(
    section,
    "p",
    `${explanation.source.title} -> ${explanation.target.title}`,
    "muted"
  );
  appendText(
    section,
    "div",
    t("pair.lexicalScore", { score: explanation.lexical_score }),
    "meta-row"
  );
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
      t("pair.chunks", {
        source: match.source_chunk_index,
        target: match.target_chunk_index,
        score: match.score
      }),
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
  status.textContent = pinned ? t("inspector.pinStatus") : t("inspector.freeStatus");
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = pinned ? t("inspector.unpin") : t("inspector.pin");
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
  return `${identity}|source:${state.graphSource}|run:${runId}|seed:${seed}|limit:${limit}|docs:${points.length}`;
}

function zoteroMapParams() {
  const params = new URLSearchParams();
  params.set("status", els.zoteroStatusFilter.value || "all");
  const tag = els.zoteroTagFilter.value.trim();
  const collection = els.zoteroCollectionFilter.value.trim();
  if (tag) {
    params.set("tag", tag);
  }
  if (collection) {
    params.set("collection", collection);
  }
  return params;
}

function renderMapRunSelect() {
  els.mapRunSelect.replaceChildren();
  const liveOption = document.createElement("option");
  liveOption.value = "";
  liveOption.textContent = t("graph.liveMap");
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
    theme === "dark" ? t("theme.aria.light") : t("theme.aria.dark")
  );
}

function sourceLabel(source) {
  if (source === "manual") {
    return t("clusters.manual");
  }
  if (source === "generated") {
    return t("clusters.generated");
  }
  return source || t("clusters.generated");
}

function t(key, values = {}) {
  return i18n.t(key, values);
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
