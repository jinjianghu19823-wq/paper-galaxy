# 安装

[English](INSTALL.md) | 简体中文

英文版仍是规范版本；本文件提供简体中文说明。

## 安装前试用

打开公开静态演示：

```text
https://jinjianghu19823-wq.github.io/paper-galaxy/
```

演示只使用合成数据，包含英文和简体中文页面。它不会运行本地 FastAPI 应用，也不会读取用户文档。

## 本地工作站安装

先克隆仓库。下面的命令都在仓库根目录中运行。`full` extra 汇总本地网页应用、
TF-IDF/地图和 PDF 抽取依赖；它有意不包含 OCR、dense embeddings、模型下载或开发
工具。

### pip 和 venv

```bash
git clone https://github.com/jinjianghu19823-wq/paper-galaxy.git
cd paper-galaxy
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install ".[full]"
paper-galaxy doctor
```

### pipx

```bash
git clone https://github.com/jinjianghu19823-wq/paper-galaxy.git
cd paper-galaxy
pipx install ".[full]"
paper-galaxy doctor
```

### uv tool

```bash
git clone https://github.com/jinjianghu19823-wq/paper-galaxy.git
cd paper-galaxy
uv tool install ".[full]"
paper-galaxy doctor
```

`pipx` 和 `uv tool` 都会创建独立的工具环境，并提供 `paper-galaxy` 命令，适合日常
使用。参与代码开发时，请使用 venv 和 editable install。

## 一个命令启动

```bash
paper-galaxy launch \
  --project-dir ~/PaperGalaxy \
  --corpus ~/Papers \
  --no-open
```

该命令会安全地创建或重新打开项目，把论文目录登记为 source（不复制、不修改），为
新登记的 source 排队索引任务，并启动本地工作站。重复执行会复用已有项目和 source
登记，不会覆盖它们。需要自动打开浏览器时可使用 `--open`。

网页服务器默认绑定 loopback（`127.0.0.1`）。Paper Galaxy 不上传语料、不收集遥测，
也不会自动下载 OCR 或 embedding 模型。

## 开发安装

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,ml,pdf,app]"
paper-galaxy doctor
```

这个 editable install 会在本地工作站依赖之外增加测试、lint、类型检查和构建工具。

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

OCR 只有在用户传入 OCR 相关 flag 时才会运行。Embedding 命令默认仍然需要显式的本地模型路径，除非用户使用 `--allow-model-download`。这两个可选 extra 都不属于 `full`，安装 `full` 绝不会下载模型。

## Smoke test

```bash
PROJECT_DIR="$(mktemp -d)/paper-galaxy-project"
paper-galaxy init "$PROJECT_DIR"
paper-galaxy index examples/tiny_corpus --project-dir "$PROJECT_DIR" --min-chars 40
paper-galaxy validate-project --project-dir "$PROJECT_DIR"
paper-galaxy build-map-run --project-dir "$PROJECT_DIR" --name "Tiny corpus map"
paper-galaxy serve --project-dir "$PROJECT_DIR" --no-open
```

这个 shell 示例使用全新的临时项目。在 Windows PowerShell 中，请把 `$PROJECT_DIR` 设为
`$env:TEMP` 下的新目录，再把该值传给相同命令。本地服务器默认绑定到 `127.0.0.1`。
项目数据库可能包含抽取文本、文本块、向量、标签和保存的地图运行，所以不要提交
`.paper-galaxy/`。
