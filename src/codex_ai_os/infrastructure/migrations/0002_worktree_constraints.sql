CREATE UNIQUE INDEX uq_worktrees_task_id ON worktrees(task_id);
CREATE INDEX ix_worktrees_run_status ON worktrees(run_id, status);
