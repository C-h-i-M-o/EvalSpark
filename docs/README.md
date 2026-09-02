# 文档目录说明

本文档用于区分当前仍在维护的权威文档、React 替代 Vue 的历史重构文档和 v2 历史归档文档。

## 当前权威文档

这些文档描述当前系统已经实现并继续维护的后端、数据库、接口和 React 主前端：

- `architecture.md`：系统架构、核心流程和 React 前端展示层。
- `api.md`：后端接口说明。
- `database.md`：数据库表结构和持久化说明。
- `system-features-status.md`：系统功能状态和后续维护建议。
- `docker-development-spec-plan.md`：全栈 Docker 开发环境的需求、方案、实施步骤和验收标准。
- `v3-rag-spec-plan.md`：V3 RAG 需求与实施合并文档，包含已批准设计、分阶段提交和实际验收；计划中的后续能力不代表已实现。
- 阶段 2 知识库 API 见 `api.md`；新表与业务迁移门禁、独立 `docker-compose.rag-test.yml` 测试方式见 `database.md`。测试配置不连接业务库。
- `uml.md`：当前实现对应的图示文档。
- `open-source-reuse.md`：开源项目参考和复用说明。
- `demo-v1.3-development-plan.md`：demo-v1.3 六阶段开发计划和验收目标。

## React 历史重构文档

React 替代 Vue 的阶段计划、架构和验收资料统一保留在 `react-rewrite/`：

- `react-rewrite/README.md`：React 替代 Vue 的历史文档入口。
- `react-rewrite/plan.md`：React 前端重构计划和阶段落地记录。
- `react-rewrite/architecture.md`：React 前端架构、模块边界和数据流。
- `react-rewrite/acceptance.md`：React 替代完成时的验收清单。

## v2 历史阶段设计

v2 已冻结，原 v2 开发计划已废除并移除。`legacy-v2/` 仅保留已落地阶段的设计说明，用于回看认证、权限、模型配置、计费和额度等历史决策，不再作为当前开发目标：

- `legacy-v2/stage1-auth-rbac-design.md`
- `legacy-v2/stage2-3-model-billing-quota-design.md`

## 维护规则

- 当前功能状态优先写入 `system-features-status.md`；React 替代过程资料继续保留在 `react-rewrite/`。
- 已废除的 v2 开发计划不要继续引用或改写成当前目标。
- 接口变化同步 `api.md`。
- 数据库变化同步 `database.md`。
- 架构或主流程变化同步 `architecture.md` 和 `system-features-status.md`。
- 评分规则、Judge Prompt 或评分状态变化需要同步 `api.md`、`database.md`、`architecture.md` 和 `system-features-status.md`。
