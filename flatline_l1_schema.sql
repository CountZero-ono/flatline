PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    user_annotation TEXT,
    crystallized_at INTEGER,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED', 'CRYSTALLIZED'))
);

CREATE TABLE observations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    content TEXT NOT NULL,
    recorded_at INTEGER NOT NULL,
    decay_class TEXT NOT NULL CHECK (decay_class IN ('ARCHITECTURAL', 'OPERATIONAL', 'TRANSIENT', 'PERSONAL')),
    decay_score REAL NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'CANDIDATE' CHECK (status IN ('CANDIDATE', 'ACTIVE', 'VALIDATED', 'INVALIDATED', 'SUPERSEDED', 'DECAYED', 'GAP')),
    source_type TEXT NOT NULL DEFAULT 'SESSION' CHECK (source_type IN ('SESSION', 'EXTERNAL', 'INFERRED')),
    contradiction_flag TEXT,
    promoted_at INTEGER
);

CREATE TABLE contradiction_flags (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    observation_a_id TEXT NOT NULL REFERENCES observations(id),
    observation_b_id TEXT NOT NULL REFERENCES observations(id),
    description TEXT NOT NULL,
    verdict TEXT CHECK (verdict IN ('A_WINS', 'B_WINS', 'NEITHER', 'DEFERRED')),
    resolved_at INTEGER
);

CREATE INDEX idx_observations_session_id ON observations(session_id);
CREATE INDEX idx_observations_status ON observations(status);
CREATE INDEX idx_observations_decay_class ON observations(decay_class);
CREATE INDEX idx_contradiction_flags_session_id ON contradiction_flags(session_id);
CREATE INDEX idx_contradiction_flags_unresolved_verdict ON contradiction_flags(verdict) WHERE verdict IS NULL;

-- Task relay queue: Antigravity ↔ Dixie
-- post_task writes here; read_task reads and marks in_progress; post_result writes to task_results.
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,                          -- UUID
    created_at INTEGER NOT NULL,                  -- Unix epoch
    posted_by TEXT NOT NULL DEFAULT 'antigravity',-- 'antigravity' | 'naima' etc.
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'in_progress', 'done', 'failed')),
    content TEXT NOT NULL,                        -- full task instruction (markdown)
    picked_up_at INTEGER                          -- set when Dixie reads it
);

CREATE TABLE IF NOT EXISTS task_results (
    id TEXT PRIMARY KEY,                          -- UUID
    task_id TEXT NOT NULL REFERENCES tasks(id),
    created_at INTEGER NOT NULL,                  -- Unix epoch
    posted_by TEXT NOT NULL DEFAULT 'dixie',
    content TEXT NOT NULL                         -- Dixie's result/report (markdown)
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_task_results_task_id ON task_results(task_id);

