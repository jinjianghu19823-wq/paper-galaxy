# 早期反馈指南

[English](FEEDBACK.md) | 简体中文

Paper Galaxy 现在是 public alpha。最有用的早期反馈应该小、可复现，并且不泄露隐私。

## 建议先试这些任务

1. 打开公开演示：
   <https://jinjianghu19823-wq.github.io/paper-galaxy/>
2. 本地安装：
   `python -m pip install -e ".[dev,ml,pdf,app]"`
3. 索引 tiny corpus：
   `paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40`
4. 启动本地 app：
   `paper-galaxy serve --project-dir .`
5. 索引一个不含敏感文本的小型个人测试文件夹。
6. 运行验证：
   `paper-galaxy validate-project --project-dir .`
7. 尝试解释：
   `paper-galaxy explain-pair SOURCE TARGET --project-dir .`

## 有用的反馈

- 抽取质量和令人困惑的 skipped-file 原因。
- 图谱可用性、标签重叠或难以理解的邻居链接。
- 令人困惑或太啰嗦的命令。
- 特定 Python/系统版本上的安装问题。
- 隐私担忧或不清楚的本地数据边界。
- 文档缺口。

## 不要分享

- 私人论文文本或敏感抽取片段。
- `.paper-galaxy/` 目录或 SQLite 数据库。
- API key、token、secret 或私钥。
- 会暴露敏感姓名或机构的本地路径。
- 备份包，除非它是专门准备的合成数据。

## 去哪里反馈

使用 issue templates：
<https://github.com/jinjianghu19823-wq/paper-galaxy/issues/new/choose>

如果 bug 依赖私人文本，请创建一个能复现同类失败形状的 tiny synthetic 文件。
