-- ============================================================
-- CODEVIEW 2.0: TRACK DOCKER IDE OPERATIONS
-- ============================================================

-- Add a column to track operation progress.
-- Existing records default to pending because their
-- completion status was not previously recorded.

ALTER TABLE runner.sandbox_operation_requests
ADD COLUMN IF NOT EXISTS status VARCHAR(20)
NOT NULL DEFAULT 'pending';


-- Add a column to record when an operation finishes.

ALTER TABLE runner.sandbox_operation_requests
ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;


-- Add a column to store operation failure messages.

ALTER TABLE runner.sandbox_operation_requests
ADD COLUMN IF NOT EXISTS error_message TEXT;


-- Restrict operation status to supported values.
-- This migration is intended to be applied once.

ALTER TABLE runner.sandbox_operation_requests
ADD CONSTRAINT sandbox_operation_status_check
CHECK (status IN ('pending', 'completed', 'failed', 'legacy'));


-- Create an index to find unfinished operations quickly.

CREATE INDEX IF NOT EXISTS idx_sandbox_operation_requests_pending
ON runner.sandbox_operation_requests (sandbox_id, created_at)
WHERE status = 'pending';