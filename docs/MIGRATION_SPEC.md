# 数据迁移规格

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"review-ready","owner":"database-engineer","requirement_refs":["REQ-1.6.2","CFG-001","VERSION-001"]} -->

版本：V2.0-derived-migration
状态：可执行实现规格基线
存储：SQLite；事实文档仍由 Markdown + Git 保存。

## 1. Schema 版本

公共配置/文档/Profile Schema 使用 `MAJOR.MINOR`，0.2.0 目标为 `1.2`，兼容读取 1.0/1.1。SQLite 使用四位追加迁移编号，目标为 `0007`。不兼容升级禁止静默执行；每条 SQLite 迁移必须确定、可审计、校验 checksum 并写入迁移记录。

## 2. 表和约束

### `schema_migrations`

`version` 主键、`name`、`app_version`、`checksum`、`applied_by`、`applied_at`。每个成功迁移只追加一行；当前版本由 `applied_at` 和版本顺序上的最后一条记录推导。

### `workflows`

`id` 主键、`project_id`、`name`、`version`、`state`、`state_version`、`risk_level`、`checkpoint_ref`、`config_hash`、`created_at`、`updated_at`。

### `tasks`

`id` 主键、`workflow_id` 外键、`agent`、`branch`、`worktree`、`status`、`input_hash`、`output_ref`、`review_status`、`created_at`、`updated_at`。

### `events`

`id` 主键、`event_id` 唯一、`workflow_id`、`task_id`、`event_type`、`payload_json`、`approval_required`、`created_at`。事件只追加，不更新历史 payload。

### `approvals`

`id` 主键、`workflow_id`、`gate`、`decision`、`reviewer`、`reason`、`evidence_refs`、`created_at`。

### `artifacts`

`id` 主键、`workflow_id`、`task_id`、`path`、`kind`、`content_hash`、`source_commit`、`status`、`created_at`。

### `executions`

`id` 主键、`task_id`、`risk_level`、`command_hash`、`image_digest`、`container_id`、`exit_code`、`stdout_ref`、`stderr_ref`、`started_at`、`ended_at`。

### `documents`

`path` 主键、`doc_type`、`status`、`content_hash`、`source_commit`、`last_checked_at`。

### `memory_index`

`id` 主键、`memory_id`、`source_path`、`source_hash`、`record_type`、`text`、`tags`、`created_at`、`deleted_at`；FTS5 索引与事实来源 hash 关联。

### `memory_records`

`memory_id` 主键、`project_id`、`workflow_id`、`task_id`、`type`、`title`、`content_ref`、`source_refs_json`、`source_hashes_json`、`confidence`、`tags_json`、`scope`、`status`、`created_at`、`updated_at`、`expires_at`、`audit_event_id`；项目级记录的 workflow/task 为空时必须记录原因。

### `memory_links`

`id` 主键、`from_memory_id`、`to_memory_id`、`relation`、`source_project_id`、`reuse_scope`、`approved_by`、`created_at`。

### `plugin_runs`

`id` 主键、`request_id`、`plugin_api`、`hook`、`operation`、`project_id`、`workflow_id`、`task_id`、`source_hash`、`status`、`retry_count`、`error_code`、`started_at`、`ended_at`。

### `worktrees`

`id` 主键、`worktree_id`、`project_id`、`workflow_id`、`task_id`、`agent`、`path`、`branch`、`base_commit`、`source_hash`、`status`、`dirty`、`created_at`、`cleaned_at`、`cleanup_approval_ref`。

### `handoffs`

`id` 主键、`handoff_id`、`project_id`、`workflow_id`、`task_id`、`producer`、`consumer`、`source_hash`、`status`、`artifact_refs`、`commit_refs`、`tests_json`、`open_questions_json`、`risk_json`、`created_at`、`accepted_at`、`validation_json`。

### `routing_decisions`

`id` 主键、`routing_id`、`project_id`、`workflow_id`、`task_id`、`input_hash`、`source_hash`、`score`、`dimension_scores_json`、`risk_level`、`workflow`、`profiles_json`、`reasons_json`、`approval_required`、`human_override_json`、`created_at`。

### `host_operations`

`operation_id` 主键、project/run/task/group/handoff/release 关联、operation kind、幂等键、request hash、expected versions、`pending/running/succeeded/failed/reconcile_required` 状态、租约、attempt、脱敏 request/result、错误码和开始/结束时间。该表是 Git、OCI、GitHub 副作用的恢复事实源。

### `api_call_audits`

保存 request/correlation ID、受信 principal、operation/对象 ID、结果码、状态版本、时长和脱敏摘要；不得保存凭据、Secret 或原始授权头。

## 3. 迁移流程

1. 读取当前 Schema 版本和迁移 checksum。
2. 创建数据库备份并校验可读性。
3. 校验所有已应用迁移 checksum 未漂移，在独立事务中按顺序执行未应用迁移至 `0007`。
4. 向 `schema_migrations` 追加成功记录并提交。
5. 运行 integrity、foreign key、索引、FTS rebuild/query、事件计数和关键查询校验。
6. 失败时回滚事务，将备份恢复到同目录临时数据库并完成相同校验后原子替换活动库；备份不可恢复时进入 `blocked`。
7. 新增表迁移必须同时创建项目、Workflow、Task 外键/索引，并回填来源 hash 和审计引用；不能以空值绕过旧记录校验。

`0006 -> 0007` 的显式维护调用存在一次性引导边界：`host_operations` 本身由 `0007` 创建。Runtime 必须先从已校验的 `0007` SQL 提取并事务性预建完全相同的 intent 表、写入并租赁 `database_migrate` Operation，再执行正常迁移；迁移器只在 SQLite 中的表定义与迁移 SQL 严格一致时跳过重复建表。备份因此包含运行中的 intent，失败恢复或迁移成功后进程崩溃均可按同一幂等键对账，禁止在 Python 中维护第二份表定义或修改 `0001`～`0007`。

迁移脚本必须是确定性的、可审计的，并记录执行人、应用版本和时间。

## 4. 备份与恢复

- 每次写迁移前生成带时间戳的 SQLite 备份和 SHA-256 校验和；只读 `status`/`step` 不触发迁移或创建目录。
- 每日保留最近 7 份备份，发布前保留发布前快照。
- 恢复先复制到与活动库同文件系统的临时路径，执行 integrity、foreign-key、FTS 和关键查询检查，再原子替换活动数据库。
- 恢复后必须重建 Memory FTS 索引并校验文档 hash。

## 5. 兼容限制

- 不支持跨主版本自动降级。
- 旧事件和审批记录不得被迁移脚本删除或改写。
- 数据库迁移不能替代 Git 文档迁移；两者必须在同一变更记录和 ADR 中关联。
- Memory FTS5 重建、Handoff hash 校验、Worktree 路径校验和 Routing Decision 输入 hash 校验必须作为迁移后的完整性检查。
- 受管 Worktree 中的公共调用不得依据旧配置绝对路径静默迁移另一 checkout；必须明确返回 coordinator root 指引。
- 旧活动 Workflow 保留原 task/handoff/approval/event。证据不足时设置 `MIGRATION_REVALIDATION_REQUIRED`，不得为满足 1.2 约束伪造 Task Group、Review 或 Evidence。
- `0007` 重建 Handoff/merge 相关 trigger，使 push/对账失败只阻塞 merge/run，不把 accepted Handoff 改写为 rejected。

## 6. 0007 验收矩阵

- fresh install `0001 -> 0007`、真实 `0006 -> 0007`、重复迁移和 checksum 冲突。
- 备份损坏、迁移中断、恢复校验失败、原子替换失败与活动库保持可恢复。
- FK、integrity、FTS rebuild/query、Host Operation 唯一/租约/状态、Memory version 和 Handoff trigger。
- 配置/文档/Profile 1.0/1.1 兼容读取，输出 1.2 warning；旧自由文本证据不满足 1.2 Gate。

## 7. 完成定义

迁移规格只有在 Schema、约束、版本、事务、备份、恢复、校验、失败回滚和兼容限制均有测试时才算完成。
