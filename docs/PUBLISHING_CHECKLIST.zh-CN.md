# 公开发布检查清单

[English](PUBLISHING_CHECKLIST.md) | 简体中文

英文版仍是规范版本；本文件提供简体中文说明。仓库已经公开；本清单现在也用于 post-public activation 和 release 准备。

## 本地检查

```bash
python -m ruff check .
python -m ruff format . --check
python -m mypy src
python -m pytest
python -m build
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist --serve
python scripts/public_readiness_check.py --strict --require-site-dist
python scripts/check_live_site.py --allow-not-deployed
```

也可以运行：

```bash
make launch-check
make post-public-check
make release-check
```

## 公开前确认

- 仓库里没有 `.paper-galaxy/`。
- 仓库里没有 `*.sqlite3`。
- 仓库里没有 `galaxy.html`、`galaxy.json`、`extraction-report.json` 或本地备份 zip。
- 仓库里没有用户文档、私人路径、API key 或 tokens。
- 仓库和公开演示里没有真实 Zotero 数据：没有 `zotero.sqlite`、Zotero `storage/` 文件夹、PDF、`zotero://items/...` 记录或私人 Zotero 路径。
- README、隐私、安全、贡献和路线图文档是最新的。
- 公开演示只包含合成 tiny corpus 数据。
- 静态资源没有 CDN、远程字体、远程图片或远程运行时依赖。

## GitHub Pages

- Pages workflow 从 `site/` 和构建脚本生成静态站点。
- Pages 站点不运行 FastAPI 后端。
- Pages 站点不读取本地 SQLite 数据库。
- Pages 站点支持英文和简体中文页面。
- 仓库 Settings -> Pages -> Source 应为 GitHub Actions。

验证：

```bash
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist
gh workflow run pages.yml
gh run list --workflow=pages.yml --limit 5
python scripts/check_live_site.py --base-url https://jinjianghu19823-wq.github.io/paper-galaxy/
```

## 仓库公开后

- 检查仓库 visibility 是否为 public。
- 打开 GitHub Pages 首页和 `/zh-cn/` 页面。
- 运行 `python scripts/check_live_site.py` 并确认通过。
- 确认 Actions 中 CI 和 Pages 最新运行成功。
- 检查 README 中的链接、徽章和在线演示链接。
- 确认 issue templates、PR template、license、security 和 code of conduct 可见。
- 确认没有生成的敏感文件。
- 确认 release notes 已发布或准备完毕。
- 确认首批 issue labels 已创建或已在 `docs/TRIAGE.md` 记录。

## GitHub Release

除非用户明确批准，否则只准备 release，不创建真实 release：

```bash
make clean-artifacts
make release-check
python -m build
gh release create v0.1.0 dist/* \
  --title "Paper Galaxy v0.1.0" \
  --notes-file docs/LAUNCH_NOTES.md
```

除非明确要求，不发布 PyPI。

## 建议 topics

可考虑使用这些 GitHub topics：

- `research-tools`
- `local-first`
- `python`
- `sqlite`
- `tf-idf`
- `document-search`
- `knowledge-graph`
- `privacy`

## 未来发布工作

未来可以继续改进截图、演示视频、PyPI 发布、更多中文文档、桌面打包或 opt-in 云库设计，但这些都不应该破坏当前本地优先边界。
