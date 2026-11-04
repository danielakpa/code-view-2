-- ============================================================
-- CODEVIEW IDE GRANT CONSUMPTION MIGRATION
-- ============================================================

-- STEP 1: Ensure the Runner schema exists.
CREATE SCHEMA IF NOT EXISTS runner;


-- STEP 2: Create the table for consumed IDE grants.
CREATE TABLE IF NOT EXISTS runner.ide_grant_consumptions (

    -- Unique identifier for each authorization grant.
    -- PRIMARY KEY prevents duplicate grant IDs.
    grant_id VARCHAR(128) PRIMARY KEY,

    -- Sandbox authorized by this grant.
    sandbox_id UUID NOT NULL,

    -- Developer who received the grant.
    developer_id INTEGER NOT NULL,

    -- Private development group.
    group_id INTEGER NOT NULL,

    -- Time when the grant expires.
    expires_at TIMESTAMPTZ NOT NULL,

    -- Time when the grant was consumed.
    consumed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- STEP 3: Create an index for expired-grant cleanup.
CREATE INDEX IF NOT EXISTS idx_ide_grant_consumptions_expires_at
ON runner.ide_grant_consumptions (expires_at);
