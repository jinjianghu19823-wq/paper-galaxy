# 架构

[English](ARCHITECTURE.md) | 简体中文

英文版仍是规范版本；本文件提供简体中文架构说明。

Paper Galaxy 是本地优先的研究资料地图工具。核心思想是：先在本地抽取和清洗文档，再建立本地记录、向量、图结构、地图坐标和主题簇，最后通过静态 HTML 或本地浏览器应用展示。

```text
files -> extraction -> cleaning -> records -> vectors -> graph -> map -> clusters -> UI
```

## 模块

- `paper_galaxy.cli`：命令行入口。
- `paper_galaxy.config`：项目配置和 `.paper-galaxy/project.toml`。
- `paper_galaxy.extractors`：本地文件抽取，包括文本、Markdown、LaTeX、PDF 和可选图片 OCR。
- `paper_galaxy.pipeline`：扫描、抽取、清洗和聚合。
- `paper_galaxy.ml`：TF-IDF、降维、聚类和邻居计算。
- `paper_galaxy.export`：静态 HTML/JSON 导出。
- `paper_galaxy.storage`：SQLite schema、文档、文本块、索引状态、报告、向量、标签、地图运行和备份。
- `paper_galaxy.search`：本地 SQLite FTS5 搜索。
- `paper_galaxy.embeddings`：可选本地 dense embeddings。
- `paper_galaxy.labels`：本地主题簇标签和解释。
- `paper_galaxy.web`：FastAPI 本地后端和静态 vanilla JS 前端。
- `paper_galaxy.backup`：本地备份导出/导入。
- `paper_galaxy.plugins`：静态内置抽取边界。
- `paper_galaxy.zotero`：只读 Zotero 本地 API 连接、规范化、附件路径解析、导入器、SQLite 诊断和阅读图谱构建。

## Phase 1 静态导出

Phase 1 的输出是自包含的离线 HTML。它适合快速查看一个本地文件夹，不要求数据库或本地服务器。

静态图中的最近邻来自高维 TF-IDF 余弦相似度，不来自 2D 视觉距离。2D 坐标只用于展示。

## Phase 2 本地存储

Phase 2 引入 `.paper-galaxy/paper_galaxy.sqlite3`。SQLite 保存文档、文本块、扫描运行、FTS5 索引和文件状态。

索引使用内容 hash 跳过未变化文件，并用 corpus-relative path 维持稳定文档 ID。源文件消失时记录为 `missing`，现存但当前无法索引时记录为 `unindexed`。

## Phase 3 本地网页应用

`paper-galaxy serve --project-dir .` 启动本地只读浏览器应用。默认绑定到 `127.0.0.1`，读取本地 SQLite，并服务本地 HTML/CSS/JavaScript。

主要 API：

- `/api/health`：健康状态和数据库存在性。
- `/api/stats`：本地数据库统计。
- `/api/map`：active 文档、主题簇、邻居、解释元数据和初始坐标。
- `/api/search`：本地搜索。
- `/api/documents`：文档列表。
- `/api/documents/{id}`：文档元数据、文本块预览和邻居。
- `/api/clusters`：主题簇标签、代表文档和证据词项。
- `/api/map-runs`：保存的地图运行。
- `/api/zotero/status`：Zotero 导入状态、计数和最近一次导入运行。
- `/api/zotero/items`：已导入 Zotero 条目列表。
- `/api/zotero/item/{id}`：已导入 Zotero 条目详情。
- `/api/zotero/reading-map`：由已导入 Zotero 记录生成的阅读图谱。

前端是无构建步骤的 vanilla JavaScript。动态图谱的节点拖拽、布局偏好和图谱显示设置只保存在浏览器 `localStorage`，不会写回 SQLite。

## Zotero Reading Graph

Zotero 支持把 Zotero Desktop 只读导入 Paper Galaxy 本地项目：

```text
Zotero Desktop local API
  -> 规范化 Zotero 条目、集合、笔记和附件
  -> 解析本地附件路径但不复制 PDF
  -> 写入 Zotero 元数据和 Paper Galaxy 文档
  -> 建立文本块和 FTS 索引
  -> 保存 "Zotero Reading Graph" 地图运行
  -> 本地网页应用的 Zotero 图谱来源
```

主要路径是 Zotero 本地 API。直接读取 `zotero.sqlite` 只用于只读诊断和路径提示。Paper Galaxy 不写回 Zotero，不上传 Zotero 数据，也不会默认复制 PDF。

导入的 Zotero 条目会根据 source 和 Zotero key 生成稳定 ID。metadata-only 条目使用 `zotero://items/<key>` 作为文档路径。可读本地 PDF 的抽取文本和文本块会存入 Paper Galaxy SQLite，但原 PDF 文件保持在 Zotero 原位置。

## Phase 4 抽取质量

Phase 4 改进 PDF/Markdown/LaTeX 抽取，加入扫描 PDF 检测、抽取 warning 和可选本地 OCR。抽取报告存储在 SQLite 中，也可以写出本地 JSON sidecar，但不会包含完整抽取文本。

OCR 仍然是可选、本地、默认关闭的。

## Phase 5 语义 embeddings

Phase 5 增加可选本地 dense embeddings。向量存储在 SQLite 中，使用明确的本地模型身份和文本 hash 跳过未变化向量。

远程模型名默认被拒绝，避免隐藏下载。用户必须提供本地模型路径，或显式使用 `--allow-model-download`。

## Phase 6 可解释性与标签

Phase 6 从本地词项生成主题簇标签，保存稳定的 cluster signature，并允许用户在 SQLite 中手动重命名主题簇。

`paper-galaxy explain-pair` 和网页 inspector 使用共享 TF-IDF 词项及短文本块摘录解释两个文档为什么相近。不使用 LLM，也不打印完整抽取文本。

## Phase 7 专业化

Phase 7 增加项目验证、保存的 TF-IDF 地图运行、本地备份导入/导出、静态内置插件边界和 Python distribution 构建。

保存的地图运行是 SQLite 快照。它们可以作为网页应用中的初始图谱运行来源；用户在浏览器中拖拽固定节点仍然只影响 `localStorage`。

## 公开静态演示站

GitHub Pages 演示由 `scripts/build_demo_site.py` 从合成 tiny corpus 生成。输出是静态站点，不连接后端，不包含本地 SQLite 数据库，也不读取用户文档。

检查脚本：

```bash
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist
python scripts/public_readiness_check.py --strict
```

## 公开发布检查

`scripts/public_readiness_check.py --strict` 会检查仓库中是否混入常见 secrets、本地生成数据、SQLite 数据库、`.paper-galaxy/`、演示站远程运行时依赖等风险。

Post-public launch activation 增加了 `scripts/check_live_site.py` 用于验证已部署的 GitHub Pages 站点，并增加 `scripts/launch_report.py` 生成简洁的本地发布报告。这些脚本只检查静态页面和仓库状态，不增加 analytics、遥测、云运行时或托管后端代码。

## 未来云库边界

未来个人云库目前只是设计文档，不是当前 runtime。任何云功能都必须 opt-in，并且不能破坏本地优先路径。

当前本地应用没有账号、云同步、托管后端、遥测、文档上传、远程插件加载或 React/Node 构建链。

## 本地数据边界

本地项目状态保存在 `.paper-galaxy/`。该目录可能包含抽取文本、文本块、向量、标签、地图运行和备份相关元数据，因此不应提交到 git，也不应粘贴到公开 issue。
