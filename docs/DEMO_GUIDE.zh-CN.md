# 公开演示指南

[English](DEMO_GUIDE.md) | 简体中文

公开演示是一个静态 GitHub Pages 站点：
<https://jinjianghu19823-wq.github.io/paper-galaxy/>

## 它如何工作

演示由合成 `examples/tiny_corpus` fixture 生成。构建脚本会在临时项目中索引该语料，生成 TF-IDF 地图 payload，移除本地路径和数据库细节，并把静态 JSON 写入 `site/data/tiny-map.json`。

## 哪些内容是合成的

所有演示文档都是关于 neural operators、numerical PDEs、randomized linear algebra 和 thesis ideas 的合成笔记。不包含用户论文或私人文档。

## 模拟功能与真实功能

公开演示包含静态图谱、主题簇图例、文档 inspector 和预计算解释片段。它不运行 FastAPI 后端，不修改 SQLite 数据库，不重新索引文件，也不读取本地文档。

安装后的本地 app 可以读取你的本地项目数据库，运行本地搜索，展示文档分块，使用保存的地图运行，重命名主题簇，并从你的索引语料中检查 pair explanations。

## 本地复现演示

```bash
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist --serve
```

然后打开检查命令打印的本地 server URL，或查看生成的 `site_dist/` 目录。不要提交 `site_dist/`；它是生成产物。
