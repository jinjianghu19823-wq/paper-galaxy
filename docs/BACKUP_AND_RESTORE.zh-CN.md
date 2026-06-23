# 备份与恢复

[English](BACKUP_AND_RESTORE.md) | 简体中文

英文版仍是规范版本；本文件提供简体中文说明。

Paper Galaxy 的备份命令只处理本地项目状态。默认不会包含源文档。

## 导出

```bash
paper-galaxy export-project --project-dir . --out paper-galaxy-backup.zip --yes
```

备份包包含 manifest、校验和、项目元数据，以及用户确认后的本地 SQLite 数据库。SQLite 可能包含抽取文本、文本块、向量、标签和地图运行，因此备份 zip 应视为敏感数据。

## Dry run 导入

```bash
paper-galaxy import-project paper-galaxy-backup.zip --project-dir /path/to/restore --dry-run
```

dry run 会验证备份包并展示计划写入内容，不会实际写入。

## 导入

```bash
paper-galaxy import-project paper-galaxy-backup.zip --project-dir /path/to/restore
```

如果目标已经存在 `.paper-galaxy/`，导入会拒绝覆盖，除非显式传入 `--force`。

## 验证恢复项目

```bash
paper-galaxy validate-project --project-dir /path/to/restore
paper-galaxy db-stats --project-dir /path/to/restore
paper-galaxy map-runs --project-dir /path/to/restore
```

不要把备份 zip、`.paper-galaxy/` 或 `*.sqlite3` 提交到 git。
