-- Rebuild host_operations without renaming its referenced table. The migration
-- runner disables FK enforcement only for this transaction and checks all FKs
-- before committing; existing release/audit references retain their target.
CREATE TABLE runtime_actors (
    actor_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    principal TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('human', 'agent', 'runtime')),
    role TEXT NOT NULL,
    task_id TEXT REFERENCES tasks(id) ON DELETE RESTRICT,
    issuer TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE operation_authorizations (
    authorization_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    actor_id TEXT NOT NULL REFERENCES runtime_actors(actor_id) ON DELETE RESTRICT,
    principal TEXT NOT NULL,
    source_commit TEXT NOT NULL CHECK (length(source_commit) IN (40, 64)),
    operation_kind TEXT NOT NULL CHECK (operation_kind IN (
        'environment_prepare', 'verification_prepare', 'database_migrate'
    )),
    request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
    scope_json TEXT NOT NULL CHECK (json_valid(scope_json)),
    allowed_hosts_json TEXT NOT NULL CHECK (json_valid(allowed_hosts_json)),
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('approved', 'revoked')),
    idempotency_key TEXT NOT NULL,
    state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    UNIQUE(project_id, idempotency_key)
);
CREATE INDEX ix_operation_authorizations_scope
ON operation_authorizations(project_id, task_id, operation_kind, status, expires_at);

CREATE TABLE host_operation_legacy_snapshots (
    operation_id TEXT PRIMARY KEY,
    original_kind TEXT NOT NULL,
    original_status TEXT NOT NULL,
    original_state_version INTEGER NOT NULL,
    original_request_json TEXT NOT NULL,
    original_result_json TEXT NOT NULL,
    original_lease_owner TEXT,
    original_lease_expires_at TEXT
);
INSERT INTO host_operation_legacy_snapshots
SELECT operation_id, kind, status, state_version, request_json, result_json,
    lease_owner, lease_expires_at FROM host_operations;

CREATE TABLE host_operations_next (
    operation_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    run_id TEXT REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    task_id TEXT REFERENCES tasks(id) ON DELETE RESTRICT,
    task_group_id TEXT REFERENCES task_groups(id) ON DELETE RESTRICT,
    handoff_id TEXT REFERENCES handoffs(id) ON DELETE RESTRICT,
    release_id TEXT REFERENCES release_records(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN (
        'integration_prepare', 'integration_merge', 'release_prepare',
        'release_publish', 'verification_prepare', 'database_migrate',
        'environment_adopt', 'environment_prepare', 'environment_verify'
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
    execution_generation INTEGER NOT NULL DEFAULT 0 CHECK (execution_generation >= 0),
    authorization_id TEXT REFERENCES operation_authorizations(authorization_id) ON DELETE RESTRICT,
    UNIQUE(project_id, kind, idempotency_key)
);
INSERT INTO host_operations_next
SELECT *, attempt_count, NULL FROM host_operations;
DROP TABLE host_operations;
ALTER TABLE host_operations_next RENAME TO host_operations;
CREATE INDEX ix_host_operations_run_status_lease
ON host_operations(run_id, status, lease_expires_at);
CREATE INDEX ix_host_operations_object
ON host_operations(project_id, kind, task_id, handoff_id, release_id);

UPDATE host_operations
SET kind = json_extract(request_json, '$.operation_type')
WHERE json_valid(request_json) AND (
    (kind = 'integration_prepare' AND json_extract(request_json, '$.operation_type') = 'environment_adopt')
    OR (kind = 'verification_prepare' AND json_extract(request_json, '$.operation_type') IN (
        'environment_prepare', 'environment_verify'
    ))
);
UPDATE host_operations
SET status = 'reconcile_required', state_version = state_version + 1,
    lease_owner = NULL, lease_expires_at = NULL,
    error_code = 'MIGRATION_REVALIDATION_REQUIRED'
WHERE (json_valid(request_json) AND json_extract(request_json, '$.operation_type') LIKE 'environment_%')
    OR (status = 'running' AND kind != 'database_migrate');

CREATE TABLE operation_attempts (
    operation_id TEXT NOT NULL REFERENCES host_operations(operation_id) ON DELETE RESTRICT,
    generation INTEGER NOT NULL CHECK (generation >= 1),
    lease_owner TEXT NOT NULL,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'reconcile_required')),
    result_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(result_json)),
    PRIMARY KEY(operation_id, generation)
);

CREATE TABLE operation_resources (
    resource_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL REFERENCES host_operations(operation_id) ON DELETE RESTRICT,
    generation INTEGER NOT NULL,
    backend TEXT NOT NULL CHECK (backend IN ('docker', 'podman', 'git', 'filesystem')),
    resource_kind TEXT NOT NULL,
    external_id TEXT NOT NULL,
    ownership_json TEXT NOT NULL CHECK (json_valid(ownership_json)),
    status TEXT NOT NULL CHECK (status IN ('allocated', 'retained', 'released', 'reconcile_required')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(operation_id, backend, resource_kind, external_id),
    FOREIGN KEY(operation_id, generation) REFERENCES operation_attempts(operation_id, generation)
);

CREATE TABLE evidence_contexts (
    evidence_id TEXT PRIMARY KEY REFERENCES check_evidence(id) ON DELETE RESTRICT,
    execution_id TEXT NOT NULL REFERENCES executions(id) ON DELETE RESTRICT,
    operation_id TEXT REFERENCES host_operations(operation_id) ON DELETE RESTRICT,
    generation INTEGER,
    source_commit TEXT NOT NULL CHECK (length(source_commit) IN (40, 64)),
    definition_hash TEXT NOT NULL CHECK (length(definition_hash) = 64),
    input_hash TEXT NOT NULL CHECK (length(input_hash) = 64),
    policy_hash TEXT NOT NULL CHECK (length(policy_hash) = 64),
    context_json TEXT NOT NULL CHECK (json_valid(context_json)),
    created_at TEXT NOT NULL,
    FOREIGN KEY(operation_id, generation) REFERENCES operation_attempts(operation_id, generation)
);
CREATE INDEX ix_evidence_contexts_operation ON evidence_contexts(operation_id, generation);

UPDATE workflow_runs SET migration_revalidation_required = 1
WHERE run_status NOT IN ('completed', 'cancelled');
