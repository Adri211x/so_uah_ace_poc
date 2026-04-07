-- ============================================================================
-- DATABASE INITIALIZATION
-- Creates the required database for ACE platform
-- ============================================================================

CREATE DATABASE ace;
GRANT ALL PRIVILEGES ON DATABASE ace TO "ace-admin";

\connect ace;

-- Lightweight catalog of scenarios stored in DVC/S3.
-- Full case data (input, expected_output, fixtures) lives in object store.
CREATE TABLE scenarios (
    scenario_ref    TEXT PRIMARY KEY,
    scenario_id     TEXT NOT NULL,
    family          TEXT,
    root_cause_key  TEXT,
    variation_type  TEXT,
    source_file     TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Split assignments per training mode and fold.
-- Supports: stratified, leave_family_out, leave_scenario_out, leave_variation_out
CREATE TABLE split_assignments (
    id              SERIAL PRIMARY KEY,
    scenario_ref    TEXT NOT NULL REFERENCES scenarios(scenario_ref) ON DELETE CASCADE,
    split_type      TEXT NOT NULL,
    fold_name       TEXT NOT NULL,
    split           TEXT NOT NULL,
    UNIQUE(scenario_ref, split_type, fold_name)
);

CREATE INDEX idx_scenarios_scenario_id ON scenarios (scenario_id);
CREATE INDEX idx_scenarios_family ON scenarios (family);
CREATE INDEX idx_scenarios_variation_type ON scenarios (variation_type);

CREATE INDEX idx_split_assignments_split_type ON split_assignments (split_type);
CREATE INDEX idx_split_assignments_fold ON split_assignments (fold_name);
CREATE INDEX idx_split_assignments_split ON split_assignments (split);
CREATE INDEX idx_split_assignments_ref ON split_assignments (scenario_ref);

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO "ace-admin";
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO "ace-admin";
