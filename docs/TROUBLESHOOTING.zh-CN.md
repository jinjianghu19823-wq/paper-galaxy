# 故障排查

[English](TROUBLESHOOTING.md) | 简体中文

## 安装失败

建议先创建干净虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,ml,pdf,app]"
paper-galaxy doctor
```

报告安装问题时，请提供 Python 版本、系统、命令和最后的错误片段。不要包含会暴露敏感姓名的私人路径。

## 缺少 scikit-learn

安装 `ml` extra：

```bash
python -m pip install -e ".[dev,ml,pdf,app]"
```

然后重新运行 `paper-galaxy doctor`。

## 缺少 pypdf

PDF 抽取是可选的。安装 `pdf` extra：

```bash
python -m pip install -e ".[dev,ml,pdf,app]"
```

如果 `pypdf` 不可用，PDF 应被明确跳过，而不是让扫描崩溃。

## OCR 缺少 Tesseract

OCR 默认关闭。如果要使用 OCR，请安装 Python extra 和本地 Tesseract：

```bash
python -m pip install -e ".[dev,ml,pdf,app,ocr]"
paper-galaxy scan /path/to/corpus --include-images --ocr --out galaxy.html --force
```

缺少 OCR 包或本地二进制时，应该报告为 skip。

## Embeddings 拒绝隐藏下载

`paper-galaxy embed` 默认拒绝远程模型名。请传入本地模型路径：

```bash
paper-galaxy embed --project-dir . --model /path/to/local/model
```

只有在你明确希望 Sentence Transformers 解析/下载模型时，才使用 `--allow-model-download`。

## 数据库缺失

如果本地 app 提示数据库不存在，请初始化并索引语料：

```bash
paper-galaxy init . --force
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
paper-galaxy serve --project-dir .
```

## Zotero local API 不可用

`paper-galaxy zotero status` 需要 Zotero Desktop 正在运行，并且本地 API 已启用。先打开 Zotero，然后运行：

```bash
paper-galaxy zotero detect
paper-galaxy zotero status
paper-galaxy zotero doctor --project-dir .
```

如果仍不可用，请在 Zotero Settings -> Advanced 中确认 local API 已启用。Zotero 设置里显示的 data directory 是权威路径；`paper-galaxy zotero detect` 只是 best-effort 猜测。
`paper-galaxy zotero doctor --json-out zotero-doctor.json` 可以写出不写入数据的 readiness 报告，适合私下排查。

## Zotero 导入时 PDF 缺失

缺失附件或位于 data dir 外部的链接附件不会阻塞导入。Paper Galaxy 仍可以从题名、摘要、笔记、标签和集合创建 metadata-only 文档：

```bash
paper-galaxy zotero import --project-dir . --include-metadata-only --build-reading-map
```

Paper Galaxy 不写回 Zotero，不上传 Zotero 数据，也不会默认复制 PDF。

如果想把元数据问题和 PDF 抽取问题分开，可以运行：

```bash
paper-galaxy zotero import --project-dir . --pdf-policy metadata --dry-run
paper-galaxy zotero import --project-dir . --pdf-policy skip-missing --dry-run
```

## FTS5 不可用

本地全文搜索需要 SQLite FTS5。运行：

```bash
paper-galaxy validate-project --project-dir .
```

如果 FTS5 缺失，请换用包含 FTS5 的 Python/SQLite 构建。

## Pages 或静态演示站无法加载

本地构建和检查：

```bash
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist --serve
python scripts/check_live_site.py --allow-not-deployed
```

GitHub Pages 需要确认仓库 Settings -> Pages -> Source 为 GitHub Actions，然后运行 Pages workflow。

## 图谱空白

公开演示请检查浏览器 console，并确认 `/data/tiny-map.json` 能加载。本地 app 请运行：

```bash
paper-galaxy validate-project --project-dir .
paper-galaxy db-stats --project-dir .
```

## 没有 active indexed documents

测试时可以用较低 min chars 重新索引：

```bash
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
```

`missing` 和 `unindexed` 文件默认不出现在地图中。

## 权限问题

请索引你有读取权限的文件夹，并把项目元数据写到你有写权限的位置。不要提交 `.paper-galaxy/`、SQLite 文件或备份包。

## 运行验证

```bash
paper-galaxy validate-project --project-dir .
```

验证报告包含计数、schema 状态、warning 和 error，不包含完整抽取文本。
