# 阅读图谱

[English](READING_GRAPH.md) | 简体中文

Paper Galaxy 的 reading graph 是面向阅读记忆的本地保存地图。第一个真实来源是 Zotero：导入的 Zotero 论文、笔记、标签、集合和本地 PDF 文本会组成 `Zotero Reading Graph`。

## 图谱展示什么

- 每个已导入的 active Zotero 文档对应一个点。
- 主题簇标签来自本地 TF-IDF 证据。
- 最近邻链接由高维 TF-IDF 余弦相似度计算，而不是由 2D 地图距离计算。
- Inspector 展示 Zotero key、作者、年份、发表来源、标签、集合、附件状态和阅读状态。
- 本地应用提供 reading status、tag 和 collection 过滤。

即使 Zotero 条目没有可读 PDF，也可以进入图谱。题名、摘要、作者、笔记、标签和集合仍会组成 metadata-only 文档，并参与相似度计算。

## 阅读状态

第一版从标签中推断简单本地状态：

- `read`
- `reading`
- `to_read`
- `unknown`

导入命令支持可重复的 `--read-tag`、`--reading-tag` 和 `--to-read-tag`，这样用户可以把自己的 Zotero 标签习惯映射到 Paper Galaxy，而不需要写回 Zotero。

旧写法 `unclassified` 仍作为 `unknown` 的 deprecated alias 接受；新脚本建议使用 `unknown`。

## 保存地图运行

`paper-galaxy zotero import --build-reading-map` 会创建名为 `Zotero Reading Graph` 的保存地图运行。它复用普通 TF-IDF 地图运行的本地存储机制，并在元数据中标记来源是 Zotero。

也可以显式重建：

```bash
paper-galaxy zotero graph --project-dir . --name "Zotero Reading Graph"
```

本地网页应用会通过 `/api/zotero/reading-map` 渲染当前 Zotero 阅读图谱。保存运行则适合作为某一时刻的稳定快照。

## 隐私边界

阅读图谱是本地项目状态。它不会上传文档，不会写回 Zotero，也不会默认复制 PDF。导入后的全文仍可能敏感，所以 `.paper-galaxy/` 和 `*.sqlite3` 文件应保持私有并加入 gitignore。

## 当前限制

- 默认方法是 TF-IDF。Dense 或 hybrid reading graph 可以以后基于现有可选 embedding 层继续扩展。
- Zotero 导入是单向进入 Paper Galaxy，没有 Zotero 写回。
- 浏览器中的节点位置仍是 `localStorage` 里的 UI 状态，不写入 SQLite。
- 云同步是单独的未来设计，目前没有实现。
