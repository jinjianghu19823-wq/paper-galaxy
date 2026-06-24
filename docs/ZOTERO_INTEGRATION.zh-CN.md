# Zotero 集成

[English](ZOTERO_INTEGRATION.md) | 简体中文

Paper Galaxy 以本地优先、只读方式从 Zotero Desktop 导入资料。主要连接方式是 Zotero Desktop 的本地 API：`http://localhost:23119/api/`。Paper Galaxy 不写回 Zotero，不上传数据，也不会默认复制或移动本地 PDF。

## 快速开始

```bash
paper-galaxy init .
paper-galaxy zotero detect
paper-galaxy zotero status
paper-galaxy zotero doctor --project-dir .
paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map
paper-galaxy serve --project-dir .
```

打开本地应用后，把图谱来源切换到 Zotero，就可以查看保存的 `Zotero Reading Graph`。

## 会导入什么

- Zotero 条目元数据：题名、条目类型、年份、期刊/会议名、DOI、URL、摘要、日期、版本和 Zotero key。
- 作者/创作者、标签和 collection 归属。
- 开启 `--include-notes` 时导入 child notes 和 annotation 文本。
- 开启 `--include-pdfs` 且本地 PDF 可解析时，导入附件元数据并抽取 PDF 文本。
- 没有可读 PDF 的条目仍可作为 metadata-only 文档导入。

导入后会使用稳定 ID：

- Zotero 条目行：`zotero_item_<sha16>`
- Paper Galaxy 文档行：`doc_zotero_<sha16>`

PDF 路径会就地引用。Paper Galaxy 默认不会复制、移动或修改 Zotero 附件文件。

## 连接边界

主连接器通过本地 API 读取数据，不需要认证。直接读取 `zotero.sqlite` 只作为 fallback-only、read-only 的诊断和路径提示，因为 Zotero 数据库 schema 可能随版本变化。

Paper Galaxy 永远不写回 Zotero。本功能不包含 Zotero OAuth、不包含在线 Zotero Web API 同步、不包含云同步，也不包含托管账号系统。

## CLI 命令

- `paper-galaxy zotero detect`：检测本地 API 和可能的 Zotero data dir。
- `paper-galaxy zotero status`：检查 Zotero Desktop 本地 API 是否可访问。
- `paper-galaxy zotero doctor`：不写入数据的真实机器 readiness 检查，覆盖本地 API、collections、tags、附件样本、可选 `pypdf` 和已有项目状态。
- `paper-galaxy zotero validate-local`：`zotero doctor` 的别名。
- `paper-galaxy zotero collections`：列出本地 API 中的 collections。
- `paper-galaxy zotero items`：预览 top-level items，不写入数据库。
- `paper-galaxy zotero import`：导入到本地 Paper Galaxy SQLite。
- `paper-galaxy zotero graph`：从已导入条目构建或重建 Zotero 阅读图谱。
- `paper-galaxy zotero imported`：列出已经导入的 Zotero 条目。
- `paper-galaxy zotero validate`：报告 Zotero 表计数和悬空链接。
- `paper-galaxy zotero smoke-test`：对小样本做 dry-run。

常用导入选项包括 `--collection`、可重复的 `--tag`、可重复的 `--item-type`、`--include-pdfs/--no-include-pdfs`、`--include-notes/--no-include-notes`、`--include-metadata-only`、`--pdf-policy`、`--include-status`、`--limit`、`--since-version`、`--dry-run`、`--force` 和 `--build-reading-map`。

`--collection` 可以使用 collection key、精确 collection 名称或 slash-style 路径。名称和路径匹配大小写不敏感；如果名称有歧义或找不到 collection，导入会在写入前失败。目前本地 beta 只支持 Zotero Desktop user library 别名 `local`、`user`、`users/0` 和 `/users/0`。

`--include-status` 支持 `all`、`read`、`reading`、`to_read` 和 `unknown`。旧写法 `unclassified` 仍作为 `unknown` 的 deprecated alias 接受。

`--pdf-policy extract` 是默认策略。`metadata` 只记录附件元数据，不抽取 PDF 文本。`skip-missing` 会跳过看起来有 PDF 但无法生成本地 PDF 文本的条目。

## 数据存放位置

导入的元数据、本地 PDF 抽取文本、文本块和保存的阅读图谱都会存入项目 `.paper-galaxy/` 下的 Paper Galaxy 数据库。这个目录可能包含敏感研究资料，不应提交到 git。

可以运行：

```bash
paper-galaxy zotero validate --project-dir .
paper-galaxy validate-project --project-dir .
```

来检查计数和一致性；这些验证不会打印完整源文本。

## 常见失败状态

- Zotero Desktop 没打开：打开 Zotero 后重试 `paper-galaxy zotero status`。
- 本地 API 没启用：在 Zotero 设置中启用 local API，并重启 Zotero。
- PDF 缺失：条目仍可作为 metadata-only 文档导入。
- 链接 PDF 位于 data dir 之外：路径会保守记录，并且默认不会复制。
- 抽取失败：导入会记录 warning，并继续保留元数据。

真实库测试清单见 [ZOTERO_REAL_WORLD_TESTING.zh-CN.md](ZOTERO_REAL_WORLD_TESTING.zh-CN.md)。阅读图谱行为见 [READING_GRAPH.zh-CN.md](READING_GRAPH.zh-CN.md)。
