# ADR-0003：治理运行时边界、仓库拓扑与版本基线

<!-- codex-os-document: {"schema_version":"1.1","document_version":"0.2.0","status":"proposed","owner":"architect","requirement_refs":["REQ-1.6.2","GOV-001","CFG-001","REPO-001","AGENT-001","VERSION-001"]} -->

- 状态：Proposed（随 G2 批准生效）
- 日期：2026-08-24
- 范围：AI Engineering OS 0.2.0 的宿主边界、事实源、目录、正式仓库、多 Agent 集成与版本治理
- 澄清：ADR-0002 第 5、8 条
- 取代：ADR-0002 第 6 条中“Docker Desktop 是默认沙箱”的单一后端表述；0.2.0 由策略选择 Docker 或 Podman，安全约束不变

## 背景

`REQ-1.6.2` 与实现审计显示三个会破坏治理闭环的冲突：项目被同时描述为“自主 AI 研发团队”和“工程治理层”；历史需求使用 `.aios/`，当前实现使用 `.codex-os/`；现有单任务 Worktree 在 Handoff `ready` 时即推进，缺少并行任务图、独立 Review、集成分支与 GitHub PR 闭环。

如果这些边界不先冻结，后续实现会产生两个模型运行时、两个配置事实源，或者让任务分支绕过 Reviewer/G4 直接进入目标分支。历史材料还曾把需求版本与软件版本混用，导致包、Plugin、配置、文档、数据库和 Git tag 无法形成一一对应的 Release Manifest。

## 决策

1. AI Engineering OS 定位为 Codex Host 周围的确定性工程治理与执行层。Codex Host 负责推理、Session、工具调用和工程执行；AIOS 负责仓库、Workflow、证据、审批、隔离、版本和发布规则。`0.2.0` 不内嵌第二个模型客户端。
2. Markdown 与 Git 是项目事实源；SQLite 是运行状态、事件、索引、证据和 provenance 源。生成上下文和 `.codex-os/*.md` 索引不能覆盖或复制领域事实。
3. `.codex-os/` 是唯一 AIOS 配置与运行目录；`.codex/` 只保存 Codex Host 配置与 Hook。拒绝长期 `.aios/` 兼容树和同名 Skill 覆盖。
4. 正式项目必须使用 `github.com` 或配置允许的 GitHub Enterprise 域名。SSH/HTTPS 都可；`fixture_local_only` 只能由测试配置启用。首个写任务前必须验证 GitHub remote、可访问性、upstream、当前 HEAD、目标分支、干净工作树和冲突状态。
5. 多 Agent 使用“任务 Worktree -> 独立 Review/Handoff accepted -> Workflow 集成 Worktree -> G4 GitHub PR -> 目标分支”的拓扑。默认目标分支为 `main`，集成分支为 `workflow/<run-id>/integration`，任务分支为 `agent/<agent>/<task-id>`。
6. 任务最大并行数为 4。只有声明写路径互不重叠、依赖已满足且影响分析确定的任务可并行；无法证明时默认串行。任务完成不能直接推进 Workflow，join barrier 只接受全部 reviewed/merged 的任务组。
7. 集成合并持有独占锁并使用 `--no-ff`；Runtime 不自动 force push、rebase 或覆盖冲突。Worktree 删除需要已合并、干净、无开放 Review 和人工批准。
8. 执行后端由项目策略选择 Docker 或 Podman，但必须使用锁定 digest、非 root、默认断网、只读根、最小权限、资源限制和受管挂载。沙箱不可用时实现、测试、删除、迁移和发布阻塞。
9. 需求基线固定为 `REQ-1.6.2`；软件/CLI/Plugin 核心版本为 `0.2.0`；Plugin API 与配置 Schema 为 `1.1`；SQLite 使用追加迁移 `0004-0006`；Git tag 为 `v0.2.0`。
10. G4 前不得合并 `main`、创建 tag、发布 GitHub Release 或部署。G4 批准必须绑定 GitHub PR、merge Commit、版本、制品 hash 和独立发布授权；发布权限不包含部署权限。

## 官方机制依据

- Git 官方说明 linked worktree 共享仓库 refs，但拥有独立 HEAD/index，并提供 list/lock/remove/repair 等管理命令：[git-worktree](https://git-scm.com/docs/git-worktree)。
- GitHub protected branch 可要求 PR Review、状态检查并阻止 force push：[About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)。
- GitHub merge commit 为 PR 保留显式合并点；与本 ADR 的集成 `--no-ff` 证据目标一致：[Configuring commit merging](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/configuring-commit-merging-for-pull-requests)。
- Podman 提供断网、只读根、PID 与 capability 控制：[podman-run](https://docs.podman.io/en/latest/markdown/podman-run.1.html)。

## 未采用的替代方案

- 不把 AIOS 扩展为自带模型客户端的第二套 Agent Runtime；这会分裂推理、Session 和权限事实源。
- 不引入 `.aios/` 与 `.codex-os/` 长期双写或自动同步；迁移和冲突成本高于兼容收益。
- 不让任务分支直接合入 `main`，也不把 `ready` Handoff 视为验收。
- 不允许共享可写 Worktree、乐观假设路径不冲突或超过 4 个无界并行 Agent。
- 不使用 squash/rebase 重写已推送任务证据；公开历史通过新 Commit、merge Commit 或 `git revert` 修正。
- 不把需求基线编号当作软件、Plugin 或配置版本。

## 后果

正面结果是职责、事实源、目录、分支、证据和版本均具有唯一权威路径；并行任务可恢复且不会因为单个任务完成而越过 Review。正式发布可以从 Requirement ID 一直追踪到 PR、merge Commit、tag、SBOM 与 Memory。

代价是 Runtime 必须新增仓库治理、任务组/依赖、Handoff Review、集成锁、GitHub 验证、结构化 Gate 证据和迁移复核；旧活动 Workflow 不能直接沿用旧 Gate；GitHub 或沙箱不可用时写流程会明确阻塞。

## 验证与生效条件

1. G2 批准时将本 ADR 状态更新为 Accepted，并同步 `PROJECT_MASTER.md`、`ARCHITECTURE.md`、`WORKFLOW_SPEC.md`、`WORKTREE_SPEC.md`、`SECURITY.md` 与 `CHANGELOG.md`。
2. 公共 MCP E2E 必须证明三个以上 Agent 并行、冲突串行、Handoff accepted 后合并、join barrier、恢复和幂等。
3. 正式仓库负向测试必须覆盖无 Git、无/非 GitHub remote、不可达、HEAD 未推送、脏工作树和冲突。
4. G3 必须提供真实 Podman/Docker、Ruff、Pyright、pytest、Secret、依赖、代码 Review 与安全 Review 的结构化证据。
5. G4 必须验证 GitHub PR、merge Commit、版本、Manifest、SBOM、制品 hash、Memory 和发布授权后，才允许 `v0.2.0`。
