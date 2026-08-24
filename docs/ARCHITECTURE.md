# AI Engineering OS 0.2.0 系统架构

<!-- codex-os-document: {"schema_version":"1.1","document_version":"0.2.0","status":"approved","owner":"architect","requirement_refs":["REQ-1.6.2","GOV-001","CFG-001","REPO-001","GATE-001","AGENT-001","HANDOFF-001","WORKTREE-001","RELEASE-001","EXEC-001","DOC-001","HYGIENE-001","VERSION-001","MEMORY-001","ROUTING-001","FRONTEND-001"]} -->

本架构实现 [ADR-0003](ADR/ADR-0003-governance-runtime-boundary.md)。它保留现有 Python 状态机、SQLite、Git 证据、Worktree、Plugin 和 OCI 沙箱，不引入第二个模型客户端。

## 1. 定位与事实源

`ARC-001`：Codex Host 负责推理、Session、工具调用和工程执行；AI Engineering OS 是确定性治理与执行层，负责仓库、Workflow、证据、审批、隔离、版本和发布规则。

`ARC-002`：Markdown/Git 保存需求、设计、ADR、CHANGELOG 与发布事实；SQLite 保存运行状态、事件、索引、证据和 provenance。两者以路径、Commit 和 SHA-256 关联，不复制事实正文到 SQLite。

`ARC-003`：目录职责固定如下：

```text
.codex/                 Codex Host 配置与 Hook
.codex-os/              AIOS 配置、入口索引和被忽略的运行目录
  bootstrap.md          启动与读取顺序索引
  rules.md              规则索引
  workflow.md           Workflow/Gate 索引
  memory.md             Memory 索引
  state/ logs/ cache/   被 Git 忽略的运行状态
  tmp/ artifacts/       被 Git 忽略的临时与发布制品
.agents/skills/         项目专属 Skill；不得覆盖 Plugin 同名 Skill
plugins/ai-engineering-os/skills/  Plugin 默认 Skill
docs/                   领域事实源
.worktrees/             受管任务与集成 Worktree；被 Git 忽略
```

## 2. 运行拓扑

```text
Codex Host
  -> Plugin Skills / MCP Adapter / Typer CLI
  -> Application Services
       RepositoryGovernanceService
       WorkflowCoordinator + GateService
       CoordinationService + HandoffReviewService
       ExecutionService
       DocumentGovernanceService
       ReleaseService + VersionService
       MemoryService
  -> Domain Contracts
       Project/Repository/Evidence/Workflow/TaskGroup/Task/Handoff/Release/Memory
  -> Adapters
       Git/GitHub/Worktree/Docker/Podman/File System
  -> Infrastructure
       SQLite repositories + append-only events + migrations 0001-0006
```

Runtime 不调用模型 API。Codex Host 根据 `next_actions` 选择子 Agent；每个 action 是包含 Agent、Skill、输入、输出 Schema、允许路径、Branch 和 Worktree 的确定性任务契约。

## 3. 组件所有权

| ID | 组件 | 负责 | 不负责 |
| --- | --- | --- | --- |
| `CMP-REPO` | Repository Governance Service | Git/GitHub 准备度、卫生、`.gitignore`、一次性文件登记和版本矩阵 | 创建模型任务、自动删除未知文件 |
| `CMP-WF` | Workflow Coordinator | 双轴状态、Profile 路由、任务组、依赖、join、Gate 和恢复 | 修改领域事实或自动批准 Gate |
| `CMP-EVIDENCE` | Gate Evidence Service | 产物、检查、Review、Gate bundle 的来源与完整性 | 执行测试或信任自由文本结果 |
| `CMP-COORD` | Coordination Service | 集成 Worktree、任务 Worktree、Handoff Review、合并锁和清理条件 | 模型推理、force push、自动解决冲突 |
| `CMP-EXEC` | Execution Service | 锁定 OCI 执行、挂载/命令策略、脱敏日志和 dirty 检查 | 宿主机降级执行、高风险权限放宽 |
| `CMP-DOC` | Document Governance Service | 元数据、章节、状态、占位、过期、链接和影响分析 | 成为领域事实源本身 |
| `CMP-REL` | Release/Version Service | Release Worktree、Manifest、SBOM/hash、PR/merge/tag/Release 验证 | 生产部署、无 G4 发布 |
| `CMP-MEM` | Memory Service | 候选、复核、生命周期、FTS5、来源变化和项目隔离 | 保存 Secret、原始聊天或自动改写 Git 历史 |
| `CMP-STORE` | SQLite repositories | 乐观锁、事务、事件、索引、迁移与恢复 | 保存 Markdown 事实正文 |

## 4. 正式仓库准备度

### 4.1 模式来源

正式/fixture 模式只从经过严格 Schema 校验的项目配置产生。普通 CLI/MCP 不接收放宽参数；`fixture_local_only` 需要测试进程注入不可由项目文件表达的测试能力令牌。

### 4.2 预检顺序

```text
project_init
  -> 生成骨架与 repository_ready=false
repository_check(read-only)
  -> Git top-level
  -> unresolved conflicts / clean worktree
  -> remote URL parser + GitHub/GHE allowlist
  -> ls-remote reachability
  -> upstream ref + current HEAD ancestry/equality
  -> target branch exists
  -> push/PR capability probe without repository mutation
  -> hygiene + version matrix + ignore rules
  -> RepositoryCheckReport
workflow_start / first write allocation
  -> require latest passing report bound to HEAD/config hash
```

报告绑定 `project_id`、HEAD、配置 hash、目标分支和检查时间。任一绑定值变化即失效并重检。失败只阻塞写任务；status、docs、memory search 与 repository check 保持可用。

### 4.3 卫生规则

扫描分为 Git 跟踪内容、项目自有目录/文件、忽略规则和登记生命周期。路径遍历首先解析真实路径并排除 `.git`、虚拟环境、依赖目录、受管 Worktree 与 `.codex-os/state|logs|cache|tmp|artifacts`；junction/symlink 越界直接报告 `PATH_ESCAPE`。

`disposable` 自动清理必须同时满足：Runtime 创建、数据库登记、目标仍位于批准根、无用户修改、任务进入可清理状态。`promotable` 和 `audit-evidence` 不进入自动删除路径。

## 5. 多 Agent 协调模型

### 5.1 Workflow 拓扑

```text
target: main
  ^
  | G4 GitHub PR
workflow/<run-id>/integration  (独立集成 Worktree + 独占 merge lock)
  ^             ^             ^
  | --no-ff     | --no-ff     | --no-ff
agent/A/T1   agent/B/T2    agent/C/T3
worktree A   worktree B    worktree C
```

Workflow 创建时保存 `profiles`、`target_branch`、`integration_branch`、`base_commit`、`integration_head` 和 `max_parallel=4`。集成分支只由 Runtime 合并服务写入。

### 5.2 任务图生成

1. Routing Decision 选择基础 Workflow 与 frontend/backend/large Profile，并保存输入 hash、理由、风险和人工覆盖。
2. Planner 为每个任务声明 `allowed_paths`、输入产物、输出 Schema、风险和依赖。
3. 调度器对所有写路径做规范化、祖先/子路径重叠、glob 交集和符号链接边界检查。
4. 路径重叠、产物依赖或影响不确定的任务添加 `task_dependencies`；其余任务进入同一 task group。
5. Ready set 按依赖、group 状态和并发租约选择最多 4 个任务，从同一记录的 `integration_head` 创建 Worktree。

### 5.3 状态与 Handoff

任务执行状态与 Handoff 审核状态独立：

```text
Task: pending -> running -> completed | blocked | failed | cancelled
Handoff: ready -> accepted | rejected | blocked
Merge: pending -> merged | conflicted | blocked
Group: pending -> running -> joining -> completed | blocked | failed
```

`task_complete` 只把任务变为 completed 并创建 `ready` Handoff；不会推进 Workflow。Reviewer 用 `handoff_review` 绑定被审 Commit 和 ReviewEvidence：

- accepted：获得集成合并资格。
- rejected：任务回到原 Agent/原 Worktree；新 Commit 使旧 Review 失效。
- blocked：保留现场并阻塞所有依赖任务。

### 5.4 集成与 join

accepted Handoff 进入单消费者合并队列。Runtime 在 SQLite 获取带过期时间的集成锁，确认 integration HEAD 未漂移、任务分支包含被审 Commit、Worktree 干净，然后 `git merge --no-ff --no-edit <task-branch>`。成功保存 merge Commit 与父 Commit；冲突执行 `git merge --abort`，标记 conflicted 并返回生产者，不自动修改任务分支。

每个任务完成在 `WHERE state_version=?` 条件下更新任务版本。协调器另起事务重算 group：只有所有任务 Handoff accepted 且 merge 状态 merged，才将 group 置 completed、递增 Workflow `state_version` 并越过 join barrier。

## 6. 结构化 Gate 证据

```text
ArtifactEvidence(path, kind, sha256, source_commit, task_id, status)
CheckEvidence(name, command_hash, execution_id, exit_code, report_path,
              source_commit, started_at, ended_at)
ReviewEvidence(reviewer, reviewed_commit, decision, findings, risks, report_ref)
GateEvidenceBundle(gate, run_id, state_version, version, artifacts[], checks[],
                   reviews[], approval, bundle_hash)
```

Gate Service 从数据库读取证据，不接收调用方自报的 `passed`。bundle 采用规范 JSON 计算 SHA-256，并绑定当前 `state_version`；证据或状态变化会使旧审批失效。

| Gate | 强制检查 |
| --- | --- |
| G0 | 目标、范围、成功标准、风险、Routing Decision |
| G1 | 需求、用户故事、业务规则、范围、非草案元数据和可测试验收 |
| G2 | 官方研究、版本/License、技术栈、架构、API、数据库、安全、迁移和 Accepted ADR |
| G3 | pytest/coverage、Ruff、Pyright、Secret、依赖、OCI、安全 Review、代码 Review，全部绑定 integration HEAD |
| G4 | Manifest、SBOM、checksums、rollback、CHANGELOG、ADR 索引、Memory、GitHub PR/merge、版本和发布授权 |

## 7. 受控执行

ExecutionRequest 必须绑定 `run_id/task_id/worktree_id`、命令 argv、风险、镜像 digest、超时和受管挂载。ExecutionService 验证任务租约、Worktree 归属/干净基线、命令 allowlist 与审批后，选择 Docker 或 Podman Adapter。

`0.2.0` 目标镜像锁定为：

```text
python:3.12.14-slim-bookworm@
sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134
```

该 index digest 由 Docker Registry v2 对标签 `3.12.14-slim-bookworm` 的 `Docker-Content-Digest` 于 2026-08-24 核验。实现仍必须实际拉取、inspect、生成 SBOM/漏洞报告并通过策略阈值后替换历史 3.12.13 digest。

容器固定 `--network none --read-only --cap-drop ALL --security-opt no-new-privileges`、非 root UID/GID、PID/CPU/内存/时长限制；只挂载当前任务 Worktree 和按类型批准的 artifacts/cache。日志先脱敏再落盘，数据库只保存引用与 hash。执行结束重新检查 Git dirty；异常 dirty 使验证失败。

## 8. Release 与版本

G3 后创建专用 Release task/Worktree。CHANGELOG、Release Manifest 和回滚文档在该 Worktree 提交并经 Handoff Review/集成合并；Wheel、源码包、SBOM、checksums 写入 `.codex-os/artifacts/<run-id>/`。

Manifest 绑定 `REQ-1.6.2`、软件/CLI/Plugin `0.2.0`、Plugin API/配置 `1.1`、SQLite `0006`、integration build Commit、PR、merge Commit、目标 tag、文档/配置/lock/制品 hash 和 Memory IDs。

G4 顺序：验证完整证据 -> 验证 GitHub PR head/base/merge -> 验证目标分支包含 integration HEAD -> 记录独立发布授权 -> 创建/推送 annotated tag -> 创建 GitHub Release。任一步失败保持 blocked，重复调用按 Manifest hash 幂等恢复。部署不在本系统权限内。

## 9. Memory 与 Routing

Memory 事实正文仍位于 Markdown/Git 或脱敏审计制品。ADR accepted、Bug closed、Release、rollback 和重要 failure 事件生成 `pending` 候选。Review 校验来源 hash、Secret、项目范围和置信度后才可 active；来源漂移进入 needs_review。

FTS5 只索引 `title/tags/search_text` 与记录 ID，不索引 Secret 或原始聊天。查询先以 `project_id`、状态和 scope 过滤，再执行 FTS，跨项目链接需要显式批准。

Profile Router 将 `frontend-project`、`backend-project`、`large-project` 组合为 Routing Decision。frontend 影响必须生成 `frontend-engineer` 任务与 Frontend Implementation Skill；large Profile 只扩大任务图和必需 Review，不放宽并发上限或安全策略。

## 10. 幂等、并发与失败恢复

- 所有写接口使用 `idempotency_key = hash(project_id, operation, normalized_input, expected_version)`。
- Workflow、Task、TaskGroup、Handoff Review、Integration Merge 与 Memory Review 使用独立乐观版本。
- SQLite 写事务使用 `BEGIN IMMEDIATE`、外键开启和短事务；Git/OCI/GitHub 外部操作先写 intent，完成后写 result，恢复器根据 intent/result 对账。
- 进程崩溃时：未完成执行标记 interrupted；过期任务/合并租约可由同一 run 接管；不确定 Git 合并通过 HEAD/parents 重建，不重复合并。
- 配置、HEAD、source hash 或证据变化会使相关 repository report、Review、Gate approval 和 release authorization 失效。
- 自动恢复不执行删除、force push、rebase、tag、Release 或部署。

## 11. 迁移与回滚

启动 0.2.0 时，在任何状态写入前复制数据库到 `.codex-os/state/backups/` 并写 SHA-256；验证备份可打开、foreign_key_check 和 integrity_check 后，依次应用 0004-0006。每条迁移在单独事务中记录 checksum。

迁移失败关闭写服务并保留原库/失败副本；恢复通过校验后的备份原子替换。活动旧 Workflow 在下一次转换进入 `MIGRATION_REVALIDATION_REQUIRED`，新 Gate bundle 审计完成后才恢复。应用降级不反向执行 destructive SQL，只恢复备份或使用前一版本只读模式。

## 12. 信任边界与部署假设

- 受信：已安装 Runtime 代码、只读 Plugin 资产、校验后的项目配置、SQLite 迁移和锁定镜像 digest。
- 不受信：用户仓库内容、Agent 输出、Handoff 自报、远端响应、依赖包、日志文本、符号链接/junction 和环境变量。
- 半受信：GitHub 身份/权限；每个 G4 操作重新查询并绑定响应 ID/Commit。
- 唯一支持宿主：Windows + Python 3.12 + Git + 可用 Docker/Podman；容器运行 Linux workload。
- 网络默认关闭；只有 repository/GitHub/依赖审计等明确只读或批准的宿主适配器可访问 allowlist 域名。

## 13. 可观测性与验证缝

每个外部调用生成 `request_id`，每个状态事件记录 project/run/task/group/handoff/execution IDs、before/after version、source Commit、config hash、result/error code 和时间。日志必须脱敏，报告文件计算 hash。

Adapter 均通过 Protocol 注入，单元测试使用 deterministic fake；公共 MCP E2E 必须走真实服务器和 SQLite/Git Worktree；OCI 安全使用真实 Podman 测试；GitHub G4 使用真实 PR/merge 证据，离线 fixture 只能覆盖负向与解析逻辑。

## 14. 实现模块映射

| 目标模块 | 责任 |
| --- | --- |
| `domain/governance.py` | repository/evidence/version/release 值对象与严格枚举 |
| `domain/workflow.py` | Workflow/Task/TaskGroup/Handoff/Review/NextActions 状态契约 |
| `application/repository.py` | Repository Governance Service |
| `application/coordination.py` | DAG、调度、Review、join、集成与清理用例 |
| `application/gates.py` | Gate bundle 构建与校验 |
| `application/release.py` | Release Worktree、Manifest 与 G4 验证 |
| `application/execution.py` | 受控执行主路径；保留现有 Adapter |
| `infrastructure/migrations/0004-0006.sql` | 追加 Schema |
| `infrastructure/*_repository.py` | 原子持久化与乐观锁 |
| `adapters/github.py` | GitHub remote/PR/merge/tag/Release 查询与操作 |
| `cli/app.py` / `cli/mcp_server.py` | Plugin API 1.1 适配；不包含业务规则 |

## 15. G2 完成定义

本架构只有在 [API_SPEC.md](API_SPEC.md)、[DATABASE.md](DATABASE.md)、[SECURITY.md](SECURITY.md)、[OPEN_SOURCE_RESEARCH.md](OPEN_SOURCE_RESEARCH.md)、[TECH_STACK.md](TECH_STACK.md) 和 Accepted [ADR-0003](ADR/ADR-0003-governance-runtime-boundary.md) 内容一致，且所有公共输入/输出、失败、并发、迁移、回滚、信任边界与测试缝均无占位契约时，才可作为实现授权。
