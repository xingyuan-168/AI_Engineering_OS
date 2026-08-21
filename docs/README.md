# Codex AI Engineering OS 文档中心

状态：可执行文档基线
基线：Codex AI Engineering OS 最终需求规格说明书 V2.0（完整会话整理 + 文档治理增强版）

## 文档定位

本目录是 Codex AI Engineering OS 的项目事实来源。实现、评审、发布和经验沉淀均以这里的文档为准；代码、配置和 Workflow 不得与文档长期不一致。

## 文档地图

| 文档 | 作用 | 维护时机 |
| --- | --- | --- |
| [PROJECT_MASTER.md](PROJECT_MASTER.md) | 项目总览、架构、模块、流程和治理规则 | 目标、架构或流程发生变化时 |
| [SCOPE.md](SCOPE.md) | MVP 边界、非目标、约束和成功标准 | 需求进入或退出范围时 |
| [OPEN_SOURCE_RESEARCH.md](OPEN_SOURCE_RESEARCH.md) | 各能力板块的开源项目核心提取和复用决策 | 引入、升级或淘汰外部项目时 |
| [TECH_STACK.md](TECH_STACK.md) | 技术栈、接口约定、运行环境和安全基线 | 技术选型或运行环境变化时 |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | 分阶段实施计划、验收门禁和试点方案 | 每个阶段开始和结束时 |
| [RUNTIME_SPEC.md](RUNTIME_SPEC.md) | 运行时生命周期、恢复、幂等和 Windows 约束 | 运行时行为变化时 |
| [CONFIG_SPEC.md](CONFIG_SPEC.md) | 项目、Workflow、Skill、Agent 和执行策略配置契约 | 配置字段或覆盖规则变化时 |
| [WORKFLOW_SPEC.md](WORKFLOW_SPEC.md) | 状态机、V1 Workflow 和 G0-G4 门禁 | 流程、状态或门禁变化时 |
| [SKILL_SPEC.md](SKILL_SPEC.md) | Skill 输入、输出、权限和完成定义 | Skill 能力或权限变化时 |
| [AGENT_SPEC.md](AGENT_SPEC.md) | Agent 角色、任务、Worktree 和 Review 契约 | Agent 角色或协作规则变化时 |
| [EXECUTION_POLICY.md](EXECUTION_POLICY.md) | Docker/Podman 沙箱、命令、挂载、网络和资源策略 | 执行安全策略变化时 |
| [OBSERVABILITY.md](OBSERVABILITY.md) | 日志、事件、指标、诊断和审计保留 | 事件或审计策略变化时 |
| [MIGRATION_SPEC.md](MIGRATION_SPEC.md) | SQLite Schema、迁移、备份和恢复 | 数据结构或迁移方式变化时 |
| [MEMORY_SPEC.md](MEMORY_SPEC.md) | 长期记忆分类、来源、检索、失效和跨项目隔离 | Memory 生命周期或索引变化时 |
| [PLUGIN_SPEC.md](PLUGIN_SPEC.md) | Codex Host、Plugin 与本地 Runtime 的边界、Hook 和兼容性 | 宿主、Plugin 或调用协议变化时 |
| [WORKTREE_SPEC.md](WORKTREE_SPEC.md) | Worktree/Branch 创建、权限、合并、冲突和清理 | 并行 Agent 或 Git 隔离策略变化时 |
| [AGENT_HANDOFF.md](AGENT_HANDOFF.md) | Agent 交接包、角色交付、hash 校验和阻塞规则 | 任务交付或角色协作变化时 |
| [WORKFLOW_ROUTING_RULES.md](WORKFLOW_ROUTING_RULES.md) | 可解释 Workflow 评分、Profile 组合和人工覆盖 | 路由规则或复杂度模型变化时 |
| [BOUNDARY_SPEC.md](BOUNDARY_SPEC.md) | 模块职责、权限归属和越权冲突处理 | 模块边界或权限策略变化时 |
| [design/UX_RESEARCH.md](design/UX_RESEARCH.md) | 用户画像、场景、痛点、研究假设和设计映射 | UX 研究结论或主要场景变化时 |
| [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) | Windows、Plugin、沙箱和发布候选物检查 | 发布前和发布流程变化时 |
| [PILOT_ACCEPTANCE.md](PILOT_ACCEPTANCE.md) | ERP 采购模块端到端验收场景 | 试点范围或验收标准变化时 |
| [PRODUCT_REQUIREMENTS.md](PRODUCT_REQUIREMENTS.md) | 产品目标、用户、功能和验收标准 | 需求分析和需求变更时 |
| [USER_STORY.md](USER_STORY.md) | 用户故事和可验证场景 | 需求拆解时 |
| [BUSINESS_RULES.md](BUSINESS_RULES.md) | 业务规则、约束和例外 | 规则确定或变更时 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 模块、数据流、部署和边界 | 架构设计或重大演进时 |
| [API_SPEC.md](API_SPEC.md) | 命令/API、参数、返回、错误和权限 | 接口变更时 |
| [DATABASE.md](DATABASE.md) | 数据结构、索引、迁移和保留策略 | 数据模型变更时 |
| [SECURITY.md](SECURITY.md) | 命令、文件、凭据、依赖和发布安全 | 安全策略变更时 |
| [TEST_PLAN.md](TEST_PLAN.md) | 测试策略、测试矩阵和质量门禁 | 测试策略或风险变化时 |
| [DEPLOYMENT.md](DEPLOYMENT.md) | 本地运行、打包和发布流程 | 运行/发布方式变化时 |
| [CHANGELOG.md](CHANGELOG.md) | 面向用户和维护者的变更记录 | 每次可交付变更时 |
| `ADR/` | 重大技术决策及其后果 | 重大决策确认时 |

## 统一规则

1. 开发前读取与任务相关的文档，至少读取 `SCOPE.md`、`PROJECT_MASTER.md`、`BOUNDARY_SPEC.md`、`CONFIG_SPEC.md` 和对应领域文档。
2. 需求变更必须先做影响检查，再修改代码；影响 API、数据库或架构时必须更新相关文档。
3. 不使用 `v1`、`v2`、`new`、`final`、`backup`、`copy` 等复制目录做版本管理，版本由 Git 分支、提交和标签管理。
4. 重大技术决策写入 `ADR/`，失败方案也要记录，避免长期记忆丢失。
5. 涉及 UI 的项目必须先完成 `docs/design/` 下的流程、原型、UI 规范、组件和设计 Token 文档。
6. 进入实现前必须按 `SCOPE -> PROJECT_MASTER -> BOUNDARY_SPEC -> CONFIG_SPEC -> WORKFLOW_ROUTING_RULES -> WORKFLOW_SPEC -> SKILL_SPEC/AGENT_SPEC/AGENT_HANDOFF -> WORKTREE_SPEC -> PLUGIN_SPEC -> EXECUTION_POLICY -> 领域文档 -> TEST_PLAN/PILOT_ACCEPTANCE` 顺序读取相关文档；涉及长期知识时追加读取 `MEMORY_SPEC`，涉及界面时先读取 `design/UX_RESEARCH`。
7. 当前实现宿主为 Codex Host + Codex Plugin + Windows 本地 CLI Runtime。DeepSeek Harness 仅保留未来 `HarnessAdapter` 兼容接口，不属于 V1 验收范围。
8. 仓库操作必须遵循根目录 [AGENTS.md](../AGENTS.md) 的事实源、验证和 Git 事务规则。

## 当前状态

- 需求基线：已从 V2.0 需求文档整理。
- 文档状态：本批次文档已补齐并通过结构审计；实施前如发生范围变化，必须按变更规则更新。
- 技术栈状态：见 [TECH_STACK.md](TECH_STACK.md)；运行时基线见 [ADR-0001](ADR/ADR-0001-mvp-runtime-stack.md)，契约收敛见 [ADR-0002](ADR/ADR-0002-v1-runtime-contract-convergence.md)。
- 实施状态：见 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。
- 实现契约：已补齐运行时、配置、Workflow 路由、Skill、Agent 交接、Plugin、Worktree、边界、Memory、执行、可观测性、迁移、发布和试点验收文档。
