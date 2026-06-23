# 路线图

[English](ROADMAP.md) | 简体中文

Paper Galaxy 应该增量成长。每个阶段都必须让仓库保持可运行、已测试的状态。英文版仍是规范版本；本文件提供简体中文说明。

## Phase 0：脚手架（完成）

目标：创建仓库基础。

交付物：Python package 布局、最小 CLI、测试、文档、CI、隐私立场和 agent 指令。

完成定义：package 可以 import，`paper-galaxy doctor` 可以运行，`init` 能安全创建项目元数据，测试通过，lint 通过，typecheck 通过或诚实记录问题。

非目标：文档抽取、OCR、解析、向量、地图、数据库、服务器、前端、Zotero 集成、打包、云同步和 LLM 聊天。

## Phase 1：静态 CLI MVP（已实现）

目标：从文件夹生成静态本地 galaxy map。

交付物：文件夹扫描、简单 `.txt`/`.md`/`.tex` 和可选基础 PDF 文本抽取、TF-IDF 向量、2D 降维、k-means 聚类、最近邻摘要、top-term 主题簇标签、可选 JSON sidecar，以及静态离线 `galaxy.html`。

完成定义：`examples/tiny_corpus` 可以生成可读的静态 HTML 地图，包含悬停标签、点击检查、主题簇图例、top terms、跳过文件摘要，以及基于 TF-IDF 余弦相似度的邻居数据。

## Phase 2：本地数据库（已实现）

目标：持久化文档记录并支持增量本地扫描。

交付物：SQLite schema、文档 hash、稳定文档 ID、文档和文本块表、扫描运行记录、missing 文件追踪、增量索引，以及基础本地 FTS5 全文搜索。

完成定义：重复运行索引会跳过未变化文档，更新变化文件并保留基于路径的文档 ID，把删除的文件标记为 `missing`，把当前无法索引的现存文件标记为 `unindexed`，写出确定性文本块，记录扫描摘要，并返回有用的本地搜索结果。

## Phase 3：交互式本地网页应用（已实现）

目标：提供交互式本地地图和文档 inspector。

交付物：本地后端、类似 Obsidian 的动态图谱 UI、文档 inspector、主题簇 panel、图谱控制和搜索视图。

完成定义：用户可以打开本地应用，浏览地图，检查文档，并在不上传数据的情况下查看附近文档。

非目标：OCR、dense embeddings、Zotero 集成、桌面打包、云托管、账号系统、遥测、LLM 聊天，以及 React/Node tooling，除非后续任务明确要求。

## Phase 4：更好的抽取质量（已实现）

目标：提升语料 ingestion 质量。

交付物：更好的 PDF 抽取、LaTeX 结构抽取、Markdown frontmatter 和 backlink 解析、可选本地 OCR、扫描 PDF 检测、抽取 warning，以及持久化抽取质量报告。

完成定义：在小型 fixture 语料上可度量抽取质量，失败可见而不是静默。图谱标签默认只显示焦点标签，避免小语料中标签堆叠。

## Phase 5：语义 embeddings（已实现）

目标：在保留可检查 baseline 的同时增加 dense semantic similarity。

交付物：可选 Sentence Transformer 文档 embeddings、文本块 embeddings、本地 SQLite 向量存储、语义搜索、vector stats，以及 dense embeddings 与 TF-IDF 的 hybrid similarity。

完成定义：语义模式是可选、本地、已文档化，并能与 TF-IDF baseline 对比。默认拒绝远程模型名以避免隐藏下载；用户必须提供本地模型路径，或显式传入 `--allow-model-download`。

## Phase 6：可解释性与标签（已实现）

目标：让主题簇和邻居关系可理解。

交付物：类似 c-TF-IDF 的生成标签、稳定主题簇 signature、代表文档、SQLite 中的手动主题簇重命名、`/api/map` 和 `/api/clusters` 中的主题簇元数据，以及基于共享词项和匹配文本块的“为什么相近？”解释。

完成定义：地图视图暴露距离背后的证据，用户可以修正主题簇名称，CLI 暴露主题簇和 pair explanations，并且测试覆盖标签、覆盖、API 路由、静态资源和 Phase 5 normalization 兼容性。

## Phase 7：专业化（已实现）

目标：让 Paper Galaxy 更稳定、更可扩展。

交付物：Python 打包元数据、构建检查、项目验证、稳定保存的 TF-IDF 地图运行、本地备份导入/导出、静态内置插件边界，以及安装/发布/备份 workflow 文档。

完成定义：用户可以构建 package、验证本地项目、持久化和检查地图快照、导出/导入项目状态、列出内置抽取器边界，并运行之前 Phase 0-6 的检查。

## 公开发布准备（已实现）

目标：让仓库足够安全、完整，可以公开发布。

交付物：公开发布检查、社区文件、issue templates、pull request template、静态 GitHub Pages 演示站、简体中文公开站页面、演示 build/check 脚本、Pages 部署 workflow 和发布 checklist。

完成定义：演示只由合成数据生成，静态资源没有外部运行时依赖，公开检查会在发现 secrets 或生成的本地数据时失败，并且现有 Phase 0-7 检查仍通过。

非目标：云运行时、托管后端、账号系统、文档上传、远程插件加载、React/Node 前端工具链或云同步。

## 公开发布 / v0.1.0（当前里程碑）

目标：在仓库公开后完成 public activation，并准备保守的 `v0.1.0` GitHub Release。

交付物：live-site checker、post-public Makefile targets、launch report、release workflow checks、launch notes、FAQ、故障排查、演示指南、反馈指南、triage labels 指南、公开 demo social metadata，以及更强的 public-readiness 检查。

完成定义：GitHub Pages 已验证，live demo URL 通过 live-site checker，source-only 和 site-dist public readiness 模式通过，CI 和 Pages workflow 通过，并且 release instructions 已准备好但不发布 PyPI。

非目标：未经明确批准创建 tag 或 GitHub Release、PyPI 发布、云运行时、托管后端、账号系统、遥测、analytics、文档上传、Zotero online sync、Zotero 写回或个人云库实现。

## Zotero Reading Graph（已实现）

目标：把用户本机 Zotero Desktop 库连接到 Paper Galaxy，并把已收集/已阅读论文变成个人阅读图谱。

交付物：只读 Zotero local API 连接器、Zotero data dir 检测、规范化 item/creator/tag/collection/attachment 表、元数据和本地 PDF 导入、metadata-only fallback 文档、阅读状态标签映射、保存的 `Zotero Reading Graph` 地图运行、本地 web API，以及带 status/tag/collection 过滤的本地应用 Zotero 图谱来源。

完成定义：用户可以运行 `paper-galaxy zotero detect`、`paper-galaxy zotero status`、`paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map` 和 `paper-galaxy serve --project-dir .`，在本地应用里检查导入后的 Zotero 库。本功能使用 local API，不写回 Zotero，不上传 Zotero 数据，也不会默认复制 PDF。

非目标：Zotero OAuth、Zotero online API sync、Zotero 写回、云同步、托管账号、遥测、强制 embeddings、LLM 聊天、React/Node 工具链或桌面打包。

## 未来个人云库（仅设计）

个人云库是未来 opt-in 设计，不是当前实现。分阶段设计从加密备份 vault 开始，然后是元数据同步，最后才考虑托管计算。本地优先使用必须在没有账号时仍然可用。

## 下一步建议里程碑

- L1：公开发布稳定化。重点处理安装摩擦、抽取质量、图谱可用性、文档缺口和隐私安全 bug 报告。
- Z1：真实 Zotero 库反馈。用本机真实 Zotero Desktop 库测试导入，改进附件路径边界情况，并为 bug 增加合成 fixture，避免提交任何私人 Zotero 数据。
- L2：用户反馈 triage。尽可能把早期反馈转成带合成 fixture 的小 issue。
- C1：加密备份 vault 设计审查。除非未来有单独明确实现请求，否则仍保持 design-only。

## Phase 8+：未来工作

未来阶段可以在明确要求时改进抽取、地图稳定性、导入/导出格式或桌面打包。Phase 8+ 仍在当前实现边界之外。
