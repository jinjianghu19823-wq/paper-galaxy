# 贡献指南

[English](CONTRIBUTING.md) | 简体中文

Paper Galaxy 是本地优先项目。贡献应该保留默认隐私边界：没有遥测、没有默认云调用、没有账号、没有远程前端资源，也没有隐藏模型下载。

英文版仍是规范版本；本文件用于中文协作者快速上手。

## 开发环境

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,ml,pdf,app]"
```

## 检查命令

```bash
python -m ruff check .
python -m ruff format . --check
python -m mypy src
python -m pytest
python -m build
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist
python scripts/public_readiness_check.py --strict
```

小型端到端本地 smoke test：

```bash
make validate-example
```

公开发布相关改动前运行：

```bash
make launch-check
```

## 不要提交的生成文件

不要提交 `.paper-galaxy/`、SQLite 数据库、生成的 HTML/JSON 报告、备份 zip、向量索引或下载模型文件。

不要在公开 issue 中粘贴私人文档文本、API key、包含敏感姓名的本地路径，或项目 SQLite 内容。
