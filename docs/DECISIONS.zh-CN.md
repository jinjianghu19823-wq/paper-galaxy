# 架构决策

[English](DECISIONS.md) | 简体中文

英文版仍是规范版本；本文件提供简体中文摘要。

## 核心原则

- Python-first：优先使用 Python 包、CLI 和轻量本地服务。
- 本地优先：默认不需要账号、云服务、遥测或上传。
- SQLite：用标准库 `sqlite3` 存储本地项目状态，避免过早引入 ORM。
- 可解释 baseline：先用 TF-IDF，再把 dense embeddings 做成可选增强。
- 重依赖可选：PDF、OCR、app、embeddings 等依赖通过 extras 安装。
- 前端静态：本地 app 使用 vanilla JS/CSS，不引入 React/Node 构建链。

## 重要 ADR 摘要

- ADR 0001-0008：项目采用 Python-first、本地优先、无遥测、无默认云依赖，并避免有风险的许可证陷阱。
- ADR 0009-0016：Phase 1/2 使用静态 HTML、TF-IDF/SVD/KMeans baseline、可选 PDF 支持、SQLite FTS5、稳定文档 ID、missing/unindexed 状态。
- ADR 0017-0023：Phase 3 使用可选 FastAPI/Uvicorn、本地静态 vanilla JS 前端、默认 localhost 绑定、只读网页应用、无依赖 force graph，以及浏览器 `localStorage` 中的手动布局。
- ADR 0024-0028：Phase 4 在 SQLite 中保存抽取质量报告；OCR 是可选、本地、默认关闭的；图谱标签默认 focus-only；继续保持静态 vanilla 前端。
- ADR 0029-0032：Phase 5 embeddings 是可选、本地的；不允许隐藏模型下载；向量以 SQLite float32 BLOB 存储；语义 workflow 仍由 CLI 驱动。
- ADR 0033-0036：Phase 6 标签来自透明本地词项，cluster signature 来自 active document IDs，手动标签是 SQLite display override，pair explanations 使用短本地证据。
- ADR 0037-0041：Phase 7 地图运行是 SQLite 快照，备份默认不包含源文档，插件是静态内置边界，验证报告避免全文。
- ADR 0042-0047：公开演示是静态且只用合成数据；GitHub Pages 部署构建产物；公开准备由本地脚本审计；云库只是设计；Pages 不提供 server-side backend；公开站支持英文和简体中文。
- ADR 0048-0053：Zotero Desktop local API 是主要连接方式；直接读取 `zotero.sqlite` 只作为只读 fallback；Paper Galaxy 永远不写回 Zotero；Zotero PDF 默认只引用不复制；Zotero 导入会生成 Paper Galaxy 文档和保存的阅读图谱；Zotero cloud sync 属于未来单独设计。

## 当前边界

当前实现不包含默认云运行时、托管后端、账号系统、遥测、文档上传、远程插件加载、React/Node 工具链、Zotero 写回、Zotero online sync、桌面打包或强制 LLM 标签。

未来如果更改这些边界，应先新增 ADR，并同步更新英文文档与本中文摘要。
