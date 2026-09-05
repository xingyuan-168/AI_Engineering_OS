# Codex AI Engineering OS 项目总文档

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"review-ready","owner":"product-manager","requirement_refs":["REQ-1.6.2","GOV-001"]} -->

版本：V2.0-derived
状态：可执行文档基线
目标：建设一个个人 AI 软件研发团队式操作系统。

## 1. 愿景与原则

用户负责提出业务目标，系统负责在可控边界内完成完整软件生命周期。系统不是一次性代码生成器，而是由项目管理、设计、执行、质量和长期知识沉淀组成的研发体系。

核心原则：

1. 文档是开发过程的事实来源，不是开发结束后的补充。
2. 先拆解需求和调研开源方案，再决定复用或自研。
3. 复杂任务按角色拆分，Agent 通过产物和事件协作。
4. 每个变更都必须可测试、可 Review、可恢复、可追溯。
5. 关键风险由人确认，低风险步骤由系统自动推进。
6. 失败方案和重大决策必须沉淀，防止重复造轮子。

## 2. 系统架构

```text
用户目标
  -> AI Project Manager
  -> Workflow Routing Decision
  -> Workflow Engine
  -> Skill / Agent 调度 + Agent Handoff
  -> 文档、设计和技术门禁
  -> Execution Manager + Git Worktree
  -> 测试 / Review / Security
  -> Release Candidate
  -> Memory / ADR / CHANGELOG / Audit
```

V1 最终交付由以下部分组成：Windows 本地 CLI 运行时、Codex 宿主适配层、全局 Codex Plugin、项目 `.codex/` 覆盖配置、Skill/Agent/Workflow 资产、Docker/Podman 默认沙箱、SQLite 状态和 Markdown/Git 事实文档。它不是单一 Skill，也不是 ERP 业务系统本身。

宿主边界固定为：

```text
Codex Host
  -> Codex Plugin
  -> Local CLI Runtime
  -> Workflow / Skill / Agent
  -> Execution Policy
  -> Memory / Audit / Release
```

Codex Host 负责模型、Session、Tool Runtime 和宿主交互；AI Engineering OS 负责项目管理、Workflow、文档治理、权限、审批、Memory 和发布门禁。DeepSeek Harness 仅作为未来 `HarnessAdapter`，不改变 V1 的实现边界。

### 模块职责

| 模块 | 职责 | 关键产物 |
| --- | --- | --- |
| AI Project Manager | 理解目标、判断复杂度、选择流程 | 项目清单、初始计划、待确认问题 |
| Workflow Engine | 编排状态、暂停、恢复和重试 | Workflow 状态、检查点、事件日志 |
| Skill System | 提供专业能力 | 分析、设计、测试和发布产物 |
| Agent System | 提供角色协作 | 任务、分支、Worktree 和交付结果 |
| Execution Manager | 受控执行命令和文件操作 | 补丁、命令记录、测试结果 |
| Memory System | 沉淀长期知识并管理来源、置信度和失效 | Memory 记录、ADR、失败记录、索引 |
| Document Manager | 管理文档事实、影响检查和文档索引 | Markdown 变更、完整性报告 |
| Approval Manager | 管理 G0-G4 审批和高风险确认 | 审批记录、阻塞/放行决定 |
| Release Manager | 汇总证据并生成发布候选物 | 候选物、校验和、回滚包 |
| Host Operation Manager | 持久化并对账 Git、OCI 与 GitHub 外部副作用 | intent、租约、尝试、结果和恢复证据 |
| Environment Governance | 编译项目 OCI 环境契约，审计 Compose、依赖、存储和宿主占用 | environment manifest、镜像 digest、重建和持久化证据 |

### 版本矩阵

0.2.1 固定使用需求 `REQ-1.6.2`、软件/CLI/Plugin `0.2.1`、Plugin API/配置/文档/Profile Schema `1.2` 和 SQLite `0007`。1.0/1.1 兼容入口在 0.2.x 内保留并返回弃用 warning；最早只可在 0.3.0 经 ADR 移除。版本事实由 Runtime 唯一版本对象提供，禁止各模块散落硬编码。

## 3. 项目事实来源

所有项目必须建立 [文档中心](README.md) 中列出的 `docs/` 结构。AI 在进入实现阶段前，必须读取与任务相关的文档；文档缺失、冲突或过期时，应先暂停并修复事实来源。

实现契约读取顺序为：`SCOPE.md` -> `PROJECT_MASTER.md` -> `BOUNDARY_SPEC.md` -> `CONFIG_SPEC.md` -> `WORKFLOW_ROUTING_RULES.md` -> `WORKFLOW_SPEC.md` -> `SKILL_SPEC.md`/`AGENT_SPEC.md`/`AGENT_HANDOFF.md` -> `WORKTREE_SPEC.md` -> `PLUGIN_SPEC.md` -> `EXECUTION_POLICY.md` -> 领域文档 -> `TEST_PLAN.md`/`PILOT_ACCEPTANCE.md`；涉及长期知识或界面时分别追加 `MEMORY_SPEC.md` 和 `design/UX_RESEARCH.md`。

## 4. 标准生命周期

### 新项目

目标输入 -> 需求分析 -> 产品需求 -> 范围边界 -> 开源调研 -> 架构设计 -> 技术选型 -> API/数据库设计 -> UI/交互设计（如适用） -> 任务拆分 -> Agent 执行 -> 测试 -> Review -> 安全检查 -> 发布 -> 经验沉淀。

### 功能修改

功能变更开始前必须检查是否影响 API、数据库、架构、ADR 和 CHANGELOG；涉及 UI 时必须检查 User Flow、原型、UI 规范、组件和设计 Token。

### Workflow

V1 支持 `new-project`、`feature-development`、`bug-fix`、`release`；路由可叠加 `frontend-project`、`backend-project` 和 `large-project` Profile，`refactor` 作为后续 Workflow。Workflow 负责选择流程、管理状态和调度能力，不负责替代领域文档。

## 5. 文档治理

`PRODUCT_REQUIREMENTS.md` 定义产品目标、用户、功能和验收标准；`SCOPE.md` 定义做什么和不做什么；`ARCHITECTURE.md` 定义模块、数据流和技术结构；`TECH_STACK.md` 记录技术选型及原因；`API_SPEC.md`、`DATABASE.md` 和 `SECURITY.md` 分别定义接口、数据和安全；`ADR/` 记录重大决策。

禁止使用复制项目和目录重命名实现版本管理。版本、变更和回滚使用 Git Branch、Commit、Tag 和 CHANGELOG。

治理文档自身也受治理：修改指令优先级、Gate、执行/安全策略、角色权限、生命周期、ID 契约或审计留存等语义，必须由 maintenance authority 发起，关联 accepted ADR、独立 Review 和受保护路径证据。仅修复 metadata、链接、错别字或排版且不改变语义时，仍需文档检查和 Review，但不强制新增 ADR。

## 6. 开源复用治理

任何能力板块必须先完成开源候选分析，提取其核心抽象、流程、扩展点和边界，再选择直接使用、二次开发、提取设计思想或自研。详细矩阵见 [OPEN_SOURCE_RESEARCH.md](OPEN_SOURCE_RESEARCH.md)。未核验 License、版本和安全风险的项目不得进入正式技术栈。

## 7. Agent 与 Worktree

复杂项目可启用 Product Manager、Architect、Frontend Engineer、Backend Engineer、Database Engineer、QA Engineer、Security Engineer 和 Reviewer 等角色。每个 Agent 使用独立 Branch/Worktree，完成后必须通过 [AGENT_HANDOFF.md](AGENT_HANDOFF.md) 交付并经 Review 合并；禁止多个 Agent 直接修改同一目录。具体目录和生命周期以 [WORKTREE_SPEC.md](WORKTREE_SPEC.md) 为准。

## 8. 质量与安全门禁

- G0：目标、范围和验收标准明确。
- G1：需求、用户故事、业务规则和产品文档完成。
- G2：架构、技术栈、API、数据库和安全设计完成。
- G3：实现、测试、代码 Review、安全扫描、Plugin/Skill/Hook/MCP、构建安装与真实 OCI 证据全部绑定 integration Commit 并通过。
- G4：候选/final manifest、SBOM、checksums、rollback、Release Review、Memory 与 PR/tag/assets 对账完成；发布授权先持久化，再执行外部副作用。

需求冻结、架构冻结、外部依赖、破坏性迁移、敏感数据处理和生产发布必须人工确认。命令和文件操作必须受 Execution Manager 策略约束。模块职责和越权冲突以 [BOUNDARY_SPEC.md](BOUNDARY_SPEC.md) 为准；冲突默认阻塞，不允许静默覆盖。

## 9. MVP 试点与最终状态

以 ERP 采购模块作为端到端试点，验证从一句目标到发布候选物的完整链路。最终形成具备项目管理、产品设计、架构、开发、测试、安全、发布和长期学习能力的个人 AI 软件研发团队。

## 10. 关联文档

- [SCOPE.md](SCOPE.md)
- [OPEN_SOURCE_RESEARCH.md](OPEN_SOURCE_RESEARCH.md)
- [TECH_STACK.md](TECH_STACK.md)
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
- [ADR-0001](ADR/ADR-0001-mvp-runtime-stack.md)
- [ADR-0004](ADR/ADR-0004-release-closure-transaction-boundaries.md)
- [RELEASE_CLOSURE_MATRIX.md](RELEASE_CLOSURE_MATRIX.md)
- [RUNTIME_SPEC.md](RUNTIME_SPEC.md)
- [CONFIG_SPEC.md](CONFIG_SPEC.md)
- [WORKFLOW_SPEC.md](WORKFLOW_SPEC.md)
- [EXECUTION_POLICY.md](EXECUTION_POLICY.md)
- [PILOT_ACCEPTANCE.md](PILOT_ACCEPTANCE.md)
- [MEMORY_SPEC.md](MEMORY_SPEC.md)
- [PLUGIN_SPEC.md](PLUGIN_SPEC.md)
- [WORKTREE_SPEC.md](WORKTREE_SPEC.md)
- [AGENT_HANDOFF.md](AGENT_HANDOFF.md)
- [WORKFLOW_ROUTING_RULES.md](WORKFLOW_ROUTING_RULES.md)
- [BOUNDARY_SPEC.md](BOUNDARY_SPEC.md)
- [design/UX_RESEARCH.md](design/UX_RESEARCH.md)

## 11. 文档完成定义

本总文档只有在以下条件同时满足时才算完成：目标、边界、模块职责、生命周期、门禁、开源治理、Agent 隔离和试点验收均已定义；每个定义均能链接到一个领域文档、配置契约或测试场景；任何未决事项必须有责任人、截止阶段和阻塞策略，而不能只写无责任人的待办说明。
