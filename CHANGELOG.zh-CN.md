# 更新日志

[English](CHANGELOG.md) | 简体中文

英文版仍是规范版本；本文件提供简体中文摘要。

## Unreleased

- 强化 Zotero Reading Graph beta 以适配真实本地库：新增不写入的 `zotero doctor`、按 key/name/path 过滤 collection、校验 reading status 和本地 library 别名、显式 PDF 策略、annotation 导入、更完整的导入 summary，以及 DOI/URL/PDF/Zotero 链接等 inspector 元数据。
- 增加第一版 Zotero Reading Graph 集成：只读 local API client、Zotero schema v6 表、导入器、元数据/PDF/笔记处理、CLI 命令、本地 web API、UI 过滤器、文档，以及只使用合成数据的公开演示边界。
- 增加公开发布后的 activation 文档、FAQ、故障排查、演示指南、反馈指南、triage guide、发布说明和 live-site 验证。
- 增加 `scripts/check_live_site.py`、`scripts/launch_report.py`、release workflow 检查，以及 `live-check`、`post-public-check`、`release-check`、`launch-report` Makefile target。
- 强化 public readiness 检查，加入 source-only/site-dist 模式、release/feedback 文档检查、云设计边界检查和云运行时源码扫描。
- 增加本地 social preview SVG 资源，以及 Open Graph、Twitter card、canonical URL 和语言 alternate metadata。
- 增加公开发布检查脚本、社区文件和 GitHub Pages 演示部署 workflow。
- 增加由合成 tiny corpus 生成的英文/简体中文静态公开演示站。
- 给本地网页应用增加英文/简体中文语言切换。
- 增加未来个人云库设计文档，但没有实现云运行时功能。
- 增加 GitHub 仓库主要文档的简体中文版本。

## 0.1.0

- 增加 Phase 7 项目验证，支持控制台和 JSON 报告。
- 增加 SQLite schema v5 保存地图运行、地图运行 CLI 命令，以及网页端运行选择。
- 增加本地项目备份导出/导入，包含 manifest 和校验和。
- 增加静态内置插件 registry，用于本地抽取器边界。
- 增加包元数据、构建检查、发布文档和备份文档。
- 保持 Phase 7 本地优先：没有遥测、账号、云同步、远程插件加载、LLM 聊天、React 或 Node 构建工具链。

## 0.0.1

- 从 Phase 0 到 Phase 6 的初始本地优先脚手架和功能开发。
