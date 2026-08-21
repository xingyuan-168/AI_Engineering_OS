ALTER TABLE executions ADD COLUMN status TEXT NOT NULL DEFAULT 'running';
ALTER TABLE executions ADD COLUMN error_code TEXT;
ALTER TABLE executions ADD COLUMN network_mode TEXT NOT NULL DEFAULT 'disabled';
ALTER TABLE executions ADD COLUMN mounts_json TEXT NOT NULL DEFAULT '[]';

CREATE INDEX ix_executions_task_status ON executions(task_id, status);
