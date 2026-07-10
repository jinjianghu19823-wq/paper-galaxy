# 发布检查清单

[English](RELEASE.md) | 简体中文

英文版仍是规范版本；本文件提供简体中文说明。

## 基础检查

```bash
make clean-build
make release-check
python -m ruff check .
python -m ruff format . --check
python -m mypy src
python -m pytest
python -m build
```

`clean-build` 只删除明确的构建目录、包 metadata 和
Python/pytest/Ruff/Mypy cache。`clean` 与兼容的 `clean-artifacts` 具有相同的
build-only 边界；它们绝不会删除 `.paper-galaxy/`、SQLite 或 Zotero 数据库、
备份、向量索引、地图导出或其他用户项目状态。

## 本地功能检查

```bash
release_project="$(mktemp -d "${TMPDIR:-/tmp}/paper-galaxy-release.XXXXXX")"
paper-galaxy doctor
paper-galaxy scan examples/tiny_corpus \
  --out "$release_project/galaxy.html" \
  --json-out "$release_project/galaxy.json" --force --min-chars 40
paper-galaxy init "$release_project" --force
paper-galaxy index examples/tiny_corpus --project-dir "$release_project" --min-chars 40
paper-galaxy search "neural operator" --project-dir "$release_project"
paper-galaxy db-stats --project-dir "$release_project"
paper-galaxy validate-project --project-dir "$release_project"
paper-galaxy build-map-run --project-dir "$release_project" --name "Tiny corpus map"
paper-galaxy map-runs --project-dir "$release_project"
paper-galaxy export-project --project-dir "$release_project" \
  --out "$release_project/paper-galaxy-backup.zip" --yes
paper-galaxy import-project "$release_project/paper-galaxy-backup.zip" \
  --project-dir "$release_project/restore" --dry-run
paper-galaxy plugins
paper-galaxy zotero detect
paper-galaxy zotero smoke-test --project-dir "$release_project" || true
paper-galaxy serve --help
```

Zotero smoke test 是可选项，因为 CI 或 release 机器可能没有运行 Zotero Desktop。local API 不可用时应该优雅失败。

## 公开发布检查

```bash
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist
python scripts/public_readiness_check.py --strict --require-site-dist
python scripts/check_live_site.py --allow-not-deployed
```

发布前审计 Git 已跟踪内容和公开构建产物，确认其中没有：

- `galaxy.html`
- `galaxy.json`
- `.paper-galaxy/`
- `paper-galaxy-backup.zip`
- 任意 `*.sqlite3`
- `zotero.sqlite`、Zotero `storage/` 文件夹、PDF 或真实 Zotero 路径
- 任意本地模型或向量下载

这是一项版本控制/公开内容审计，不是删除本机数据的指令。被 Git 忽略的本地
项目状态应保留；`clean-build` 不会触碰它。默认 demo 构建只写
`site_dist/data/tiny-map.json`，不会修改 `site/`。构建器先在 sibling staging
目录完成构建和校验，再用可崩溃恢复的 sibling rename 发布；它只能替换空目录或
带受支持 marker 的构建输出，并拒绝 symlink、危险路径和非空未认领目录。开头的
`make clean-build` 可清理旧的无 marker 构建产物，但不会触碰项目数据。公开浮点
采用有限数值、小数点后最多八位的契约。
只有在明确更新 committed
fixture 时才运行：

```bash
python scripts/build_demo_site.py --out site_dist --refresh-source-data
git diff -- site/data/tiny-map.json
```

CI、Pages、`release-check`、`launch-check` 和普通构建不得使用
`--refresh-source-data`。

## 发布原则

- 不把用户文档、抽取文本、SQLite 数据库、secrets 或本地路径放进公开仓库。
- 公开演示只使用合成 tiny corpus 数据。
- OCR、embeddings 和未来云库功能必须保持显式 opt-in。
- 任何 cloud/runtime 行为变更都需要更新隐私、架构和 ADR 文档。

## v0.1.0 公开 Alpha Release

第一版公开 GitHub Release 应被视为 alpha 公告，不是 package registry 发布。

1. 确认检查通过：

```bash
make clean-build
make release-check
make post-public-check
```

2. 确认 live site：

```bash
python scripts/check_live_site.py --base-url https://jinjianghu19823-wq.github.io/paper-galaxy/
```

3. 检查构建产物：

```bash
python -m build
ls dist/
```

4. 审阅发布说明：

```bash
sed -n '1,220p' docs/LAUNCH_NOTES.md
```

5. 只有在用户明确批准后才创建 tag 和 GitHub Release：

```bash
git tag v0.1.0
git push origin v0.1.0
gh release create v0.1.0 dist/* \
  --title "Paper Galaxy v0.1.0" \
  --notes-file docs/LAUNCH_NOTES.md
```

Tag push 会触发 `.github/workflows/release.yml`，运行检查、构建 Python distributions，并把 wheel/sdist 作为 workflow artifacts 上传。该 workflow 不发布 PyPI，也不需要 secrets。

## 回滚计划

如果错误创建了 release tag 或 GitHub Release：

1. 在 GitHub UI 或用 `gh release` 删除/编辑 GitHub Release。
2. 如果 tag 错误且不应被使用，删除远端 tag：

```bash
git push origin :refs/tags/v0.1.0
```

3. 修复 `main`，重新运行 `make release-check`，再经 review 后创建新 tag。

不要重写已经公开的 `main` 历史。

除非用户明确要求，否则不要发布到 PyPI。
