# Codex AI Engineering OS 实施计划

版本：V2.0-derived
状态：可执行文档基线
实施方式：个人本地 MVP，关键门禁人工确认。

## 1. 实施结果

交付一个可在本地 Git 仓库运行的最小系统：用户输入业务目标后，系统能初始化文档、选择 Workflow、执行开源调研、拆分任务、调度 Agent、在受控 Worktree 中完成开发，并输出测试、Review、安全和发布记录。

## 2. 阶段计划

### 阶段 0：规格冻结

任务：

- 评审 `docs/README.md` 文档地图中的完整规范集，重点确认项目总文档、范围、架构、配置、Workflow、权限和验收边界。
- 明确术语、输入输出、风险等级和人工审批边界。
- 将确认后的文档标记为基线版本。

产物：确认版 `PROJECT_MASTER.md`、`SCOPE.md`、`TECH_STACK.md`、`IMPLEMENTATION_PLAN.md`，以及运行时、配置、Workflow 路由、Skill、Agent 交接、Plugin、Worktree、模块边界、Memory、执行、可观测性、迁移、发布和试点验收契约。

完成条件：没有未处理的范围冲突、关键术语冲突或未定义的发布边界。

### 阶段 1：文档治理与实现契约基线

任务：

- 创建标准 `docs/` 和 `docs/design/` 模板。
- 实现文档完整性、链接、元数据和状态检查。
- 实现变更影响检查：API、数据库、架构、ADR、CHANGELOG。
- 增加禁止复制目录和未授权文件写入的检查。
- 完成 `RUNTIME_SPEC.md`、`CONFIG_SPEC.md`、`WORKFLOW_SPEC.md`、`SKILL_SPEC.md`、`AGENT_SPEC.md`、`EXECUTION_POLICY.md`、`OBSERVABILITY.md`、`MIGRATION_SPEC.md`、`RELEASE_CHECKLIST.md` 和 `PILOT_ACCEPTANCE.md`。
- 完成 `MEMORY_SPEC.md`、`PLUGIN_SPEC.md`、`WORKTREE_SPEC.md`、`AGENT_HANDOFF.md`、`WORKFLOW_ROUTING_RULES.md`、`BOUNDARY_SPEC.md` 和 `design/UX_RESEARCH.md`，并同步领域文档索引。
- 完成 Codex Host、CLI、Plugin、项目 `.codex/`、Worktree、Handoff、Memory 和 Docker/Podman 的边界冻结。

产物：文档模板、实现契约文档、`check-docs` 规格、影响检查规则、首批 ADR。

完成条件：新项目初始化后所有必需文档存在；实现前必读顺序、Schema、状态、权限和验收边界无冲突；本阶段不创建代码。

### 阶段 2：开源研究与技术选型

任务：

- 按 [OPEN_SOURCE_RESEARCH.md](OPEN_SOURCE_RESEARCH.md) 的能力矩阵逐项研究。
- 固定官方仓库、版本/commit、License、依赖和安全信息。
- 对每个候选项目完成“直接使用 / 二次开发 / 提取设计思想 / 自研”决策。
- 将最终采用项写入 `TECH_STACK.md`，重大决策写入 ADR。

产物：开源研究记录、技术选型记录、许可证清单、集成边界说明。

完成条件：每个系统模块都有开源研究结论，且不存在未经核验的外部依赖。

### 阶段 3：Workflow Engine MVP

任务：

- 实现 `new-project`、`feature-development`、`bug-fix`、`release`。
- 实现状态转换、检查点、暂停、恢复、有限重试和事件日志。
- 实现可解释路由评分、Profile 组合、人工覆盖和 `needs_approval`/`blocked` 恢复路径。
- 实现 G0-G4 门禁和人工确认接口。
- 实现项目清单、任务事件和产物索引。

产物：CLI、Workflow 定义、SQLite 状态库、事件日志和恢复测试。

完成条件：任意 Workflow 中断后可以从最近检查点恢复，关键门禁没有审批不能跳过。

### 阶段 4：Skill、Agent 与执行管理

任务：

- 首批实现需求、开源调研、架构、产品、API、数据库、测试、Review、安全和发布 Skill。
- 实现 Product Manager、Architect、Engineer、QA、Security 和 Reviewer Agent 配置。
- 实现 Branch/Worktree 创建、任务隔离、产物回收和 Review 合并。
- 实现 Agent Handoff 的产物、hash、Commit、测试和风险校验；交接不合格时阻塞消费者。
- 实现命令 allowlist、允许路径、dry-run、敏感操作拦截和执行记录。

产物：Skill/Agent 契约、Worktree 管理器、Execution Manager、安全策略。

完成条件：Agent 不能修改未授权路径；所有任务都能关联输入、输出、分支、提交和 Review。

### 阶段 5：ERP 采购模块试点与验收

任务：

- 从业务目标启动 `new-project`。
- 完成需求、范围、开源调研、架构、技术栈、API、数据库和安全文档。
- 通过 Worktree 执行一个完整功能切片。
- 运行测试、Review、安全扫描和发布候选物流程。
- 将重大决策、失败方案和发布结果写入 Memory。
- 验证 Decision、Project、Bug、Experience 四类 Memory 的来源 hash、脱敏、检索、失效和跨项目隔离。

产物：试点项目、端到端日志、验收报告、问题清单和改进 ADR。

完成条件：从目标输入到发布候选物的全过程可重放、可审计、可恢复。

## 3. 推荐命令面

```text
codex-os init
codex-os run new-project --goal "开发一个 ERP 采购模块"
codex-os status
codex-os resume <workflow-id>
codex-os check-docs
codex-os research <capability>
codex-os verify
codex-os release --candidate
```

## 4. 测试计划

### 单元测试

- 项目、Workflow、Skill、Agent 配置校验。
- 状态转换、前置条件、重试和恢复。
- 文档影响判定和必需产物检查。
- 命令风险、路径权限和人工审批判定。

### 集成测试

- 一句业务目标生成完整项目骨架。
- 功能修改正确触发 API/数据库/架构/ADR/CHANGELOG 检查。
- Agent 在独立 Worktree 中执行并通过 Review 合并。
- 外部 License 未确认时阻止依赖进入技术栈。
- 失败任务可以恢复，重复执行不会产生复制目录。

### 验收指标

- 文档完整率：100%。
- 关键门禁绕过次数：0。
- 发布候选物具备完整测试、Review 和安全证据。
- 所有开源采用结论均有版本和 License 记录。
- 试点 Workflow 能够中断并恢复。

## 5. 主要风险与应对

| 风险 | 应对 |
| --- | --- |
| 自动化范围过大 | 先做文件化状态机和关键门禁，逐步增加自动化 |
| 多 Agent 冲突 | 强制 Branch/Worktree 和路径边界，禁止共享写入 |
| 外部项目许可证或升级风险 | 上游核验、版本固定、许可证清单和 ADR |
| 记忆污染或失去来源 | 事实写入 Markdown/Git，检索结果保留来源和时间 |
| 命令执行造成破坏 | allowlist、dry-run、人工确认和完整日志 |
| 文档与实现漂移 | CI 文档检查、变更影响检查和发布门禁 |

## 6. Definition of Done

一个阶段只有在代码、文档、测试、Review、风险记录和变更记录均完成后才算结束。任何“代码已完成但文档未更新”的状态都视为未完成。

## 7. 责任与证据

| 阶段 | 责任角色 | 必须保留的证据 |
| --- | --- | --- |
| 规格冻结 | Product Manager + 用户 | 评审结论、范围变更记录、基线提交 |
| 文档治理 | Project Manager | 文档检查报告、影响检查报告 |
| 开源研究 | Architect + Security | 官方来源、版本、License、风险记录、决策 ADR |
| Workflow MVP | Architect + Execution Manager | 状态转换测试、检查点、事件日志、恢复记录 |
| Agent/Skill | Agent Manager + Reviewer | 配置、任务输入输出、Worktree、Review 结果 |
| 试点验收 | QA + Security + 用户 | 端到端日志、测试报告、安全报告、发布候选物 |

任何阶段缺少责任角色或证据，状态必须保持为 `blocked`，不得标记完成。
