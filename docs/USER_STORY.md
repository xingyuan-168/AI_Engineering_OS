# AI Engineering OS 0.2.0 用户故事

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"review-ready","owner":"product-manager","requirement_refs":["REQ-1.6.2"]} -->

本文使用 Given/When/Then 表达可测试行为。每个故事必须映射到 [产品需求](PRODUCT_REQUIREMENTS.md) 和 [业务规则](BUSINESS_RULES.md)。

## US-001：识别产品治理边界

作为项目维护者，我希望系统明确区分 Codex Host 与 AI Engineering OS 的职责，以便判断一项能力应由推理宿主还是治理运行时负责。

- 关联需求：`GOV-001`
- Given：项目正在使用 Codex Host 执行工程任务。
- When：维护者查看项目总文档、ADR 和运行时依赖。
- Then：文档明确 Codex 负责推理与工具执行，AIOS 负责确定性治理；运行时不存在第二个模型客户端。

## US-002：在安全仓库基线上开始工作

作为项目维护者，我希望首个写任务前自动验证 GitHub 远端、upstream、HEAD、目标分支、工作树和冲突状态，以免在不可追溯仓库中产生开发结果。

- 关联需求：`CFG-001`、`REPO-001`
- Given：项目已初始化但可能尚未满足正式仓库要求。
- When：维护者启动会创建代码任务或 Worktree 的 Workflow。
- Then：所有预检通过才返回写任务；任一失败均返回结构化阻塞原因且不创建任务 Worktree。
- And：只读 status、文档和仓库检查仍可运行。

## US-003：获得可信 Gate 结论

作为 Gate 审批人，我希望看到结构化产物、检查、Review、来源 Commit 和 hash，以便拒绝缺失或伪造的通过声明。

- 关联需求：`GATE-001`、`DOC-001`
- Given：Workflow 请求 G0-G4 中的某个 Gate。
- When：审批人查看证据摘要并提交决定。
- Then：系统校验该 Gate 的完整必需清单、证据来源和文档状态；自由文本结果不能替代真实检查记录。
- And：批准只适用于当前 Gate、当前 `state_version` 和当前证据包。

## US-004：发现仓库卫生问题

作为项目维护者，我希望一次检查同时发现复制式版本目录、可疑文件、被跟踪缓存/日志/Secret 和忽略规则缺口，以便在开发或发布前阻断污染。

- 关联需求：`HYGIENE-001`、`HYGIENE-002`
- Given：仓库可能包含合法依赖内部目录与项目自有异常文件。
- When：运行 `repository_check` 或进入相关 Gate。
- Then：报告按规则分类问题并排除 `.git`、依赖、受管 Worktree 与 AIOS 运行目录。
- And：未知用户文件不会被自动删除。

## US-005：并行执行互不冲突的 Agent 任务

作为 Workflow 协调者，我希望根据 Profile、任务依赖和写路径生成任务组，以便最多 4 个 Agent 安全并行。

- 关联需求：`AGENT-001`、`ROUTING-001`
- Given：一个阶段包含至少三个可独立交付的写任务。
- When：调度器从同一集成 HEAD 计算任务图。
- Then：写路径互不重叠且依赖满足的任务获得独立 Branch/Worktree 并并行；路径冲突、不确定影响或产物依赖转为显式串行关系。
- And：任一任务完成不直接推进 Workflow；join barrier 重新计算任务组整体状态。

## US-006：交付并复核 Agent 结果

作为生产者 Agent，我希望提交结构化 Handoff 并在原 Worktree 处理拒绝，以便保留完整上下文而不制造修复副本。

- 关联需求：`HANDOFF-001`
- Given：Agent 已提交并推送任务分支，Handoff 状态为 `ready`。
- When：Reviewer 提交 `accepted`、`rejected` 或 `blocked` 决定。
- Then：只有 `accepted` 可触发集成合并；`rejected` 指向原 Agent 和原 Worktree；`blocked` 保留现场并阻塞依赖任务。
- And：每次决定绑定被审 Commit、Reviewer、理由和时间。

## US-007：安全合并与清理 Worktree

作为 Reviewer，我希望任务只在验收后串行合入集成分支，并通过显式条件清理 Worktree，以免丢失冲突现场或覆盖他人结果。

- 关联需求：`WORKTREE-001`
- Given：一个 Handoff 已 accepted。
- When：Runtime 获取集成锁并执行合并。
- Then：使用 `--no-ff` 生成可审计合并；冲突返回生产者，且新 Commit 必须重新 Review。
- And：只有已合并、干净、无开放 Review 且获人工批准的 Worktree 可被清理。

## US-008：通过受控沙箱执行验证

作为 QA 或工程 Agent，我希望测试、构建、代码执行和高风险命令都通过同一个 ExecutionService，以便获得可比较、可追溯的隔离证据。

- 关联需求：`EXEC-001`
- Given：任务已经绑定 `run_id/task_id/worktree`。
- When：发起受控执行。
- Then：命令在锁定镜像、非 root、默认断网、只读根、最小权限与资源限制下运行，并保存 execution ID、command hash、退出码、脱敏日志与 dirty 状态。
- And：沙箱不可用时实现、测试、删除和发布任务被阻塞，需求与文档只读工作仍可进行。

## US-009：从正确来源生成发布候选

作为 Release Manager，我希望可提交发布文件来自 Release Worktree、二进制制品进入受管制品区，以便所有 hash 都对应同一集成 Commit。

- 关联需求：`RELEASE-001`、`VERSION-001`
- Given：G3 已批准且 Release 任务 Worktree 已创建。
- When：生成 CHANGELOG、Manifest、回滚文档、Wheel、压缩包、SBOM 和校验和。
- Then：可提交文件只写 Release Worktree；二进制制品只写 `.codex-os/artifacts/<run-id>/`；Manifest 和 SQLite 记录来源 Commit、环境与 hash。

## US-010：通过 GitHub PR 完成 G4

作为发布审批人，我希望 G4 验证 PR head、目标分支 merge Commit、版本和发布授权，以免为错误来源创建 tag 或 Release。

- 关联需求：`RELEASE-002`、`VERSION-001`
- Given：Workflow 集成分支已提交 GitHub PR 到 `main`。
- When：提交 G4 审批。
- Then：Runtime 验证 PR、集成分支、目标分支和制品来源一致。
- And：只有批准后才允许 annotated tag `v0.2.0` 和 GitHub Release；该操作不授权生产部署。

## US-011：安全迁移旧项目与活动 Workflow

作为运行时维护者，我希望配置与 SQLite 使用兼容、追加和可恢复的迁移，以免升级 0.2.0 时沿用无效 Gate 或损坏状态。

- 关联需求：`MIGRATION-001`、`COMPAT-001`
- Given：项目可能使用配置 1.0/1.1、SQLite 0006 或存在活动 Workflow。
- When：首次用 0.2.0 打开或推进状态。
- Then：先备份并验证 checksum，追加应用 0007；重复执行无副作用；失败先在临时库验证恢复副本再原子替换活动库。
- And：旧活动 Workflow 进入 `MIGRATION_REVALIDATION_REQUIRED`，通过新证据审计后才能恢复。

## US-012：复核和检索长期 Memory

作为项目维护者，我希望重要事件自动形成候选 Memory，并由人复核其来源、Secret、范围和置信度，以便保留可信经验而不泄露敏感信息。

- 关联需求：`MEMORY-001`
- Given：ADR 接受、Bug 关闭、Release、回滚或重要失败事件发生。
- When：系统创建候选并执行 Memory Review。
- Then：只有来源 hash、Secret、项目范围和置信度检查通过才可变为 `active`。
- And：来源变化进入 `needs_review`；查询使用 FTS5 并按项目、类型、状态、标签、时间和来源过滤。

## US-013：让 Profile 真实影响任务

作为 Product Manager，我希望 frontend、backend 和 large Profile 参与路由与任务生成，以便项目类型和规模不再只是被加载的配置。

- 关联需求：`ROUTING-001`、`FRONTEND-001`
- Given：目标或影响分析显示前端、后端或大型项目特征。
- When：生成 Routing Decision 与任务组。
- Then：选择证据被持久化，相关角色、Skill、允许路径、必需产物和审批规则进入 `next_actions` 与状态响应。
- And：前端实现任务由真实 `frontend-engineer` Profile 和 Frontend Implementation Skill 承担。

## US-014：通过公共接口审计全流程

作为审计者，我希望使用 Plugin API 1.2 查看仓库、任务组、依赖、Handoff、Host Operation、集成分支、Gate、执行、版本、Release 和 Memory 状态，以便无需读取内部数据库也能重建决策链。

- 关联需求：`API-001`
- Given：Workflow 已启动且产生一个或多个任务。
- When：调用公开 MCP/CLI status 与治理接口。
- Then：响应包含稳定 ID、状态、版本、阻塞原因和证据引用；单任务兼容调用仍返回 `next_action`，新调用返回 `next_actions`。

## 验收映射规则

1. 每个 Given/When/Then 场景必须在 [TEST_PLAN.md](TEST_PLAN.md) 中映射到自动化或人工验收测试。
2. 负向路径与恢复路径必须和成功路径具有同等可追溯性。
3. 任何故事若缺少关联需求、可观察结果或失败状态，均不能作为 G1 证据。
4. G1 批准只冻结用户价值、范围和业务规则；技术实现契约需在 G2 单独批准。
