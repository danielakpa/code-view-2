-- Migration 003
-- Preserve temporary code execution alongside persistent IDE operations.

BEGIN;

-- Remove the previous operation constraint.
ALTER TABLE runner.sandbox_operation_requests
DROP CONSTRAINT IF EXISTS sandbox_operation_requests_operation_check;

-- Allow all five supported operations.
ALTER TABLE runner.sandbox_operation_requests
ADD CONSTRAINT sandbox_operation_requests_operation_check
CHECK (
    operation IN (
        'run',
        'create',
        'start',
        'stop',
        'inspect'
    )
);

COMMIT;