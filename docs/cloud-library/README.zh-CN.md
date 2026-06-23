# 个人云库

[English](README.md) | 简体中文

本目录描述 Paper Galaxy 未来可能提供的 opt-in 个人云库。它目前只是设计文档。当前代码库没有实现云 runtime、托管后端、账号系统、同步 worker、storage SDK、支付代码或遥测。

Cloud sync is not implemented in this repository.

英文版和各子文档仍是规范版本；本文件提供简体中文总览。

## 原则

- 本地优先仍然是默认模式。
- 用户不需要账号也可以使用 Paper Galaxy。
- 除非用户显式启用云库功能，否则不会上传。
- 本地 app 代码和云同步代码必须清楚分离。
- 源文档默认绝不公开共享。
- 任何公开云发布前都必须有导出和删除控制。
- 自托管应该继续是一等设计选项。

## 候选方案

1. 只做备份的云 vault：上传加密项目备份包。这是最安全的第一步，因为它避免 live merge 逻辑。
2. 元数据同步：同步项目 manifest、标签、地图运行和选定元数据；源文档仍留在本地，除非用户显式备份。
3. 完整托管库：托管索引、搜索、图谱生成和存储。这带来最高的隐私与运维风险，不应作为第一版云发布。

建议路径是先做 C1 加密备份 vault，再在安全审查和用户测试后考虑 C2 元数据同步。

## 下一审查目标

C1 加密备份 vault 是下一步设计审查目标。除非未来有单独明确的实现阶段请求，否则它仍然只是 design-only。任何未来实现都必须保留无需账号的本地优先用法，并在公开发布前提供清晰的导出/删除控制。

## 子文档

- [Product spec](PRODUCT_SPEC.md)
- [Architecture](ARCHITECTURE.md)
- [Privacy and security](PRIVACY_AND_SECURITY.md)
- [API sketch](API_SKETCH.md)
- [Data model](DATA_MODEL.md)
- [Roadmap](ROADMAP.md)
- [Threat model](THREAT_MODEL.md)
