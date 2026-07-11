# 架构

[English](ARCHITECTURE.md) | 简体中文

英文版仍是规范版本；本文件提供简体中文架构说明。

Paper Galaxy 是本地优先的研究资料地图工具。核心思想是：先在本地抽取和清洗文档，再建立本地记录、向量、图结构、地图坐标和主题簇，最后通过静态 HTML 或本地浏览器应用展示。

```text
files -> extraction -> cleaning -> records -> vectors -> graph -> map -> clusters -> UI
```

## 模块

- `paper_galaxy.cli`：命令行入口。
- `paper_galaxy.config`：项目配置和 `.paper-galaxy/project.toml`。
- `paper_galaxy.projects`：原子、不覆盖的本地项目创建与安全重开。
- `paper_galaxy.extractors`：本地文件抽取，包括文本、Markdown、LaTeX、PDF 和可选图片 OCR。
- `paper_galaxy.pipeline`：扫描、抽取、清洗和聚合。
- `paper_galaxy.ml`：TF-IDF、降维、聚类和邻居计算。
- `paper_galaxy.export`：静态 HTML/JSON 导出。
- `paper_galaxy.storage`：SQLite schema、显式连接模式、顺序 migration、严格 JSON 解码、文档、文本块、索引状态、报告、向量、标签、地图运行和备份。
- `paper_galaxy.search`：本地 SQLite FTS5 搜索。
- `paper_galaxy.embeddings`：可选本地 dense embeddings。
- `paper_galaxy.labels`：本地主题簇标签和解释。
- `paper_galaxy.web`：FastAPI 本地后端和静态 vanilla JS 前端。
- `backup.snapshot`：SQLite online snapshot 与完整性/schema 校验。
- `backup.archive`：严格流式 ZIP 拓扑、资源上限、manifest 与 checksum 校验。
- `backup.publish`：同文件系统原子发布与失败回滚。
- `backup.staging`：私有已认领 staging 与保守 orphan 清理。
- `backup.bundle`：可移植 v2 备份编排及严格 v1 检查。
- `storage.locking`：跨进程 shared operation / exclusive maintenance lock，
  包含 legacy project 认领和旧数据库 handle drain。
- `services.sources`：持久、路径私有的 corpus / 只读 Zotero source registry，
  并在每次使用前重新校验 locator。
- `services.jobs`：有界、持久的单 writer job queue、协作取消、安全 summary 与重启
  恢复；`services.worker_lease` 负责跨进程 worker 串行化和 owner fencing。
- `services.launch`：一个命令启动时幂等准备项目、source 和 job。
- `paper_galaxy.plugins`：静态内置抽取边界。
- `paper_galaxy.zotero`：只读 Zotero 本地 API 连接、规范化、附件路径解析、导入器、SQLite 诊断和阅读图谱构建。
- `web.security`：loopback Host allowlist、写请求同源校验、每进程 write token 与
  浏览器安全响应头。

## Phase 1 静态导出

Phase 1 的输出是自包含的离线 HTML。它适合快速查看一个本地文件夹，不要求数据库或本地服务器。

静态图中的最近邻来自高维 TF-IDF 余弦相似度，不来自 2D 视觉距离。2D 坐标只用于展示。

## Phase 2 本地存储

Phase 2 引入 `.paper-galaxy/paper_galaxy.sqlite3`。SQLite 保存文档、文本块、扫描运行、FTS5 索引和文件状态。

索引使用内容 hash 跳过未变化文件，并用 corpus-relative path 维持稳定文档 ID。源文件消失时记录为 `missing`，现存但当前无法索引时记录为 `unindexed`。

### SQLite 生命周期与连接边界

Schema v9 不再在每次连接时隐式执行整份 `CREATE IF NOT EXISTS`，而是采用显式、只向前的生命周期：

- 新数据库在一个明确事务中直接 bootstrap 到当前版本，并在 `schema_migrations` 记录当前 migration registry；函数返回前自行 commit。
- 当前最早支持的历史版本是 v6。测试 fixture 来自仓库真实历史，升级必须按 registry 执行 v6 -> v7 -> v8 -> v9。v7 新增 migration history、结构化 run error 与 Zotero child-version manifest；v8 增加覆盖 title、relative path 与完整提取正文的 document content revision、chunk hash、模型内容 fingerprint、vector 的 source/model/algorithm provenance、run owner PID 和 vector-index provenance。无法证明 freshness 的历史向量保留为 `legacy-unknown`，重建前不会进入语义结果。v9 增加路径私有的 corpus/Zotero source registry、持久有界的本地 job 状态机、精确 partial-index capability 校验，以及确定性的历史 Zotero profile 回填。
- 修改 schema 前，系统用 SQLite online backup API 创建唯一的 mode-`0600` 快照，并对快照执行 `quick_check` 与 `foreign_key_check`。它不会覆盖已有备份、活动数据库或 WAL/SHM sidecar。
- migration 序列、schema version 更新和 history 记录位于同一个 `BEGIN IMMEDIATE` 事务；任一步失败都会完整 rollback。成功迁移不依赖调用者日后碰巧 commit。
- 低于 v6 且没有受支持路径的数据库会被明确拒绝；高于当前版本的 future schema 同样会被拒绝，绝不会降级或把版本号改回 v9。
- 系统不会只相信版本号；bootstrap、migration 和运行连接还会验证表/列、主键顺序、关键 UNIQUE 身份、外键目标及删除行为、索引、FTS5 虚表与 `MATCH`、migration history。

连接类型按用途分开：

- `connect_read_only` 使用 SQLite URI `mode=ro` 打开已经存在且版本匹配的项目数据库，启用 `query_only`、foreign keys 和有界 `busy_timeout`；不会创建目录、数据库、表或 `schema_meta`。
- `connect_read_write` 只打开已经存在且版本匹配的数据库，并只用于短写事务。writer 启用 foreign keys、busy timeout、rollback-journal `DELETE` 和 `synchronous=FULL`。这优先保障本地研究数据耐久性，并让普通只读打开不创建 WAL/SHM；单 writer 与短事务控制锁等待。一致备份仍必须使用 SQLite backup API，不能复制活动数据库文件。
- `connect_migration` 是唯一允许创建数据库或修改 schema 的连接；新数据库先以 `0600` 权限认领，再执行事务化 bootstrap。
- `connect_external_read_only` 只读打开外部 SQLite，供 Zotero 诊断使用；它不会创建 Paper Galaxy schema，也不会写入 Zotero。

只读连接会拒绝符号链接或不完整的 WAL/SHM sidecar，并检查数据库 header：若旧项目处于 WAL 模式但 sidecar 已消失，则拒绝打开，避免一次“读取”反而创建文件。Paper Galaxy writer 可把项目安全归一化为 DELETE/FULL；外部 Zotero 数据库绝不会被归一化，只会提示用户打开 Zotero Desktop。若活动 WAL 与已有 SHM 均有效，SQLite 可能更新 SHM 协调字节；`query_only` 仍会阻止数据与 schema 写入，且不创建新目录项。

SQLite 中的 JSON 现在按预期形状严格解码：可空 list/object 字段有明确空默认值；损坏 JSON、重复 key、NaN/Infinity、要求容器时得到 scalar、或嵌套类型不匹配都会抛出结构化 `StoredJSONError`，不再被静默吞掉并替换为空值。普通 Web API 还会过滤 Zotero raw payload、本地路径和内部配置。

项目验证通过诊断专用只读连接运行，检查 SQLite `quick_check`、`foreign_key_check`、表/列/PK/UNIQUE/FK/索引/FTS 能力、migration history、FTS 与 documents/chunks/text 的一致性，以及向量的 target、active 状态、source hash、模型 fingerprint、algorithm、dimension、dtype、BLOB、有限浮点和 index provenance；还会检查 Zotero cursor、parent/collection/attachment version、child manifest 和 filter profile 一致性。无法执行的检查会明确标记为 `not_run` 或 `check_errors`，不会伪装成计数为零的成功状态。

### 备份归档与恢复边界

备份不直接复制项目文件，而是分成三个信任边界：

1. `backup.snapshot` 通过 `sqlite3.Connection.backup()` 把包含 active WAL 已提交
   pages 的一致视图写入 mode-`0600` staging 文件。快照会规范成 rollback-journal，
   并通过 `quick_check`、`foreign_key_check`、声明版本与 schema capability registry。
2. `backup.archive` 把所有 ZIP 当作不可信输入，拒绝重复/不可移植名称、special 或
   symlink entry、checksum 缺失/额外/重复、异常压缩方式、entry 数量、展开大小、
   压缩比，以及 manifest、payload、schema 不一致。摘要和解压都采用有界流式读取；
   path/component、project config、archive size 与 free-space 预算同时限制内存、CPU
   和磁盘消耗。
3. `backup.publish` 把导出 staging 放在输出旁，把恢复 staging 放在目标文件系统。
   导出只对完整校验后的文件执行一次 `os.replace`；新项目以完整目录 rename 发布；
   强制恢复先把旧文件移入仅属主可访问的 rollback transaction，最后发布配置，失败
   时恢复原 inode。持久 prepared/committed journal 记录 original/new digest，因此
   进程中断后也能恢复，而不只处理同一进程内的 exception。

备份在读取所有输入期间持有 shared project lock；恢复从 interrupted transaction
recovery、preflight、校验到发布一直持有 exclusive maintenance marker。marker 建立前
的旧连接通过 legacy DB file lock drain，新连接统一使用稳定的 build-owned marker。
prepared sibling transaction 本身也会阻断普通 connection，直到恢复完成。Dry run 不创建
marker。私有 staging root 带版本化 operation/target/PID ownership metadata；后续同类
操作只保守清理由失效 PID 留下的已认领 orphan。
PID 存活检查在 Windows 上使用无破坏进程句柄，绝不会调用 POSIX 风格的
`os.kill(pid, 0)`。

v2 manifest 明确记录项目相对数据库与向量索引目标。项目内部自定义数据库路径可原位
round-trip；绝对或逃逸路径会映射到内部默认位置，项目外向量索引会被省略。恢复目标
绝不直接取自未经检查的浏览器输入或 ZIP filename。旧 v1 配置和数据库仍可读取，但其
basename-only 向量索引因无法证明逻辑路径而不会自动恢复。

### 短写事务与运行审计

索引的文件发现、stat、hash、抽取和 chunk 准备发生在 SQLite 写事务之外；每个文件结果、抽取诊断或 document/chunk replacement 使用短原子事务。单文件抽取失败会被记录并允许其他文件继续；编排或 JSON sidecar 输出失败会把 run 标为 `failed`，中断则标为 `interrupted`，并保存安全且有长度上限的错误类型与信息。显式 writer readiness 还会检查未完成 scan、embedding、Zotero run 的 owner PID：死亡进程遗留行在短事务中转为 `interrupted`，存活进程不会被改写，只读连接也不会执行恢复写入。

Embedding inference 与 vector encoding 同样发生在写事务之外，验证后的 vectors 按有界 batch 提交；每批落库前再次比较 document/chunk source revision，推理期间已变化的来源会丢弃结果并计入 `sources_changed`。document revision 以带算法命名空间的 canonical JSON array 编码 title、corpus-relative path 与完整提取正文，字段中的 NUL 等边界字符不会产生拼接碰撞，也不会因原文件 bytes 未变而误复用旧提取结果；chunk revision 使用精确 chunk text。后续 batch 失败时，audit 计数只反映已经 commit 的进度。文档或 chunks 被替换/停用时会删除对应 vectors；任何 vector 写入都会失效相关 vector-index metadata；完全相同的 Zotero sync 则保留 chunks/vectors。Zotero 网络获取、规范化和 PDF 抽取不会包在一个长写事务内：run 在 fetch 前登记，条目以短事务提交，source cursor 只在全部成功后的最后事务推进。fetch、规范化、条目或进程中断都会进入 audit；导入完成后的 reading-map 构建失败只产生可重试 warning。回退、同版本内容分叉或部分 parent/collection/attachment/note/annotation 响应会失败并 rollback，cursor 不推进。v6 升级后的未知 child 状态必须通过显式 `--force` 完整子抓取建立基线；在后续增量 checkpoint 加入 tombstone 前，child 遗漏不会被当作删除。

保存地图的普通 API 与 export 共用 maps 领域层的深层白名单；旧数据中的嵌套 raw JSON、绝对路径、内部 metadata 和原始 warnings 不会跨越该边界。

## Phase 3 本地网页应用

`paper-galaxy serve --project-dir .` 启动本地浏览器应用。默认绑定到 `127.0.0.1`，读取本地 SQLite，并服务本地 HTML/CSS/JavaScript。所有 Web GET 都使用 read-only connection，不能 bootstrap 或迁移数据库。missing、locked、corrupt、需要 migration 和 future schema 会返回结构化安全错误；普通 health/config/data 响应不暴露绝对 project、database、source、attachment 或 model path，详细本地路径只保留给显式 CLI 诊断。

主要 API：

- `/api/health`：健康状态和数据库存在性。
- `/api/stats`：本地数据库统计。
- `/api/map`：active 文档、主题簇、邻居、解释元数据和初始坐标。
- `/api/search`：本地搜索。
- `/api/documents`：文档列表。
- `/api/documents/{id}`：文档元数据、文本块预览和邻居。
- `/api/clusters`：主题簇标签、代表文档和证据词项。
- `/api/map-runs`：保存的地图运行。
- `/api/zotero/status`：Zotero 导入状态、计数和最近一次导入运行。
- `/api/zotero/items`：已导入 Zotero 条目列表。
- `/api/zotero/item/{id}`：已导入 Zotero 条目详情。
- `/api/zotero/reading-map`：由已导入 Zotero 记录生成的阅读图谱。

前端是无构建步骤的 vanilla JavaScript。动态图谱的节点拖拽、布局偏好和图谱显示设置只保存在浏览器 `localStorage`，不会写回 SQLite。

## Zotero Reading Graph

Zotero 支持把 Zotero Desktop 只读导入 Paper Galaxy 本地项目：

```text
Zotero Desktop local API
  -> 规范化 Zotero 条目、集合、笔记、annotations 和附件
  -> 解析本地附件路径但不复制 PDF
  -> 写入 Zotero 元数据和 Paper Galaxy 文档
  -> 建立文本块和 FTS 索引
  -> 保存 "Zotero Reading Graph" 地图运行
  -> 本地网页应用的 Zotero 图谱来源
```

主要路径是 Zotero 本地 API。直接读取 `zotero.sqlite` 只用于只读诊断和路径提示。Paper Galaxy 不写回 Zotero，不上传 Zotero 数据，也不会默认复制 PDF。

导入的 Zotero 条目会根据 source 和 Zotero key 生成稳定 ID。metadata-only 条目使用 `zotero://items/<key>` 作为文档路径。可读本地 PDF 的抽取文本和文本块会存入 Paper Galaxy SQLite，但原 PDF 文件保持在 Zotero 原位置。

真实库 beta 增加了 `paper-galaxy zotero doctor` 不写入 readiness 检查、按 key/name/path 解析 collection、校验 reading-status 过滤、本地 user-library 校验，以及 PDF 抽取、metadata-only 或跳过缺失 PDF 的显式策略。

## Phase 4 抽取质量

Phase 4 改进 PDF/Markdown/LaTeX 抽取，加入扫描 PDF 检测、抽取 warning 和可选本地 OCR。抽取报告存储在 SQLite 中，也可以写出本地 JSON sidecar，但不会包含完整抽取文本。

OCR 仍然是可选、本地、默认关闭的。

## Phase 5 语义 embeddings

Phase 5 增加可选本地 dense embeddings。向量存储在 SQLite 中；本地模型 fingerprint 覆盖相对文件布局、长度与精确字节，并在加载前后复核，不能只靠路径或名称识别权重。向量同时保存 source hash、模型 fingerprint 与 algorithm version。

远程模型名默认被拒绝，避免隐藏下载。用户必须提供本地模型路径，或显式使用 `--allow-model-download`。

语义搜索只流式读取 active 且 provenance 与当前 source/model 一致的向量，使用 NumPy 有界 block 计算精确 top-k，并一次批量读取展示 metadata，不再逐向量 N+1 查询。neighbor comparison 使用 SQLite `data_version` 做有界乐观快照重试，索引并发提交时不会把旧 TF-IDF text 与新 dense metadata 混为一个结果。当前实现不宣称或安装未实际维护的 FAISS 路径。

`paper-galaxy prune-stale-vectors --project-dir .` 默认只读报告 stale/orphan/invalid vector；只有同时传入 `--apply --yes` 才删除对应 SQLite vector 与 index metadata，绝不删除源论文、项目数据库、备份或用户文件。

## Phase 6 可解释性与标签

Phase 6 从本地词项生成主题簇标签，保存稳定的 cluster signature，并允许用户在 SQLite 中手动重命名主题簇。

`paper-galaxy explain-pair` 和网页 inspector 使用共享 TF-IDF 词项及短文本块摘录解释两个文档为什么相近。不使用 LLM，也不打印完整抽取文本。

## Phase 7 专业化

Phase 7 增加项目验证、保存的 TF-IDF 地图运行、本地备份导入/导出、静态内置插件边界和 Python distribution 构建。

保存的地图运行是 SQLite 快照。它们可以作为网页应用中的初始图谱运行来源；用户在浏览器中拖拽固定节点仍然只影响 `localStorage`。

## 公开静态演示站

GitHub Pages 演示由 `scripts/build_demo_site.py` 从合成 tiny corpus 生成。输出是静态站点，不连接后端，不包含本地 SQLite 数据库，也不读取用户文档。

检查脚本：

```bash
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist
python scripts/public_readiness_check.py --strict
```

## 公开发布检查

`scripts/public_readiness_check.py --strict` 会检查仓库中是否混入常见 secrets、本地生成数据、SQLite 数据库、`.paper-galaxy/`、演示站远程运行时依赖等风险。

Post-public launch activation 增加了 `scripts/check_live_site.py` 用于验证已部署的 GitHub Pages 站点，并增加 `scripts/launch_report.py` 生成简洁的本地发布报告。这些脚本只检查静态页面和仓库状态，不增加 analytics、遥测、云运行时或托管后端代码。

## 未来云库边界

未来个人云库目前只是设计文档，不是当前 runtime。任何云功能都必须 opt-in，并且不能破坏本地优先路径。

当前本地应用没有账号、云同步、托管后端、遥测、文档上传、远程插件加载或 React/Node 构建链。

## 本地数据边界

本地项目状态保存在 `.paper-galaxy/`。该目录可能包含抽取文本、文本块、向量、标签、地图运行和备份相关元数据，因此不应提交到 git，也不应粘贴到公开 issue。
