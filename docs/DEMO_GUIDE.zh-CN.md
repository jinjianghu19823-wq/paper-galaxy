# 公开演示指南

[English](DEMO_GUIDE.md) | 简体中文

公开演示是一个静态 GitHub Pages 站点：
<https://jinjianghu19823-wq.github.io/paper-galaxy/>

## 它如何工作

演示由合成 `examples/tiny_corpus` fixture 生成。构建脚本会在临时项目中索引该
语料，生成 TF-IDF 地图 payload，移除本地路径和数据库细节，并在输出目录旁的
唯一 staging 目录完成整站构建。只有 staging 校验通过后，才用可崩溃恢复的
sibling rename 发布到目标目录；默认情况下只写 `site_dist/data/tiny-map.json`。

已有输出只有在为空，或带受支持的 `.paper-galaxy-demo-build.json` ownership
marker 时才可替换。非空未认领目录、symlink 路径分量、filesystem root、用户
home、仓库根目录、Git metadata、site source 和 corpus 都会被拒绝。构建或发布
失败会保留旧输出；中断状态可在下次运行安全恢复，不会暴露半成品站点。在 macOS
上，root-owned 的系统 `/tmp` 别名会规范为 `/private/tmp`；用户创建的 symlink
仍然一律拒绝。

## 哪些内容是合成的

所有演示文档都是关于 neural operators、numerical PDEs、randomized linear algebra 和 thesis ideas 的合成笔记。不包含用户论文或私人文档。演示不包含真实 Zotero 数据库、Zotero storage 文件夹、PDF、本地 Zotero 路径或 `zotero://items/...` 记录。

## 模拟功能与真实功能

公开演示包含静态图谱、主题簇图例、文档 inspector 和预计算解释片段。它不运行 FastAPI 后端，不修改 SQLite 数据库，不重新索引文件，也不读取本地文档。

安装后的本地 app 可以读取你的本地项目数据库，运行本地搜索，展示文档分块，使用保存的地图运行，重命名主题簇，并从你的索引语料中检查 pair explanations。你在本机显式从 Zotero Desktop 导入后，它也可以显示 Zotero Reading Graph。

## 本地复现演示

```bash
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist --serve
```

然后打开检查命令打印的本地 server URL，或查看生成的 `site_dist/` 目录。不要
提交 `site_dist/`；它是生成产物。默认构建不会修改 `site/`。公开 artifact 中的
浮点必须有限、统一为小数点后最多八位，并用稳定 UTF-8 JSON 设置序列化。如果本地
仍有旧的无 marker `site_dist/`，先运行仅清构建产物的 `make clean-build`；不要把
`--out` 指向个人数据或项目数据目录。

只有在明确、已 review 的 payload 变更需要更新 committed source fixture 时，才
显式运行一次：

```bash
python scripts/build_demo_site.py --out site_dist --refresh-source-data
git diff -- site/data/tiny-map.json
```

CI、Pages、release checks 和普通 demo 构建不得使用
`--refresh-source-data`。

本次已为八位数值契约和 canonical cluster 顺序显式刷新 committed fixture 一次；
后续默认构建必须保持 worktree 不变。
