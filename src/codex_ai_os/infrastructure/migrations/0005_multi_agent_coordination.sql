CREATE TABLE task_groups (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'joining', 'completed', 'blocked', 'failed')),
    join_policy TEXT NOT NULL DEFAULT 'all_accepted_merged' CHECK (join_policy = 'all_accepted_merged'),
    base_commit TEXT NOT NULL,
    state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, name, phase)
);

ALTER TABLE tasks ADD COLUMN task_group_id TEXT REFERENCES task_groups(id) ON DELETE RESTRICT;
ALTER TABLE tasks ADD COLUMN task_key TEXT;
ALTER TABLE tasks ADD COLUMN allowed_paths_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE tasks ADD COLUMN head_commit TEXT;
ALTER TABLE tasks ADD COLUMN producer TEXT;
ALTER TABLE tasks ADD COLUMN skill TEXT;
ALTER TABLE tasks ADD COLUMN prompt TEXT;

CREATE UNIQUE INDEX uq_tasks_group_key ON tasks(task_group_id, task_key) WHERE task_group_id IS NOT NULL;
CREATE INDEX ix_tasks_group_status ON tasks(task_group_id, status, updated_at);

CREATE TABLE task_dependencies (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    depends_on_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    dependency_type TEXT NOT NULL DEFAULT 'artifact' CHECK (dependency_type IN ('artifact', 'path_overlap', 'ordering')),
    created_at TEXT NOT NULL,
    PRIMARY KEY(task_id, depends_on_task_id),
    CHECK (task_id <> depends_on_task_id)
);

CREATE INDEX ix_task_dependencies_producer ON task_dependencies(depends_on_task_id, task_id);

ALTER TABLE handoffs ADD COLUMN reviewed_commit TEXT;
ALTER TABLE handoffs ADD COLUMN reviewer TEXT;
ALTER TABLE handoffs ADD COLUMN decision_reason TEXT;
ALTER TABLE handoffs ADD COLUMN rejection_reason TEXT;
ALTER TABLE handoffs ADD COLUMN blocked_reason TEXT;
ALTER TABLE handoffs ADD COLUMN updated_at TEXT;
ALTER TABLE handoffs ADD COLUMN state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0);

UPDATE handoffs SET updated_at = created_at WHERE updated_at IS NULL;

CREATE TABLE handoff_reviews (
    id TEXT PRIMARY KEY,
    handoff_id TEXT NOT NULL REFERENCES handoffs(id) ON DELETE RESTRICT,
    reviewer TEXT NOT NULL,
    reviewed_commit TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('accepted', 'rejected', 'blocked')),
    reason TEXT NOT NULL,
    findings_json TEXT NOT NULL,
    risks_json TEXT NOT NULL,
    report_ref TEXT NOT NULL,
    report_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(handoff_id, reviewed_commit, reviewer, report_hash)
);

CREATE TABLE integration_merges (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    task_group_id TEXT NOT NULL REFERENCES task_groups(id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    handoff_id TEXT NOT NULL REFERENCES handoffs(id) ON DELETE RESTRICT,
    source_branch TEXT NOT NULL,
    source_commit TEXT NOT NULL,
    integration_branch TEXT NOT NULL,
    integration_head_before TEXT NOT NULL,
    merge_commit TEXT,
    parent_commits_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (status IN ('pending', 'merged', 'conflicted', 'blocked')),
    conflict_paths_json TEXT NOT NULL DEFAULT '[]',
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(handoff_id, source_commit)
);

CREATE TABLE coordination_locks (
    lock_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    owner TEXT NOT NULL,
    lease_token_hash TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0)
);

CREATE TABLE workflow_worktrees (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('integration', 'release')),
    path TEXT NOT NULL UNIQUE,
    branch TEXT NOT NULL UNIQUE,
    base_commit TEXT NOT NULL,
    head_commit TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('provisioning', 'active', 'blocked', 'cleaned')),
    open_review_count INTEGER NOT NULL DEFAULT 0 CHECK (open_review_count >= 0),
    cleanup_status TEXT NOT NULL DEFAULT 'not_requested' CHECK (cleanup_status IN ('not_requested', 'requested', 'approved', 'completed', 'blocked')),
    state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    created_at TEXT NOT NULL,
    cleaned_at TEXT,
    UNIQUE(run_id, kind)
);

ALTER TABLE worktrees ADD COLUMN kind TEXT NOT NULL DEFAULT 'task' CHECK (kind IN ('task', 'integration', 'release'));
ALTER TABLE worktrees ADD COLUMN state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0);
ALTER TABLE worktrees ADD COLUMN head_commit TEXT;
ALTER TABLE worktrees ADD COLUMN open_review_count INTEGER NOT NULL DEFAULT 0 CHECK (open_review_count >= 0);
ALTER TABLE worktrees ADD COLUMN cleanup_status TEXT NOT NULL DEFAULT 'not_requested' CHECK (cleanup_status IN ('not_requested', 'requested', 'approved', 'completed', 'blocked'));

CREATE TABLE worktree_cleanups (
    id TEXT PRIMARY KEY,
    worktree_id TEXT NOT NULL REFERENCES worktrees(id) ON DELETE RESTRICT,
    requested_by TEXT NOT NULL,
    approved_by TEXT,
    reason TEXT NOT NULL,
    precondition_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('requested', 'approved', 'completed', 'blocked', 'failed')),
    git_result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(worktree_id, created_at)
);

CREATE TRIGGER trg_handoff_state_transition
BEFORE UPDATE OF status ON handoffs
WHEN NOT (
    OLD.status = NEW.status OR
    (OLD.status = 'ready' AND NEW.status IN ('accepted', 'rejected', 'blocked')) OR
    (OLD.status = 'accepted' AND NEW.status IN ('rejected', 'blocked')) OR
    (OLD.status = 'rejected' AND NEW.status = 'ready') OR
    (OLD.status = 'blocked' AND NEW.status = 'ready')
)
BEGIN
    SELECT RAISE(ABORT, 'invalid handoff state transition');
END;

CREATE TRIGGER trg_handoff_review_identity
BEFORE UPDATE OF status, reviewer, reviewed_commit ON handoffs
WHEN NEW.status IN ('accepted', 'rejected', 'blocked') AND (
    NEW.reviewer IS NULL OR NEW.reviewer = NEW.producer OR
    NEW.reviewed_commit IS NULL OR NEW.reviewed_commit = ''
)
BEGIN
    SELECT RAISE(ABORT, 'handoff decision requires independent reviewer and reviewed commit');
END;

CREATE TRIGGER trg_merge_requires_accepted_handoff
BEFORE INSERT ON integration_merges
WHEN NEW.status IN ('pending', 'merged') AND NOT EXISTS (
    SELECT 1 FROM handoffs
    WHERE id = NEW.handoff_id AND task_id = NEW.task_id
      AND status = 'accepted' AND reviewed_commit = NEW.source_commit
)
BEGIN
    SELECT RAISE(ABORT, 'integration merge requires accepted handoff for source commit');
END;

CREATE TRIGGER trg_group_complete_requires_join
BEFORE UPDATE OF status ON task_groups
WHEN NEW.status = 'completed' AND EXISTS (
    SELECT 1 FROM tasks t
    LEFT JOIN handoffs h ON h.task_id = t.id AND h.status = 'accepted'
    LEFT JOIN integration_merges m ON m.task_id = t.id AND m.status = 'merged'
    WHERE t.task_group_id = NEW.id
      AND (t.status <> 'completed' OR h.id IS NULL OR m.id IS NULL)
)
BEGIN
    SELECT RAISE(ABORT, 'task group join requires completed accepted merged tasks');
END;

CREATE TRIGGER trg_cleanup_complete_requires_approval
BEFORE UPDATE OF status ON worktree_cleanups
WHEN NEW.status = 'completed' AND (
    OLD.status <> 'approved' OR NEW.approved_by IS NULL OR
    json_extract(NEW.precondition_json, '$.merged') <> 1 OR
    json_extract(NEW.precondition_json, '$.clean') <> 1 OR
    json_extract(NEW.precondition_json, '$.no_open_review') <> 1 OR
    json_extract(NEW.precondition_json, '$.no_unknown_files') <> 1
)
BEGIN
    SELECT RAISE(ABORT, 'worktree cleanup requires approved safe preconditions');
END;

CREATE INDEX ix_task_groups_run_phase_status ON task_groups(run_id, phase, status);
CREATE INDEX ix_handoffs_task_status ON handoffs(task_id, status, updated_at);
CREATE INDEX ix_integration_merges_run_status ON integration_merges(run_id, status, updated_at);
CREATE INDEX ix_worktrees_run_kind_status ON worktrees(run_id, kind, status);
