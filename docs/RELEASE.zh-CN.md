# 发布检查清单

[English](RELEASE.md) | 简体中文

英文版仍是规范版本；本文件提供简体中文说明。

## 基础检查

```bash
python -m ruff check .
python -m ruff format . --check
python -m mypy src
python -m pytest
python -m build
```

## 本地功能检查

```bash
paper-galaxy doctor
paper-galaxy scan examples/tiny_corpus --out galaxy.html --json-out galaxy.json --force --min-chars 40
paper-galaxy init . --force
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
paper-galaxy search "neural operator" --project-dir .
paper-galaxy db-stats --project-dir .
paper-galaxy validate-project --project-dir .
paper-galaxy build-map-run --project-dir . --name "Tiny corpus map"
paper-galaxy map-runs --project-dir .
paper-galaxy export-project --project-dir . --out paper-galaxy-backup.zip --yes
paper-galaxy import-project paper-galaxy-backup.zip --project-dir /tmp/paper-galaxy-restore --dry-run
paper-galaxy plugins
paper-galaxy serve --help
```

## 公开发布检查

```bash
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist
python scripts/public_readiness_check.py --strict
```

发布前删除生成的本地产物：

- `galaxy.html`
- `galaxy.json`
- `site_dist/`
- `.paper-galaxy/`
- `paper-galaxy-backup.zip`
- 任意 `*.sqlite3`
- 任意本地模型或向量下载

## 发布原则

- 不把用户文档、抽取文本、SQLite 数据库、secrets 或本地路径放进公开仓库。
- 公开演示只使用合成 tiny corpus 数据。
- OCR、embeddings 和未来云库功能必须保持显式 opt-in。
- 任何 cloud/runtime 行为变更都需要更新隐私、架构和 ADR 文档。
