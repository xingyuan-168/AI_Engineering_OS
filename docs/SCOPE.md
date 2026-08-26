# AI Engineering OS 0.2.0 修复范围

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"review-ready","owner":"product-manager","requirement_refs":["REQ-1.6.2","GOV-001","CFG-001","REPO-001","GATE-001","AGENT-001","HANDOFF-001","WORKTREE-001","RELEASE-001","EXEC-001","DOC-001","HYGIENE-001","VERSION-001","MEMORY-001","ROUTING-001","FRONTEND-001"]} -->

需求基线：`REQ-1.6.2`

目标软件版本：`0.2.0`
目标形态：Windows 本地、面向 Codex 的可审计工程治理与执行层。

## 1. 修复目标

本轮不是重写 AI Engineering OS，而是在保留现有 Workflow 状态机、Git 证据、Worktree、SQLite、Plugin 和 Docker/Podman 沙箱基础上，补齐企业级治理闭环。

目标成功状态是：Codex 负责推理和工程执行；AI Engineering OS 负责强制执行仓库、流程、证据、审批、隔离、版本和发布规则，并确保关键规则不依赖提示词或人工自觉。

## 2. 范围内能力

### 2.1 M0：治理与版本基线

- 通过新 ADR 固定治理层边界、事实源和 `.codex-os/` 单目录决策。
- 建立需求基线 `REQ-1.6.2` 与软件版本 `0.2.0` 的版本矩阵。
- 增加 `.codex-os/bootstrap.md`、`rules.md`、`workflow.md`、`memory.md` 治理索引。
- 更新主文档、架构、范围、CHANGELOG 和 ADR 索引的一致性要求。

### 2.2 M1：仓库准备度、卫生与版本治理

- 新增 Repository Governance Service 与 `repository_check` / `repo-check`。
- 在首个写任务或 Worktree 前执行 Git/GitHub/upstream/HEAD/目标分支/工作树/冲突/权限预检。
- 实现禁止目录、禁止文件、Git 跟踪污染、`.gitignore`、Secret 和一次性文件生命周期检查。
- 正式项目强制 GitHub；测试 fixture 的 `local-only` 例外不暴露给普通 CLI/MCP。

### 2.3 M2：强证据 Gate 与文档治理

- 引入 `ArtifactEvidence`、`CheckEvidence`、`ReviewEvidence`、`GateEvidenceBundle`。
- 为 G0-G4 建立阶段必需产物、真实检查、来源 Commit、Review 和审批校验。
- 增加文档元数据、必需章节、状态、占位内容、版本、过期、链接和影响分析检查。
- 拒绝以自由文本成功声明代替测试、Review、安全或发布证据。

### 2.4 M3：Profile、多 Agent、Handoff 与集成

- Workflow 持久化 Profile、目标分支、集成分支、基础 Commit、并行上限与任务图。
- 增加任务组、依赖图、任务级乐观版本、最多 4 个并行任务与 join barrier。
- 增加 `handoff_review`，强制 `ready -> accepted | rejected | blocked`。
- 建立 Workflow 集成 Worktree、独占合并锁、`--no-ff` 合并、冲突阻塞与显式 `worktree_cleanup`。
- 将 frontend/backend/large Profile 接入运行时；增加 `frontend-engineer` Profile 与 Frontend Implementation Skill。

### 2.5 M4：Execution、Release、Memory 与 Plugin API 1.2

- 将 ExecutionService 接入正常 MCP/Workflow 的测试、构建、代码执行和高风险命令路径。
- 修复 Release Worktree 与 `.codex-os/artifacts/<run-id>/` 的职责边界。
- 扩展 G4 GitHub PR、merge Commit、版本、发布授权和 tag/Release 验证。
- 将 Memory 状态迁移为完整生命周期，增加候选提交、Review、supersedes、撤销、过期和 FTS5。
- 提供 Plugin API 1.2 的统一输入/响应/错误契约；1.0/1.1 单任务、短 Profile 名和健康检查入口保留弃用兼容。

### 2.6 M5：迁移与完整验收

- 增加 SQLite 追加迁移 `0004-0007`，包含备份、checksum、外键、幂等和恢复验证；不得改写 `0001`～`0006`。
- 对旧活动 Workflow 强制 `MIGRATION_REVALIDATION_REQUIRED`。
- 完成配置 `1.0/1.1 -> 1.2` 兼容、SQLite `0006 -> 0007`、公共 MCP 多 Agent E2E 与真实 Podman 复验。
- 生成 Release Manifest、SBOM、校验和、回滚、ADR、CHANGELOG 和 Memory 证据。

### 2.7 M6：0.2.0 发布收口

- 以持久化 Host Operation 闭合 G2 集成准备、Handoff 合并、G3 发布准备和 G4 发布副作用。
- 所有 Gate、Review、文档和发布证据从登记 Commit 或受管审计区重新读取并校验。
- 增加 verification cache prepare、可复现离线构建、官方 Bookworm digest/SBOM/Trivy 证据和 final manifest。
- 统一 CLI/MCP 应用模型，补齐 Release Candidate、Memory、Migration、Verification Prepare 与 Host Operation 映射。
- 以 [RELEASE_CLOSURE_MATRIX.md](RELEASE_CLOSURE_MATRIX.md) 作为缺陷、测试和 Gate 的关闭索引。

## 3. 明确不在本轮范围

- 不替换 Codex Host，不内嵌第二个模型客户端，不实现自研通用大模型。
- 不把 AIOS 重构为多人 SaaS、租户/计费平台或远程编排服务。
- 不允许多个 Agent 在同一工作目录写入，不采用共享可写 Worktree。
- 不自动 force push、rebase、覆盖冲突或删除未知用户文件。
- 不以复制目录、文件后缀或长期 `.aios/` 兼容树管理版本。
- 不将所有研究过的开源项目直接引入运行时依赖。
- 不包含生产环境部署；发布权限不包含部署权限。
- 本实施请求本身不授权合并 `main`、创建 tag 或发布 GitHub Release；这些动作必须等待显式 G4 批准。

## 4. 关键边界

### 4.1 事实与状态

- Markdown/Git 保存需求、设计、ADR、CHANGELOG 和发布事实。
- SQLite 保存 Workflow/任务状态、事件、索引、证据与 provenance。
- `.codex-os/*.md` 是入口和索引；`.codex-os/context` 是可再生缓存。

### 4.2 Codex 与 Runtime

- Codex Host 依据 `next_actions` 选择并运行子 Agent。
- Runtime 生成确定性任务契约、验证状态转换和持久化审计，不执行模型推理。
- Reviewer/Security Reviewer 对代码只读；结构化结论写入 SQLite。

### 4.3 Git 与发布

- 默认目标分支：`main`。
- 集成分支：`workflow/<run-id>/integration`。
- 任务分支：`agent/<agent>/<task-id>`。
- 正式发布路径：任务分支 -> Review -> 集成分支 -> G4 GitHub PR -> 目标分支 -> 经批准的 annotated tag/GitHub Release。

### 4.4 自动化与人工批准

可自动执行：确定性只读检查、任务图计算、证据校验、合并前检查和安全范围内的状态更新。

必须人工批准：G1 需求冻结、G2 设计冻结、G3 质量放行、G4 发布授权、Worktree 删除、破坏性迁移、敏感数据操作、tag/Release 和任何生产部署。

## 5. 缺陷关闭范围

| 优先级 | ID | 本轮关闭条件 |
| --- | --- | --- |
| P0 | `GOV-001`、`CFG-001`、`REPO-001`、`GATE-001` | 边界、目录、正式仓库预检和结构化 Gate 均由代码与测试强制 |
| P0 | `AGENT-001`、`HANDOFF-001`、`WORKTREE-001` | DAG 并行、Handoff Review、集成与清理形成闭环 |
| P0 | `RELEASE-001` | 发布文件与制品来源路径、Commit 和 hash 一致 |
| P1 | `EXEC-001`、`DOC-001`、`HYGIENE-001`、`VERSION-001` | 正常执行路径、文档内容、仓库卫生与版本矩阵可验证 |
| P2 | `MEMORY-001`、`ROUTING-001`、`FRONTEND-001` | 能力完整实现；若存在延期项，不得影响正式 Workflow 或 P0/P1 闭环 |

## 6. 必测范围

### 6.1 仓库与卫生

- 无 Git、无 remote、非 GitHub remote、远端不可达、HEAD 未推送、目标分支缺失、脏工作树和未解决冲突。
- 禁止目录/文件、跟踪缓存/日志/Secret、缺失 `.gitignore`、符号链接或 junction 逃逸、未知临时文件和扫描排除项。

### 6.2 Workflow 与并发

- 至少三个 Agent 并行、路径重叠转串行、依赖 join、并发完成、乐观锁、恢复、重复调用和进程中断。
- Handoff 未接受不推进；拒绝复用原 Worktree；blocked 保留现场；合并冲突不自动覆盖；新 Commit 重新 Review。

### 6.3 证据与 Gate

- G0-G4 各缺少一种必需证据、草案/过期文档、未批准占位内容、伪造成功字符串、错误来源 Commit 和 hash 不匹配。
- API、数据库、安全、架构、ADR、CHANGELOG 的影响同步与负向场景。

### 6.4 Execution、发布、版本与 Memory

- 锁定镜像、非 root、断网、只读根、最小权限、资源限制、挂载逃逸、沙箱不可用和 dirty 检查。
- Release Candidate 的 Worktree/制品区位置、来源 Commit、SBOM、校验和、回滚、PR、merge、tag 与 Release 失败恢复。
- Memory 来源变化、跨项目隔离、Secret 拦截、FTS5 过滤、supersedes 和状态转换。

### 6.5 兼容与公共接口

- 配置/接口 1.0 与 1.1 到 1.2、SQLite fresh install 与 0006 到 0007、失败恢复、备份 checksum、FTS 重建和重复迁移。
- 未 mock 的公共 MCP 完成至少一次多 Agent 端到端流程。
- Plugin validator、全部新增/更新 Skills、Agent Profile、Hook fixture 与 MCP Schema。

## 7. 质量与完成标准

1. 所有 P0/P1 缺陷关闭；任何 P2 残项均不影响正式 Workflow。
2. Ruff、Pyright、Secret、依赖审计和全部测试通过；总分支覆盖率不低于 85%，新增治理和状态转换模块不低于 90%。
3. 真实 Podman 测试显式通过，不得以 skip 或 mock 替代。
4. 每个逻辑变更使用独立 Conventional Commit 并推送到任务或里程碑分支，证据包含 Branch、Commit、remote、push、artifact hash 和验证结果。
5. Git 工作树干净，无孤儿 Worktree、未知临时文件或未登记制品。
6. Hook 信任经过人工复核；Release Manifest、SBOM、校验和、回滚、ADR、CHANGELOG 和 Memory 完整。
7. G4 GitHub PR 经批准并合并后，才可创建 `v0.2.0` tag 和 GitHub Release。

## 8. 约束、依赖与风险

- 目标运行环境为 Windows、本地 Git、Python 3.12、`uv.lock` 锁定依赖和 Docker/Podman。
- 正式流程依赖可访问 GitHub 远端及相应 branch、push、PR、tag 和 Release 权限；权限不足必须返回可操作阻塞原因。
- 自举升级会触及当前 Workflow 数据；迁移必须先备份并强制旧活动 Workflow 复核。
- 并行安全依赖完整写路径声明；无法证明互不重叠时默认串行。
- 自动清理的错误目标具有高数据风险，因此只允许清理受管、登记、确定且人工批准的路径。
- 当前范围决策已确认；后续 Gate 审批仍需独立命名审批人，不能由本文件自动推定。

## 9. 范围变更规则

新增能力必须记录目标、用户价值、Requirement ID、涉及模块、写路径、文档影响、风险、验证方式、迁移影响和是否需要 ADR。若变更影响合规、数据所有权、发布权限或既定边界，必须回到相应 Gate 重新审批。

## 10. G1 完成定义

本范围文档已明确修复目标、里程碑、范围内/外能力、自动化与人工边界、缺陷关闭条件、测试范围、质量标准、依赖和风险。只有 [产品需求](PRODUCT_REQUIREMENTS.md)、[用户故事](USER_STORY.md)、[业务规则](BUSINESS_RULES.md) 与本文四者内容一致且通过文档检查后，才可请求 G1 批准。
