# 常见问题

[English](FAQ.md) | 简体中文

## 我的数据会被上传吗？

不会。Paper Galaxy 默认本地优先。本地应用读取你的文件和本地 SQLite 数据库，没有自动上传、遥测、账号系统或托管后端。

## 需要账号吗？

不需要。当前应用可以完全本地运行。

## 支持哪些文件？

扫描器支持文本、Markdown、LaTeX、通过可选 `pypdf` 支持的基础 PDF，以及只有在显式开启图片扫描/OCR 时才处理的图片文件。

## 为什么 OCR 需要 Tesseract？

OCR 是可选且本地的。Python extra 会安装 wrapper，但实际图片 OCR 可能需要用户自己安装本地 Tesseract 二进制。

## Embeddings 是本地的吗？

是的。Embedding 命令是显式本地 workflow。默认拒绝远程模型名以避免隐藏下载；请提供本地模型路径，或显式使用 `--allow-model-download`。

## 公开演示使用真实论文吗？

不使用。GitHub Pages 公开演示只使用 `examples/tiny_corpus` 中的合成样例内容。

## 可以和 Zotero 一起用吗？

目前不可以。Zotero 集成没有在公开 alpha 中实现。

## 可以用云同步吗？

不可以。云同步尚未实现。个人云库文档只是 design-only，描述未来可能的 opt-in 方向。

## 数据库在哪里？

默认项目状态存储在 `.paper-galaxy/` 下，包括 `.paper-galaxy/paper_galaxy.sqlite3`。

## 如何删除本地项目状态？

删除该项目的 `.paper-galaxy/` 目录。这会删除该项目的 Paper Galaxy 元数据、抽取文本、文本块、向量、标签和保存的地图运行。

## 如何报告 bug 又不泄露私人论文文本？

使用合成文件、最小化脱敏示例、不包含私人文本的命令输出，以及遮挡敏感信息的截图。不要上传 `.paper-galaxy/`、SQLite 数据库、抽取片段、API key 或私人本地路径。
