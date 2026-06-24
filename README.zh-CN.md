# Paper Galaxy

[English](README.md) | 简体中文

Paper Galaxy 是一个本地优先的研究地图工具，用来把个人论文、笔记和资料库变成可浏览的文档地图、主题簇和概念邻域。

当前状态：public alpha / Zotero Reading Graph。仓库已经可以扫描本地示例语料，导出自包含的离线 `galaxy.html`，把文档和文本块索引到本地 SQLite，增量重建索引，用 SQLite FTS5 搜索本地文本，并启动一个本地英文/简体中文浏览器应用。这个本地应用包含类似 Obsidian 的动态文档图谱，可以浏览已经索引的语料，也可以查看从本地 Zotero Desktop 导入的 Zotero Reading Graph。

Paper Galaxy 还会记录抽取质量报告，改进 Markdown/LaTeX/PDF 解析，支持用户显式开启的本地图像 OCR，并可选地把本地文档/文本块的 dense embeddings 存进 SQLite，用于语义搜索和邻居对比。主题簇标签由可检查的本地词项生成，可以在 SQLite 中手动重命名；文档邻居解释可以展示共享词项和匹配文本块摘录。Zotero 集成使用 Zotero Desktop 的本地 API，默认不上传、不写回 Zotero，也不会默认复制或移动 Zotero PDF。

默认情况下，Paper Galaxy 是本地优先的：没有账号，没有遥测，没有自动上传，也没有云依赖。生成的 HTML 是本地离线文件。

> 注：英文文档仍是规范版本；本中文文档用于帮助中文用户快速理解和使用项目。

## 在线演示

公开静态演示站：

<https://jinjianghu19823-wq.github.io/paper-galaxy/>

演示只使用 `examples/tiny_corpus` 中的合成数据，不包含真实用户文档。站点提供英文和简体中文页面，并包含一个不依赖后端的静态图谱演示。

第一版公开 alpha 发布说明见 [docs/LAUNCH_NOTES.zh-CN.md](docs/LAUNCH_NOTES.zh-CN.md)。

## 为什么做 Paper Galaxy？

- 本地优先：文档、抽取文本、向量、标签和地图运行结果默认留在你的项目目录中。
- Zotero Reading Graph：把 Zotero Desktop 中的论文元数据、标签、集合、笔记和本地 PDF 文本导入 Paper Galaxy。
- 可视化图谱：用轻量浏览器 UI 浏览主题簇和最近邻关系。
- 可解释：查看 TF-IDF 词项和短文本块证据，理解为什么文档相近。
- 无需云服务：不需要账号、遥测、上传或托管后端。

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,ml,pdf,app]"
paper-galaxy doctor
python -m pytest
```

可选的本地图像 OCR 支持：

```bash
python -m pip install -e ".[dev,ml,pdf,app,ocr]"
```

OCR 仍然是本地、可选、默认关闭的。`ocr` extra 只安装 Python wrapper；实际运行 OCR 可能仍需要用户自己安装本地 Tesseract。

可选的本地语义 embedding 支持：

```bash
python -m pip install -e ".[dev,ml,pdf,app,embeddings]"
```

Embedding 命令保持本地优先且显式触发。`paper-galaxy embed` 默认拒绝远程模型名，避免隐藏下载；请传入本地 Sentence Transformer 模型路径，或显式使用 `--allow-model-download`。

如果你安装了 `uv`，也可以这样创建环境：

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev,ml,pdf,app]"
paper-galaxy doctor
python -m pytest
```

## 常用 CLI

```bash
paper-galaxy --help
paper-galaxy doctor
paper-galaxy init
paper-galaxy init /path/to/project
paper-galaxy scan examples/tiny_corpus --out galaxy.html --force
paper-galaxy scan examples/tiny_corpus --out galaxy.html --json-out galaxy.json --force
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40 --extraction-report-json extraction-report.json
paper-galaxy search "neural operator" --project-dir .
paper-galaxy db-stats --project-dir .
paper-galaxy embed --project-dir . --model /path/to/local/sentence-transformer-model
paper-galaxy semantic-search "operator learning for PDEs" --project-dir . --model /path/to/local/sentence-transformer-model
paper-galaxy compare-neighbors neural_operators/fourier_neural_operator.md --project-dir . --model /path/to/local/sentence-transformer-model
paper-galaxy vector-stats --project-dir .
paper-galaxy clusters --project-dir .
paper-galaxy explain-pair neural_operators/fourier_neural_operator.md neural_operators/deep_operator_network.txt --project-dir .
paper-galaxy rename-cluster CLUSTER_SIGNATURE "Neural Operators" --project-dir .
paper-galaxy reset-cluster-label CLUSTER_SIGNATURE --project-dir .
paper-galaxy validate-project --project-dir . --json-out validation.json
paper-galaxy build-map-run --project-dir . --name "Tiny corpus map"
paper-galaxy map-runs --project-dir .
paper-galaxy show-map-run MAP_RUN_ID --project-dir .
paper-galaxy export-map-run MAP_RUN_ID --project-dir . --out map-run.json
paper-galaxy delete-map-run MAP_RUN_ID --project-dir . --yes
paper-galaxy export-project --project-dir . --out paper-galaxy-backup.zip --yes
paper-galaxy import-project paper-galaxy-backup.zip --project-dir /path/to/restore --dry-run
paper-galaxy plugins
paper-galaxy zotero detect
paper-galaxy zotero status
paper-galaxy zotero doctor --project-dir .
paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map
paper-galaxy zotero graph --project-dir . --name "Zotero Reading Graph"
paper-galaxy serve --project-dir .
paper-galaxy extract-preview examples/tiny_corpus/neural_operators/fourier_neural_operator.md
```

`paper-galaxy init` 只创建 `.paper-galaxy/project.toml`，不会扫描文档，也不会复制语料文件。

## Zotero Reading Graph

Paper Galaxy 可以把本机 Zotero Desktop 库导入本地 Paper Galaxy SQLite 项目。主要连接方式是 Zotero Desktop 的本地 API：`http://localhost:23119/api/`。Paper Galaxy 对 Zotero 是只读的，不需要 Zotero Web API key。直接读取 `zotero.sqlite` 只作为只读诊断和路径提示，不作为主要导入路径。

典型流程：

```bash
paper-galaxy init .
paper-galaxy zotero detect
paper-galaxy zotero status
paper-galaxy zotero doctor --project-dir .
paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map
paper-galaxy serve --project-dir .
```

导入会创建稳定的 `doc_zotero_*` 文档 ID、Zotero 元数据表、可读本地 PDF 的文本块，以及名为 `Zotero Reading Graph` 的保存地图运行。如果 PDF 缺失、不支持或位于 Zotero data dir 之外，条目仍可作为 metadata-only 文档导入，包含题名、摘要、作者、标签、集合、笔记和 `zotero://items/<key>` 引用。

真实库过滤保持显式和保守。`--collection` 可以使用 Zotero collection key、精确名称或路径；名称大小写不敏感，但歧义匹配会报清楚的错误。阅读状态过滤支持 `all`、`read`、`reading`、`to_read` 和 `unknown`；`unclassified` 仍作为 `unknown` 的旧别名接受。`--pdf-policy metadata` 可用于快速 metadata-only 导入，`--pdf-policy skip-missing` 会跳过本应有本地 PDF 但无法读取的条目。

隐私边界：Zotero 连接器只使用本地 API，不写回 Zotero，不上传数据，也不会默认复制或移动 PDF。导入后的元数据和本地 PDF 抽取文本会进入 `.paper-galaxy/` 下的 Paper Galaxy 数据库，因此不要把该目录提交到 git。

更多说明：

- [docs/ZOTERO_INTEGRATION.zh-CN.md](docs/ZOTERO_INTEGRATION.zh-CN.md)
- [docs/ZOTERO_REAL_WORLD_TESTING.zh-CN.md](docs/ZOTERO_REAL_WORLD_TESTING.zh-CN.md)
- [docs/READING_GRAPH.zh-CN.md](docs/READING_GRAPH.zh-CN.md)

`paper-galaxy scan` 会递归扫描本地文件夹并写出静态 HTML 地图。当前支持 `.txt`、`.md`、`.markdown`、保守解析的 `.tex`、安装可选 `pypdf` 后的基础 PDF，以及显式传入 `--include-images` 时的图片文件。

最近邻由高维 TF-IDF 余弦相似度计算，而不是由 2D 地图上的视觉距离计算。2D 布局只是视图。

`paper-galaxy index` 会把抽取文本和确定性文本块持久化到本地 SQLite，默认位置是 `.paper-galaxy/paper_galaxy.sqlite3`。它用 SHA-256 跳过未变化文件，文件路径不变时保留文档 ID，并把消失的文件标记为 `missing`，而不是直接删除。

`paper-galaxy search` 使用本地 SQLite FTS5 搜索文档标题、相对路径和抽取文本。默认只返回 active 文档；`--include-missing` 可以包含源文件已经消失的文档。

## 本地网页应用

安装 app extra 后可以启动本地网页应用：

```bash
python -m pip install -e ".[dev,ml,pdf,app]"
paper-galaxy init .
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
paper-galaxy serve --project-dir .
```

`paper-galaxy serve` 默认绑定到 `127.0.0.1`。网页应用读取本地 SQLite 数据库，服务本地 HTML/CSS/JavaScript，没有 CDN 资源。它不会上传文档，不会收集遥测，也不会从浏览器 UI 里执行索引；索引仍然由 CLI 完成。

本地图谱支持轻量 force simulation、拖拽固定节点、缩放平移、悬停淡化无关节点，以及只显示焦点标签来减少重叠。手动节点位置和图谱显示设置只存储在浏览器 `localStorage`，不会写入 SQLite。

## Phase 7 工具

`paper-galaxy validate-project` 检查项目配置、SQLite schema、必需表、FTS 表、文档/向量/地图运行数量、悬空记录、地图运行一致性、可选依赖，以及过期的主题簇标签覆盖。

`paper-galaxy build-map-run` 会把当前 TF-IDF 地图的确定性快照保存进 SQLite。`map-runs`、`show-map-run`、`export-map-run` 和 `delete-map-run` 用于列出、检查、导出和删除这些快照。

`paper-galaxy export-project` 会写出 zip 备份包，包含 manifest、校验和、项目元数据，以及用户用 `--yes` 确认后包含的本地 SQLite 数据库。默认不包含源文档。`paper-galaxy import-project` 会验证备份包，并在没有 `--force` 时拒绝覆盖已有 `.paper-galaxy/` 目录。

`paper-galaxy plugins` 会列出内置的本地抽取插件边界。Phase 7 只有静态内置边界，没有远程插件加载。

## 构建与检查

```bash
python -m build
make validate-example
make check
make launch-check
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist
python scripts/public_readiness_check.py --strict
```

## 下一阶段

仓库现在是 public alpha，并包含本地 Zotero Reading Graph 第一版。下一阶段是公开发布和真实 Zotero 库反馈稳定化：收集安装反馈、Zotero 导入反馈、抽取质量反馈、图谱可用性问题，并用不泄露隐私的方式 triage 早期 issue。未来的个人云库仍然只是设计文档，尚未实现；见 [docs/CLOUD_LIBRARY_DESIGN.zh-CN.md](docs/CLOUD_LIBRARY_DESIGN.zh-CN.md)。

本地应用仍然没有云依赖、Zotero 写回、Zotero online sync、桌面打包、账号系统、遥测、LLM 聊天、强制 LLM 标签、远程插件加载，或 React/Node 前端工具链。

## Public Alpha 资源

- [发布说明](docs/LAUNCH_NOTES.zh-CN.md)
- [常见问题](docs/FAQ.zh-CN.md)
- [故障排查](docs/TROUBLESHOOTING.zh-CN.md)
- [演示指南](docs/DEMO_GUIDE.zh-CN.md)
- [反馈指南](docs/FEEDBACK.zh-CN.md)
- [Triage guide](docs/TRIAGE.md)

## 安全与隐私

不要提交 `.paper-galaxy/`、SQLite 数据库、生成的 HTML/JSON 导出、备份 zip、向量索引、下载模型文件或 secrets。公开 issue 中不要粘贴私人文档文本。详见 [SECURITY.zh-CN.md](SECURITY.zh-CN.md) 和 [docs/PRIVACY.zh-CN.md](docs/PRIVACY.zh-CN.md)。

## 路线图与贡献

- 路线图：[docs/ROADMAP.zh-CN.md](docs/ROADMAP.zh-CN.md)
- 安装说明：[docs/INSTALL.zh-CN.md](docs/INSTALL.zh-CN.md)
- 架构说明：[docs/ARCHITECTURE.zh-CN.md](docs/ARCHITECTURE.zh-CN.md)
- 架构决策：[docs/DECISIONS.zh-CN.md](docs/DECISIONS.zh-CN.md)
- 贡献指南：[CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md)
- 行为准则：[CODE_OF_CONDUCT.zh-CN.md](CODE_OF_CONDUCT.zh-CN.md)
- 更新日志：[CHANGELOG.zh-CN.md](CHANGELOG.zh-CN.md)
