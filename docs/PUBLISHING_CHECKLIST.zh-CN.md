# 公开发布检查清单

[English](PUBLISHING_CHECKLIST.md) | 简体中文

英文版仍是规范版本；本文件提供简体中文说明。

## 本地检查

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

也可以运行：

```bash
make launch-check
```

## 公开前确认

- 仓库里没有 `.paper-galaxy/`。
- 仓库里没有 `*.sqlite3`。
- 仓库里没有 `galaxy.html`、`galaxy.json`、`extraction-report.json` 或本地备份 zip。
- 仓库里没有用户文档、私人路径、API key 或 tokens。
- README、隐私、安全、贡献和路线图文档是最新的。
- 公开演示只包含合成 tiny corpus 数据。
- 静态资源没有 CDN、远程字体、远程图片或远程运行时依赖。

## GitHub Pages

- Pages workflow 从 `site/` 和构建脚本生成静态站点。
- Pages 站点不运行 FastAPI 后端。
- Pages 站点不读取本地 SQLite 数据库。
- Pages 站点支持英文和简体中文页面。

验证：

```bash
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist
```

## 仓库公开后

- 检查仓库 visibility 是否为 public。
- 打开 GitHub Pages 首页和 `/zh-cn/` 页面。
- 确认 Actions 中 CI 和 Pages 最新运行成功。
- 检查 README 中的链接、徽章和在线演示链接。
- 确认 issue templates、PR template、license、security 和 code of conduct 可见。

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
