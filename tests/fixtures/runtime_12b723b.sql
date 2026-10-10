-- ADR-0016: governance-core runtime schema.
-- Four tables only. Memory facts live in the Git-tracked
-- docs/memory/memory.jsonl; memory_index is a locally rebuildable search
-- index refreshed by "codex-os memory reindex".

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    branch TEXT,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'in_progress', 'blocked', 'done')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    gate TEXT NOT NULL CHECK (gate IN ('code_start', 'frontend', 'finish')),
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    decided_by TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE worktrees (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    name TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL UNIQUE,
    branch TEXT NOT NULL UNIQUE,
    disposable INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'merged', 'cleaned')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE memory_index (
    id TEXT PRIMARY KEY,
    record_type TEXT NOT NULL
        CHECK (record_type IN ('decision', 'bug', 'lesson', 'pattern', 'project-summary')),
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    source TEXT NOT NULL,
    source_commit TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'invalid')),
    superseded_by TEXT,
    line_number INTEGER NOT NULL,
    indexed_at TEXT NOT NULL
);

CREATE INDEX ix_tasks_status ON tasks(status);
CREATE INDEX ix_approvals_subject ON approvals(subject, created_at);
CREATE INDEX ix_worktrees_status ON worktrees(status);
CREATE INDEX ix_memory_type_status ON memory_index(record_type, status);
