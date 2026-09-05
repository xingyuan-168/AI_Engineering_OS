# AI Engineering OS 0.2.0 业务与治理规则

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"review-ready","owner":"product-manager","requirement_refs":["REQ-1.6.2"]} -->

这些规则是运行时必须强制执行的产品约束。提示词、说明文字或操作者自觉不能替代程序校验和审计证据。

## 1. 事实源与职责边界

- `BR-001`：Markdown 与 Git 保存项目事实；SQLite 只保存运行状态、事件、索引、证据和 provenance。
- `BR-002`：`.codex-os/*.md` 只提供治理入口与索引，不复制领域事实；生成上下文永远不能覆盖其来源文档。
- `BR-003`：`.codex-os/` 是唯一 AIOS 配置和运行目录；`.codex/` 仅保存 Codex Host 配置与 Hook。
- `BR-004`：Codex Host 负责推理、Session 和工具执行；AIOS 负责确定性治理，不在 `0.2.0` 内嵌第二个模型客户端。
- `BR-005`：事实冲突时停止受影响的状态转换，先更新治理文档或接受 ADR，再改变实现行为。

## 2. 仓库准备度

- `BR-006`：`project_init` 可以生成文档与配置骨架，但仓库预检完成前必须返回 `repository_ready=false`，且不得创建代码任务或 Worktree。
- `BR-007`：正式模式只接受 `github.com` 或项目配置允许的 GitHub Enterprise 域名，SSH 与 HTTPS 形式均可。
- `BR-008`：`fixture_local_only` 只允许由自动化测试配置启用；普通 CLI/MCP 参数、环境变量或项目文档不得放宽该策略。
- `BR-009`：首个写任务前必须验证 Git 顶层目录、干净工作树、无未解决冲突、GitHub remote 可访问、当前 HEAD 已存在于 upstream、目标分支存在和所需权限。
- `BR-010`：任一正式仓库预检失败时，只读 status、docs、memory search 和 repository check 可用；写任务、Worktree、执行、删除和发布保持阻塞。

## 3. 仓库卫生与文件生命周期

- `BR-011`：项目自有复制式目录名禁止用于版本管理，包括 `old`、`backup`、`copy`、`final`、`new`、`temp`、`tmp`、`debug`、独立 `v1/v2/vN` 和 `src_backup` 等规则匹配项。
- `BR-012`：项目自有可疑文件必须报告，包括 `final.py`、`backup.py`、`fix_*_vN.py`、`test_new.py`、`.bak`、无登记日志、临时转换脚本和未声明调试文件。
- `BR-013`：Git 跟踪内容不得包含缓存、日志、编译产物、Secret、AIOS 运行状态或普通临时文件。
- `BR-014`：初始化 `.gitignore` 必须覆盖日志、缓存、构建、环境、Node/Python 依赖、调试文件和 AIOS 运行目录。
- `BR-015`：卫生扫描必须排除 `.git`、虚拟环境、依赖目录、受管 Worktree 与 `.codex-os/state|logs|cache|tmp|artifacts`，避免扫描依赖内部合法版本目录。
- `BR-016`：`disposable` 文件只在任务结束时自动清理由 Runtime 创建、登记且解析后仍位于批准根目录的确定路径。
- `BR-017`：`promotable` 文件只有迁入 `scripts/`、`tools/` 或 `utilities/`，并补齐说明、测试和负责人后才能长期保留。
- `BR-018`：`audit-evidence` 按保留策略保存；用户未跟踪文件、失败 Worktree 和未知文件必须人工确认，自动清理不得触碰。

## 4. 文档与 Gate 证据

- `BR-019`：受治理文档必须提供 `schema_version`、`document_version`、`status`、`owner` 和 `requirement_refs`，且文档版本与目标发布版本一致。
- `BR-020`：未批准占位内容、草案状态、缺少必需章节、过期元数据或失效链接均阻塞相应 Gate。
- `BR-021`：API、数据库、安全或架构变更必须同步对应事实文档；重大变化必须有 ADR；用户可见或治理变化必须更新 CHANGELOG。
- `BR-022`：`ArtifactEvidence` 必须包含路径、类型、SHA-256、来源 Commit、任务和状态。
- `BR-023`：`CheckEvidence` 必须包含检查名称、command hash、沙箱 execution ID、退出码、报告路径和执行时间。
- `BR-024`：`ReviewEvidence` 必须包含 Reviewer、被审 Commit、结论、发现、风险和报告引用。
- `BR-025`：`GateEvidenceBundle` 必须绑定 Gate、必需产物、检查、Review、审批人、版本和当前 `state_version`。
- `BR-026`：自由文本成功声明、错误来源 Commit、缺失报告或未执行检查均不能作为通过证据。
- `BR-027`：Gate 批准仅对请求中的 Gate、证据包和状态版本有效，不能隐含批准后续 Gate、发布或部署。

## 5. 多 Agent 与 Handoff

- `BR-028`：Workflow 持久化 `profiles`、`target_branch`、`integration_branch`、`base_commit`、并行上限和任务图。
- `BR-029`：每个 Workflow 建立独立集成 Worktree；写任务从同一已记录集成 HEAD 创建任务 Branch/Worktree。
- `BR-030`：最大并行 Agent 数为 4；只有允许写路径互不重叠、依赖已满足且影响分析确定的任务可以并行。
- `BR-031`：写路径重叠、产物依赖或影响范围不确定的任务必须建立显式依赖并串行。
- `BR-032`：Codex Host 根据 `next_actions` 分派 Agent；Runtime 只生成确定性任务契约，不执行模型推理。
- `BR-033`：Agent 完成后必须提交并推送任务分支，再提交绑定该 Commit 的结构化 Handoff。
- `BR-034`：Handoff 状态只能从 `ready` 进入 `accepted`、`rejected` 或 `blocked`；`ready` 不解锁依赖任务，也不触发集成合并。
- `BR-035`：`rejected` 由原 Agent 在原 Worktree 修复；`blocked` 保留现场并阻塞下游；任何新 Commit 都必须重新 Review。
- `BR-036`：Reviewer 与 Security Reviewer 不直接修改被审代码，只通过结构化 Review 接口写入审计结论；QA/Security 报告来自独立验证任务。
- `BR-037`：单个任务完成只递增任务自身乐观版本，不直接推进 Workflow；协调器在事务内重新计算任务组状态并单独递增 Workflow `state_version`。
- `BR-038`：只有任务组全部 accepted 且已合入集成分支，join barrier 才可解锁下一阶段。

## 6. 集成与 Worktree 清理

- `BR-039`：集成合并必须持有独占锁并使用 `--no-ff`，保存合并 Commit、父 Commit、目标分支和冲突状态。
- `BR-040`：Runtime 永不自动 force push、rebase 或覆盖合并冲突；冲突必须返回生产者处理并重新验收。
- `BR-041`：`worktree_cleanup` 只清理已合并、干净、无开放 Review 且由人显式批准的受管 Worktree。
- `BR-042`：失败、blocked、dirty、存在未知文件或仍被任务引用的 Worktree 必须保留。

## 7. 受控执行

- `BR-043`：测试、构建、代码执行与高风险命令统一通过 ExecutionService，并绑定 `run_id/task_id/worktree`。
- `BR-044`：受控执行使用锁定 digest 镜像、非 root、默认断网、只读根、最小 capability、资源限制和只包含当前任务 Worktree/受管制品目录的挂载。
- `BR-045`：执行证据必须保存 execution ID、command hash、退出码、脱敏日志、镜像 digest、开始/结束时间和执行后 dirty 检查。
- `BR-046`：`doctor` 无可用沙箱时，需求与文档只读工作可以继续；实现、测试、删除、迁移执行和发布保持 `blocked`。
- `BR-047`：Host 执行结果不能冒充沙箱执行证据；兼容健康检查必须明确标注其证据级别。

## 8. 发布与版本

- `BR-048`：CHANGELOG、Release Manifest 和回滚文档写入 Release Worktree，Review 后合入集成分支。
- `BR-049`：Wheel、压缩包、SBOM 和校验和写入 `.codex-os/artifacts/<run-id>/` 并被 Git 忽略；SQLite 与 Manifest 保存 hash、来源 Commit 和生成环境。
- `BR-050`：需求基线为 `REQ-1.6.2`；软件、CLI、Plugin 核心版本为 `0.2.0`；Plugin API、配置、文档与 Profile Schema 为 `1.2`；SQLite 通过 `0001-0007` 管理。
- `BR-051`：Release Manifest 必须绑定版本、构建 Commit、GitHub PR、merge Commit、tag、文档版本、配置 hash、依赖锁 hash、制品 hash 和 Memory 记录。
- `BR-052`：G4 审批必须携带 PR 编号、URL、merge Commit、版本和独立发布授权；Runtime 必须验证 PR head 对应集成分支且目标分支包含集成提交。
- `BR-053`：只有 G4 批准后才允许创建并推送 annotated tag `v0.2.0`；GitHub Release 失败时 Workflow 保持阻塞。
- `BR-054`：本版本不包含生产部署；发布授权不能隐含部署权限。

## 9. 迁移与兼容

- `BR-055`：SQLite 迁移顺序固定为 `0004_repository_governance.sql`、`0005_multi_agent_coordination.sql`、`0006_memory_fts.sql`、`0007_release_closure.sql`，不得改写已发布迁移。
- `BR-056`：迁移前强制备份数据库并生成 checksum；迁移必须验证外键、重复执行和备份恢复。
- `BR-057`：旧活动 Workflow 在下一次状态转换时进入 `MIGRATION_REVALIDATION_REQUIRED`，完成新 Gate 证据审计后才能恢复。
- `BR-058`：配置/API 1.2 兼容读取/调用 1.0/1.1；旧 verification 无运行参数时仅执行健康检查并返回弃用提示，不能满足 1.2 Gate。
- `BR-059`：未配置 GitHub 的旧项目可执行 status、docs 与只读检查，但所有仓库写任务保持阻塞。

## 10. Memory 与 Routing

- `BR-060`：Memory 状态只能是 `pending/active/needs_review/superseded/revoked/expired/deleted`；旧 `candidate` 迁移为 `pending`，旧 `invalidated` 迁移为 `needs_review`。
- `BR-061`：ADR 接受、Bug 关闭、Release、回滚和重要失败自动产生候选 Memory，但不能自动激活。
- `BR-062`：只有来源 hash、Secret 检查、项目范围和置信度通过后 Memory 才能激活；来源变化必须进入 `needs_review`。
- `BR-063`：Memory 检索使用 FTS5，按项目隔离，并支持类型、状态、标签、时间、来源和 supersedes 关系过滤。
- `BR-064`：frontend、backend 和 large Profile 必须参与路由、任务生成、证据与审批；选择理由保存为 `routing_decisions`。
- `BR-065`：涉及前端实现的任务必须可路由到 `frontend-engineer` Profile 与 Frontend Implementation Skill。
- `BR-066`：项目类型或 impact paths 表明存在前端页面时必须启用 `frontend-project`，不得通过显式 Profile 或人工 override 移除原型门禁。
- `BR-067`：前端实现前必须提交 Commit-bound、离线自包含的 HTML 交互原型；`html-prototype-validator` 和非生产者的 `ux-prototype` accepted Review 均为 G2 必需证据，原型 Commit 或 hash 变化立即使确认失效。

## 11. 规则例外与优先级

1. 例外必须记录适用范围、原因、风险、替代控制、批准人和失效日期；例外不能删除审计记录或绕过 Gate。
2. 安全、数据完整性、证据真实性和发布授权规则不可由普通项目参数关闭。
3. 需求与事实冲突时按 `AGENTS.md`、项目主文档、范围、已接受 ADR 和子系统契约的优先级处理，并阻塞受影响转换。
4. 本规则集通过 G1 后冻结业务约束；实现细节与技术权衡必须在 G2 证据中单独批准。
