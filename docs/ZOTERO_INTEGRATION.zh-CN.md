# Zotero 集成

[English](ZOTERO_INTEGRATION.md) | 简体中文

Paper Galaxy 以本地优先、只读方式从 Zotero Desktop 导入资料。主要连接方式是 Zotero Desktop 的本地 API：`http://localhost:23119/api/`。Paper Galaxy 不写回 Zotero，不上传数据，也不会默认复制或移动本地 PDF。

## 快速开始

```bash
paper-galaxy init .
paper-galaxy zotero detect
paper-galaxy zotero status
paper-galaxy zotero doctor --project-dir .
paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map
paper-galaxy serve --project-dir .
```

打开本地应用后，把图谱来源切换到 Zotero，就可以查看保存的 `Zotero Reading Graph`。

## 会导入什么

- Zotero 条目元数据：题名、条目类型、年份、期刊/会议名、DOI、URL、摘要、日期、版本和 Zotero key。
- 作者/创作者、标签和 collection 归属。
- 开启 `--include-notes` 时导入 child notes 和 annotation 文本。
- 开启 `--include-pdfs` 且本地 PDF 可解析时，导入附件元数据并抽取 PDF 文本。
- 没有可读 PDF 的条目仍可作为 metadata-only 文档导入。

导入后会使用稳定 ID：

- Zotero 条目行：`zotero_item_<sha16>`
- Paper Galaxy 文档行：`doc_zotero_<sha16>`

PDF 路径会就地引用。Paper Galaxy 默认不会复制、移动或修改 Zotero 附件文件。

## 连接边界

主连接器通过本地 API 读取数据，不需要认证。直接读取 `zotero.sqlite` 只作为 fallback-only、read-only 的诊断和路径提示，因为 Zotero 数据库 schema 可能随版本变化。
Local API URL 会被规范为同一个 HTTP loopback origin。请求忽略环境 proxy、拒绝
redirect，并拒绝改变 origin 的 pagination link，因此本地响应不能把文库数据静默发送到
其他地址。

Paper Galaxy 永远不写回 Zotero。本功能不包含 Zotero OAuth、不包含在线 Zotero Web API 同步、不包含云同步，也不包含托管账号系统。

## CLI 命令

- `paper-galaxy zotero detect`：检测本地 API 和可能的 Zotero data dir。
- `paper-galaxy zotero status`：检查 Zotero Desktop 本地 API 是否可访问。
- `paper-galaxy zotero doctor`：不写入数据的真实机器 readiness 检查，覆盖本地 API、collections、tags、附件样本、可选 `pypdf` 和已有项目状态。
- `paper-galaxy zotero validate-local`：`zotero doctor` 的别名。
- `paper-galaxy zotero collections`：列出本地 API 中的 collections。
- `paper-galaxy zotero items`：预览 top-level items，不写入数据库。
- `paper-galaxy zotero import`：导入到本地 Paper Galaxy SQLite。
- `paper-galaxy zotero graph`：从已导入条目构建或重建 Zotero 阅读图谱。
- `paper-galaxy zotero imported`：列出已经导入的 Zotero 条目。
- `paper-galaxy zotero validate`：报告 Zotero 表计数和悬空链接。
- `paper-galaxy zotero smoke-test`：对小样本做 dry-run。

常用导入选项包括 `--collection`、可重复的 `--tag`、可重复的 `--item-type`、`--include-pdfs/--no-include-pdfs`、`--include-notes/--no-include-notes`、`--include-metadata-only`、`--pdf-policy`、`--include-status`、`--limit`、`--since-version`、`--dry-run`、`--full`、`--force` 和 `--build-reading-map`。

`--collection` 可以使用 collection key、精确 collection 名称或 slash-style 路径。名称和路径匹配大小写不敏感；如果名称有歧义或找不到 collection，导入会在写入前失败。目前本地 beta 只支持 Zotero Desktop user library 别名 `local`、`user`、`users/0` 和 `/users/0`。
每个持久工作站 source profile 当前最多接受一个 collection；不同 collection 请登记为
不同 profile。多 collection union 尚未实现；每个独立 collection/tag/status profile
都有自己的持久 cursor，不会让另一个 profile 永久跳过记录。

## 增量同步语义

默认导入是增量同步。一个 profile 的首次同步从 library version 0 建立完整基线。从
schema v9 迁移来的 profile 不会获得猜测的 cursor；它的首次 v10 baseline 会明确重新
materialize 已有共享 document，不会把旧全局 cursor 当作完整性证明。后续运行只使用这个
精确 registered profile 自己的 cursor。`--full` 会明确从 version 0 重新抓取，不能与
`--since-version` 同时使用。
规范化 loopback API origin 与可选 Zotero data directory 属于已登记本地 profile 的
identity。项目建立 locator 后，换用其他 origin 或 data directory 会在任何远端请求和
项目写入前被拒绝；原 profile 仍可继续使用，source、cursor、membership 与 run audit 行
保持不变。在提供明确的重新登记流程前，有意更换 locator 应使用独立项目。
若首次远端读取在得到完整、带 version fence 的响应前失败，failed run 与 removed profile
会保留为 audit evidence，但未验证 locator 不会成为 active claim；修正后的 locator 可以
无需 `--full` 建立项目的首次成功 profile。
后者只用于专家诊断或恢复，并且必须等于当前精确 profile 已保存的 cursor，不能跳过
尚未读取的 version 区间。完整 reconciliation 还需要覆盖本地完全相同的 materialization
时使用 `--full --force`；单独使用 `--force` 只作用于 changed feed 返回的记录，绝不隐式
触发全量抓取。

filter identity 与本地 document materialization 是两个独立契约。collection/tag/
item-type/status filter 各自拥有 cursor，但同一 Zotero source 下的 profile 共享 item 与
document 行。因此 source-global fingerprint 会覆盖解析后的 attachment root、PDF/note/
attachment/metadata 是否纳入、PDF policy、read/reading/to-read 标签集合、`min_chars` 与
chunk size/overlap。若这些配置改变，系统会在远端抓取或本地写入前拒绝，除非显式使用
`--full`。只有完整远端响应具有统一且已验证的 version，并通过 source-wide fence 后，
该 full sync 才准备新的 materialization generation，并要求其他 profile 重新建立
baseline。没有显式内容覆盖的持久 Zotero job 会继承与当前 generation 兼容的最近一次
completed 配置，而不会静默退回默认值。改变内容配置的 full sync 会在写锁外准备
attachment/PDF/text/chunk，再让 generation 切换与 cursor 发布共享一个事务；不完整、
取消、失败或进程中断都会完整保留上一代。

连接器通过 `/items?since=` 一次获取变化的 parent 与 child；只有 parent 确实缺失时才
使用有界 `itemKey` 批量补取，并通过 `/deleted?since=` 读取删除日志。所有分页和 endpoint
必须返回同一个非负 `Last-Modified-Version`。Header drift、分页失败、取消、畸形 payload、
数据库错误或被 `--limit` 截断的不完整运行都不会推进 profile cursor。最终 cursor、
completed run audit 与 profile success time 在同一个短、带 ownership fencing 的事务发布。
source-wide 已发布 library version 同时是单调 lower bound：比它更旧的响应会在业务行写入
前被拒绝；collection/child/deletion/item 的每个事务和最终 cursor 发布都会重新检查是否有
peer sync 已并发推进该 fence。
初始登记事务还会在 upsert 前重新核对 source row 与全部 active profile 中保存的规范
API/data directory/library locator，因此两个并发首次登记不能互相覆盖。
CLI 与持久 job summary 会报告 previous/new cursor、changed parent/child 数量、删除数量和
duration。

远端删除的 parent 会保留本地 tombstone/audit，但其 Paper Galaxy document 会变成
`missing`，不再进入普通搜索和地图；删除会级联到缓存 child、attachment 和全部 profile
membership，同时保留本地 audit 行。经过删除日志确认的 child 删除会重建 parent 并移除
该 child。collection 单独重命名或删除时，会先补取并重建已知使用它的 parent，再发布
cursor。没有 deletion evidence 的遗漏仍视为危险的 partial response。完全相同的
materialization 会跳过附件/PDF 工作，不重写 document text、chunks、FTS 或 vectors。

`zotero_profile_items` 以观测到的 library version 保存 membership。每个 changed parent
都会按所有 active 且 materialization-compatible profile 的持久 filter 重新判定；系统只
更新这些带版本的 membership，不推进其他 profile 的 cursor。因此较旧的 tag、collection、
item-type 或 status 正匹配不能在新 metadata 已否定它后继续维持文档可见。仍等待新 full
materialization baseline 的 peer 会继续被 fencing，不会提前重新激活。只要任一未移除的
registered profile 仍包含该条目，关联 document 就保持 active；active profile 并集为空时
转为 `unindexed`。移除或重新登记 source 会重新计算同一个并集，不会删除共享 item 或其
audit history。

成功导入并登记只读本地 profile 后，可以在启动工作站时排队同步：

```bash
paper-galaxy launch --project-dir . --zotero-sync --open
```

`--include-status` 支持 `all`、`read`、`reading`、`to_read` 和 `unknown`。旧写法 `unclassified` 仍作为 `unknown` 的 deprecated alias 接受。

`--pdf-policy extract` 是默认策略。`metadata` 只记录附件元数据，不抽取 PDF 文本。`skip-missing` 会跳过看起来有 PDF 但无法生成本地 PDF 文本的条目。

## 数据存放位置

导入的元数据、本地 PDF 抽取文本、文本块和保存的阅读图谱都会存入项目 `.paper-galaxy/` 下的 Paper Galaxy 数据库。这个目录可能包含敏感研究资料，不应提交到 git。

可以运行：

```bash
paper-galaxy zotero validate --project-dir .
paper-galaxy validate-project --project-dir .
```

来检查计数和一致性；这些验证不会打印完整源文本。

## 常见失败状态

- Zotero Desktop 没打开：打开 Zotero 后重试 `paper-galaxy zotero status`。
- 本地 API 没启用：在 Zotero 设置中启用 local API，并重启 Zotero。
- PDF 缺失：条目仍可作为 metadata-only 文档导入。
- 链接 PDF 位于 data dir 之外：路径会保守记录，并且默认不会复制。
- 抽取失败：导入会记录 warning，并继续保留元数据。

真实库测试清单见 [ZOTERO_REAL_WORLD_TESTING.zh-CN.md](ZOTERO_REAL_WORLD_TESTING.zh-CN.md)。阅读图谱行为见 [READING_GRAPH.zh-CN.md](READING_GRAPH.zh-CN.md)。
