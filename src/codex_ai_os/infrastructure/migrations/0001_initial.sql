CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root TEXT NOT NULL UNIQUE,
    config_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE workflow_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    workflow_name TEXT NOT NULL,
    goal TEXT NOT NULL,
    workflow_phase TEXT NOT NULL,
    run_status TEXT NOT NULL,
    state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    risk_level TEXT NOT NULL,
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    config_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX workflow_runs_project_idx ON workflow_runs(project_id, updated_at);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    agent TEXT NOT NULL,
    status TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    branch TEXT,
    worktree TEXT,
    output_ref TEXT,
    review_status TEXT,
    state_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, agent, input_hash)
);

CREATE TABLE events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    run_id TEXT REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    task_id TEXT REFERENCES tasks(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    payload_json TEXT NOT NULL,
    approval_required INTEGER NOT NULL DEFAULT 0 CHECK (approval_required IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE INDEX events_project_idx ON events(project_id, sequence);
CREATE INDEX events_run_idx ON events(run_id, sequence);

CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    gate TEXT NOT NULL,
    decision TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, gate, state_version)
);

CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    task_id TEXT REFERENCES tasks(id) ON DELETE RESTRICT,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_commit TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, path, content_hash)
);

CREATE TABLE documents (
    path TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    doc_type TEXT NOT NULL,
    status TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_commit TEXT,
    last_checked_at TEXT NOT NULL
);

CREATE TABLE handoffs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    producer TEXT NOT NULL,
    consumer TEXT NOT NULL,
    status TEXT NOT NULL,
    artifact_refs_json TEXT NOT NULL,
    commit_refs_json TEXT NOT NULL,
    tests_json TEXT NOT NULL,
    risks_json TEXT NOT NULL,
    open_questions_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    accepted_at TEXT
);

CREATE TABLE executions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    risk_level TEXT NOT NULL,
    command_hash TEXT NOT NULL,
    image_digest TEXT,
    container_id TEXT,
    exit_code INTEGER,
    stdout_ref TEXT,
    stderr_ref TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE worktrees (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    agent TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    branch TEXT NOT NULL UNIQUE,
    base_commit TEXT NOT NULL,
    status TEXT NOT NULL,
    dirty INTEGER NOT NULL DEFAULT 0 CHECK (dirty IN (0, 1)),
    created_at TEXT NOT NULL,
    cleaned_at TEXT
);

CREATE TABLE memory_records (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    run_id TEXT REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    task_id TEXT REFERENCES tasks(id) ON DELETE RESTRICT,
    record_type TEXT NOT NULL,
    title TEXT NOT NULL,
    content_ref TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    source_hashes_json TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    tags_json TEXT NOT NULL,
    scope TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT
);
