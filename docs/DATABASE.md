# 数据与状态存储

V1 使用 SQLite 保存运行状态、任务、审批、执行事件和文档索引；Markdown 和 Git 保存事实内容。

## 表

### `workflows`

`id`、`project_id`、`name`、`state`、`risk_level`、`checkpoint_ref`、`created_at`、`updated_at`。

### `tasks`

`id`、`workflow_id`、`agent`、`branch`、`worktree`、`status`、`input_ref`、`output_ref`、`review_status`。

### `events`

`id`、`workflow_id`、`task_id`、`event_type`、`payload_json`、`approval_required`、`created_at`。

### `approvals`

`id`、`workflow_id`、`gate`、`decision`、`reviewer`、`reason`、`created_at`。

### `documents`

`path`、`doc_type`、`status`、`content_hash`、`source_commit`、`updated_at`。

### `schema_migrations`

追加记录版本、名称、应用版本、checksum、执行人和迁移时间；当前 Schema 版本由最后一条成功记录推导。

### `artifacts`

记录 Workflow/Task 产物路径、类型、hash、来源提交和状态。

### `executions`

记录风险级别、命令 hash、镜像 digest、容器 ID、退出码、日志引用和执行时间。

### `memory_records`

记录 `memory_id`、`project_id`、`workflow_id`、`task_id`、`type`、`title`、`content_ref`、`source_refs_json`、`source_hashes_json`、`confidence`、`tags_json`、`scope`、`status`、`created_at`、`updated_at`、`expires_at` 和审计引用。`workflow_id`、`task_id` 在项目级记忆中可为空，但必须显式记录为空的原因。事实内容仍在 Markdown/Git 或脱敏产物中，数据库不保存 Secret 或原始聊天记录。

### `memory_links`

记录记忆之间的 `supersedes`、`conflicts_with`、`derived_from` 和跨项目复用关系，包含来源项目、适用范围、批准人和创建事件。

### `memory_index`

记录 Memory 的 FTS5 检索字段和来源 hash；索引失效不能删除 `memory_records` 或审计事件。

### `plugin_runs`

记录 `request_id`、`plugin_api`、`hook`、`operation`、`project_id`、`workflow_id`、`task_id`、`source_hash`、`status`、`retry_count`、`error_code`、`started_at` 和 `ended_at`。

### `worktrees`

记录 `worktree_id`、`project_id`、`workflow_id`、`task_id`、`agent`、`path`、`branch`、`base_commit`、`source_hash`、`status`、`dirty`、`created_at`、`cleaned_at` 和回收审批引用。

### `handoffs`

记录 `handoff_id`、`project_id`、`workflow_id`、`task_id`、`producer`、`consumer`、`source_hash`、`status`、`artifact_refs`、`commit_refs`、`tests_json`、`open_questions_json`、`risk_json`、`created_at`、`accepted_at` 和校验结果。

### `routing_decisions`

记录 `routing_id`、`project_id`、`workflow_id`、`task_id`、`input_hash`、`source_hash`、`score`、维度评分、`risk_level`、`workflow`、`profiles`、`reasons_json`、`approval_required`、`human_override` 和 `created_at`。

## 约束

- 事件和审批只追加，不静默覆盖历史。
- 文档索引必须保留来源路径和 commit。
- Memory 更新、撤销、过期和删除只追加状态/审计事件，不静默覆盖旧记录；删除时可移除 FTS5 索引，但不擅自改写 Git 历史。
- 所有新增表必须关联 `project_id`，涉及运行时的记录同时关联 `workflow_id` 和 `task_id`；跨项目关系必须显式记录来源和批准。
- `handoffs` 的 artifact hash、Commit 和测试结果必须能回查 `artifacts`、`events` 和 `executions`。
- `routing_decisions` 不得成为安全策略的唯一来源，用户覆盖不能降低 `risk_level`。
- 数据库迁移必须记录在 `ADR/` 和 `CHANGELOG.md`。

## 版本与迁移

当前数据库 Schema 基线为 `1.0`。迁移前必须备份并校验，迁移在事务中执行，失败回滚；不支持跨主版本自动降级。详细规则见 [MIGRATION_SPEC.md](MIGRATION_SPEC.md)。
