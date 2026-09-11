# 数据库

SQLite 只保存运行状态与可重建索引；Markdown/Git 保存事实正文。数据库位于 `.codex-os/state/state.db`，整个 `.codex-os/` 被 Git 忽略。

## 单迁移 0001（四表 + 索引）

- `tasks` — id, title, branch, status(open|in_progress|blocked|done), created_at, updated_at。worktree prepare 自动登记任务。
- `approvals` — id, subject, gate(code_start|frontend|finish), decision(approved|rejected), decided_by, reason, created_at。
- `worktrees` — id, task_id→tasks, name/path/branch/target_branch（name/path/branch 均 UNIQUE）, disposable, status(active|ready|cleaned), created_at, updated_at。cleanup 注销删除行。
- `memory_index` — id, record_type, title, summary, source, source_commit, tags, status, superseded_by, line_number, indexed_at。可由 docs/memory/memory.jsonl 随时重建。

## 迁移与保护

- 仅保留一个编号迁移；schema_migrations 记录 version/name/checksum/applied_at，迁移前校验已应用迁移 checksum。
- 预重构数据库（表集、列集或 checksum 不匹配）自动导出为 `.codex-os/state/backups/<stem>-legacy-<stamp>.db`（附 SHA-256 sidecar）后从头重建；不做数据迁移（旧表全部被取代）。
- 连接参数：WAL、foreign_keys=ON、busy_timeout=5000；迁移在 BEGIN IMMEDIATE 内执行并做 foreign_key_check 与 integrity_check。
