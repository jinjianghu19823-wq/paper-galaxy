(function () {
  const LANGUAGE_KEY = "paper-galaxy:language";
  const DEFAULT_LANGUAGE = "en";
  const CHINESE = "zh-CN";

  const STRINGS = {
    en: {
      "app.checking": "Checking local database...",
      "topbar.subtitle": "Local research map",
      "topbar.hint": "Local only - index from CLI",
      "metric.active": "Active",
      "metric.missing": "Missing",
      "metric.unindexed": "Unindexed",
      "metric.lastScan": "Last scan",
      "language.toggle": "中文",
      "language.name": "English",
      "language.aria": "Switch to Simplified Chinese",
      "theme.aria.light": "Switch to light theme",
      "theme.aria.dark": "Switch to dark theme",
      "search.title": "Search",
      "search.placeholder": "Search indexed text",
      "search.submit": "Search",
      "search.includeMissing": "Include missing",
      "search.failed": "Search failed: {message}",
      "search.noDatabase": "No Paper Galaxy database found.",
      "search.noResults": "No matching indexed documents found.",
      "clusters.title": "Clusters",
      "clusters.kicker": "groups",
      "clusters.filterPlaceholder": "Filter visible points",
      "clusters.empty": "No clusters yet.",
      "clusters.meta": "{source} - {count} docs",
      "clusters.generated": "generated",
      "clusters.manual": "manual",
      "clusters.fallback": "Cluster {id}",
      "source.title": "Source and filters",
      "source.graph": "Graph",
      "source.mapRun": "Map run",
      "layout.title": "Layout",
      "layout.summary": "force and labels",
      "layout.animate": "Animate graph",
      "layout.arrows": "Show arrows",
      "layout.center": "Center",
      "layout.repel": "Repel",
      "layout.linkForce": "Link force",
      "layout.linkDistance": "Link distance",
      "layout.nodeSize": "Node size",
      "layout.linkWidth": "Link width",
      "layout.labelMode": "Label mode",
      "layout.labelFocus": "Focus labels only",
      "layout.labelZoom": "Show at high zoom",
      "layout.labelAlways": "Always show labels",
      "layout.zoomLabels": "Zoom labels",
      "graph.title": "Document Graph",
      "graph.loading": "Loading active indexed documents...",
      "graph.aria": "Paper Galaxy document similarity graph",
      "graph.sourceDocuments": "Documents",
      "graph.sourceZotero": "Zotero",
      "graph.liveMap": "Live map",
      "graph.resetView": "Reset view",
      "graph.resetLayout": "Reset layout",
      "graph.pause": "Pause animation",
      "graph.resume": "Resume animation",
      "graph.clearSelection": "Clear selection",
      "graph.caption": "{visible} of {total} active documents - similarity links - {mode}",
      "graph.modeAnimated": "animated",
      "graph.modePaused": "paused",
      "graph.zeroActive": "0 active documents",
      "inspector.title": "Details",
      "inspector.default": "Select a document point or search result.",
      "inspector.loading": "Loading document...",
      "inspector.unavailable": "Document unavailable: {message}",
      "inspector.fileFallback": "file",
      "inspector.documentMeta": "{type} - {status} - {chars} chars",
      "inspector.topTerms": "Terms",
      "inspector.noTerms": "No terms available for this document.",
      "inspector.neighbors": "Related papers",
      "inspector.noNeighbors": "No related papers available.",
      "inspector.why": "Evidence",
      "inspector.whyPrompt": "Choose Evidence beside a related paper.",
      "inspector.whyButton": "Evidence",
      "inspector.chunks": "Chunks ({count})",
      "inspector.chunkMeta": "Chunk {index} - {chars} chars",
      "inspector.noChunk": "No chunk preview available.",
      "inspector.cluster": "Cluster",
      "inspector.generatedLabel": "Generated: {label}",
      "inspector.pinStatus": "Pinned manual position",
      "inspector.freeStatus": "Free force layout",
      "inspector.pin": "Pin",
      "inspector.unpin": "Unpin",
      "pair.loading": "Loading local explanation...",
      "pair.failed": "Explanation failed: {message}",
      "pair.lexicalScore": "Lexical score {score}",
      "pair.chunks": "Chunks {source} -> {target} - {score}",
      "cluster.rename": "Rename",
      "cluster.reset": "Reset",
      "cluster.save": "Save",
      "cluster.cancel": "Cancel",
      "cluster.labelAria": "Cluster label",
      "cluster.labelError": "Cluster label must be 1-120 characters.",
      "health.connected": "local database connected",
      "health.noDatabase": "no database",
      "health.none": "none",
      "missing.title": "No Paper Galaxy database found",
      "missing.body": "Run indexing from the command line before opening the graph.",
      "missing.command": "paper-galaxy index /path/to/corpus --project-dir /path/to/project",
      "map.noActiveTitle": "No active documents",
      "map.noActiveMessage": "No active indexed documents found.",
      "map.loadFailed": "Unable to load Paper Galaxy.",
      "zotero.title": "Zotero Reading Graph",
      "zotero.kicker": "local",
      "zotero.statusFilter": "Reading status",
      "zotero.all": "All",
      "zotero.read": "Read",
      "zotero.reading": "Reading",
      "zotero.toRead": "To read",
      "zotero.unknown": "Unknown",
      "zotero.tagPlaceholder": "Filter Zotero tag",
      "zotero.collectionPlaceholder": "Filter Zotero collection",
      "zotero.filters": "Zotero filters",
      "zotero.status": "{items} imported Zotero items - {attachments} attachments",
      "zotero.noImports": "No Zotero imports yet.",
      "zotero.instructions": "Open Zotero Desktop, run paper-galaxy zotero detect, then paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map.",
      "zotero.metadata": "Paper metadata",
      "zotero.key": "Zotero key",
      "zotero.creators": "Creators",
      "zotero.publication": "Publication",
      "zotero.itemType": "Item type",
      "zotero.year": "Year",
      "zotero.doi": "DOI",
      "zotero.url": "URL",
      "zotero.dateModified": "Date modified",
      "zotero.tags": "Tags",
      "zotero.collections": "Collections",
      "zotero.attachmentStatus": "Attachment status",
      "zotero.primaryAttachment": "Primary attachment",
      "zotero.pdfText": "PDF text",
      "zotero.open": "Open in Zotero"
    },
    "zh-CN": {
      "app.checking": "正在检查本地数据库...",
      "topbar.subtitle": "本地研究图谱",
      "topbar.hint": "仅本地 - 使用 CLI 建立索引",
      "metric.active": "有效",
      "metric.missing": "缺失",
      "metric.unindexed": "未索引",
      "metric.lastScan": "最近扫描",
      "language.toggle": "EN",
      "language.name": "简体中文",
      "language.aria": "切换到英文",
      "theme.aria.light": "切换到浅色主题",
      "theme.aria.dark": "切换到深色主题",
      "search.title": "搜索",
      "search.placeholder": "搜索已索引文本",
      "search.submit": "搜索",
      "search.includeMissing": "包含缺失文档",
      "search.failed": "搜索失败：{message}",
      "search.noDatabase": "未找到 Paper Galaxy 数据库。",
      "search.noResults": "没有找到匹配的已索引文档。",
      "clusters.title": "聚类",
      "clusters.kicker": "分组",
      "clusters.filterPlaceholder": "筛选可见点",
      "clusters.empty": "还没有聚类。",
      "clusters.meta": "{source} - {count} 篇文档",
      "clusters.generated": "自动生成",
      "clusters.manual": "手动",
      "clusters.fallback": "聚类 {id}",
      "source.title": "来源与筛选",
      "source.graph": "图谱",
      "source.mapRun": "地图运行",
      "layout.title": "布局",
      "layout.summary": "力导向和标签",
      "layout.animate": "动态图谱",
      "layout.arrows": "显示箭头",
      "layout.center": "居中",
      "layout.repel": "排斥",
      "layout.linkForce": "连线强度",
      "layout.linkDistance": "连线距离",
      "layout.nodeSize": "节点大小",
      "layout.linkWidth": "连线宽度",
      "layout.labelMode": "标签模式",
      "layout.labelFocus": "仅焦点标签",
      "layout.labelZoom": "高缩放时显示",
      "layout.labelAlways": "始终显示标签",
      "layout.zoomLabels": "标签缩放",
      "graph.title": "文档图谱",
      "graph.loading": "正在加载有效的已索引文档...",
      "graph.aria": "Paper Galaxy 文档相似度图谱",
      "graph.sourceDocuments": "文档",
      "graph.sourceZotero": "Zotero",
      "graph.liveMap": "实时图谱",
      "graph.resetView": "重置视图",
      "graph.resetLayout": "重置布局",
      "graph.pause": "暂停动画",
      "graph.resume": "继续动画",
      "graph.clearSelection": "清除选择",
      "graph.caption": "{visible}/{total} 篇有效文档 - 相似度连接 - {mode}",
      "graph.modeAnimated": "动态",
      "graph.modePaused": "暂停",
      "graph.zeroActive": "0 篇有效文档",
      "inspector.title": "详情",
      "inspector.default": "请选择一个文档点或搜索结果。",
      "inspector.loading": "正在加载文档...",
      "inspector.unavailable": "文档不可用：{message}",
      "inspector.fileFallback": "文件",
      "inspector.documentMeta": "{type} - {status} - {chars} 个字符",
      "inspector.topTerms": "术语",
      "inspector.noTerms": "此文档没有可用术语。",
      "inspector.neighbors": "相关论文",
      "inspector.noNeighbors": "没有可用的相关论文。",
      "inspector.why": "证据",
      "inspector.whyPrompt": "点击相关论文旁边的“证据”查看依据。",
      "inspector.whyButton": "证据",
      "inspector.chunks": "文本块（{count}）",
      "inspector.chunkMeta": "文本块 {index} - {chars} 个字符",
      "inspector.noChunk": "没有可用的文本块预览。",
      "inspector.cluster": "聚类",
      "inspector.generatedLabel": "自动生成：{label}",
      "inspector.pinStatus": "已固定手动位置",
      "inspector.freeStatus": "自由力导向布局",
      "inspector.pin": "固定",
      "inspector.unpin": "取消固定",
      "pair.loading": "正在加载本地解释...",
      "pair.failed": "解释失败：{message}",
      "pair.lexicalScore": "词汇分数 {score}",
      "pair.chunks": "文本块 {source} -> {target} - {score}",
      "cluster.rename": "重命名",
      "cluster.reset": "重置",
      "cluster.save": "保存",
      "cluster.cancel": "取消",
      "cluster.labelAria": "聚类标签",
      "cluster.labelError": "聚类标签长度必须为 1-120 个字符。",
      "health.connected": "本地数据库已连接",
      "health.noDatabase": "没有数据库",
      "health.none": "无",
      "missing.title": "未找到 Paper Galaxy 数据库",
      "missing.body": "打开图谱前，请先在命令行中建立索引。",
      "missing.command": "paper-galaxy index /path/to/corpus --project-dir /path/to/project",
      "map.noActiveTitle": "没有有效文档",
      "map.noActiveMessage": "没有找到有效的已索引文档。",
      "map.loadFailed": "无法加载 Paper Galaxy。",
      "zotero.title": "Zotero 阅读图谱",
      "zotero.kicker": "本地",
      "zotero.statusFilter": "阅读状态",
      "zotero.all": "全部",
      "zotero.read": "已读",
      "zotero.reading": "在读",
      "zotero.toRead": "待读",
      "zotero.unknown": "未知",
      "zotero.tagPlaceholder": "筛选 Zotero 标签",
      "zotero.collectionPlaceholder": "筛选 Zotero collection",
      "zotero.filters": "Zotero 筛选",
      "zotero.status": "{items} 条 Zotero 导入 - {attachments} 个附件",
      "zotero.noImports": "还没有 Zotero 导入。",
      "zotero.instructions": "打开 Zotero Desktop，运行 paper-galaxy zotero detect，然后运行 paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map。",
      "zotero.metadata": "论文信息",
      "zotero.key": "Zotero key",
      "zotero.creators": "作者",
      "zotero.publication": "出版信息",
      "zotero.itemType": "条目类型",
      "zotero.year": "年份",
      "zotero.doi": "DOI",
      "zotero.url": "URL",
      "zotero.dateModified": "修改时间",
      "zotero.tags": "标签",
      "zotero.collections": "文献集合",
      "zotero.attachmentStatus": "附件状态",
      "zotero.primaryAttachment": "主附件",
      "zotero.pdfText": "PDF 文本",
      "zotero.open": "在 Zotero 中打开"
    }
  };

  function createI18n() {
    let language = loadLanguage();
    return {
      get language() {
        return language;
      },
      t(key, values = {}) {
        return format(lookup(language, key), values);
      },
      toggle() {
        language = language === CHINESE ? DEFAULT_LANGUAGE : CHINESE;
        localStorage.setItem(LANGUAGE_KEY, language);
        return language;
      },
      apply(root = document) {
        document.documentElement.lang = language === CHINESE ? "zh-Hans" : "en";
        for (const element of root.querySelectorAll("[data-i18n]")) {
          element.textContent = format(lookup(language, element.dataset.i18n));
        }
        for (const element of root.querySelectorAll("[data-i18n-placeholder]")) {
          element.setAttribute(
            "placeholder",
            format(lookup(language, element.dataset.i18nPlaceholder))
          );
        }
        for (const element of root.querySelectorAll("[data-i18n-aria]")) {
          element.setAttribute(
            "aria-label",
            format(lookup(language, element.dataset.i18nAria))
          );
        }
      }
    };
  }

  function loadLanguage() {
    const saved = localStorage.getItem(LANGUAGE_KEY);
    if (saved === CHINESE || saved === DEFAULT_LANGUAGE) {
      return saved;
    }
    return navigator.language && navigator.language.toLowerCase().startsWith("zh")
      ? CHINESE
      : DEFAULT_LANGUAGE;
  }

  function lookup(language, key) {
    return (STRINGS[language] && STRINGS[language][key]) || STRINGS.en[key] || key;
  }

  function format(template, values = {}) {
    return String(template).replace(/\{([a-zA-Z0-9_]+)\}/g, (_, name) =>
      values[name] === undefined || values[name] === null ? "" : String(values[name])
    );
  }

  window.PaperGalaxyI18n = {
    createI18n
  };
})();
