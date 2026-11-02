-- ============================================================
-- CODEVIEW 2.0: IDE OPERATION LEASES
-- ============================================================

-- A lease identifies the process currently responsible
-- for completing a persistent IDE operation.
--
-- These columns do not change existing RUN operations.

-- ------------------------------------------------------------
-- 1. LEASE OWNER
-- ------------------------------------------------------------

-- Random UUID identifying the current operation owner.
-- NULL means no owner has been assigned.

ALTER TABLE runner.sandbox_operation_requests
ADD COLUMN IF NOT EXISTS lease_owner UUID;


-- ------------------------------------------------------------
-- 2. LEASE EXPIRATION
-- ------------------------------------------------------------

-- The time after which the current lease may be reclaimed.
-- A worker must renew its lease while still operating.

ALTER TABLE runner.sandbox_operation_requests
ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;


-- ------------------------------------------------------------
-- 3. LEASE GENERATION
-- ------------------------------------------------------------

-- Incremented whenever a different worker claims ownership.
-- This acts as a fencing token to reject stale updates.

ALTER TABLE runner.sandbox_operation_requests
ADD COLUMN IF NOT EXISTS lease_generation BIGINT NOT NULL DEFAULT 0;


-- ------------------------------------------------------------
-- 4. VALIDATION
-- ------------------------------------------------------------

-- An owner and expiration must either both exist or
-- both be NULL.

ALTER TABLE runner.sandbox_operation_requests
ADD CONSTRAINT sandbox_operation_lease_pair_check
CHECK (
    (lease_owner IS NULL AND lease_expires_at IS NULL)
    OR
    (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
);


-- Lease generations cannot be negative.

ALTER TABLE runner.sandbox_operation_requests
ADD CONSTRAINT sandbox_operation_lease_generation_check
CHECK (lease_generation >= 0);


-- ------------------------------------------------------------
-- 5. RECOVERY INDEX
-- ------------------------------------------------------------

-- Helps recovery workers locate expired pending operations.

CREATE INDEX IF NOT EXISTS idx_sandbox_operation_expired_leases
ON runner.sandbox_operation_requests (
    lease_expires_at,
    created_at
)
WHERE status = 'pending'
  AND operation IN ('create', 'start', 'stop', 'inspect');