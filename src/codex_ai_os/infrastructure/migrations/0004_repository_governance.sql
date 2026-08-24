ALTER TABLE projects ADD COLUMN config_schema_version TEXT NOT NULL DEFAULT '1.0';
ALTER TABLE projects ADD COLUMN repository_ready INTEGER NOT NULL DEFAULT 0 CHECK (repository_ready IN (0, 1));
ALTER TABLE projects ADD COLUMN repository_mode TEXT NOT NULL DEFAULT 'formal' CHECK (repository_mode IN ('formal', 'fixture_local_only'));
ALTER TABLE projects ADD COLUMN target_branch TEXT NOT NULL DEFAULT 'main';

ALTER TABLE workflow_runs ADD COLUMN profiles_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE workflow_runs ADD COLUMN target_branch TEXT NOT NULL DEFAULT 'main';
ALTER TABLE workflow_runs ADD COLUMN integration_branch TEXT;
ALTER TABLE workflow_runs ADD COLUMN base_commit TEXT;
ALTER TABLE workflow_runs ADD COLUMN integration_head TEXT;
ALTER TABLE workflow_runs ADD COLUMN max_parallel_agents INTEGER NOT NULL DEFAULT 4 CHECK (max_parallel_agents BETWEEN 1 AND 4);
ALTER TABLE workflow_runs ADD COLUMN migration_revalidation_required INTEGER NOT NULL DEFAULT 0 CHECK (migration_revalidation_required IN (0, 1));

UPDATE workflow_runs
SET migration_revalidation_required = 1
WHERE run_status NOT IN ('completed', 'cancelled');

ALTER TABLE approvals ADD COLUMN evidence_bundle_id TEXT;
ALTER TABLE approvals ADD COLUMN evidence_bundle_hash TEXT;
ALTER TABLE approvals ADD COLUMN release_authority_json TEXT;

ALTER TABLE documents ADD COLUMN schema_version TEXT;
ALTER TABLE documents ADD COLUMN document_version TEXT;
ALTER TABLE documents ADD COLUMN owner TEXT;
ALTER TABLE documents ADD COLUMN requirement_refs_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE documents ADD COLUMN findings_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE executions ADD COLUMN run_id TEXT REFERENCES workflow_runs(id) ON DELETE RESTRICT;
ALTER TABLE executions ADD COLUMN worktree_id TEXT REFERENCES worktrees(id) ON DELETE RESTRICT;
ALTER TABLE executions ADD COLUMN report_hash TEXT;
ALTER TABLE executions ADD COLUMN dirty_before INTEGER NOT NULL DEFAULT 0 CHECK (dirty_before IN (0, 1));
ALTER TABLE executions ADD COLUMN dirty_after INTEGER NOT NULL DEFAULT 0 CHECK (dirty_after IN (0, 1));

CREATE TABLE repository_audits (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    run_id TEXT REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    mode TEXT NOT NULL CHECK (mode IN ('formal', 'fixture_local_only')),
    remote_name TEXT,
    remote_host TEXT,
    remote_url_hash TEXT,
    target_branch TEXT NOT NULL,
    head_commit TEXT,
    upstream_ref TEXT,
    repository_ready INTEGER NOT NULL CHECK (repository_ready IN (0, 1)),
    hygiene_ok INTEGER NOT NULL CHECK (hygiene_ok IN (0, 1)),
    check_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX ix_repository_audits_project_created
ON repository_audits(project_id, created_at);

CREATE TABLE repository_findings (
    id TEXT PRIMARY KEY,
    audit_id TEXT NOT NULL REFERENCES repository_audits(id) ON DELETE RESTRICT,
    code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error', 'critical')),
    path TEXT,
    details_json TEXT NOT NULL,
    blocking INTEGER NOT NULL CHECK (blocking IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE INDEX ix_repository_findings_audit_blocking
ON repository_findings(audit_id, blocking, code);

CREATE TABLE file_lifecycle_entries (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    run_id TEXT REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    task_id TEXT REFERENCES tasks(id) ON DELETE RESTRICT,
    path TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('disposable', 'promotable', 'audit-evidence')),
    creator TEXT NOT NULL,
    content_hash TEXT,
    status TEXT NOT NULL CHECK (status IN ('registered', 'promoted', 'retained', 'cleanup_requested', 'cleaned', 'blocked')),
    retention_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, path, created_at)
);

CREATE TABLE routing_decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    profiles_json TEXT NOT NULL,
    project_type TEXT NOT NULL,
    impact_json TEXT NOT NULL,
    rationale TEXT NOT NULL,
    source_commit TEXT,
    decision_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, decision_hash)
);

CREATE TABLE gate_evidence_bundles (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    gate TEXT NOT NULL CHECK (gate IN ('G0', 'G1', 'G2', 'G3', 'G4')),
    state_version INTEGER NOT NULL CHECK (state_version >= 0),
    source_commit TEXT,
    status TEXT NOT NULL CHECK (status IN ('building', 'complete', 'stale', 'rejected')),
    required_artifacts_json TEXT NOT NULL,
    bundle_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(run_id, gate, state_version),
    UNIQUE(run_id, bundle_hash)
);

CREATE TABLE artifact_evidence (
    id TEXT PRIMARY KEY,
    bundle_id TEXT REFERENCES gate_evidence_bundles(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    task_id TEXT REFERENCES tasks(id) ON DELETE RESTRICT,
    path TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_commit TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('candidate', 'verified', 'stale', 'rejected')),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, path, content_hash, source_commit)
);

CREATE TABLE check_evidence (
    id TEXT PRIMARY KEY,
    bundle_id TEXT REFERENCES gate_evidence_bundles(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    task_id TEXT REFERENCES tasks(id) ON DELETE RESTRICT,
    check_name TEXT NOT NULL,
    command_hash TEXT NOT NULL,
    execution_id TEXT REFERENCES executions(id) ON DELETE RESTRICT,
    exit_code INTEGER NOT NULL,
    report_path TEXT NOT NULL,
    report_hash TEXT NOT NULL,
    source_commit TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('passed', 'failed', 'blocked', 'stale')),
    executed_at TEXT NOT NULL,
    UNIQUE(run_id, check_name, source_commit, report_hash)
);

CREATE TABLE review_evidence (
    id TEXT PRIMARY KEY,
    bundle_id TEXT REFERENCES gate_evidence_bundles(id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    task_id TEXT REFERENCES tasks(id) ON DELETE RESTRICT,
    review_type TEXT NOT NULL CHECK (review_type IN ('code', 'security', 'handoff', 'release')),
    reviewer TEXT NOT NULL,
    reviewed_commit TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('accepted', 'rejected', 'blocked')),
    findings_json TEXT NOT NULL,
    risks_json TEXT NOT NULL,
    report_ref TEXT NOT NULL,
    report_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, review_type, reviewer, reviewed_commit, report_hash)
);

CREATE TABLE version_records (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    requirement_baseline TEXT NOT NULL,
    software_version TEXT NOT NULL,
    plugin_api_version TEXT NOT NULL,
    config_schema_version TEXT NOT NULL,
    sqlite_schema_version TEXT NOT NULL,
    git_tag TEXT NOT NULL,
    source_commit TEXT,
    config_hash TEXT NOT NULL,
    dependency_lock_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('planned', 'candidate', 'released', 'revoked')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, software_version)
);

CREATE TABLE release_records (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    task_id TEXT REFERENCES tasks(id) ON DELETE RESTRICT,
    version_record_id TEXT NOT NULL REFERENCES version_records(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('candidate', 'g4_ready', 'authorized', 'tagged', 'published', 'blocked', 'revoked')),
    release_worktree_id TEXT REFERENCES worktrees(id) ON DELETE RESTRICT,
    manifest_path TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    artifact_root TEXT NOT NULL,
    sbom_path TEXT,
    sbom_hash TEXT,
    checksums_path TEXT,
    checksums_hash TEXT,
    rollback_path TEXT,
    rollback_hash TEXT,
    pr_number INTEGER,
    pr_url_hash TEXT,
    pr_head TEXT,
    pr_base TEXT,
    pr_head_commit TEXT,
    merge_commit TEXT,
    tag TEXT,
    github_release_id TEXT,
    authorization_json TEXT,
    source_commit TEXT NOT NULL,
    state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, version_record_id)
);

CREATE INDEX ix_check_evidence_run_source ON check_evidence(run_id, source_commit, status);
CREATE INDEX ix_review_evidence_run_source ON review_evidence(run_id, reviewed_commit, decision);
CREATE INDEX ix_release_records_run_status ON release_records(run_id, status, updated_at);
