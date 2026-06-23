(function () {
  const script = document.querySelector("script[data-map-src]");
  const mapSource = script ? script.getAttribute("data-map-src") : "../data/tiny-map.json";
  const locale = document.documentElement.lang.startsWith("zh") ? "zh" : "en";
  const text = {
    en: {
      loading: "Loading synthetic map...",
      select: "Select a document",
      selectHint: "Click a node to inspect metadata, cluster evidence, and TF-IDF neighbors.",
      terms: "Top terms",
      neighbors: "Nearest neighbors",
      why: "Why nearby?",
      noExplanation: "Choose a linked neighbor to see the static explanation.",
      cluster: "Cluster",
      saved: "Saved locally",
    },
    zh: {
      loading: "正在加载合成图谱...",
      select: "选择一篇文档",
      selectHint: "点击节点查看元数据、聚类证据和 TF-IDF 邻居。",
      terms: "关键词",
      neighbors: "最近邻文档",
      why: "为什么相近？",
      noExplanation: "选择一条邻近关系后查看静态解释。",
      cluster: "聚类",
      saved: "已保存在本地浏览器",
    },
  }[locale];
  const colors = ["#68d8c8", "#b9b1ff", "#f3bd5c", "#86b7ff", "#ff8d9e"];
  const state = {
    data: null,
    nodes: [],
    links: [],
    selectedId: null,
    hoveredId: null,
    width: 1000,
    height: 700,
    labels: new Map(),
  };

  const svg = document.querySelector("[data-graph-canvas]");
  const legend = document.querySelector("[data-legend]");
  const inspector = document.querySelector("[data-inspector]");
  const whyPanel = document.querySelector("[data-why]");
  if (!svg || !legend || !inspector || !whyPanel) {
    return;
  }

  inspector.innerHTML = `<h3>${text.select}</h3><p class="muted">${text.selectHint}</p>`;
  whyPanel.innerHTML = `<h3>${text.why}</h3><p class="muted">${text.noExplanation}</p>`;

  fetch(mapSource)
    .then((response) => response.json())
    .then((data) => {
      state.data = data;
      state.labels = loadLabels(data);
      prepareGraph(data);
      renderLegend();
      renderGraph();
      selectNode(initialNode().id);
      tick();
    })
    .catch((error) => {
      inspector.innerHTML = `<p>${text.loading}</p><p class="muted">${error}</p>`;
    });

  function prepareGraph(data) {
    const documents = new Map(data.documents.map((doc) => [doc.document_id, doc]));
    const xs = data.points.map((point) => Number(point.x));
    const ys = data.points.map((point) => Number(point.y));
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    state.nodes = data.points.map((point, index) => {
      const x = scale(Number(point.x), minX, maxX, 120, 860);
      const y = scale(Number(point.y), minY, maxY, 75, 420);
      const doc = documents.get(point.document_id) || {};
      return {
        ...point,
        id: point.document_id,
        title: doc.title || point.document_id,
        relative_path: doc.relative_path || "",
        file_type: doc.file_type || "",
        x,
        y,
        vx: 0,
        vy: 0,
        color: colors[Math.abs(Number(point.cluster_id)) % colors.length],
      };
    });
    const seen = new Set();
    state.links = [];
    for (const node of state.nodes) {
      for (const neighbor of node.nearest_neighbors || []) {
        const key = [node.id, neighbor.document_id].sort().join("|");
        if (!seen.has(key)) {
          seen.add(key);
          state.links.push({ source: node.id, target: neighbor.document_id });
        }
      }
    }
  }

  function renderLegend() {
    const clusters = state.data.clusters || [];
    legend.innerHTML = clusters
      .map((cluster, index) => {
        const label = displayClusterLabel(cluster);
        const color = colors[index % colors.length];
        return `<button class="cluster-button" data-cluster="${cluster.cluster_signature}">
          <span class="cluster-swatch" style="background:${color}"></span>${escapeHtml(label)}
          <br><span class="muted">${cluster.size || 0} docs</span>
        </button>`;
      })
      .join("");
    legend.querySelectorAll("button[data-cluster]").forEach((button) => {
      button.addEventListener("click", () => {
        const signature = button.getAttribute("data-cluster");
        const node = state.nodes.find((item) => item.cluster_signature === signature);
        if (node) {
          selectNode(node.id);
        }
      });
    });
  }

  function renderGraph() {
    svg.innerHTML = "";
    for (const link of state.links) {
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.classList.add("graph-link");
      line.dataset.source = link.source;
      line.dataset.target = link.target;
      svg.appendChild(line);
    }
    for (const node of state.nodes) {
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.classList.add("graph-node");
      circle.dataset.id = node.id;
      circle.setAttribute("r", String(11 + Math.min(8, node.top_terms.length)));
      circle.setAttribute("fill", node.color);
      circle.setAttribute("tabindex", "0");
      circle.setAttribute("role", "button");
      circle.setAttribute("aria-label", node.title);
      circle.addEventListener("mouseenter", () => {
        state.hoveredId = node.id;
        updateGraph();
      });
      circle.addEventListener("mouseleave", () => {
        state.hoveredId = null;
        updateGraph();
      });
      circle.addEventListener("click", () => selectNode(node.id));
      circle.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectNode(node.id);
        }
      });
      svg.appendChild(circle);

      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.classList.add("graph-label");
      label.dataset.id = node.id;
      label.textContent = shortTitle(node.title);
      svg.appendChild(label);
    }
    updateGraph();
  }

  function tick() {
    for (const link of state.links) {
      const source = byId(link.source);
      const target = byId(link.target);
      if (!source || !target) {
        continue;
      }
      const dx = target.x - source.x;
      const dy = target.y - source.y;
      const distance = Math.max(1, Math.hypot(dx, dy));
      const force = (distance - 190) * 0.00045;
      source.vx += dx * force;
      source.vy += dy * force;
      target.vx -= dx * force;
      target.vy -= dy * force;
    }
    for (let i = 0; i < state.nodes.length; i += 1) {
      for (let j = i + 1; j < state.nodes.length; j += 1) {
        const a = state.nodes[i];
        const b = state.nodes[j];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const distance = Math.max(1, Math.hypot(dx, dy));
        const push = Math.min(0.9, 1600 / (distance * distance));
        a.vx -= (dx / distance) * push;
        a.vy -= (dy / distance) * push;
        b.vx += (dx / distance) * push;
        b.vy += (dy / distance) * push;
      }
    }
    for (const node of state.nodes) {
      node.vx += (500 - node.x) * 0.0008;
      node.vy += (275 - node.y) * 0.0008;
      node.vx *= 0.86;
      node.vy *= 0.86;
      node.x = clamp(node.x + node.vx, 45, 955);
      node.y = clamp(node.y + node.vy, 45, 570);
    }
    updateGraph();
    window.requestAnimationFrame(tick);
  }

  function updateGraph() {
    const focus = focusSet();
    svg.querySelectorAll(".graph-link").forEach((line) => {
      const source = byId(line.dataset.source);
      const target = byId(line.dataset.target);
      if (!source || !target) {
        return;
      }
      line.setAttribute("x1", source.x);
      line.setAttribute("y1", source.y);
      line.setAttribute("x2", target.x);
      line.setAttribute("y2", target.y);
      const focused = focus.has(source.id) && focus.has(target.id);
      line.classList.toggle("is-focused", focused);
      line.classList.toggle("is-muted", focus.size > 1 && !focused);
    });
    svg.querySelectorAll(".graph-node").forEach((circle) => {
      const node = byId(circle.dataset.id);
      if (!node) {
        return;
      }
      circle.setAttribute("cx", node.x);
      circle.setAttribute("cy", node.y);
      circle.classList.toggle("is-selected", node.id === state.selectedId);
      circle.classList.toggle("is-muted", focus.size > 1 && !focus.has(node.id));
    });
    svg.querySelectorAll(".graph-label").forEach((label) => {
      const node = byId(label.dataset.id);
      if (!node) {
        return;
      }
      label.setAttribute("x", node.x + 16);
      label.setAttribute("y", node.y - 14);
      const visible = node.id === state.selectedId || node.id === state.hoveredId;
      label.style.display = visible ? "block" : "none";
      label.classList.toggle("is-muted", focus.size > 1 && !focus.has(node.id));
    });
  }

  function selectNode(id) {
    state.selectedId = id;
    const node = byId(id);
    if (!node) {
      return;
    }
    renderInspector(node);
    renderWhy(node);
    updateGraph();
  }

  function renderInspector(node) {
    const cluster = state.data.clusters.find(
      (item) => item.cluster_signature === node.cluster_signature
    );
    const label = cluster ? displayClusterLabel(cluster) : node.cluster_label;
    inspector.innerHTML = `
      <h3>${escapeHtml(node.title)}</h3>
      <p class="muted">${escapeHtml(node.relative_path)} · ${escapeHtml(node.file_type)}</p>
      <p><strong>${text.cluster}:</strong> ${escapeHtml(label)}</p>
      <h3>${text.terms}</h3>
      <ul class="term-list">${(node.top_terms || []).map((term) => `<li>${escapeHtml(term)}</li>`).join("")}</ul>
      <h3>${text.neighbors}</h3>
      <ul class="neighbor-list">${(node.nearest_neighbors || [])
        .map((neighbor) => `<li><button data-neighbor="${neighbor.document_id}">${escapeHtml(neighbor.title)}<br><span class="muted">${Number(neighbor.score).toFixed(3)}</span></button></li>`)
        .join("")}</ul>
      <h3>${locale === "zh" ? "重命名聚类演示" : "Rename cluster demo"}</h3>
      <div class="rename-row">
        <input data-label-input value="${escapeHtml(label)}" aria-label="Cluster label">
        <button data-save-label>${locale === "zh" ? "保存" : "Save"}</button>
      </div>
      <p class="muted">${locale === "zh" ? "这里只写入浏览器 localStorage，不会修改 SQLite。" : "This writes to browser localStorage only, not SQLite."}</p>
    `;
    inspector.querySelectorAll("[data-neighbor]").forEach((button) => {
      button.addEventListener("click", () => selectNode(button.dataset.neighbor));
    });
    const save = inspector.querySelector("[data-save-label]");
    const input = inspector.querySelector("[data-label-input]");
    save.addEventListener("click", () => {
      state.labels.set(node.cluster_signature, input.value.trim() || label);
      window.localStorage.setItem("paper-galaxy-demo-labels", JSON.stringify([...state.labels]));
      renderLegend();
      renderInspector(node);
      save.textContent = text.saved;
    });
  }

  function renderWhy(node) {
    const explanation = (state.data.explanations || []).find(
      (item) =>
        item.source.document_id === node.id ||
        item.target.document_id === node.id
    );
    if (!explanation) {
      whyPanel.innerHTML = `<h3>${text.why}</h3><p class="muted">${text.noExplanation}</p>`;
      return;
    }
    const other =
      explanation.source.document_id === node.id
        ? explanation.target
        : explanation.source;
    whyPanel.innerHTML = `
      <h3>${text.why}</h3>
      <p class="muted">${escapeHtml(node.title)} ↔ ${escapeHtml(other.title)}</p>
      <ul class="explain-list">${(explanation.shared_terms || [])
        .map((term) => `<li>${escapeHtml(term.term)} · ${Number(term.score).toFixed(3)}</li>`)
        .join("")}</ul>
      ${(explanation.chunk_matches || [])
        .slice(0, 1)
        .map((match) => `<p>${escapeHtml(match.source_excerpt)}</p><p class="muted">${escapeHtml(match.target_excerpt)}</p>`)
        .join("")}
    `;
  }

  function focusSet() {
    const active = state.hoveredId || state.selectedId;
    if (!active) {
      return new Set();
    }
    const set = new Set([active]);
    for (const link of state.links) {
      if (link.source === active) {
        set.add(link.target);
      }
      if (link.target === active) {
        set.add(link.source);
      }
    }
    return set;
  }

  function displayClusterLabel(cluster) {
    return state.labels.get(cluster.cluster_signature) || cluster.display_label;
  }

  function loadLabels(data) {
    try {
      const stored = new Map(JSON.parse(window.localStorage.getItem("paper-galaxy-demo-labels") || "[]"));
      const valid = new Set((data.clusters || []).map((cluster) => cluster.cluster_signature));
      return new Map([...stored].filter(([key]) => valid.has(key)));
    } catch {
      return new Map();
    }
  }

  function byId(id) {
    return state.nodes.find((node) => node.id === id);
  }

  function initialNode() {
    return [...state.nodes].sort((a, b) => {
      const da = Math.hypot(a.x - 500, a.y - 275);
      const db = Math.hypot(b.x - 500, b.y - 275);
      return da - db;
    })[0];
  }

  function scale(value, min, max, outMin, outMax) {
    if (max === min) {
      return (outMin + outMax) / 2;
    }
    return outMin + ((value - min) / (max - min)) * (outMax - outMin);
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function shortTitle(value) {
    return value.length > 28 ? `${value.slice(0, 25)}...` : value;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }
})();
