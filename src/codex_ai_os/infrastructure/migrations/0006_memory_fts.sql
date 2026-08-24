UPDATE memory_records SET status = 'pending' WHERE status = 'candidate';
UPDATE memory_records SET status = 'needs_review' WHERE status = 'invalidated';

CREATE TABLE memory_reviews (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES memory_records(id) ON DELETE RESTRICT,
    reviewer TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('activate', 'needs_review', 'revoke', 'expire', 'delete')),
    reason TEXT NOT NULL,
    source_hashes_json TEXT NOT NULL,
    secret_check_passed INTEGER NOT NULL CHECK (secret_check_passed IN (0, 1)),
    scope_check_passed INTEGER NOT NULL CHECK (scope_check_passed IN (0, 1)),
    confidence_check_passed INTEGER NOT NULL CHECK (confidence_check_passed IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE(memory_id, reviewer, decision, source_hashes_json)
);

CREATE TABLE memory_links (
    source_memory_id TEXT NOT NULL REFERENCES memory_records(id) ON DELETE RESTRICT,
    target_memory_id TEXT NOT NULL REFERENCES memory_records(id) ON DELETE RESTRICT,
    relation TEXT NOT NULL CHECK (relation IN ('supersedes', 'supports', 'contradicts', 'derived_from')),
    created_at TEXT NOT NULL,
    PRIMARY KEY(source_memory_id, target_memory_id, relation),
    CHECK (source_memory_id <> target_memory_id)
);

CREATE TABLE memory_search_documents (
    rowid INTEGER PRIMARY KEY,
    memory_id TEXT NOT NULL UNIQUE REFERENCES memory_records(id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'needs_review', 'superseded', 'revoked', 'expired', 'deleted')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE memory_fts USING fts5(
    title,
    content,
    tags,
    source_ref,
    content='memory_search_documents',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER memory_search_ai AFTER INSERT ON memory_search_documents
WHEN NEW.status <> 'deleted'
BEGIN
    INSERT INTO memory_fts(rowid, title, content, tags, source_ref)
    VALUES (NEW.rowid, NEW.title, NEW.content, NEW.tags, NEW.source_ref);
END;

CREATE TRIGGER memory_search_ad AFTER DELETE ON memory_search_documents
WHEN OLD.status <> 'deleted'
BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, title, content, tags, source_ref)
    VALUES ('delete', OLD.rowid, OLD.title, OLD.content, OLD.tags, OLD.source_ref);
END;

CREATE TRIGGER memory_search_au AFTER UPDATE ON memory_search_documents
BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, title, content, tags, source_ref)
    SELECT 'delete', OLD.rowid, OLD.title, OLD.content, OLD.tags, OLD.source_ref
    WHERE OLD.status <> 'deleted';
    INSERT INTO memory_fts(rowid, title, content, tags, source_ref)
    SELECT NEW.rowid, NEW.title, NEW.content, NEW.tags, NEW.source_ref
    WHERE NEW.status <> 'deleted';
END;

CREATE TRIGGER trg_memory_status_transition
BEFORE UPDATE OF status ON memory_records
WHEN NOT (
    OLD.status = NEW.status OR
    (OLD.status = 'pending' AND NEW.status IN ('active', 'needs_review', 'revoked', 'deleted')) OR
    (OLD.status = 'active' AND NEW.status IN ('needs_review', 'superseded', 'revoked', 'expired', 'deleted')) OR
    (OLD.status = 'needs_review' AND NEW.status IN ('active', 'superseded', 'revoked', 'expired', 'deleted')) OR
    (OLD.status = 'superseded' AND NEW.status IN ('needs_review', 'revoked', 'deleted')) OR
    (OLD.status = 'revoked' AND NEW.status IN ('needs_review', 'deleted')) OR
    (OLD.status = 'expired' AND NEW.status IN ('needs_review', 'deleted'))
)
BEGIN
    SELECT RAISE(ABORT, 'invalid memory state transition');
END;

CREATE TRIGGER trg_memory_activate_requires_review
BEFORE UPDATE OF status ON memory_records
WHEN NEW.status = 'active' AND OLD.status <> 'active' AND NOT EXISTS (
    SELECT 1 FROM memory_reviews r
    WHERE r.memory_id = NEW.id AND r.decision = 'activate'
      AND r.secret_check_passed = 1 AND r.scope_check_passed = 1
      AND r.confidence_check_passed = 1
)
BEGIN
    SELECT RAISE(ABORT, 'memory activation requires passing review');
END;

CREATE TRIGGER trg_memory_superseded_requires_link
BEFORE UPDATE OF status ON memory_records
WHEN NEW.status = 'superseded' AND NOT EXISTS (
    SELECT 1 FROM memory_links l
    WHERE l.target_memory_id = NEW.id AND l.relation = 'supersedes'
)
BEGIN
    SELECT RAISE(ABORT, 'superseded memory requires supersedes link');
END;

CREATE INDEX ix_memory_records_project_status_updated
ON memory_records(project_id, status, updated_at);
CREATE INDEX ix_memory_reviews_memory_created ON memory_reviews(memory_id, created_at);
CREATE INDEX ix_memory_search_project_status ON memory_search_documents(project_id, status, updated_at);
