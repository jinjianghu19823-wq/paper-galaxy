# 安全政策

[English](SECURITY.md) | 简体中文

Paper Galaxy 目前还没有正式的支持版本窗口。英文版仍是规范版本；本文件提供简体中文说明。

## 报告安全问题

请先私下向仓库所有者报告安全问题，再打开公开 issue。报告时尽量包含受影响的命令、输入类型，以及问题是否涉及本地文件、SQLite 数据、浏览器 `localStorage` 或备份包。

## 本地数据边界

Paper Galaxy 设计为本地运行。默认情况下，它不应该上传文档、发送遥测、加载远程前端资源，或加载远程插件。备份包可能包含本地 SQLite 数据库，因此应当把它们视为敏感项目数据。

公开 GitHub Pages 演示是静态的，并且只使用合成数据。它不应包含用户文档、本地数据库、模型文件、私人路径或远程运行时资源。公开仓库或发布前请运行：

```bash
python scripts/public_readiness_check.py --strict
```
