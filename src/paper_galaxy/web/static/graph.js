(function () {
  const SVG_NS = ["http:", "//www.w3.org/2000/svg"].join("");
  const SETTINGS_KEY = "paper-galaxy:graph-settings:v1";

  const CLUSTER_COLORS = [
    "#6ee7d8",
    "#8aa4ff",
    "#eab86b",
    "#f48ca8",
    "#b99cff",
    "#67d4f2",
    "#a7d779",
    "#f39a72"
  ];

  const DEFAULT_SETTINGS = {
    animate: true,
    showArrows: false,
    centerForce: 0.007,
    repelForce: 1600,
    linkForce: 0.018,
    linkDistance: 118,
    nodeSize: 7,
    linkThickness: 1.1,
    labelThreshold: 1.4
  };

  class ForceGraph {
    constructor(svg, options = {}) {
      this.svg = svg;
      this.options = options;
      this.nodes = [];
      this.links = [];
      this.nodeById = new Map();
      this.adjacency = new Map();
      this.pointCount = 0;
      this.layoutKey = "default";
      this.layoutStorageKey = "";
      this.filter = "";
      this.selectedId = null;
      this.hoveredId = null;
      this.pointerWorld = null;
      this.dragging = null;
      this.panning = null;
      this.width = 1000;
      this.height = 640;
      this.zoom = 1;
      this.pan = { x: 0, y: 0 };
      this.alpha = 0;
      this.raf = 0;
      this.settings = this.loadSettings();
      this.layers = null;
      this.controls = {};
      this.bindCanvasEvents();
    }

    bindControls(controls) {
      this.controls = controls || {};
      const rangeControls = [
        ["centerForce", "centerForce", (value) => Number(value) / 1000],
        ["repelForce", "repelForce", Number],
        ["linkForce", "linkForce", (value) => Number(value) / 1000],
        ["linkDistance", "linkDistance", Number],
        ["nodeSize", "nodeSize", Number],
        ["linkThickness", "linkThickness", Number],
        ["labelThreshold", "labelThreshold", Number]
      ];

      for (const [key, setting, parse] of rangeControls) {
        const input = this.controls[key];
        if (!input) {
          continue;
        }
        input.addEventListener("input", () => {
          this.updateSettings({ [setting]: parse(input.value) });
        });
      }

      if (this.controls.animate) {
        this.controls.animate.addEventListener("change", () => {
          this.updateSettings({ animate: this.controls.animate.checked });
        });
      }
      if (this.controls.showArrows) {
        this.controls.showArrows.addEventListener("change", () => {
          this.updateSettings({ showArrows: this.controls.showArrows.checked });
        });
      }
      if (this.controls.resetView) {
        this.controls.resetView.addEventListener("click", () => this.resetView());
      }
      if (this.controls.resetLayout) {
        this.controls.resetLayout.addEventListener("click", () => this.resetLayout());
      }
      if (this.controls.pause) {
        this.controls.pause.addEventListener("click", () => {
          this.updateSettings({ animate: !this.settings.animate });
        });
      }
      this.syncControls();
    }

    setData(payload, options = {}) {
      this.payload = payload;
      this.layoutKey = options.layoutKey || "default";
      this.layoutStorageKey = `paper-galaxy:layout:${this.layoutKey}`;
      this.documentsById = new Map(
        (payload.documents || []).map((document) => [document.document_id, document])
      );
      this.buildModel(payload.points || []);
      this.createElements();
      this.applyFilter();
      this.resetView();
      this.reheat(0.75);
      this.render();
      this.reportVisibleCount();
    }

    clear() {
      this.stop();
      this.nodes = [];
      this.links = [];
      this.nodeById = new Map();
      this.adjacency = new Map();
      this.pointCount = 0;
      this.layers = null;
      this.svg.replaceChildren();
      this.reportVisibleCount();
    }

    setFilter(filter) {
      this.filter = filter.trim().toLowerCase();
      this.applyFilter();
      this.reheat(0.25);
      this.render();
      this.reportVisibleCount();
    }

    setSelected(documentId) {
      this.selectedId = documentId;
      this.render();
    }

    isPinned(documentId) {
      const node = this.nodeById.get(documentId);
      return Boolean(node && node.fixed);
    }

    togglePin(documentId, pinned) {
      const node = this.nodeById.get(documentId);
      if (!node) {
        return false;
      }
      const nextPinned = pinned === undefined ? !node.fixed : Boolean(pinned);
      node.fixed = nextPinned;
      node.fx = nextPinned ? node.x : null;
      node.fy = nextPinned ? node.y : null;
      if (!nextPinned) {
        node.vx = 0;
        node.vy = 0;
      }
      this.saveLayout();
      this.reheat(0.35);
      this.render();
      if (this.options.onPinChange) {
        this.options.onPinChange(node.id, node.fixed);
      }
      return nextPinned;
    }

    resetView() {
      this.resize();
      this.zoom = 1;
      this.pan = { x: 0, y: 0 };
      this.reheat(0.2);
      this.render();
    }

    resetLayout() {
      if (this.layoutStorageKey) {
        localStorage.removeItem(this.layoutStorageKey);
      }
      for (const node of this.nodes) {
        const base = this.initialPosition(node.point);
        node.x = base.x;
        node.y = base.y;
        node.vx = 0;
        node.vy = 0;
        node.fixed = false;
        node.fx = null;
        node.fy = null;
      }
      this.reheat(0.85);
      this.render();
      if (this.options.onLayoutReset) {
        this.options.onLayoutReset();
      }
    }

    updateSettings(patch, persist = true) {
      this.settings = { ...this.settings, ...patch };
      if (persist) {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(this.settings));
      }
      this.syncControls();
      this.reheat(0.45);
      this.render();
      if (this.settings.animate) {
        this.start();
      } else {
        this.stop();
      }
    }

    bindCanvasEvents() {
      this.svg.addEventListener("pointerdown", (event) => this.onCanvasPointerDown(event));
      this.svg.addEventListener("pointermove", (event) => this.onPointerMove(event));
      this.svg.addEventListener("pointerup", (event) => this.onPointerUp(event));
      this.svg.addEventListener("pointercancel", (event) => this.onPointerUp(event));
      this.svg.addEventListener("pointerleave", () => {
        if (!this.dragging && !this.panning) {
          this.pointerWorld = null;
          this.setHovered(null);
        }
      });
      this.svg.addEventListener(
        "wheel",
        (event) => {
          this.onWheel(event);
        },
        { passive: false }
      );
      window.addEventListener("resize", () => {
        this.resize();
        this.render();
      });
    }

    buildModel(points) {
      const saved = this.loadLayout();
      this.nodes = [];
      this.links = [];
      this.nodeById = new Map();
      this.adjacency = new Map();
      this.pointCount = points.length;

      for (const point of points) {
        const id = point.document_id;
        const document = this.documentsById.get(id);
        const base = this.initialPosition(point);
        const savedNode = saved[id];
        const node = {
          id,
          point,
          document,
          x: savedNode ? Number(savedNode.x) : base.x,
          y: savedNode ? Number(savedNode.y) : base.y,
          vx: 0,
          vy: 0,
          clusterId: Number(point.cluster_id) || 0,
          radius: this.settings.nodeSize,
          fixed: Boolean(savedNode && savedNode.fixed),
          fx: savedNode && savedNode.fixed ? Number(savedNode.x) : null,
          fy: savedNode && savedNode.fixed ? Number(savedNode.y) : null,
          visible: true,
          searchText: this.searchText(point, document),
          element: null,
          circle: null,
          pinMarker: null,
          label: null
        };
        this.nodes.push(node);
        this.nodeById.set(id, node);
        this.adjacency.set(id, new Set());
      }

      const seenLinks = new Set();
      for (const point of points) {
        const source = this.nodeById.get(point.document_id);
        if (!source) {
          continue;
        }
        for (const neighbor of point.nearest_neighbors || []) {
          const target = this.nodeById.get(neighbor.document_id);
          if (!target || source.id === target.id) {
            continue;
          }
          const key = [source.id, target.id].sort().join(":");
          if (seenLinks.has(key)) {
            continue;
          }
          seenLinks.add(key);
          const score = Number(neighbor.score) || 0;
          const link = {
            source,
            target,
            score,
            strength: 0.5 + Math.max(0, Math.min(1, score)),
            element: null
          };
          this.links.push(link);
          this.adjacency.get(source.id).add(target.id);
          this.adjacency.get(target.id).add(source.id);
        }
      }
    }

    createElements() {
      this.svg.replaceChildren();
      this.resize();

      const defs = svgEl("defs");
      const marker = svgEl("marker", {
        id: "graph-arrow",
        viewBox: "0 0 10 10",
        refX: "9",
        refY: "5",
        markerWidth: "5",
        markerHeight: "5",
        orient: "auto-start-reverse"
      });
      marker.append(
        svgEl("path", {
          d: "M 0 0 L 10 5 L 0 10 z",
          class: "graph-arrow-head"
        })
      );
      defs.append(marker);

      const background = svgEl("rect", {
        class: "graph-background",
        x: "0",
        y: "0",
        width: this.width,
        height: this.height
      });
      const linkLayer = svgEl("g", { class: "graph-link-layer" });
      const nodeLayer = svgEl("g", { class: "graph-node-layer" });
      const labelLayer = svgEl("g", { class: "graph-label-layer" });
      this.svg.append(defs, background, linkLayer, nodeLayer, labelLayer);
      this.layers = { background, linkLayer, nodeLayer, labelLayer };

      for (const link of this.links) {
        const line = svgEl("line", {
          class: "graph-link",
          "data-source": link.source.id,
          "data-target": link.target.id
        });
        link.element = line;
        linkLayer.append(line);
      }

      for (const node of this.nodes) {
        const group = svgEl("g", {
          class: "graph-node",
          tabindex: "0",
          role: "button",
          "aria-label": `Select ${node.document ? node.document.title : node.id}`
        });
        const circle = svgEl("circle", {
          class: "graph-node-core",
          fill: clusterColor(node.clusterId)
        });
        const pinMarker = svgEl("circle", {
          class: "graph-node-pin",
          r: "3.2"
        });
        const title = svgEl("title");
        title.textContent = node.document
          ? `${node.document.title}\n${node.point.cluster_label}`
          : node.id;
        group.append(circle, pinMarker, title);
        group.addEventListener("pointerenter", () => this.setHovered(node.id));
        group.addEventListener("pointerleave", () => {
          if (!this.dragging) {
            this.setHovered(null);
          }
        });
        group.addEventListener("pointerdown", (event) => this.onNodePointerDown(event, node));
        group.addEventListener("dblclick", (event) => {
          event.preventDefault();
          event.stopPropagation();
          this.togglePin(node.id, false);
        });
        group.addEventListener("keydown", (event) => this.onNodeKeydown(event, node));

        const label = svgEl("text", {
          class: "graph-label"
        });
        label.textContent = node.document ? node.document.title : node.id;

        node.element = group;
        node.circle = circle;
        node.pinMarker = pinMarker;
        node.label = label;
        nodeLayer.append(group);
        labelLayer.append(label);
      }
    }

    applyFilter() {
      for (const node of this.nodes) {
        node.visible = !this.filter || node.searchText.includes(this.filter);
      }
    }

    reportVisibleCount() {
      if (!this.options.onVisibleCountChange) {
        return;
      }
      const visible = this.nodes.filter((node) => node.visible).length;
      this.options.onVisibleCountChange(visible, this.nodes.length);
    }

    setHovered(documentId) {
      if (this.hoveredId === documentId) {
        return;
      }
      this.hoveredId = documentId;
      this.reheat(documentId ? 0.28 : 0.12);
      this.render();
    }

    onNodeKeydown(event, node) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        this.selectNode(node.id);
      }
      if (event.key.toLowerCase() === "u") {
        event.preventDefault();
        this.togglePin(node.id, false);
      }
    }

    onNodePointerDown(event, node) {
      event.preventDefault();
      event.stopPropagation();
      node.element.setPointerCapture(event.pointerId);
      const world = this.clientToWorld(event.clientX, event.clientY);
      this.dragging = {
        node,
        pointerId: event.pointerId,
        startX: world.x,
        startY: world.y,
        wasFixed: node.fixed,
        wasFx: node.fx,
        wasFy: node.fy,
        moved: false
      };
      this.reheat(0.24);
      this.render();
    }

    onCanvasPointerDown(event) {
      if (event.target.closest && event.target.closest(".graph-node")) {
        return;
      }
      event.preventDefault();
      this.svg.setPointerCapture(event.pointerId);
      this.panning = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        panX: this.pan.x,
        panY: this.pan.y
      };
      this.svg.classList.add("is-panning");
    }

    onPointerMove(event) {
      const world = this.clientToWorld(event.clientX, event.clientY);
      this.pointerWorld = world;
      if (this.dragging && this.dragging.pointerId === event.pointerId) {
        const node = this.dragging.node;
        const dx = world.x - this.dragging.startX;
        const dy = world.y - this.dragging.startY;
        if (Math.hypot(dx, dy) > 3) {
          this.dragging.moved = true;
        }
        if (!this.dragging.moved) {
          return;
        }
        node.fixed = true;
        node.x = world.x;
        node.y = world.y;
        node.fx = world.x;
        node.fy = world.y;
        node.vx = 0;
        node.vy = 0;
        this.reheat(0.7);
        this.render();
        return;
      }
      if (this.panning && this.panning.pointerId === event.pointerId) {
        this.pan.x = this.panning.panX + event.clientX - this.panning.startX;
        this.pan.y = this.panning.panY + event.clientY - this.panning.startY;
        this.render();
        return;
      }
      if (this.settings.animate && this.nodes.length) {
        this.reheat(0.08);
      }
    }

    onPointerUp(event) {
      if (this.dragging && this.dragging.pointerId === event.pointerId) {
        const { node, moved, wasFixed, wasFx, wasFy } = this.dragging;
        this.dragging = null;
        if (moved) {
          node.fixed = true;
          node.fx = node.x;
          node.fy = node.y;
          this.saveLayout();
          if (this.options.onPinChange) {
            this.options.onPinChange(node.id, node.fixed);
          }
        } else {
          node.fixed = wasFixed;
          node.fx = wasFx;
          node.fy = wasFy;
          this.selectNode(node.id);
        }
        this.render();
      }
      if (this.panning && this.panning.pointerId === event.pointerId) {
        this.panning = null;
        this.svg.classList.remove("is-panning");
      }
    }

    onWheel(event) {
      event.preventDefault();
      const before = this.clientToWorld(event.clientX, event.clientY);
      const rect = this.svg.getBoundingClientRect();
      const screenX = event.clientX - rect.left;
      const screenY = event.clientY - rect.top;
      const delta = Math.sign(event.deltaY) * Math.min(0.22, Math.abs(event.deltaY) / 600);
      const nextZoom = clamp(this.zoom * (1 - delta), 0.25, 4);
      this.zoom = nextZoom;
      this.pan.x = screenX - this.width / 2 - before.x * this.zoom;
      this.pan.y = screenY - this.height / 2 - before.y * this.zoom;
      this.reheat(0.12);
      this.render();
    }

    selectNode(documentId) {
      if (this.options.onSelect) {
        this.options.onSelect(documentId);
      }
    }

    start() {
      if (!this.settings.animate || this.raf || !this.nodes.length) {
        return;
      }
      this.raf = requestAnimationFrame(() => this.animationLoop());
    }

    stop() {
      if (this.raf) {
        cancelAnimationFrame(this.raf);
        this.raf = 0;
      }
    }

    reheat(value) {
      this.alpha = Math.max(this.alpha, value);
      if (this.settings.animate) {
        this.start();
      }
    }

    animationLoop() {
      this.raf = 0;
      if (!this.settings.animate) {
        return;
      }
      this.tick();
      this.render();
      if (this.alpha > 0.004) {
        this.raf = requestAnimationFrame(() => this.animationLoop());
      }
    }

    tick() {
      const heat = this.alpha;
      if (heat <= 0) {
        return;
      }
      const centerForce = this.settings.centerForce * heat;
      for (const node of this.nodes) {
        if (node.fixed) {
          node.x = node.fx;
          node.y = node.fy;
          node.vx = 0;
          node.vy = 0;
          continue;
        }
        node.vx += -node.x * centerForce;
        node.vy += -node.y * centerForce;
      }

      for (const link of this.links) {
        if (!link.source.visible || !link.target.visible) {
          continue;
        }
        const dx = link.target.x - link.source.x;
        const dy = link.target.y - link.source.y;
        const distance = Math.max(1, Math.hypot(dx, dy));
        const pull =
          (distance - this.settings.linkDistance) *
          this.settings.linkForce *
          link.strength *
          heat;
        const fx = (dx / distance) * pull;
        const fy = (dy / distance) * pull;
        if (!link.source.fixed) {
          link.source.vx += fx;
          link.source.vy += fy;
        }
        if (!link.target.fixed) {
          link.target.vx -= fx;
          link.target.vy -= fy;
        }
      }

      this.applyRepulsion(heat);
      this.applyPointerForce(heat);

      for (const node of this.nodes) {
        if (node.fixed) {
          continue;
        }
        node.vx *= 0.84;
        node.vy *= 0.84;
        node.x += node.vx;
        node.y += node.vy;
      }
      this.alpha *= 0.985;
    }

    applyRepulsion(heat) {
      const count = this.nodes.length;
      if (count < 2) {
        return;
      }
      const sampleStride = count > 300 ? Math.ceil(count / 220) : 1;
      const sampleLimit = count > 300 ? 44 : count;
      for (let i = 0; i < count; i += 1) {
        const a = this.nodes[i];
        if (!a.visible) {
          continue;
        }
        let sampled = 0;
        for (let j = i + 1; j < count && sampled < sampleLimit; j += sampleStride) {
          const b = this.nodes[j];
          if (!b.visible) {
            continue;
          }
          sampled += 1;
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          let distanceSq = dx * dx + dy * dy;
          if (distanceSq < 0.01) {
            dx = (i % 3) - 1 || 0.5;
            dy = (j % 3) - 1 || -0.5;
            distanceSq = dx * dx + dy * dy;
          }
          const distance = Math.sqrt(distanceSq);
          const minDistance = this.settings.nodeSize * 2.8 + 16;
          const collision = distance < minDistance ? (minDistance - distance) * 0.018 : 0;
          const repel = (this.settings.repelForce / Math.max(180, distanceSq) + collision) * heat;
          const fx = (dx / distance) * repel;
          const fy = (dy / distance) * repel;
          if (!a.fixed) {
            a.vx += fx;
            a.vy += fy;
          }
          if (!b.fixed) {
            b.vx -= fx;
            b.vy -= fy;
          }
        }
      }
    }

    applyPointerForce(heat) {
      if (!this.pointerWorld || this.dragging || this.panning) {
        return;
      }
      const radius = 110 / Math.max(0.8, this.zoom);
      for (const node of this.nodes) {
        if (!node.visible || node.fixed) {
          continue;
        }
        const dx = node.x - this.pointerWorld.x;
        const dy = node.y - this.pointerWorld.y;
        const distance = Math.hypot(dx, dy);
        if (distance <= 0 || distance > radius) {
          continue;
        }
        const push = ((radius - distance) / radius) * 0.16 * heat;
        node.vx += (dx / distance) * push;
        node.vy += (dy / distance) * push;
      }
    }

    render() {
      if (!this.layers) {
        return;
      }
      this.resize();
      this.layers.background.setAttribute("width", String(this.width));
      this.layers.background.setAttribute("height", String(this.height));
      const focusSet = this.focusSet();
      const hasHoverFocus = Boolean(this.hoveredId && focusSet.size);
      const selectedNeighbors = this.selectedId
        ? this.adjacency.get(this.selectedId) || new Set()
        : new Set();

      for (const link of this.links) {
        if (!link.element) {
          continue;
        }
        const source = this.worldToScreen(link.source.x, link.source.y);
        const target = this.worldToScreen(link.target.x, link.target.y);
        link.element.setAttribute("x1", source.x);
        link.element.setAttribute("y1", source.y);
        link.element.setAttribute("x2", target.x);
        link.element.setAttribute("y2", target.y);
        link.element.style.strokeWidth = String(this.settings.linkThickness);
        if (this.settings.showArrows) {
          link.element.setAttribute("marker-end", "url(#graph-arrow)");
        } else {
          link.element.removeAttribute("marker-end");
        }
        const hidden = !link.source.visible || !link.target.visible;
        const hoverRelated =
          hasHoverFocus && focusSet.has(link.source.id) && focusSet.has(link.target.id);
        const selectedRelated =
          this.selectedId &&
          (link.source.id === this.selectedId ||
            link.target.id === this.selectedId ||
            (selectedNeighbors.has(link.source.id) && selectedNeighbors.has(link.target.id)));
        link.element.className.baseVal = classNames("graph-link", {
          "is-hidden": hidden,
          "is-faded": hasHoverFocus && !hoverRelated,
          "is-focused": hoverRelated,
          "is-selected": selectedRelated
        });
      }

      for (const node of this.nodes) {
        if (!node.element || !node.circle || !node.pinMarker || !node.label) {
          continue;
        }
        const screen = this.worldToScreen(node.x, node.y);
        node.element.setAttribute("transform", `translate(${screen.x} ${screen.y})`);
        node.label.setAttribute("x", String(screen.x + 11));
        node.label.setAttribute("y", String(screen.y - 11));
        const isHovered = node.id === this.hoveredId;
        const isSelected = node.id === this.selectedId;
        const isNeighbor = focusSet.has(node.id) && !isHovered;
        const radius =
          this.settings.nodeSize * (isSelected ? 1.38 : isHovered ? 1.48 : isNeighbor ? 1.16 : 1);
        node.circle.setAttribute("r", String(radius));
        node.pinMarker.setAttribute("cx", String(radius * 0.72));
        node.pinMarker.setAttribute("cy", String(-radius * 0.72));
        node.pinMarker.classList.toggle("is-visible", node.fixed);

        const showLabel =
          node.visible &&
          (isHovered ||
            isSelected ||
            isNeighbor ||
            (!hasHoverFocus && this.zoom >= this.settings.labelThreshold && this.nodes.length <= 120));
        node.label.className.baseVal = classNames("graph-label", {
          "is-visible": showLabel,
          "is-selected": isSelected
        });
        node.element.className.baseVal = classNames("graph-node", {
          "is-hidden": !node.visible,
          "is-faded": hasHoverFocus && !focusSet.has(node.id),
          "is-focused": isHovered || isNeighbor,
          "is-selected": isSelected,
          "is-pinned": node.fixed
        });
      }
    }

    focusSet() {
      const focusId = this.hoveredId || null;
      if (!focusId) {
        return new Set();
      }
      const focus = new Set([focusId]);
      const neighbors = this.adjacency.get(focusId);
      if (neighbors) {
        for (const id of neighbors) {
          focus.add(id);
        }
      }
      return focus;
    }

    resize() {
      const rect = this.svg.getBoundingClientRect();
      this.width = Math.max(320, rect.width || this.svg.clientWidth || 1000);
      this.height = Math.max(320, rect.height || this.svg.clientHeight || 640);
      this.svg.setAttribute("viewBox", `0 0 ${this.width} ${this.height}`);
      this.svg.setAttribute("preserveAspectRatio", "none");
    }

    initialPosition(point) {
      const spreadX = this.pointCount > 80 ? 520 : 390;
      const spreadY = this.pointCount > 80 ? 360 : 270;
      const x = finiteNumber(point.x) ? point.x : 0;
      const y = finiteNumber(point.y) ? point.y : 0;
      return {
        x: x * spreadX,
        y: -y * spreadY
      };
    }

    clientToWorld(clientX, clientY) {
      const rect = this.svg.getBoundingClientRect();
      const screenX = clientX - rect.left;
      const screenY = clientY - rect.top;
      return {
        x: (screenX - this.width / 2 - this.pan.x) / this.zoom,
        y: (screenY - this.height / 2 - this.pan.y) / this.zoom
      };
    }

    worldToScreen(x, y) {
      return {
        x: this.width / 2 + this.pan.x + x * this.zoom,
        y: this.height / 2 + this.pan.y + y * this.zoom
      };
    }

    loadLayout() {
      if (!this.layoutStorageKey) {
        return {};
      }
      try {
        const parsed = JSON.parse(localStorage.getItem(this.layoutStorageKey) || "{}");
        return parsed && parsed.nodes ? parsed.nodes : {};
      } catch {
        return {};
      }
    }

    saveLayout() {
      if (!this.layoutStorageKey) {
        return;
      }
      const nodes = {};
      for (const node of this.nodes) {
        if (node.fixed) {
          nodes[node.id] = {
            x: Number(node.x.toFixed(3)),
            y: Number(node.y.toFixed(3)),
            fixed: true
          };
        }
      }
      localStorage.setItem(
        this.layoutStorageKey,
        JSON.stringify({
          version: 1,
          nodes
        })
      );
    }

    loadSettings() {
      try {
        return {
          ...DEFAULT_SETTINGS,
          ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}")
        };
      } catch {
        return { ...DEFAULT_SETTINGS };
      }
    }

    syncControls() {
      if (this.controls.centerForce) {
        this.controls.centerForce.value = String(Math.round(this.settings.centerForce * 1000));
      }
      if (this.controls.repelForce) {
        this.controls.repelForce.value = String(this.settings.repelForce);
      }
      if (this.controls.linkForce) {
        this.controls.linkForce.value = String(Math.round(this.settings.linkForce * 1000));
      }
      if (this.controls.linkDistance) {
        this.controls.linkDistance.value = String(this.settings.linkDistance);
      }
      if (this.controls.nodeSize) {
        this.controls.nodeSize.value = String(this.settings.nodeSize);
      }
      if (this.controls.linkThickness) {
        this.controls.linkThickness.value = String(this.settings.linkThickness);
      }
      if (this.controls.labelThreshold) {
        this.controls.labelThreshold.value = String(this.settings.labelThreshold);
      }
      if (this.controls.animate) {
        this.controls.animate.checked = this.settings.animate;
      }
      if (this.controls.showArrows) {
        this.controls.showArrows.checked = this.settings.showArrows;
      }
      if (this.controls.pause) {
        this.controls.pause.setAttribute(
          "aria-label",
          this.settings.animate ? "Pause animation" : "Resume animation"
        );
        const icon = this.controls.pause.querySelector("span");
        if (icon) {
          icon.textContent = this.settings.animate ? "Ⅱ" : "▶";
        }
      }
    }

    searchText(point, document) {
      return [
        document ? document.title : "",
        document ? document.relative_path : "",
        point.cluster_label || "",
        ...(point.top_terms || [])
      ]
        .join(" ")
        .toLowerCase();
    }
  }

  function svgEl(tagName, attributes = {}) {
    const element = document.createElementNS(SVG_NS, tagName);
    for (const [key, value] of Object.entries(attributes)) {
      element.setAttribute(key, String(value));
    }
    return element;
  }

  function clusterColor(clusterId) {
    return CLUSTER_COLORS[Math.abs(Number(clusterId) || 0) % CLUSTER_COLORS.length];
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function finiteNumber(value) {
    return Number.isFinite(Number(value));
  }

  function classNames(base, flags) {
    const classes = [base];
    for (const [name, enabled] of Object.entries(flags)) {
      if (enabled) {
        classes.push(name);
      }
    }
    return classes.join(" ");
  }

  window.PaperGalaxyGraph = {
    ForceGraph,
    clusterColor
  };
})();
