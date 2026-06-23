# 个人云库设计

[English](CLOUD_LIBRARY_DESIGN.md) | 简体中文

个人云库是未来的 opt-in 设计，当前 Paper Galaxy runtime 中尚未实现。英文版仍是规范版本；本文件提供简体中文说明。

Cloud sync is not implemented in this repository.

Paper Galaxy 默认仍然本地优先：

- 现在不需要账号；
- 默认不上传文档；
- 本地索引、搜索、地图生成、标签、备份和验证在没有云服务时仍然有用；
- 未来任何云功能都必须显式启用，并提供导出和删除控制。

详细设计包：

- [中文总览](cloud-library/README.zh-CN.md)
- [English overview](cloud-library/README.md)
- [Product spec](cloud-library/PRODUCT_SPEC.md)
- [Architecture](cloud-library/ARCHITECTURE.md)
- [Privacy and security](cloud-library/PRIVACY_AND_SECURITY.md)
- [API sketch](cloud-library/API_SKETCH.md)
- [Data model](cloud-library/DATA_MODEL.md)
- [Roadmap](cloud-library/ROADMAP.md)
- [Threat model](cloud-library/THREAT_MODEL.md)

建议分阶段路径：

1. C0：仅设计。
2. C1：加密备份 vault。
3. C2：元数据同步。
4. C3：可选托管计算。
5. C4：只有后续明确要求时，才考虑团队或协作功能。

当前仓库没有实现云运行时、托管后端、账号系统、同步 worker、storage SDK、支付代码或遥测。
