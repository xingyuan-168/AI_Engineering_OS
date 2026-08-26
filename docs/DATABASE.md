# AI Engineering OS 0.2.0 数据库与迁移设计

<!-- codex-os-document: {"schema_version":"1.1","document_version":"0.2.0","status":"approved","owner":"architect","requirement_refs":["REQ-1.6.2","GATE-001","AGENT-001","HANDOFF-001","WORKTREE-001","VERSION-001","MEMORY-001","ROUTING-001"]} -->

SQLite 保存运行状态、事件、索引、结构化证据和 provenance；Markdown/Git 保存事实正文。Schema 通过 `0001`～`0007` 追加迁移到版本 `0007`，不得改写任何已发布迁移。

## 1. 数据库运行约束

- 每个连接执行 `PRAGMA foreign_keys=ON`，写库启用 WAL，`busy_timeout` 使用配置上限。
- 时间使用 UTC RFC3339；JSON 使用 UTF-8、排序 key、无多余空白的规范序列化。
- 布尔值使用 `INTEGER CHECK(value IN (0,1))`；hash 使用小写十六进制并由应用层验证长度。
- Workflow、Task、TaskGroup、Handoff、Worktree 和 Memory 均有独立 `state_version >= 0`。
- 事件、审批、Review、合并、清理和迁移记录只追加；当前状态表可更新，但必须与同事务事件一致。
- 外部 Git/OCI/GitHub 操作使用 intent/result 记录，禁止在长 SQLite 事务中等待外部进程。

## 2. 现有基线

`0001_initial.sql` 已建立 projects、workflow_runs、tasks、events、approvals、artifacts、documents、handoffs、executions、worktrees、memory_records；`0002` 增加 Worktree 唯一/状态索引；`0003` 增加执行 status/error/network/mounts。

新增迁移只扩展这些表或创建新表。应用启动时从 `schema_migrations` 最后一条成功记录推导版本，并校验迁移文件 SHA-256。

## 3. `0004_repository_governance.sql`

### 3.1 项目与文档扩展

向 `projects` 增加：

| 列 | 类型/默认 | 说明 |
| --- | --- | --- |
| `config_schema_version` | TEXT NOT NULL DEFAULT `'1.0'` | 当前读取的配置 Schema |
| `repository_mode` | TEXT NOT NULL DEFAULT `'formal'` | `formal|fixture_local_only`；后者仅测试能力可写 |
| `target_branch` | TEXT NOT NULL DEFAULT `'main'` | 项目默认目标分支 |

向 `documents` 增加 `schema_version`、`document_version`、`owner`、`requirement_refs_json`、`expires_at`、`validation_json`。旧记录迁移为 `schema_version='1.0'`、`document_version='legacy'`、`owner='unassigned'` 并在下一 Gate 标记复核，不自动视为 1.1 合格。

### 3.2 `repository_audits`

| 列 | 约束 |
| --- | --- |
| `id` | TEXT PRIMARY KEY |
| `project_id` | FK projects, NOT NULL |
| `run_id` | FK workflow_runs, NULLABLE |
| `mode` | `formal|fixture_local_only` |
| `target_branch`、`remote_name`、`remote_host`、`upstream_ref` | NOT NULL（fixture 可使用规范空值） |
| `head_commit`、`config_hash` | NOT NULL |
| `git_top_level_ok`、`clean`、`conflicts_free`、`remote_reachable`、`head_pushed`、`target_exists`、`permission_ok`、`hygiene_ok`、`version_ok` | 0/1 |
| `status` | `passed|failed|stale` |
| `blockers_json`、`summary_json` | NOT NULL |
| `checked_at`、`expires_at` | NOT NULL |

索引：`(project_id, head_commit, config_hash, target_branch, status, checked_at DESC)`。报告不保存含凭据的 remote URL；只保存解析后 host/name 与 URL hash。

### 3.3 `repository_findings`

`id`、`audit_id` FK、`category`（git/path/file/tracked/ignore/secret/lifecycle/version）、`rule_id`、`severity`、`path`、`path_hash`、`message`、`remediation`、`auto_cleanable`、`registry_entry_id`、`created_at`。唯一键 `(audit_id, rule_id, path_hash)`。

### 3.4 `file_lifecycle_entries`

`id`、`project_id`、`run_id`、`task_id`、`path`、`real_path_hash`、`kind=disposable|promotable|audit-evidence`、`created_by_runtime`、`owner`、`status=registered|promoted|retained|cleaned|blocked`、`content_hash`、`retention_until`、`created_at`、`updated_at`。只有 `created_by_runtime=1` 且 kind=disposable 可进入自动清理候选。

### 3.5 `routing_decisions`

`id`、`project_id`、`run_id`、`input_hash`、`source_hash`、`workflow_name`、`profiles_json`、`score_json`、`risk_level`、`reasons_json`、`approval_required`、`human_override_json`、`created_at`。唯一键 `(run_id,input_hash)`；覆盖只能提高风险或增加 Profile/审批。

### 3.6 结构化证据

`check_evidence`：`id`、project/run/task、`name`、`command_hash`、`execution_id` FK、`exit_code`、`report_path`、`report_hash`、`source_commit`、`started_at`、`ended_at`、`status`。唯一键 `(task_id,name,source_commit,command_hash)`。

`review_evidence`：`id`、project/run/task、`review_type=code|security|handoff|release`、`reviewer`、`reviewed_commit`、`decision`、`findings_json`、`risks_json`、`report_ref`、`report_hash`、`created_at`。同 reviewer/commit/type 的重复输入以 idempotency key 去重。

`gate_evidence_bundles`：`id`、`run_id`、`gate`、`state_version`、`version`、`artifact_refs_json`、`check_refs_json`、`review_refs_json`、`bundle_hash`、`status=building|complete|stale`、`created_at`。唯一键 `(run_id,gate,state_version)`。

向 `approvals` 增加 `evidence_bundle_id` FK、`evidence_bundle_hash`、`release_authority_json`。审批行与 bundle 必须同一 run/gate/state_version。

### 3.7 版本与发布元数据

`version_records`：`id`、`project_id`、`run_id`、`requirement_version`、`software_version`、`cli_version`、`plugin_version`、`plugin_api_version`、`config_schema_version`、`database_schema_version`、`git_tag`、`source_commit`、`config_hash`、`lock_hash`、`created_at`；唯一键 `(run_id,software_version)`。

`release_records`：`id`、`run_id`、`task_id`、`version_record_id`、`status=candidate|g4_ready|authorized|tagged|published|blocked|revoked`、`release_worktree_id`、`manifest_path/hash`、`artifact_root`、`sbom_path/hash`、`checksums_path/hash`、`rollback_path/hash`、`pr_number/url_hash/head/base/head_commit`、`merge_commit`、`tag`、`github_release_id`、`authorization_json`、`source_commit`、`state_version`、`created_at`、`updated_at`。唯一键 `(run_id,version_record_id)`。

### 3.8 执行关联

向 `executions` 增加 `run_id` FK、`worktree_id` FK、`command_argv_hash`、`log_redaction_version`、`worktree_dirty_before`、`worktree_dirty_after`、`result_hash`。历史行通过 tasks 回填 run_id/worktree，无法回填者标记 `status='legacy-unverified'`，不能满足 G3。

## 4. `0005_multi_agent_coordination.sql`

### 4.1 Workflow 扩展

向 `workflow_runs` 增加：

`profiles_json DEFAULT '[]'`、`target_branch DEFAULT 'main'`、`integration_branch`、`base_commit`、`integration_head`、`max_parallel DEFAULT 4 CHECK(1..4)`、`migration_status DEFAULT 'current'`。

迁移时，非 completed/cancelled 的旧 run 设置 `migration_status='revalidation_required'`；下一写转换返回 `MIGRATION_REVALIDATION_REQUIRED`。

### 4.2 `task_groups`

`id` PK、`run_id` FK、`name`、`phase`、`status=pending|running|joining|completed|blocked|failed`、`join_policy='all_accepted_merged'`、`base_commit`、`state_version`、`created_at`、`updated_at`。索引 `(run_id,phase,status)`。

### 4.3 Task 扩展与依赖

向 `tasks` 增加 `task_group_id` FK、`allowed_paths_json DEFAULT '[]'`、`dependency_input_hash`、`base_commit`、`completed_commit`、`lease_owner`、`lease_expires_at`。现有 `state_version` 继续作为任务乐观锁。

`task_dependencies`：`task_id`、`depends_on_task_id`、`dependency_type=artifact|path-order|review|manual`、`reason`、`created_at`，复合主键；CHECK 禁止自依赖，应用层进行有向无环验证。

### 4.4 Handoff Review

向 `handoffs` 增加 `source_commit`、`reviewed_by`、`review_reason`、`rejected_reason`、`blocked_reason`、`reviewed_at`、`updated_at`、`state_version DEFAULT 0`。旧 `accepted_at` 保留兼容。

`handoff_reviews`：`id`、`handoff_id`、`task_id`、`review_evidence_id`、`reviewer`、`decision=accepted|rejected|blocked`、`reason`、`reviewed_commit`、`expected_handoff_version`、`created_at`。只追加；数据库 trigger 拒绝 ready 之外的首次 review 和 terminal 状态再次改变。新 Commit 由应用服务创建新 handoff version，不改写旧 Review。

### 4.5 集成合并与锁

`coordination_locks`：`lock_key` PK（`integration:<run-id>`）、`owner`、`lease_token_hash`、`acquired_at`、`expires_at`、`state_version`。获取/续租使用 expected version；过期锁只有同 run 的恢复器可接管。

`integration_merges`：`id`、`run_id`、`task_group_id`、`task_id`、`handoff_id`、`source_branch`、`source_commit`、`integration_branch`、`integration_head_before`、`merge_commit`、`parent_commits_json`、`status=pending|merged|conflicted|blocked`、`conflict_paths_json`、`error_code`、`created_at`、`updated_at`。唯一键 `(handoff_id,source_commit)`。

### 4.6 Worktree 扩展与清理

向 `worktrees` 增加 `kind=task|integration|release`、`state_version DEFAULT 0`、`head_commit`、`open_review_count DEFAULT 0`、`cleanup_status=not_requested|requested|approved|completed|blocked`。

`worktree_cleanups`：`id`、`worktree_id`、`requested_by`、`approved_by`、`reason`、`precondition_json`、`status=requested|approved|completed|blocked|failed`、`git_result_json`、`created_at`、`completed_at`。成功行要求 precondition 中 merged/clean/no_open_review/no_unknown_files 均为 true。

## 5. `0006_memory_fts.sql`

### 5.1 状态迁移

在事务内执行：

```sql
UPDATE memory_records SET status = 'pending' WHERE status = 'candidate';
UPDATE memory_records SET status = 'needs_review' WHERE status = 'invalidated';
```

其他未知状态使迁移失败并恢复备份，不静默映射。

向 `memory_records` 增加 `state_version DEFAULT 0`、`reviewed_by`、`review_reason`、`revoked_at`、`deleted_at`、`source_validation_at`。应用/trigger 只允许：

```text
pending -> active | needs_review | revoked | expired | deleted
active -> needs_review | superseded | revoked | expired | deleted
needs_review -> active | superseded | revoked | expired | deleted
superseded/revoked/expired -> deleted
deleted -> terminal
```

### 5.2 `memory_reviews` 与 `memory_links`

`memory_reviews`：`id`、`memory_id`、`reviewer`、`decision`、`reason`、`source_hashes_json`、`secret_check_id`、`scope_valid`、`confidence_valid`、`expected_version`、`created_at`，只追加。

`memory_links`：`id`、`project_id`、`from_memory_id`、`to_memory_id`、`relation=supersedes|conflicts_with|derived_from|cross_project_reuse`、`scope`、`approved_by`、`created_at`。唯一键 `(from_memory_id,to_memory_id,relation)`；跨项目 relation 要求 approved_by 非空。

### 5.3 FTS5

`memory_search_documents` 保存经 Secret 检查的派生索引文本：`memory_id` PK/FK、`project_id`、`status`、`scope`、`title`、`tags_text`、`search_text`、`source_hash`、`updated_at`。

```sql
CREATE VIRTUAL TABLE memory_fts USING fts5(
  memory_id UNINDEXED,
  project_id UNINDEXED,
  status UNINDEXED,
  scope UNINDEXED,
  title,
  tags_text,
  search_text,
  content='memory_search_documents',
  content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);
```

INSERT/UPDATE/DELETE triggers 保持 external-content FTS 同步。只有 `active` 文档进入默认查询；deleted 移除 search document 但保留 memory tombstone/reviews/events。查询必须先绑定当前 `project_id` 和允许状态，再拼接经过 FTS5 参数化的 MATCH 表达式。

## 6. `0007_release_closure.sql`

### 6.1 `host_operations` 与 `api_call_audits`

`host_operations`：`operation_id` 主键、project/run/task/group/handoff/release 外键、`kind=integration_prepare|integration_merge|release_prepare|release_publish|verification_prepare|database_migrate`、`idempotency_key`、`request_hash`、`status=pending|running|succeeded|failed|reconcile_required`、`expected_state_version`、`expected_task_version`、`expected_operation_version`、`lease_owner`、`lease_expires_at`、`attempt_count`、`request_json`、`result_json`、`error_code`、`created_at`、`started_at`、`ended_at`、`updated_at`。`(project_id,kind,idempotency_key)` 唯一；同一幂等键的 request hash 不同必须冲突。

`api_call_audits`：`call_id` 主键、request/correlation ID、principal、operation、project/run/task/operation ID、status/error code、state version、duration、脱敏 request/response 摘要、`created_at`。表和日志均不得保存 token、Authorization header、Secret 参数或完整带凭据 URL。

### 6.2 既有表的 1.2 扩展

- `memory_records` 确保存在 `state_version NOT NULL DEFAULT 0`，迁移保留旧状态历史并按实际记录初始化，不伪造 Review。
- `routing_decisions` 增加七维规范化输入、各维得分、总分、canonical profile、人工覆盖、规则/Profile 版本和理由字段。
- `release_records` 增加 `integration_source_commit`、`candidate_commit`、candidate/final manifest path/hash、registry index/platform digest、远端 tag/Release/asset 对账 JSON、publish operation ID 和 reconciliation 时间。
- `check_evidence` 增加或规范化 `started_at`、`ended_at`，并以约束保证 `ended_at >= started_at`；非零退出码只能对应 failed 状态。
- 所有写模型的版本列使用单调递增整数；迁移不把旧记录的缺失证据解释为新版本已验证。

### 6.3 Handoff trigger 重建

`0007` 删除并重建受影响 trigger，但不修改历史迁移文件。已 accepted Handoff 的审核事实不可逆：merge/push Host Operation 失败只允许将 integration merge 和 run 标记 blocked/reconcile_required，禁止把 Handoff 改为 rejected。只有 producer 产生新 Commit 后才能创建新的 ready Handoff version。

### 6.4 旧 Workflow 重验证

升级时保留所有 task、handoff、approval、event 和 evidence。仍活动且不能证明 1.2 证据/版本不变量的 Workflow 设置 `migration_revalidation_required=1` 与 `MIGRATION_REVALIDATION_REQUIRED` blocker；不得为了满足新 FK 或 Gate 人工创建 Task Group、Review 或 Evidence。

## 7. 事务边界

### 7.1 任务完成

`BEGIN IMMEDIATE` -> 验证 task expected version/status -> 插入 artifacts/check refs -> 更新 task completed/version -> 创建 handoff ready/version 0 -> 追加事件 -> COMMIT。不得在此事务推进 Workflow。

### 7.2 Handoff accepted 与合并

事务 A 保存 Review、期望版本和 `integration_merge` Host Operation，提交；Host 取得租约后执行外部 Git 持锁合并/推送；事务 B 以 integration head before、operation version 和 lock token 条件更新 merge result、Workflow integration head 与事件。若 push 或事务 B 失败，operation 进入 failed/reconcile_required，恢复器通过 Git parents/HEAD/remote ref 对账后幂等补写；accepted Handoff 不回退。

### 7.3 join barrier

`BEGIN IMMEDIATE` -> 查询 group 全任务/Handoff/merge -> 验证全部 accepted+merged -> `UPDATE task_groups ... WHERE state_version=?` -> `UPDATE workflow_runs ... WHERE state_version=?` -> 追加 group/workflow 事件 -> COMMIT。任一版本冲突整体回滚并重算。

### 7.4 Gate approval

在同事务读取 complete bundle、核验 bundle hash/state version、插入 approval、记录下一 Host Operation、更新 Workflow 双轴状态/version、将旧 bundle 标记 stale、追加事件。G4 在持久化授权后验证已合并 PR；发布结果通过同一 operation 的对账状态写回。

### 7.5 数据库强制不变量

应用服务负责给出友好错误，但以下约束必须由 SQLite 的 `CHECK`、FK、唯一索引或 trigger 再次强制：

- `workflow_runs.max_parallel_agents BETWEEN 1 AND 4`；task group、task、Handoff、merge 和 evidence 的 project/run/group 归属必须一致，删除父记录受 FK 限制。
- Handoff 只能按 `ready -> accepted|rejected|blocked` 转换；decision reviewer 不得等于 task producer，reviewed Commit 必须等于 task head Commit。
- `integration_merges.status='merged'` 只允许对应 Handoff 为 accepted、source Commit 匹配且 merge parent 列表同时包含 integration head before 和 source Commit；同一 Handoff/source Commit 唯一。
- task group 只能在全部 task completed、Handoff accepted 且 integration merge 成功时进入 completed；依赖 task 未达到 accepted+merged 时，consumer 不能进入 running。
- Worktree cleanup 只能从 approved request 进入 completed，且快照必须同时证明 merged、clean、no open review、no unknown files；失败现场 Worktree 不允许自动完成清理。
- approval 必须引用同 run/gate/state version 的 complete bundle；release record 只能在 G4 approval 与精确 release authority 存在后进入 authorized/tagged/published，tag/version 在项目内唯一。
- Memory 激活必须有通过 Secret/scope/source/confidence checks 的 review；superseded 必须有 `supersedes` link，deleted 必须保留 tombstone 且不再有 FTS search document。

跨多行的 DAG 环检测与 Git parent 可达性仍由协调器执行，但写入结果受上述 FK、版本条件和 trigger 保护；绕过应用层直接写库也不能推进治理状态。

## 8. 备份、迁移与恢复

1. 获取项目级迁移锁并关闭写入口。
2. `PRAGMA wal_checkpoint(TRUNCATE)` 后使用 SQLite backup API 创建 `.codex-os/state/backups/state-<utc>-pre-0007.db`。
3. 生成 `.sha256`，重新打开备份并运行 `integrity_check`、`foreign_key_check`、读取 `schema_migrations`。
4. 校验迁移文件 checksum，按 0004/0005/0006/0007 各自独立事务执行并记录。
5. 执行 post-migration integrity/foreign-key/FTS rebuild test；失败时先把备份恢复到同目录临时数据库，完成 integrity/FK/FTS/关键查询校验，再原子替换活动库；保留失败库供审计。
6. 重复运行时已记录相同 checksum 的迁移跳过；同版本不同 checksum 返回 `MIGRATION_CHECKSUM_MISMATCH`。

回滚不执行反向 DROP/ALTER。发布前失败恢复备份；发布后 Schema 回滚需要新 forward migration 或恢复整个版本备份并进入只读维护模式。

## 9. 数据保留与隐私

- events、approvals、reviews、merge、release 与 migration 证据按项目策略保留，不被普通清理删除。
- execution stdout/stderr、SBOM 和报告正文位于受管制品区，SQLite 只保存脱敏引用/hash。
- remote URL、命令参数和日志在写库前脱敏；token、密码、私钥和原始聊天禁止入库/FTS。
- Memory delete 保存 tombstone 和审计；法定删除需求通过受控 purge Workflow 与独立审批处理。

## 10. 索引与性能

除上述索引外，至少建立：

- `tasks(task_group_id,status,updated_at)`、`task_dependencies(depends_on_task_id)`。
- `handoffs(task_id,status,updated_at)`、`handoff_reviews(handoff_id,created_at)`。
- `integration_merges(run_id,status,updated_at)`、`worktrees(run_id,kind,status)`。
- `check_evidence(run_id,source_commit,status)`、`review_evidence(run_id,reviewed_commit,decision)`。
- `release_records(run_id,status,updated_at)`、`memory_records(project_id,status,updated_at)`。
- `host_operations(run_id,status,lease_expires_at)`、`host_operations(project_id,kind,idempotency_key)`、`api_call_audits(project_id,created_at)`。

状态查询必须在单个项目/run 范围内分页；日志正文和制品不通过 SQLite result 返回。

## 11. Schema 验收

- 从空库运行 0001-0007、从真实 0006 备份升级、重复执行、checksum 冲突、备份恢复、原子替换和每条迁移失败恢复。
- `integrity_check=ok`、`foreign_key_check` 空、FTS5 可创建/rebuild/query。
- task/group/workflow 乐观锁并发只有一个提交成功；失败方收到 `STATE_VERSION_CONFLICT`。
- ready Handoff 不能解锁依赖；accepted+merged 的全组才能越过 join。
- 旧活动 Workflow 强制 migration revalidation；旧文本验证不能满足 Gate。
- Memory 状态迁移、Secret 拦截、项目隔离、来源变化和 deleted tombstone 全部有自动化测试。
- Host Operation 覆盖 lease 过期接管、结果未知对账、merge 后 push 失败、部分 Release 资产和重复幂等请求；任何恢复路径都不改写已接受的审核事实。
