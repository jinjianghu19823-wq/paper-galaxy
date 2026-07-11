# 更新日志

[English](CHANGELOG.md) | 简体中文

英文版仍是规范版本；本文件提供简体中文摘要。

## Unreleased

- 增加本地研究工作站的首个 checkpoint：schema v9 持久化 corpus/Zotero source、
  带协作取消与崩溃恢复的单 writer job queue、安全项目初始化，以及仅绑定 loopback
  并能安全选择空闲端口的 `paper-galaxy launch`。本地 Web 应用新增有界 source/job
  控制，并使用 Host 校验、同源校验、每进程 write token、CSP 和其他浏览器安全响应头；
  Zotero HTTP 请求被限制在同一个 loopback origin，禁用代理和重定向。Launch 与直接
  indexing 会在任何写入前拒绝位于 source 内的项目或数据库；job/source enqueue 在
  同一事务内 fencing，worker 在每个 commit boundary 复核 ownership，restore 会拒绝
  活动后台 worker。
- 增加 schema v8 向量 provenance 与生命周期加固：本地模型精确 fingerprint、
  无字段边界碰撞的 canonical document revision、source revision
  compare-and-swap 落库、死亡 owner run 自动恢复、仅 active 且
  provenance 匹配的语义读取、NumPy 有界内存 top-k 与批量 metadata 查询、更完整的
  vector validation，以及默认 dry-run 的 stale-vector prune；移除未实际使用的
  FAISS extra。run/backup recovery 在 Windows 上改用无破坏进程句柄检查，不再以
  `os.kill(pid, 0)` 探测存活进程。
- 用 schema v7 事务化 bootstrap/migration、显式只读/读写/migration connection、
  future-schema 拒绝、严格 schema/JSON 校验、短写事务审计和 SQLite backup API
  migration snapshot，替换隐式 SQLite 初始化。
- 强化项目备份/恢复：使用 active-WAL-safe SQLite 快照、严格流式 ZIP/checksum/资源
  上限校验、可移植自定义数据库与向量索引映射、仅替换已认领输出的原子归档发布、
  可跨进程中断恢复的持久事务和项目级 maintenance lock；待恢复事务会阻断普通连接，
  有界解压和严格认领的 staging 清理限制资源与隐私暴露；checksum 校验不再允许关闭。
- 用先 staging、后校验、再发布的流程替换 demo 输出目录的破坏性覆盖。只有空目录或
  带受支持 Paper Galaxy 构建 marker 的输出可被替换；symlink、危险路径和未认领
  目录会被拒绝，并支持失败回滚与中断恢复。
- 主题词和 pair explanation 先按精确分数排序，再使用稳定 secondary key；公开
  demo 浮点统一为小数点后最多八位、有限 JSON 数值和正零，并规范 cluster ID 与
  UTF-8 序列化。
- 将发布清理收窄为仅清理构建产物：`clean`、`clean-build`、发布检查和兼容的
  `clean-artifacts` target 都会保留本地项目、数据库、Zotero 数据、备份、
  向量索引和用户导出。
- 默认 demo 构建现在只写输出目录；新增显式 source fixture 刷新模式、跨绝对
  语料路径稳定的全部公开 demo ID，以及 CI clean-worktree 门禁。
- 将包许可证元数据更新为当前 SPDX 字符串格式。
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
