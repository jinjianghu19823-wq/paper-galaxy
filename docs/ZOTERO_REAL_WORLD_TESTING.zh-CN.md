# Zotero 真实库测试

[English](ZOTERO_REAL_WORLD_TESTING.md) | 简体中文

这份清单用于把 Paper Galaxy 跑在真实的本地 Zotero Desktop 库上。它的设计目标是：不写回 Zotero、不打印完整文档文本，并尽量把问题定位在本机环境、API、过滤条件或 PDF 抽取上。

## 导入前

1. 打开 Zotero Desktop。
2. 在 Zotero Settings -> Advanced 中确认 local API 已启用。
3. 在 Paper Galaxy 项目目录中操作，并确保 `.paper-galaxy/` 和 `*.sqlite3` 不会被提交。

macOS/Linux 建议先用隔离环境安装：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,ml,pdf,app]"
paper-galaxy doctor
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,ml,pdf,app]"
paper-galaxy doctor
```

然后运行不写入 Zotero 的 readiness 检查：

```bash
paper-galaxy zotero detect
paper-galaxy zotero status
paper-galaxy init .
paper-galaxy zotero doctor --limit 20 --json-out zotero-doctor.json
```

`zotero doctor` 是不写入数据的 readiness 检查。它会探测本地 API root、top-level items、collections、tags、一小部分 child items、可选 PDF 支持，以及已有项目数据库状态。

## 安全 dry-run

先预览 collections 和少量条目：

```bash
paper-galaxy zotero collections --limit 20
paper-galaxy zotero items --limit 10
paper-galaxy zotero items --collection "Collection Name" --limit 20
```

然后不写入地试跑导入：

```bash
paper-galaxy zotero import --project-dir . --dry-run --limit 10 --json-out zotero-dry-run.json
paper-galaxy zotero import --project-dir . --collection "Collection Name" --dry-run
paper-galaxy zotero import --project-dir . --pdf-policy metadata --dry-run
paper-galaxy zotero import --project-dir . --pdf-policy skip-missing --dry-run
```

`--collection` 可以使用 collection key、精确名称或路径。名称和路径匹配大小写不敏感；歧义名称会在导入前报错。

## 完整本地导入

```bash
paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --pdf-policy extract --build-reading-map --json-out zotero-import.json
paper-galaxy zotero validate --project-dir .
paper-galaxy validate-project --project-dir .
paper-galaxy serve --project-dir . --open
```

Windows PowerShell 激活环境后使用同样的 Paper Galaxy 命令：

```powershell
paper-galaxy zotero doctor --limit 20 --json-out zotero-doctor.json
paper-galaxy zotero import --project-dir . --dry-run --limit 10 --json-out zotero-dry-run.json
paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --pdf-policy extract --build-reading-map --json-out zotero-import.json
paper-galaxy serve --project-dir . --open
```

打开本地应用，把图谱来源切换到 Zotero，然后检查：

- 点数量和主题簇标签；
- reading status、tag 和 collection 过滤器；
- 条目 inspector 元数据，包括 Zotero key、条目类型、年份、DOI、URL、附件/PDF 状态，以及 `Open in Zotero`；
- 由高维 TF-IDF 相似度生成的邻居关系。

## 增量运行

导入器会记录本地 API 返回的 Zotero version。大库第一次导入之后，可以用增量运行：

```bash
paper-galaxy zotero import --project-dir . --since-version VERSION --json-out zotero-import.json
```

summary 会包含 fetched、selected、filtered、skipped、unchanged、warning、PDF、attachment 和 annotation 计数。

## 状态和 PDF 策略

阅读状态过滤支持 `all`、`read`、`reading`、`to_read` 和 `unknown`。`unclassified` 仍作为 `unknown` 的旧别名接受。

PDF 策略：

- `extract`：默认策略，尽量抽取本地 PDF 文本。
- `metadata`：只记录附件元数据。
- `skip-missing`：跳过看起来有 PDF 但无法生成本地 PDF 文本的条目。

## 故障排查

- 查询本地 API 时 Zotero Desktop 必须保持打开。
- 如果 `http://localhost:23119/api` 不可访问，请在 Zotero Settings -> Advanced 中启用 local API，然后重启 Zotero。
- 如果 data directory 检测错误，使用 `--data-dir /path/to/Zotero` 手动指定。
- Zotero 设置中显示的 data directory 是权威路径。
- 缺少 `storage/` 文件夹意味着 stored Zotero PDF 可能无法解析，但 metadata-only 导入仍可能工作。
- 位于 Zotero data dir 外的 linked PDFs 会被就地引用，不会被复制。
- 启用 `--include-metadata-only` 或使用 `--pdf-policy metadata` 时，缺失 PDF 不一定阻塞导入。
- 对大库做完整导入前，始终先跑一个小的 dry-run。

## 手动验证清单

- `zotero doctor`：readiness 是 `ready` 或可解释的 `warning`；显示 API URL；报告 data directory 和 `storage/` 状态；非空库的 item/sample 计数应大于零。
- `zotero items --limit 5`：打印 top-level item 表格。
- `zotero items --collection <name>`：只显示该 collection 内的条目。
- dry-run 导入：JSON 中有 `"dry_run": true`；如果项目数据库原本不存在，dry-run 不应创建数据库。
- 完整导入：显示 imported/updated/unchanged 计数和 warning summary；当有足够文档可建图时，`map_run_id` 应存在。
- `zotero validate`：报告已导入计数和 dangling-link 计数。
- 浏览器应用：选择 Zotero source，看到点，点击一个点，并检查 Zotero key、作者、年份、DOI/URL、标签、collection、PDF 状态和 Open in Zotero。

## 隐私说明

Paper Galaxy 不写回 Zotero，不上传 Zotero 数据，不使用 Zotero online API，也不会默认复制 PDF。导入后的元数据、笔记、本地 PDF 抽取文本、文本块和保存的阅读图谱会存入 `.paper-galaxy/` 下的本地 Paper Galaxy 数据库。

分享 bug report 前，请从 JSON 报告中移除私人题名、路径、标签、DOI/URL 或文档摘录。

想干净重试时，可以删除本地 Paper Galaxy 状态：

```bash
rm -rf .paper-galaxy
```

Windows PowerShell：

```powershell
Remove-Item -Recurse -Force .paper-galaxy
```
