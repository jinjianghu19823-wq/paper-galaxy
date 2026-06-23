# Paper Galaxy v0.1.0 公开 Alpha 发布说明

[English](LAUNCH_NOTES.md) | 简体中文

Paper Galaxy 是一个本地优先的研究地图工具。它可以把论文、笔记、Markdown、LaTeX、PDF 和相关研究文件夹变成本地文档地图、主题簇和可解释邻居关系。

## 现在可用的功能

- 静态离线 `paper-galaxy scan` HTML 导出。
- 增量本地 SQLite 索引，包含文档和文本块记录。
- 本地 SQLite FTS5 搜索。
- 带交互式文档图谱的本地浏览器应用。
- 本地应用和公开站点的英文/简体中文界面。
- Markdown、LaTeX、PDF、文本和可选图像 OCR 的抽取报告。
- 可选本地 dense embeddings 和语义搜索。
- 主题簇标签、本地手动标签覆盖和 pair explanations。
- 只读 Zotero Desktop 导入和保存的 Zotero Reading Graph 地图。
- 项目验证、保存 TF-IDF 地图运行、备份导出/导入和 package 构建检查。
- 只使用合成数据的静态 GitHub Pages 演示：
  <https://jinjianghu19823-wq.github.io/paper-galaxy/>

## 本地优先边界

Paper Galaxy 不要求账号、遥测、自动上传、托管后端或云依赖。安装后的应用读取本地文件和本地 SQLite 项目状态。OCR、embeddings 和 Zotero 导入只有在用户显式启用时才运行。Zotero 导入使用 local API，不写回 Zotero，不上传数据，也不会默认复制 PDF。

## 有意不实现的功能

公开 alpha 不包含云同步、账号、托管索引、文档上传、遥测、远程插件加载、Zotero online sync、Zotero 写回、桌面打包、强制 OCR 服务、强制 LLM 标签、LLM 聊天、React 或 Node 构建工具链。

个人云库只是未来 opt-in 设计文档，不是当前 runtime 功能。

## 安装

```bash
python -m pip install -e ".[dev,ml,pdf,app]"
```

## 快速开始

```bash
paper-galaxy doctor
paper-galaxy init . --force
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
paper-galaxy serve --project-dir .
```

## Zotero 快速开始

```bash
paper-galaxy zotero detect
paper-galaxy zotero status
paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map
paper-galaxy serve --project-dir .
```

## 隐私提醒

`.paper-galaxy/` 可能包含抽取文本、文本块、向量、标签、地图运行和备份元数据。不要提交它，也不要把它上传到公开 issue。公开 bug 报告应使用合成示例或最小化脱敏片段。

## 已知限制

- PDF 抽取有用，但不是完整的学术 PDF parser。
- OCR 需要可选依赖和本地 Tesseract。
- Embeddings 默认需要显式本地模型路径，除非用户明确允许模型下载。
- Zotero 导入依赖 Zotero Desktop local API 可用，并且是单向进入 Paper Galaxy。
- 公开演示是静态站点，不运行本地 FastAPI 后端。
- 云同步尚未实现。

## 反馈与问题报告

请使用仓库 issue templates：
<https://github.com/jinjianghu19823-wq/paper-galaxy/issues/new/choose>

不要包含私人论文文本、本地数据库、API key、secret 或敏感本地路径。

## 未来方向

下一步是公开发布稳定化：安装反馈、抽取质量问题、图谱可用性修复、更清晰的文档，以及谨慎处理隐私反馈。C1 加密备份库仍只是设计审查方向，除非未来明确进入实现阶段。
