# 备份与恢复

[English](BACKUP_AND_RESTORE.md) | 简体中文

Paper Galaxy 备份是保留在本机的敏感 ZIP。它永远不包含源论文，但可能包含抽取文本、
chunks、vectors、保存的地图、标签、Zotero 派生状态和可选的向量索引，因此应按原项目
数据的安全级别保管。

## 导出

```bash
paper-galaxy export-project \
  --project-dir . \
  --out paper-galaxy-backup.zip \
  --yes
```

`--yes` 用于明确确认导出本地数据库。活动 SQLite 通过 online backup API 建立一致
快照，包含已提交的 WAL pages；系统不会直接读取活动数据库文件。快照会规范为
rollback-journal，并通过 `quick_check`、`foreign_key_check` 和 schema capability
校验。

导出先在目标旁的唯一 staging 目录构建并完整验证 ZIP，最后才原子替换输出。因此失败
不会截断已有备份，staging 也会被清理。配置、数据库快照和索引收集全过程持有项目
shared lock。已有输出只有在其自身是完整有效的 Paper Galaxy backup 时才会被替换，
任意普通文件不会被静默认领。输出不得与数据库、sidecar、项目配置或被打包的向量索引
互为别名，也不得经过 symlink。在 POSIX 上，备份权限为 `0600`。

向量索引需要显式选择：

```bash
paper-galaxy export-project \
  --project-dir . \
  --out paper-galaxy-backup.zip \
  --include-vector-indexes \
  --yes
```

v2 bundle 保存 archive path 到项目相对 path 的映射，同名索引不会互相覆盖。缺失、
symlink 或项目外部索引不会被跟随进备份。若不包含索引文件，快照会移除可重建的索引
文件元数据，恢复后可在本机重新生成。
索引文件在项目锁内捕获，但 Stage 4 的 content/model fingerprint 才是语义 freshness
依据；无法确认 provenance 时应在恢复后重建索引。

## 检查与 Dry Run

```bash
paper-galaxy import-project \
  paper-galaxy-backup.zip \
  --project-dir /path/to/restore \
  --dry-run
```

检查会拒绝重复 ZIP entry 或 checksum name、未列入 checksum 的额外文件、缺失文件、
摘要不匹配、绝对路径、Windows drive path、反斜杠、`..` traversal、symlink-like 或
特殊 entry、异常 entry 数量/展开大小/压缩比、未知格式、future schema、manifest 与
payload 不一致，以及未通过 SQLite 完整性、外键或 schema 校验的数据库。

CLI 和 import API 都不能关闭 checksum 校验；读取与解压采用有上限的流式处理。
默认预算为 1,024 entries、8 GiB 压缩归档/总展开量、单 entry 4 GiB、200:1 压缩比，
并在解压后保留至少 256 MiB 空间。可移植 path 最多 1,024 UTF-8 bytes、单 component
最多 255 bytes；`project.toml` 另有 1 MiB 内存硬上限。受限环境可通过 Python API
进一步收紧这些值。

## 恢复

```bash
paper-galaxy import-project \
  paper-galaxy-backup.zip \
  --project-dir /path/to/restore
```

恢复只把 manifest 声明的文件解到目标文件系统上的唯一 staging project，先校验配置、
数据库和索引映射，再发布。`state/sql/custom.sqlite3` 一类项目内部相对
`database_path` 会按原相对位置恢复。原配置若指向项目外部绝对数据库，bundle 会将
恢复位置安全映射到 `.paper-galaxy/paper_galaxy.sqlite3`，绝不会向归档中的绝对路径
写入。

新项目用一次目录 rename 安装完整 staging tree。已有 `.paper-galaxy` 必须显式传入
`--force`；强制恢复会先把所有将被替换的旧文件 rename 到仅属主可访问的 rollback
transaction，最后发布配置。任一步失败都会恢复原文件。它不会删除无关源论文或未被
manifest 引用的项目文件。prepared/committed transaction journal 与逐文件 digest
保证进程中断后仍可恢复：下一次真实 import 会先恢复原状态；`--dry-run` 只报告待恢复
事务，不执行恢复，也不创建项目 lock。

真实恢复全过程持有带版本的 exclusive project-maintenance marker。Paper Galaxy 的
只读、writer、migration 与 backup connection 都持有对应 shared lock，因此活动进程
会在发布前收到可操作的拒绝。legacy project 会被安全认领，并在替换前 drain 旧 DB
handle。若报告 maintenance lock 或活动 SQLite sidecar，请关闭使用该项目的进程后重试。
进程被强制终止或断电后，durable transaction 也会阻止所有普通 connection，直到下一次
真实 import 完成恢复。

持久后台 job worker 另有排他 lease。Restore 会在持有 maintenance lock 时检查该
lease，即使工作站 worker 当前空闲也会拒绝恢复，避免排队任务与恢复后状态发生竞态。
真实 import 前请停止 `paper-galaxy launch` 或 `paper-galaxy serve`；dry-run 仍只读。

导出、检查和恢复的 staging root 都是私有目录，并带严格的 operation/target/PID ownership
marker。正常退出会删除；hard kill 后敏感 staging bytes 可能保留到下一次相同操作。
系统只清理 PID 已失效、仅属主可访问且 marker 完全匹配的目录，绝不 glob 删除 lookalike
或未认领目录。

旧 v1 bundle 仍可检查，并可按历史默认路径恢复项目配置和数据库。由于 v1 的向量索引
只有 basename、缺少可靠逻辑映射，这些旧索引文件不会自动恢复。

## 验证恢复项目

```bash
paper-galaxy validate-project --project-dir /path/to/restore
paper-galaxy db-stats --project-dir /path/to/restore
paper-galaxy map-runs --project-dir /path/to/restore
```

不要把备份 ZIP、`.paper-galaxy/`、SQLite 或向量索引提交到 Git。Paper Galaxy 不会
自行加密备份；有保密要求时应使用可信本地磁盘或加密存储卷。
