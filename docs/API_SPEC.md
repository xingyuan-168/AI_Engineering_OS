# AI Engineering OS Plugin API 1.2 与 CLI 契约

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"approved","owner":"architect","requirement_refs":["REQ-1.6.2","REPO-001","GATE-001","AGENT-001","HANDOFF-001","WORKTREE-001","RELEASE-001","EXEC-001","MEMORY-001","ROUTING-001"]} -->

Plugin API 1.2 由 MCP 与 Typer CLI 共享同一输入模型、应用服务、响应封装和错误码。接口只执行确定性治理用例；不触发模型推理。所有路径输入为项目根相对路径或显式解析的 coordinator root，禁止任意绝对输出路径；受管任务 Worktree 发起的公共调用不得静默重定向到其他 checkout。

## 1. 通用响应

```json
{
  "api_version": "1.2",
  "ok": true,
  "request_id": "REQ-...",
  "correlation_id": "CORR-...",
  "run_id": "RUN-...",
  "workflow_phase": "implementation",
  "run_status": "running",
  "state_version": 7,
  "data": {},
  "next_actions": [],
  "next_action": null,
  "warnings": [],
  "error": null
}
```

- `next_actions` 是权威新字段；当且仅当长度为 1 时，`next_action` 返回相同对象以兼容 1.0。
- `warnings` 用于弃用或非阻塞风险；不得承载 Gate 失败。
- 错误响应 `ok=false`，`error={code,message,details,retryable}`，不得同时返回成功状态变更。
- RFC3339 时间统一为 UTC；hash 统一为小写十六进制 SHA-256；Git Commit 使用完整 40 位 SHA-1/64 位 SHA-256，由仓库格式决定。

## 2. 公共值对象

### 2.1 `NextAction`

```json
{
  "kind": "model_task|approval|host_operation|complete",
  "operation_id": "OP-...",
  "task_id": "TASK-...",
  "task_group_id": "GROUP-...",
  "agent": "backend-engineer",
  "skill": "backend-implementation",
  "prompt": "bounded task prompt",
  "input_artifacts": ["docs/ARCHITECTURE.md"],
  "output_schema": {},
  "allowed_paths": ["src/codex_ai_os/application/"],
  "dependencies": ["TASK-..."],
  "branch": "agent/backend-engineer/TASK-...",
  "worktree": "D:/.../.worktrees/RUN-.../backend-engineer/TASK-...",
  "risk_level": "high",
  "requires_repository_change": true,
  "expected_task_version": 1,
  "expected_state_version": 7
}
```

### 2.2 `ArtifactEvidenceInput`

```json
{
  "path": "docs/ARCHITECTURE.md",
  "kind": "document|source|migration|test-report|sbom|checksum|manifest|rollback",
  "sha256": "64-hex",
  "source_commit": "full-commit",
  "task_id": "TASK-...",
  "status": "produced|verified|superseded"
}
```

Runtime 重新读取文件、验证 real path、hash、Git blob/Commit 可达性和任务允许路径。受管制品区文件不要求被 Git 跟踪，但必须绑定 execution ID 与 source Commit。

### 2.3 `CheckEvidenceInput`

```json
{
  "name": "pytest",
  "command_hash": "64-hex",
  "execution_id": "EXEC-...",
  "exit_code": 0,
  "report_path": ".codex-os/artifacts/RUN-.../pytest.json",
  "report_sha256": "64-hex",
  "source_commit": "full-commit",
  "started_at": "RFC3339",
  "ended_at": "RFC3339"
}
```

Runtime 必须从 `executions` 表重建上述字段；调用方不能仅凭输入创建检查证据。

### 2.4 `ReviewEvidenceInput`

```json
{
  "reviewer": "reviewer-id",
  "reviewed_commit": "full-commit",
  "decision": "accepted|rejected|blocked",
  "findings": [{"id":"FINDING-...","severity":"low|medium|high|critical","status":"open|resolved","summary":"..."}],
  "risks": ["RISK-..."],
  "report_ref": ".codex-os/artifacts/RUN-.../review.json",
  "report_sha256": "64-hex"
}
```

Reviewer/Security Reviewer 只能通过该接口写 Review 元数据，不拥有被审代码写路径。

## 3. 项目与仓库接口

### 3.1 `project_init` / `codex-os init`

输入：

```json
{"project_root":"...","project_id":"PROJECT-...","name":"...","project_type":"backend|frontend|fullstack|desktop|generic","risk_level":"low|medium|high|critical"}
```

输出 `created_paths`、`config_schema`、`database_schema`、`repository_ready=false|true` 和 `repository_blockers`。初始化可生成骨架，不得因为没有 GitHub 而失败，也不得创建代码任务/Worktree。

幂等键：`project_id + normalized_root + config_input_hash`。同项目 ID 指向不同 root 返回 `PROJECT_ID_CONFLICT`。

### 3.2 `repository_check` / `codex-os repo-check`

输入：

```json
{"project_root":"...","target_branch":"main"}
```

普通调用不能提交 `fixture_local_only`。模式从受信配置/测试能力令牌读取。

输出：

```json
{
  "repository_ready": true,
  "audit_id": "REPOAUDIT-...",
  "mode": "formal|fixture_local_only",
  "head_commit": "...",
  "target_branch": "main",
  "remote": {"name":"origin","host":"github.com","reachable":true,"upstream":"origin/branch","head_pushed":true,"permissions":{"push":true,"pull_request":true}},
  "working_tree": {"clean":true,"conflicts":[]},
  "hygiene": {"valid":true,"findings":[]},
  "versions": {"valid":true,"matrix":{}},
  "blockers": [],
  "checked_at": "RFC3339"
}
```

这是只读接口。远端 URL 只返回脱敏 host/name；凭据和完整含 token URL 不落库。

## 4. Workflow 与任务接口

### 4.1 `workflow_start`

输入：

```json
{
  "project_root":"...",
  "workflow_name":"new-project|feature-development|bug-fix|release",
  "goal":"non-empty",
  "profiles":["backend-project","large-project"],
  "target_branch":"main",
  "impact_paths":["src/codex_ai_os/application/workflow.py","tests/unit/test_workflow.py"],
  "dependency_count":1,
  "release_required":false,
  "override_reason":null
}
```

规则：

1. 标准化目标、Profile、目标分支和七维 Routing Input，计算 start idempotency key。
2. 在首个 `requires_repository_change=true` action 分配前验证与当前 HEAD/config hash 绑定的 passing repository audit。
3. 建立 `workflow/<run-id>/integration` 分支与 Worktree，保存 base/integration head。
4. 返回一个或多个 `next_actions`；最多 4 个写任务，且必须通过路径冲突检查。
5. API 1.2 的实现任务允许路径只来自批准的 `impact_paths`；未映射路径返回 `ROUTING_PATH_UNMAPPED`。Profile alias 仅兼容读取并返回弃用 warning。

### 4.2 `workflow_status`

输入：`project_root`、`run_id`。

输出除双轴状态外包含：

```json
{
  "profiles": [],
  "routing_decision": {"requested_profiles":[],"effective_profiles":[],"dimension_scores":{},"override":false,"dependencies":[],"policy_hash":"64-hex"},
  "target_branch": "main",
  "integration_branch": "workflow/RUN-.../integration",
  "base_commit": "...",
  "integration_head": "...",
  "task_groups": [{"id":"GROUP-...","status":"running","state_version":2,"join_ready":false}],
  "tasks": [],
  "dependencies": [],
  "handoffs": [],
  "integration_merges": [],
  "gate_evidence_summary": {},
  "blockers": []
}
```

status 严格只读，不获取写租约，不推进 Workflow。

### 4.3 `workflow_step`、`workflow_resume`

- `workflow_step` 只重算并返回 `next_actions`，不分配新任务或推进状态。
- `workflow_resume` 需要 `expected_state_version`，重新校验 repository/config/HEAD/证据和过期租约；`MIGRATION_REVALIDATION_REQUIRED` 未完成时返回该 blocker。

### 4.4 `task_complete`

Plugin API 1.2 输入：

```json
{
  "project_root":"...",
  "run_id":"RUN-...",
  "task_id":"TASK-...",
  "expected_task_version":2,
  "change_kind":"none|repository",
  "artifacts":["ArtifactEvidenceInput"],
  "checks":["CheckEvidenceInput"],
  "git":{"branch":"agent/...","commit_sha":"...","remote_name":"origin","push_status":"pushed|not_required"}
}
```

仓库写任务必须提供结构化 artifacts/checks 和 Git 证据；自由文本 `verification_results` 仅在配置 1.0 兼容模式保存为 `legacy-note`，不能满足任何 Gate。成功后 Task 变为 completed、创建 Handoff `ready`，但不推进 Workflow、不启动依赖任务、不自动合并。

重复提交相同 task/version/commit 返回原结果；Commit 或证据不同返回 `IDEMPOTENCY_CONFLICT`。

## 5. Handoff、集成与清理

### 5.1 `handoff_review`

输入：

```json
{
  "project_root":"...",
  "run_id":"RUN-...",
  "task_id":"TASK-...",
  "expected_handoff_version":1,
  "idempotency_key":"caller-stable-key",
  "decision":"accepted|rejected|blocked",
  "reviewer":"reviewer-id",
  "reason":"non-empty",
  "review":"ReviewEvidenceInput"
}
```

约束：Adapter 使用受信 `InvocationContext` 的 principal 作为权限事实；请求体 `reviewer` 仅为 1.0/1.1 显示兼容字段，不能提升权限。Reviewer 与 producer 不得相同；reviewed Commit 必须等于 Handoff source Commit；开放 high/critical finding 禁止 accepted。

accepted 后 Runtime 在同一事务保存 Review、期望版本和 `integration_merge` Host Operation，输出包含 operation ID 的 `next_actions`。Host 执行/恢复该 operation 后才进行持锁 `--no-ff` 合并。冲突返回业务成功 `ok=true` 但 `merge.status=conflicted`、run blocked 与 `MERGE_CONFLICT` blocker，便于持久化恢复；push 失败不得把 accepted Handoff 改成 rejected。

### 5.2 `worktree_cleanup`

输入：`project_root`、`run_id`、`task_id|worktree_id`、`approved_by`、`reason`、`expected_worktree_version`。

Runtime 重新验证：受管路径、分支已合入 integration/target、Worktree clean、无开放 Review/任务/执行/冲突、无未知文件。任一不满足返回 `WORKTREE_NOT_CLEANABLE`，且不删除。成功记录清理审批与 Git worktree remove 结果；不删除远端分支，除非另有发布后授权。

## 6. Gate 与审批

### 6.1 `approval_submit`

通用输入：

```json
{"project_root":"...","run_id":"RUN-...","gate":"G0|G1|G2|G3|G4","approved":true,"reviewer":"...","reason":"...","expected_state_version":7,"evidence_bundle_hash":"64-hex"}
```

G4 必增字段：

```json
{
  "release_authority": {"authorized":true,"scope":"tag-and-github-release","authorized_by":"..."},
  "version":"0.2.0",
  "pull_request":{"number":123,"url":"https://github.com/.../pull/123","head":"workflow/RUN-.../integration","base":"main","head_commit":"..."},
  "merge_commit":"..."
}
```

Runtime 构建并校验当前 Gate bundle；调用方 hash 与当前 bundle 不同返回 `EVIDENCE_STALE`。G4 还实时查询 GitHub，验证 PR/branch/Commit/merge 包含关系。批准 Gate 不自动授权部署。

API 1.2 的 G4 成功响应表示发布授权与 `release_publish` Operation 已持久化，Workflow 仍为 `running` 并返回 `host_operation` next action；它不表示 tag/Release 已创建。只有 executor 对账成功后才返回 `workflow_phase=completed` 与最终发布信息。

## 7. 执行与验证

### 7.1 `verification_run`

新输入：

```json
{"project_root":"...","run_id":"RUN-...","task_id":"TASK-...","checks":["ruff","pyright","pytest","secret","dependency","security","plugin","docs"],"expected_task_version":1}
```

Runtime 从 task 找 Worktree，通过 ExecutionService 建立一个或多个锁定 OCI execution，输出 `CheckEvidence[]` 与报告引用。所有检查绑定执行前 Commit；执行造成 dirty 或 Commit 漂移即失败。

无 `run_id/task_id` 的 1.0 调用只运行 runtime/SQLite/docs 健康检查，返回 `DEPRECATED_HEALTH_CHECK` warning，不能满足 G3。

### 7.2 `verification_prepare`

输入：`project_root`、`run_id`、`expected_state_version`、`idempotency_key`、`network_approval_ref`、`expires_at`、目标 Python/平台；0.2.0 默认且正式支持 `3.12` / `linux-amd64`，对应 Bookworm OCI 而非 Windows Host。输出与 `uv.lock` hash 绑定的 wheelhouse manifest、pip-audit snapshot 和 Trivy DB snapshot。prepare 是显式联网 Host Operation；正式 `verification_run` 会复核审批引用、有效期、执行镜像、目标平台、manifest 全量文件 hash、wheel 清单、Trivy tree hash、只读权限以及 symlink/junction，任一漂移均阻塞。

### 7.3 `host_operation_execute` / `host_operation_reconcile`

输入：`project_root`、`operation_id`、`expected_operation_version`、`idempotency_key`。Adapter 从受信 `InvocationContext` 获取活动 principal 和允许动作，不接受请求体自报 producer/reviewer/approver 权限。结果未知时 `execute` 返回 `reconcile_required`，调用方必须先运行 reconcile；禁止盲目重放 merge、push、tag、Release 或资产上传。

### 7.4 `database_migrate`

输入：显式 coordinator `project_root`、`expected_schema_version`、`target_schema_version=0007`、`idempotency_key`。迁移是高风险 Host Operation：先备份与校验，失败恢复到临时数据库并通过 integrity/FK/FTS 后原子替换；`status`、`workflow_step` 和其他只读接口不得隐式调用迁移。

## 8. Release 接口

### 8.1 `release_candidate_create`

输入：`project_root`、`run_id`、`task_id`、`version=0.2.0`、`expected_task_version`。

前置：G3 approved 已持久化并成功执行 `release_prepare` Host Operation、当前任务角色 release-manager、Worktree 类型 release、integration HEAD 与任务 base 一致。新候选只能由 operation executor 在离线 OCI 中创建；本接口读取并复核已有候选的全部 hash。输出 Manifest path/hash、可提交文件、制品目录与 required checks。重复调用相同 version/source Commit 返回同一候选；不同 source 返回 `RELEASE_SOURCE_CHANGED`。

Tag 与 GitHub Release 不由该接口创建，只在 G4 授权已持久化且 PR 已合并后的 `release_publish` Host Operation 中执行。候选响应保留兼容 `source_commit`，并明确返回 `integration_source_commit` 与提交候选文件的 `candidate_commit`；发布完成后返回 final manifest hash 与远端资产对账结果。

## 9. Memory 接口

### 9.1 `memory_candidate_submit`

输入：`project_root`、`run_id?`、`task_id?`、`record_type`、`title`、`content_ref`、`source_refs[]`、`source_hashes{}`、`confidence`、`tags[]`、`scope`、`supersedes[]`。

Runtime 验证 content/source 路径、hash、项目范围与 Secret 扫描，创建 `pending` 记录。自动事件候选使用同一服务和幂等键。

### 9.2 `memory_review`

输入：`memory_id`、`expected_version`、`decision=activate|needs_review|revoke|expire|delete|supersede`、`reviewer`、`reason`、`replacement_id?`。

只有 activate 需要来源仍匹配、Secret 检查通过、confidence 达到策略阈值。delete 只移除 FTS 内容并保留 tombstone/audit；supersede 必须创建 `memory_links`。

### 9.3 `memory_search`

输入扩展：`query`、`types[]?`、`statuses[]?=active`、`tags[]?`、`created_from/to?`、`source_ref?`、不透明 `cursor?`、`limit<=100`。结果按稳定键排序并返回 `next_cursor|null`；`project_id` 始终从项目配置注入，普通调用不能跨项目。

## 10. 文档接口

`docs_check` / `codex-os check-docs` 输出缺失文件、断链、非法目录、元数据错误、缺少章节、未批准占位、过期、版本不一致和影响分析缺口。接口只读；修复由独立任务执行。

## 11. CLI 映射

| MCP | CLI |
| --- | --- |
| `project_init` | `codex-os init` |
| `repository_check` | `codex-os repo-check` |
| `workflow_start` | `codex-os run <workflow>` |
| `workflow_status` | `codex-os status` |
| `workflow_step` | `codex-os step` |
| `workflow_resume` | `codex-os resume` |
| `handoff_review` | `codex-os handoff review` |
| `worktree_cleanup` | `codex-os worktree cleanup` |
| `verification_run` | `codex-os verify` |
| `verification_prepare` | `codex-os verification prepare` |
| `host_operation_execute` | `codex-os host-operation execute` |
| `host_operation_reconcile` | `codex-os host-operation reconcile` |
| `database_migrate` | `codex-os database migrate` |
| `release_candidate_create` | `codex-os release candidate` |
| `approval_submit` | `codex-os approve|reject` |
| `memory_candidate_submit` | `codex-os memory submit` |
| `memory_review` | `codex-os memory review` |
| `memory_search` | `codex-os memory search` |

CLI `--json` stdout 只输出一个通用响应；日志写 stderr。MCP 与 CLI 必须产生相同业务错误码和状态变化。

## 12. 授权、分页、节流与重试

- Runtime 从 Codex Host 的本地会话和项目配置解析 principal；请求体不能自报更高权限。producer 不能审核自己的 Handoff/关键 Gate 证据，Worktree cleanup 必须有显式批准，G4 还要求独立的 `tag-and-github-release` authority。
- 列表接口统一使用不透明 `cursor`、稳定排序键和 `limit`，默认 25、最大 100；响应返回 `next_cursor|null`。不得用 OFFSET 跨项目遍历，`project_id` 始终由当前项目上下文注入。
- 同一 project/run 的状态写入按乐观版本和项目锁串行化；GitHub、registry 和 advisory 查询受并发上限、最短刷新间隔和短期 Commit-bound 缓存约束。超过策略返回 `RATE_LIMITED`，`details.retry_after_ms` 为正整数。
- Host 只自动重试只读请求和带相同 idempotency key 的安全写请求；对 429/暂时性 5xx 使用有上限的指数退避和 jitter。approval、Handoff decision、merge、tag、Release 创建在结果未知时先对账，不盲目重放。
- 每次调用记录 request/correlation ID、principal、project/run/task、operation、结果码、state version、duration 和脱敏外部调用摘要；不得记录 token、原始授权头或 Secret 命令参数。

## 13. 错误码

| 类别 | 代码 |
| --- | --- |
| 配置/兼容 | `CONFIG_INVALID`、`CONFIG_VERSION_UNSUPPORTED`、`MIGRATION_REVALIDATION_REQUIRED` |
| 仓库 | `NOT_GIT_REPOSITORY`、`GITHUB_REMOTE_REQUIRED`、`REMOTE_UNREACHABLE`、`UPSTREAM_MISSING`、`HEAD_NOT_PUSHED`、`TARGET_BRANCH_MISSING`、`WORKTREE_DIRTY`、`MERGE_CONFLICT`、`REPOSITORY_HYGIENE_FAILED` |
| 路径/执行 | `PATH_DENIED`、`PATH_ESCAPE`、`COMMAND_DENIED`、`APPROVAL_REQUIRED`、`SANDBOX_UNAVAILABLE`、`SANDBOX_IMAGE_UNAVAILABLE`、`EXECUTION_FAILED`、`EXECUTION_DIRTY` |
| 并发/证据 | `STATE_VERSION_CONFLICT`、`TASK_DEPENDENCY_BLOCKED`、`PATH_OVERLAP`、`HANDOFF_NOT_ACCEPTED`、`REVIEW_STALE`、`EVIDENCE_INCOMPLETE`、`EVIDENCE_STALE`、`IDEMPOTENCY_CONFLICT`、`RATE_LIMITED` |
| 文档/依赖 | `DOCS_INCOMPLETE`、`DOCS_STALE`、`DOCUMENT_VERSION_MISMATCH`、`DEPENDENCY_UNVERIFIED`、`SECRET_DETECTED` |
| 迁移/发布 | `MIGRATION_FAILED`、`BACKUP_INVALID`、`RELEASE_INCOMPLETE`、`RELEASE_SOURCE_CHANGED`、`GITHUB_PR_INVALID`、`RELEASE_AUTHORITY_REQUIRED`、`GITHUB_RELEASE_FAILED` |
| Memory | `MEMORY_SOURCE_CHANGED`、`MEMORY_SCOPE_DENIED`、`MEMORY_REVIEW_REQUIRED`、`MEMORY_STATE_INVALID` |

所有错误 `details` 只含脱敏标识、预期/实际版本和可操作恢复步骤，不返回 token、完整凭据 URL 或未脱敏日志。

## 14. 兼容与弃用

- Plugin API 1.2 读取配置 1.0/1.1 并将缺失新字段填为安全默认，但输出始终为 1.2 响应并标注实际兼容模式和弃用 warning。
- 1.0 `workflow_start` 未提供 Profile/target 时使用路由结果和 `main`；响应保留 `next_action`。
- 1.0 `task_complete` 自由文本验证只能保存审计备注，不能通过新 Gate。
- 1.0 `verification_run(project_root)` 只作健康检查并发出弃用 warning。
- 不提供允许正式仓库降级为 local-only、跳过 Review、跳过 Gate 或 Host 执行的兼容开关。
- API 1.0/1.1 兼容入口、单动作响应、短 Profile 名和健康检查在 0.2.x 保留；最早在 0.3.0 通过新 ADR 与 CHANGELOG 移除。

## 15. 契约完成定义

每个写接口必须具备严格输入 Schema、路径/权限校验、expected version、幂等键、结构化输出、审计事件和可测试失败码。MCP Schema、CLI JSON、应用服务类型和本文必须由契约测试保持一致。
