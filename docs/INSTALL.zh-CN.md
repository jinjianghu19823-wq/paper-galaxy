# 安装

[English](INSTALL.md) | 简体中文

英文版仍是规范版本；本文件提供简体中文说明。

## 安装前试用

打开公开静态演示：

```text
https://jinjianghu19823-wq.github.io/paper-galaxy/
```

演示只使用合成数据，包含英文和简体中文页面。它不会运行本地 FastAPI 应用，也不会读取用户文档。

## 开发安装

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,ml,pdf,app]"
paper-galaxy doctor
```

这个安装支持扫描、索引、TF-IDF 地图、本地网页应用、项目验证、保存地图运行，以及备份导入/导出。

## Zotero Reading Graph

Zotero 支持包含在同一个 app 安装中，连接方式是 Zotero Desktop 的 local API：

```bash
paper-galaxy zotero detect
paper-galaxy zotero status
paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map
paper-galaxy serve --project-dir .
```

Paper Galaxy 不写回 Zotero，不上传数据，也不会默认复制 PDF。导入的元数据和抽取文本会存入 `.paper-galaxy/`。

## 可选 extras

```bash
python -m pip install -e ".[dev,ml,pdf,app,ocr]"
python -m pip install -e ".[dev,ml,pdf,app,embeddings]"
```

OCR 只有在用户传入 OCR 相关 flag 时才会运行。Embedding 命令默认仍然需要显式的本地模型路径，除非用户使用 `--allow-model-download`。

## Smoke test

```bash
paper-galaxy init . --force
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
paper-galaxy validate-project --project-dir .
paper-galaxy build-map-run --project-dir . --name "Tiny corpus map"
paper-galaxy serve --project-dir .
```

本地服务器默认绑定到 `127.0.0.1`。本地 SQLite 数据库可能包含抽取文本、文本块、向量、标签和保存的地图运行，所以不要提交 `.paper-galaxy/`。
