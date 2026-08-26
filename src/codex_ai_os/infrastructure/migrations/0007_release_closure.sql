CREATE TABLE host_operations (
    operation_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    run_id TEXT REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    task_id TEXT REFERENCES tasks(id) ON DELETE RESTRICT,
    task_group_id TEXT REFERENCES task_groups(id) ON DELETE RESTRICT,
    handoff_id TEXT REFERENCES handoffs(id) ON DELETE RESTRICT,
    release_id TEXT REFERENCES release_records(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN (
        'integration_prepare', 'integration_merge', 'release_prepare',
        'release_publish', 'verification_prepare', 'database_migrate'
    )),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
    status TEXT NOT NULL CHECK (status IN (
        'pending', 'running', 'succeeded', 'failed', 'reconcile_required'
    )),
    expected_state_version INTEGER CHECK (expected_state_version >= 0),
    expected_task_version INTEGER CHECK (expected_task_version >= 0),
    state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    lease_owner TEXT,
    lease_expires_at TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, kind, idempotency_key)
);

CREATE INDEX ix_host_operations_run_status_lease
ON host_operations(run_id, status, lease_expires_at);

CREATE INDEX ix_host_operations_object
ON host_operations(project_id, kind, task_id, handoff_id, release_id);

CREATE TABLE api_call_audits (
    call_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    principal TEXT NOT NULL,
    operation TEXT NOT NULL,
    project_id TEXT REFERENCES projects(id) ON DELETE RESTRICT,
    run_id TEXT REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    task_id TEXT REFERENCES tasks(id) ON DELETE RESTRICT,
    host_operation_id TEXT REFERENCES host_operations(operation_id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'blocked')),
    error_code TEXT,
    state_version INTEGER CHECK (state_version >= 0),
    duration_ms INTEGER NOT NULL CHECK (duration_ms >= 0),
    request_summary_json TEXT NOT NULL,
    response_summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(request_id, operation)
);

CREATE INDEX ix_api_call_audits_project_created
ON api_call_audits(project_id, created_at);

ALTER TABLE memory_records
ADD COLUMN state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0);

ALTER TABLE memory_reviews
ADD COLUMN expected_version INTEGER NOT NULL DEFAULT 0 CHECK (expected_version >= 0);

ALTER TABLE routing_decisions ADD COLUMN routing_input_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE routing_decisions ADD COLUMN dimension_scores_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE routing_decisions ADD COLUMN total_score REAL NOT NULL DEFAULT 0
    CHECK (total_score >= 0 AND total_score <= 10);
ALTER TABLE routing_decisions ADD COLUMN risk_level TEXT;
ALTER TABLE routing_decisions ADD COLUMN workflow TEXT;
ALTER TABLE routing_decisions ADD COLUMN canonical_profiles_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE routing_decisions ADD COLUMN human_override_json TEXT;
ALTER TABLE routing_decisions ADD COLUMN rules_version TEXT NOT NULL DEFAULT '1.2';
ALTER TABLE routing_decisions ADD COLUMN profile_schema_version TEXT NOT NULL DEFAULT '1.2';

ALTER TABLE check_evidence ADD COLUMN started_at TEXT;
ALTER TABLE check_evidence ADD COLUMN ended_at TEXT;
UPDATE check_evidence
SET started_at = executed_at, ended_at = executed_at
WHERE started_at IS NULL OR ended_at IS NULL;

ALTER TABLE version_records ADD COLUMN plugin_version TEXT NOT NULL DEFAULT '0.2.0';
ALTER TABLE version_records ADD COLUMN document_schema_version TEXT NOT NULL DEFAULT '1.2';
ALTER TABLE version_records ADD COLUMN profile_schema_version TEXT NOT NULL DEFAULT '1.2';
ALTER TABLE version_records ADD COLUMN execution_image TEXT NOT NULL DEFAULT
    'python:3.12.14-bookworm@sha256:852282e520cc1754221fb2e061ab35b13b596e8112a731d60e2a8b471c973b7a';

ALTER TABLE release_records ADD COLUMN integration_source_commit TEXT;
ALTER TABLE release_records ADD COLUMN candidate_commit TEXT;
ALTER TABLE release_records ADD COLUMN candidate_manifest_path TEXT;
ALTER TABLE release_records ADD COLUMN candidate_manifest_hash TEXT;
ALTER TABLE release_records ADD COLUMN final_manifest_path TEXT;
ALTER TABLE release_records ADD COLUMN final_manifest_hash TEXT;
ALTER TABLE release_records ADD COLUMN registry_index_digest TEXT;
ALTER TABLE release_records ADD COLUMN platform_digest TEXT;
ALTER TABLE release_records ADD COLUMN external_reconciliation_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE release_records
ADD COLUMN publish_operation_id TEXT REFERENCES host_operations(operation_id) ON DELETE RESTRICT;
ALTER TABLE release_records ADD COLUMN reconciled_at TEXT;

UPDATE release_records
SET integration_source_commit = source_commit,
    candidate_manifest_path = manifest_path,
    candidate_manifest_hash = manifest_hash
WHERE integration_source_commit IS NULL;

UPDATE workflow_runs
SET migration_revalidation_required = 1
WHERE run_status NOT IN ('completed', 'cancelled');

DROP TRIGGER trg_handoff_state_transition;

CREATE TRIGGER trg_handoff_state_transition
BEFORE UPDATE OF status ON handoffs
WHEN NOT (
    OLD.status = NEW.status OR
    (OLD.status = 'ready' AND NEW.status IN ('accepted', 'rejected', 'blocked')) OR
    (OLD.status = 'rejected' AND NEW.status = 'ready') OR
    (OLD.status = 'blocked' AND NEW.status = 'ready')
)
BEGIN
    SELECT RAISE(ABORT, 'invalid handoff state transition');
END;

CREATE TRIGGER trg_check_evidence_exit_status_insert
BEFORE INSERT ON check_evidence
WHEN (NEW.status = 'passed' AND NEW.exit_code <> 0)
  OR (NEW.status = 'failed' AND NEW.exit_code = 0)
  OR (NEW.started_at IS NOT NULL AND NEW.ended_at IS NOT NULL
      AND NEW.ended_at < NEW.started_at)
BEGIN
    SELECT RAISE(ABORT, 'check evidence status/timing does not match result');
END;

CREATE TRIGGER trg_check_evidence_exit_status_update
BEFORE UPDATE OF exit_code, status, started_at, ended_at ON check_evidence
WHEN (NEW.status = 'passed' AND NEW.exit_code <> 0)
  OR (NEW.status = 'failed' AND NEW.exit_code = 0)
  OR (NEW.started_at IS NOT NULL AND NEW.ended_at IS NOT NULL
      AND NEW.ended_at < NEW.started_at)
BEGIN
    SELECT RAISE(ABORT, 'check evidence status/timing does not match result');
END;

INSERT INTO memory_fts(memory_fts) VALUES ('rebuild');
