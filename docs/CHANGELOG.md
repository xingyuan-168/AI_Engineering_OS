# 变更记录

## [Unreleased] - 2026-08-26

### Added

- 增加唯一不可变 `RuntimeVersions`，统一软件、Plugin、API、配置、文档、Profile、SQLite、需求和执行镜像版本；公共 1.2 配置使用可移植项目根。
- 增加 SQLite `0007_release_closure`：Host Operation、脱敏调用审计、Memory 乐观版本、Routing 评分、Release candidate/final 对账、Check 时间和不可逆 accepted Handoff 约束。
- 增加受管 Worktree/coordinator root 明确拒绝、0006→0007 重验证、Memory expected-version 和迁移失败临时库校验/原子恢复测试。
- `status` 与 `step` 改用 SQLite 只读连接，不再隐式迁移、创建状态目录、分配任务或推进 Workflow。
- CLI 与 MCP 共用 API 1.2 响应封装、受信本地调用上下文和 `next_actions` 语义；`NextAction` 增加 Host Operation、Task Group、依赖及期望版本字段。
- Review finding 改为 `id/severity/status/summary` 结构对象，开放 high/critical finding 会在 accepted 决策写入前被拒绝。
- 增加 Host Operation 持久化内核：稳定幂等键与请求 hash、脱敏请求、租约/尝试次数、未知结果强制对账及最多 4 个恢复动作。
- Handoff accepted 审批改为先在 SQLite 同一事务中持久化 `integration_merge` Host Operation；API 1.2 返回待执行操作，merge 后 push 失败时保持 Handoff accepted 并保存待补推送的 merge commit。
- G2 批准改为同事务持久化 `integration_prepare`，executor 再幂等创建/登记 integration Worktree、任务组和最多 4 个任务；数据库维护也先创建/租赁 `database_migrate` intent 再执行迁移，远端 push 结果未知进入 reconcile 而非盲目重试。
- CLI/MCP 增加 `host_operation_execute` 公共入口；Handoff review 接受 `expected_handoff_version` 与 `idempotency_key`，自报 `reviewer` 仅作兼容显示并返回 warning。
- `resume` 会从持久化 Host Operation、租约和失败状态重建最多 4 个可执行 `host_operation` actions，避免进程中断后丢失外部副作用恢复入口。
- Review report、Check report 与文档 Gate 校验改为从绑定 Git Commit 或受管 `.codex-os/artifacts/` 审计区复算 hash，不再信任活动 Worktree 中未提交的当前文件。
- 非零验证检查现在生成 `failed` Check Evidence、失败报告和可恢复 blocker，不再构造 `passed + 非零 exit_code` 的无效证据。
- verification prepare 默认目标修正为 Bookworm `linux-amd64` / Python 3.12，下载对应 manylinux wheels；正式消费端复核审批、期限、镜像、平台、完整 manifest/hash、Trivy tree、只读权限与 link/junction，沙箱未启动也会生成 failed Check Evidence。
- Runtime Routing 改用 canonical `backend-project`、`frontend-project`、`large-project` Profile 名，短名只作为兼容 alias；`routing_decisions` 同步写入 0007 的七维评分、canonical profiles、risk、workflow 和 schema 版本字段。
- Profile Router 从项目 `profiles/*.yaml` 读取 allowed profile 名；缺省 fixture 保留内置安全集合，large 自动扩展不再绕过 Profile 资源事实源。
- 以 CQ-OS `400a930` 为研究基线，移植其 MIT `@cq/governance` 包的 Baseline + Project 单调策略、默认拒绝、角色/路径负向测试向量并保留 attribution；新增带 hash 的 `EffectiveGovernancePolicy`，Profile Schema 1.2 声明任务模板、影响路径、增量 Gate 证据与 Reviewer，Routing 保存七维输入、override、依赖和 policy hash，审批适配器忽略自报身份的提权语义。
- Plugin 资源补齐 `frontend-engineer` Agent、`frontend-implementation` Skill，并将插件 manifest 版本对齐 Runtime `0.2.0`。
- CLI/MCP 补齐 `verification_prepare`、`host_operation_reconcile` 与 `database_migrate` 公共映射；CLI 另新增 Release Candidate 与 Memory submit/review/search 映射，统一返回 API 1.2 envelope。
- MCP `memory_review` 暴露 `expected_version`，Memory 生命周期变更可由公共接口执行乐观并发校验。
- 增加 Plugin API 1.1 公共接口：`repository_check`、结构化 `task_complete`、`handoff_review`、`worktree_cleanup`、受管 `verification_run`、Release Candidate 与 Memory 审核；配置 Schema 1.1 兼容读取 1.0。
- 增加 `0004`～`0006` 追加式迁移，覆盖仓库审计、强证据 Gate、版本/发布记录、多 Agent DAG、Handoff Review、集成合并、Worktree 清理、Memory 生命周期与 FTS5；迁移前数据库备份带 checksum、完整性及恢复校验。
- 增加正式 GitHub 仓库预检与独立 Repository Governance Service，检查 Git 根、干净状态、remote/upstream/HEAD/目标分支、复制式版本目录、临时文件、跟踪污染、Secret、`.gitignore` 和路径逃逸。
- 增加 G0～G4 Commit 绑定的 Artifact、Check、Review 与 Gate Evidence Bundle；文档 Gate 校验元数据、状态、版本、负责人、需求引用、必需章节、占位符和影响同步。
- 增加 Profile 路由、最多 4 Agent 的任务 DAG、写路径重叠串行化、join barrier、独立 Reviewer 决策、拒绝后原 Worktree 修复、集成分支独占锁和 `--no-ff` 合并。
- 增加 Release Worktree/受管制品区、SBOM、校验和、回滚说明、版本矩阵及 G4 GitHub PR/merge/tag/Release 强验证；G4 授权前不会创建 tag 或发布。
- 增加 Memory `pending/active/needs_review/superseded/revoked/expired/deleted` 生命周期、来源变化失效、Secret/范围/置信度复核、supersedes 关系和项目隔离检索。
- 增加未 mock 公共 1.1 Workflow E2E 与治理失败路径测试，覆盖三 Agent 并行、依赖 join、真实 Git Worktree 合并、受控执行、发布证据和 G4 验证。
- 建立 Python 3.12/uv 可复现包与 Ruff、Pyright、pytest、pip-audit 质量门禁。
- 增加严格 Pydantic 配置和只能收紧的执行策略覆盖。
- 增加 SQLite WAL/外键迁移、checksum、迁移前备份和追加式幂等事件。
- 增加幂等项目初始化、原子文档写入、来源 hash 上下文和文档治理检查。
- 增加 `doctor`、`init`、`status`、`check-docs` CLI 及纯 JSON 输出契约。
- 增加 `new-project` 双轴状态机、G0-G4、事务化任务/审批/制品事件和恢复。
- 增加 `run new-project`、`step`、`approve`、`reject`、`resume` CLI。
- 增加仓库级 Codex 私有插件、marketplace 和官方 MCP Python SDK 2.x stdio 服务。
- 增加 11 个 MCP 工具及从 `project_init` 到 G4 完成的 Host 握手集成测试。
- 增加 11 个工程 Skill、7 个项目级自定义 Agent profile 和可复核的插件生命周期 Hooks。
- 增加 `task_complete` 的真实 Git/远端/制品校验、验证时间及事务化 handoff 证据。
- 增加确定性任务 Branch/Worktree 的路径、冲突、磁盘与 Git 预检；仅允许经批准清理已合并且干净的 Worktree，并保留任务 Branch。
- 将 Worktree 分配、任务 Branch/路径和 `reserved/created/blocked/cleaned` 事件事务化写入 SQLite，并用唯一索引保证每个任务只有一个分配。
- 增加 Docker 执行策略适配器：镜像 digest 锁定、非 root、断网、只读根文件系统、最小权限、显式挂载、资源/超时限制及日志脱敏。
- 增加执行审计服务与迁移，事务记录 command hash、镜像、挂载、容器、退出码、失败码、脱敏日志引用及 Worktree dirty 事件。
- 将 Workflow 与 Worktree/Git 证据闭环接通：`next_action` 返回任务 Branch/Worktree，后续任务继承前一提交，越权制品或分配失败会阻塞运行。
- 增加显式 `fixture_local_only` Git 策略，仅供无独立远端的隔离试点；普通项目继续默认并强制 `remote_required`。
- 增加 ERP 采购 FastAPI fixture 生成器和真实 API 集成测试，覆盖供应商、采购申请、审批、采购订单、状态查询与幂等 SQLite 迁移。
- Git 证据校验扩展为审计 base commit 到 task commit 的全部 changed paths，漏报越权文件同样阻塞完成。
- 增加从空仓库运行 G0–G4 的 ERP 试点驱动器，每阶段独立本地提交并生成测试、安全、Review、SBOM、校验和、回滚、审计与 Memory 证据。
- 记录 ERP 试点 PA-001～PA-010 验收结果、实现 Commit、质量门禁和 Docker 实容器环境遗留项。
- 增加 `feature-development`、`bug-fix` 和 `release` 工作流入口，复用相同审批、Git、Worktree 与恢复门禁。
- 增加项目隔离的 Memory 候选、来源 hash 激活、Secret 拦截、检索和来源变更失效生命周期。
- 增加 Podman OCI 沙箱适配器和项目级基础策略选择；`doctor` 接受 Docker 或 Podman 中至少一个可用，默认仍为 Docker。
- 补齐 V1 的产品、交互、UI、API、Agent、Execution 和变更工作流 Skills，并增加可校验的 frontend/backend/large 增量 Profiles。
- 增加 V1 发布候选验收报告，归档构建、测试、Git 证据及 OCI、Codex Host 和 G4 阻塞项。
- 增加显式启用的真实 Podman 集成测试，在隔离 Git Worktree 中验证锁定镜像、非 root、断网、只读根、最小权限、资源限制和执行后干净状态。

### Governance

- 接受 ADR-0004，冻结 `REQ-1.6.2` / 软件与 Plugin `0.2.0` / API、配置、文档与 Profile `1.2` / SQLite `0007`，并规定 1.0/1.1 在 0.2.x 的弃用兼容期。
- 新增 0.2.0 发布收口矩阵，把版本漂移、项目根混淆、Host Operation、合并/发布恢复、Commit-bound Evidence、离线验证缓存、Routing/Plugin 资源和 OCI 供应链逐项绑定测试与 Gate。
- 决定 Git/OCI/GitHub 副作用先持久化 intent 与授权、结果未知先对账；文档、Review 和制品从绑定 Commit 或受管审计区重读，不接受活动 Worktree 未提交内容替代。
- 将正式镜像决策统一为 Python 3.12.14 full Bookworm，并要求 index/platform digest、SBOM、Trivy 与 high/critical finding 策略证据。
- 固定需求基线 `REQ-1.6.2`、软件/CLI/Core `0.2.0`、Plugin API `1.1`、配置 Schema `1.1`、SQLite Schema `0006` 和预期 tag `v0.2.0`，不再混用需求与软件版本。
- 迁移后的活动 Workflow 在下一次状态变化前进入 `MIGRATION_REVALIDATION_REQUIRED`；只有仓库预检及已批准 Gate 的 1.1 证据包重审通过后才能恢复。
- 绑定 GitHub 远端，实施每个逻辑变更提交并立即推送的交付协议。
- 新增 ADR-0002，统一双轴状态、G0-G4、Host Hook、Skill 路径、沙箱和迁移语义。
- 新增根目录 `AGENTS.md`，区分用户请求、项目事实和原始输入文档。

### Security

- 正式 Verification 改用包含 Git 的 Python 3.12.14 Bookworm 固定镜像；依赖从 `uv.lock` 对应的只读 wheelhouse 按 hash 安装到临时 `/deps`，离线依赖审计使用绑定锁文件、平台、时效和完整依赖集合的快照。

### Environment

- 当前 Windows 环境已验证 Git、uv、Python 3.12 与 SQLite FTS5。
- 已安装 WSL 2.7.12 和 Podman 5.8.3，启动 rootless `podman-machine-default` 并预拉取项目锁定 digest；`doctor` 返回 `ok=true`。
- 真实 Podman 沙箱复验通过，ENV-SANDBOX-001 与 ERP PA-007 环境遗留项已解除；Docker Desktop 未安装且不再是阻塞条件。
- 2026-08-26 当前主机复核发现 Podman machine 已停止且未安装 `gh`；开发诊断可继续，但新的真实 G3/G4 在恢复环境并形成证据前保持 blocked。

### Documentation baseline (2026-08-20)

- 根据 Codex AI Engineering OS V2.0 需求建立项目文档基线。
- 增加项目总文档、实施计划、技术栈、边界和开源研究文档。
- 增加产品、架构、接口、数据库、安全、测试、部署和 ADR 入口。
- 补齐 UI 设计基线、开源仓库级核验记录和文档完成定义。
- 补齐运行时、配置、Workflow、Skill、Agent、执行策略、可观测性、迁移、发布清单和 ERP 试点验收文档。
- 冻结 Windows 本地 CLI + Codex 适配层 + Docker/Podman 默认沙箱的产品边界。

## 变更记录规则

- `Unreleased` 只记录已合并或已确认的变更，不记录个人草稿。
- 每条记录说明影响范围、用户可见变化、迁移要求和关联 ADR/Issue。
- 发布时将 `Unreleased` 转为版本号和日期，并保留回滚说明。
- 文档治理、技术栈、门禁和安全策略变化必须记录，即使没有业务代码变化。

## 版本分类

- Added：新增能力或文档。
- Changed：行为、流程、接口或策略变化。
- Fixed：缺陷和错误流程修复。
- Security：安全、依赖或权限修复。
- Governance：文档、审计、门禁和流程治理变化。
