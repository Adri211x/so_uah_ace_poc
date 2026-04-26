-- ============================================================================
-- DATABASE INITIALIZATION
-- ACE Platform - Agentic Context Engineering
-- ============================================================================

CREATE DATABASE ace;
GRANT ALL PRIVILEGES ON DATABASE ace TO "ace-admin";

\connect ace;

CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================================
-- SCHEMA: cases
-- Lightweight catalog of scenarios stored in DVC/S3.
-- Full case data (input, expected_output, fixtures) lives in object store.
-- ============================================================================

CREATE SCHEMA cases;

CREATE TABLE cases.scenarios (
    scenario_ref    TEXT PRIMARY KEY,
    scenario_id     TEXT NOT NULL,
    family          TEXT,
    root_cause_key  TEXT,
    variation_type  TEXT,
    source_file     TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE cases.split_assignments (
    id              SERIAL PRIMARY KEY,
    scenario_ref    TEXT NOT NULL REFERENCES cases.scenarios(scenario_ref) ON DELETE CASCADE,
    split_type      TEXT NOT NULL,
    fold_name       TEXT NOT NULL,
    split           TEXT NOT NULL,
    UNIQUE(scenario_ref, split_type, fold_name)
);

CREATE INDEX idx_scenarios_scenario_id ON cases.scenarios (scenario_id);
CREATE INDEX idx_scenarios_family ON cases.scenarios (family);
CREATE INDEX idx_scenarios_variation_type ON cases.scenarios (variation_type);

CREATE INDEX idx_split_assignments_type ON cases.split_assignments (split_type);
CREATE INDEX idx_split_assignments_fold ON cases.split_assignments (fold_name);
CREATE INDEX idx_split_assignments_split ON cases.split_assignments (split);
CREATE INDEX idx_split_assignments_ref ON cases.split_assignments (scenario_ref);

-- ============================================================================
-- SCHEMA: runs
-- Execution tracking: agent runs, reasoning trajectories, evaluation metrics.
-- ============================================================================

CREATE SCHEMA runs;

CREATE TABLE runs.executions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scenario_ref        TEXT NOT NULL REFERENCES cases.scenarios(scenario_ref),
    split_type          TEXT,
    fold_name           TEXT,
    context_version_id  INTEGER,
    model_name          TEXT NOT NULL,
    model_config        JSONB NOT NULL DEFAULT '{}'::JSONB,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE runs.trajectory_steps (
    id              SERIAL PRIMARY KEY,
    execution_id    UUID NOT NULL REFERENCES runs.executions(id) ON DELETE CASCADE,
    step_order      INTEGER NOT NULL,
    component       TEXT NOT NULL,
    action_type     TEXT NOT NULL,
    input_data      JSONB,
    output_data     JSONB,
    duration_ms     INTEGER,
    tokens_in       INTEGER DEFAULT 0,
    tokens_out      INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE runs.evaluations (
    id              SERIAL PRIMARY KEY,
    execution_id    UUID NOT NULL REFERENCES runs.executions(id) ON DELETE CASCADE,
    metric_name     TEXT NOT NULL,
    score           DOUBLE PRECISION NOT NULL,
    details         JSONB NOT NULL DEFAULT '{}'::JSONB,
    evaluated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(execution_id, metric_name)
);

CREATE INDEX idx_executions_scenario ON runs.executions (scenario_ref);
CREATE INDEX idx_executions_status ON runs.executions (status);
CREATE INDEX idx_executions_model ON runs.executions (model_name);
CREATE INDEX idx_executions_context ON runs.executions (context_version_id);
CREATE INDEX idx_executions_created ON runs.executions (created_at);

CREATE INDEX idx_trajectory_execution ON runs.trajectory_steps (execution_id);
CREATE INDEX idx_trajectory_order ON runs.trajectory_steps (execution_id, step_order);

CREATE INDEX idx_evaluations_execution ON runs.evaluations (execution_id);
CREATE INDEX idx_evaluations_metric ON runs.evaluations (metric_name);

-- ============================================================================
-- SCHEMA: contexts
-- Evolving playbook: versioned context snapshots and insight bullets
-- with pgvector embeddings for semantic deduplication/retrieval.
-- ============================================================================

CREATE SCHEMA contexts;

CREATE TABLE contexts.context_versions (
    id              SERIAL PRIMARY KEY,
    version_tag     TEXT NOT NULL UNIQUE,
    parent_id       INTEGER REFERENCES contexts.context_versions(id),
    description     TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE contexts.bullets (
    id                      SERIAL PRIMARY KEY,
    context_version_id      INTEGER NOT NULL REFERENCES contexts.context_versions(id) ON DELETE CASCADE,
    content                 TEXT NOT NULL,
    category                TEXT,
    tool_name               TEXT,
    embedding               vector(3072),
    usage_count             INTEGER NOT NULL DEFAULT 0,
    origin_execution_id     UUID REFERENCES runs.executions(id) ON DELETE SET NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add FK from executions to context_versions now that both tables exist
ALTER TABLE runs.executions
    ADD CONSTRAINT fk_executions_context_version
    FOREIGN KEY (context_version_id) REFERENCES contexts.context_versions(id);

CREATE INDEX idx_context_versions_parent ON contexts.context_versions (parent_id);

CREATE INDEX idx_bullets_version ON contexts.bullets (context_version_id);
CREATE INDEX idx_bullets_category ON contexts.bullets (category);
CREATE INDEX idx_bullets_embedding ON contexts.bullets USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 10);
CREATE INDEX idx_bullets_origin ON contexts.bullets (origin_execution_id);

-- ============================================================================
-- PERMISSIONS
-- ============================================================================

GRANT ALL PRIVILEGES ON SCHEMA cases TO "ace-admin";
GRANT ALL PRIVILEGES ON SCHEMA runs TO "ace-admin";
GRANT ALL PRIVILEGES ON SCHEMA contexts TO "ace-admin";
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA cases TO "ace-admin";
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA runs TO "ace-admin";
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA contexts TO "ace-admin";
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA cases TO "ace-admin";
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA runs TO "ace-admin";
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA contexts TO "ace-admin";
