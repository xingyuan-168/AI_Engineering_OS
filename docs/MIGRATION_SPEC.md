# 数据迁移规格

版本：V2.0-derived-migration
状态：可执行实现规格基线
存储：SQLite；事实文档仍由 Markdown + Git 保存。

## 1. Schema 版本

格式为 `MAJOR.MINOR`，当前基线为 `1.0`。主版本不兼容时禁止自动升级；次版本迁移必须可重复执行并写入迁移记录。

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

## 3. 迁移流程

1. 读取当前 Schema 版本和迁移 checksum。
2. 创建数据库备份并校验可读性。
3. 在事务中按顺序执行未应用迁移。
4. 向 `schema_migrations` 追加成功记录并提交。
5. 运行外键、索引、事件计数和关键查询校验。
6. 失败时回滚事务；备份不可恢复时进入 `blocked`。
7. 新增表迁移必须同时创建项目、Workflow、Task 外键/索引，并回填来源 hash 和审计引用；不能以空值绕过旧记录校验。

迁移脚本必须是确定性的、可审计的，并记录执行人、应用版本和时间。

## 4. 备份与恢复

- 每次迁移前生成带时间戳的 SQLite 备份和 SHA-256 校验和。
- 每日保留最近 7 份备份，发布前保留发布前快照。
- 恢复先复制到临时路径执行完整性检查，再替换活动数据库。
- 恢复后必须重建 Memory FTS 索引并校验文档 hash。

## 5. 兼容限制

- 不支持跨主版本自动降级。
- 旧事件和审批记录不得被迁移脚本删除或改写。
- 数据库迁移不能替代 Git 文档迁移；两者必须在同一变更记录和 ADR 中关联。
- Memory FTS5 重建、Handoff hash 校验、Worktree 路径校验和 Routing Decision 输入 hash 校验必须作为迁移后的完整性检查。

## 6. 完成定义

迁移规格只有在 Schema、约束、版本、事务、备份、恢复、校验、失败回滚和兼容限制均有测试时才算完成。
