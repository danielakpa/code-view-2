"""PostgreSQL integration tests for persistent IDE recovery."""

import os
import threading
import unittest

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from server_runner.sandbox_controller.recovery import (
    SandboxRecoveryService,
)


class TestRecoveryIntegration(unittest.TestCase):
    """Test recovery using real PostgreSQL and mocked Docker."""

    @classmethod
    def setUpClass(cls):
        """Connect only to the dedicated test database."""

        database_url = os.environ.get(
            "CODEVIEW_RUNNER_DATABASE_URL"
        )

        if not database_url:
            raise RuntimeError(
                "CODEVIEW_RUNNER_DATABASE_URL is required."
            )

        cls.engine = create_engine(
            database_url,
            pool_pre_ping=True,
        )

        # Protect the main database from integration tests.
        with cls.engine.connect() as connection:
            database_name = connection.execute(
                text("SELECT current_database()")
            ).scalar_one()

            if database_name != "codeview_test_db":
                cls.engine.dispose()

                raise RuntimeError(
                    "Integration tests must use codeview_test_db."
                )

    @classmethod
    def tearDownClass(cls):
        """Close database connections."""

        cls.engine.dispose()

    def setUp(self):
        """Create isolated records for each test."""

        self.sandbox_id = uuid4()
        self.request_id = uuid4()

        self.developer_id = 900001
        self.group_id = 900001

        self.container_id = "a" * 64

        self.db = Session(self.engine)

        self.ide_service = MagicMock()

        # Simulate successful Docker inspection.
        self.ide_service.executor.execute.return_value = MagicMock(
            return_code=0,
            timed_out=False,
            stdout=f"{self.container_id}|running",
            stderr="",
        )

    def tearDown(self):
        """Remove only records created by the current test."""

        self.db.rollback()

        try:
            with self.engine.begin() as connection:

                connection.execute(
                    text(
                        """
                        DELETE FROM runner.sandbox_operation_requests
                        WHERE request_id = :request_id
                        """
                    ),
                    {"request_id": self.request_id},
                )

                connection.execute(
                    text(
                        """
                        DELETE FROM runner.sandboxes
                        WHERE sandbox_id = :sandbox_id
                        """
                    ),
                    {"sandbox_id": self.sandbox_id},
                )

        finally:
            self.db.close()

    # ========================================================
    # 1. CREATE TEST RECORDS
    # ========================================================

    def create_records(
        self,
        operation,
        sandbox_status,
        container_id=None,
        lease_owner=None,
        lease_expires_at=None,
        lease_generation=0,
    ):
        """Insert a sandbox and an interrupted operation."""

        old_time = datetime.now(timezone.utc) - timedelta(
            minutes=10
        )

        self.db.execute(
            text(
                """
                INSERT INTO runner.sandboxes (
                    sandbox_id,
                    developer_id,
                    group_id,
                    status,
                    container_id,
                    created_at,
                    updated_at
                )
                VALUES (
                    :sandbox_id,
                    :developer_id,
                    :group_id,
                    :status,
                    :container_id,
                    NOW(),
                    NOW()
                )
                """
            ),
            {
                "sandbox_id": self.sandbox_id,
                "developer_id": self.developer_id,
                "group_id": self.group_id,
                "status": sandbox_status,
                "container_id": container_id,
            },
        )

        self.db.execute(
            text(
                """
                INSERT INTO runner.sandbox_operation_requests (
                    request_id,
                    sandbox_id,
                    developer_id,
                    group_id,
                    operation,
                    status,
                    created_at,
                    lease_owner,
                    lease_expires_at,
                    lease_generation
                )
                VALUES (
                    :request_id,
                    :sandbox_id,
                    :developer_id,
                    :group_id,
                    :operation,
                    'pending',
                    :created_at,
                    :lease_owner,
                    :lease_expires_at,
                    :lease_generation
                )
                """
            ),
            {
                "request_id": self.request_id,
                "sandbox_id": self.sandbox_id,
                "developer_id": self.developer_id,
                "group_id": self.group_id,
                "operation": operation,
                "created_at": old_time,
                "lease_owner": lease_owner,
                "lease_expires_at": lease_expires_at,
                "lease_generation": lease_generation,
            },
        )

        self.db.commit()

    # ========================================================
    # 2. READ DATABASE RECORDS
    # ========================================================

    def read_records(self):
        """Read saved sandbox and operation states."""

        sandbox = self.db.execute(
            text(
                """
                SELECT status, container_id
                FROM runner.sandboxes
                WHERE sandbox_id = :sandbox_id
                """
            ),
            {"sandbox_id": self.sandbox_id},
        ).mappings().one()

        operation = self.db.execute(
            text(
                """
                SELECT
                    status,
                    completed_at,
                    lease_owner,
                    lease_generation,
                    lease_expires_at
                FROM runner.sandbox_operation_requests
                WHERE request_id = :request_id
                """
            ),
            {"request_id": self.request_id},
        ).mappings().one()

        return sandbox, operation

    # ========================================================
    # 3. EXISTING RECOVERY TESTS
    # ========================================================

    def test_start_recovery_updates_postgresql(self):
        """A confirmed running container updates both records."""

        self.create_records(
            operation="start",
            sandbox_status="starting",
            container_id=self.container_id,
        )

        recovery = SandboxRecoveryService(
            self.db,
            self.ide_service,
        )

        result = recovery.recover_operation(
            self.request_id
        )

        sandbox, operation = self.read_records()

        self.assertEqual(result.status, "completed")
        self.assertEqual(sandbox["status"], "running")
        self.assertEqual(operation["status"], "completed")
        self.assertIsNotNone(operation["completed_at"])

    def test_stop_recovery_updates_postgresql(self):
        """A confirmed stopped container updates PostgreSQL."""

        self.create_records(
            operation="stop",
            sandbox_status="running",
            container_id=self.container_id,
        )

        self.ide_service.executor.execute.return_value.stdout = (
            f"{self.container_id}|exited"
        )

        recovery = SandboxRecoveryService(
            self.db,
            self.ide_service,
        )

        result = recovery.recover_operation(
            self.request_id
        )

        sandbox, operation = self.read_records()

        self.assertEqual(result.status, "completed")
        self.assertEqual(sandbox["status"], "stopped")
        self.assertEqual(operation["status"], "completed")

    def test_create_recovery_saves_missing_container_id(self):
        """Recover a Docker ID not saved before a crash."""

        self.create_records(
            operation="create",
            sandbox_status="prepared",
            container_id=None,
        )

        self.ide_service.executor.execute.return_value.stdout = (
            f"{self.container_id}|created"
        )

        recovery = SandboxRecoveryService(
            self.db,
            self.ide_service,
        )

        result = recovery.recover_operation(
            self.request_id
        )

        sandbox, operation = self.read_records()

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            sandbox["container_id"],
            self.container_id,
        )
        self.assertEqual(operation["status"], "completed")

    def test_docker_failure_keeps_operation_pending(self):
        """Docker failures must not produce false success."""

        self.create_records(
            operation="start",
            sandbox_status="starting",
            container_id=self.container_id,
        )

        self.ide_service.executor.execute.return_value = MagicMock(
            return_code=1,
            timed_out=False,
            stdout="",
            stderr="Docker unavailable",
        )

        recovery = SandboxRecoveryService(
            self.db,
            self.ide_service,
        )

        result = recovery.recover_operation(
            self.request_id
        )

        sandbox, operation = self.read_records()

        self.assertEqual(result.status, "unresolved")
        self.assertEqual(sandbox["status"], "starting")
        self.assertEqual(operation["status"], "pending")

    def test_run_operation_is_excluded(self):
        """Temporary code execution must not enter IDE recovery."""

        self.create_records(
            operation="run",
            sandbox_status="running",
            container_id=self.container_id,
        )

        recovery = SandboxRecoveryService(
            self.db,
            self.ide_service,
        )

        pending = recovery.find_pending_operations()

        matching = [
            item
            for item in pending
            if item["request_id"] == self.request_id
        ]

        self.assertEqual(matching, [])

        self.ide_service.executor.execute.assert_not_called()

    # ========================================================
    # 4. NEW LEASE TESTS
    # ========================================================

    def test_active_lease_is_not_recovered(self):
        """Recovery must skip operations with active leases."""

        active_owner = uuid4()

        active_expiry = datetime.now(timezone.utc) + timedelta(
            minutes=5
        )

        self.create_records(
            operation="start",
            sandbox_status="starting",
            container_id=self.container_id,
            lease_owner=active_owner,
            lease_expires_at=active_expiry,
            lease_generation=1,
        )

        recovery = SandboxRecoveryService(
            self.db,
            self.ide_service,
        )

        result = recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "skipped")

        self.ide_service.executor.execute.assert_not_called()

        _, operation = self.read_records()

        self.assertEqual(
            operation["lease_owner"],
            active_owner,
        )

        self.assertEqual(
            operation["lease_generation"],
            1,
        )

    def test_expired_lease_can_be_recovered(self):
        """Recovery may claim an expired operation lease."""

        expired_owner = uuid4()

        expired_time = datetime.now(timezone.utc) - timedelta(
            minutes=5
        )

        self.create_records(
            operation="start",
            sandbox_status="starting",
            container_id=self.container_id,
            lease_owner=expired_owner,
            lease_expires_at=expired_time,
            lease_generation=1,
        )

        recovery = SandboxRecoveryService(
            self.db,
            self.ide_service,
        )

        result = recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "completed")

        _, operation = self.read_records()

        self.assertNotEqual(
            operation["lease_owner"],
            expired_owner,
        )

        self.assertEqual(
            operation["lease_generation"],
            2,
        )

    def test_recovery_increases_lease_generation(self):
        """Recovery must increment the lease generation."""

        expired_owner = uuid4()

        expired_time = datetime.now(timezone.utc) - timedelta(
            minutes=5
        )

        self.create_records(
            operation="start",
            sandbox_status="starting",
            container_id=self.container_id,
            lease_owner=expired_owner,
            lease_expires_at=expired_time,
            lease_generation=7,
        )

        recovery = SandboxRecoveryService(
            self.db,
            self.ide_service,
        )

        result = recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "completed")

        _, operation = self.read_records()

        self.assertEqual(
            operation["lease_generation"],
            8,
        )

    # ========================================================
    # 5. CONCURRENT RECOVERY CLAIMS
    # ========================================================

    def test_two_workers_cannot_claim_same_operation(self):
        """Only one worker may claim an expired IDE operation."""

        expired_owner = uuid4()

        expired_time = datetime.now(timezone.utc) - timedelta(
            minutes=5
        )

        self.create_records(
            operation="start",
            sandbox_status="starting",
            container_id=self.container_id,
            lease_owner=expired_owner,
            lease_expires_at=expired_time,
            lease_generation=1,
        )

        barrier = threading.Barrier(2)

        def claim_from_worker():
            """Use a separate database session for each worker."""

            with Session(self.engine) as worker_db:

                worker_recovery = SandboxRecoveryService(
                    worker_db,
                    MagicMock(),
                )

                # Make both workers attempt the claim together.
                barrier.wait(timeout=10)

                return worker_recovery._claim_recovery_lease(
                    request_id=self.request_id,
                    older_than_minutes=5,
                )

        with ThreadPoolExecutor(max_workers=2) as executor:

            futures = [
                executor.submit(claim_from_worker)
                for _ in range(2)
            ]

            results = [
                future.result(timeout=15)
                for future in futures
            ]

        successful_claims = [
            result
            for result in results
            if result is not None
        ]

        self.assertEqual(
            len(successful_claims),
            1,
        )

        _, operation = self.read_records()

        self.assertEqual(
            operation["lease_generation"],
            2,
        )

    # ========================================================
    # 6. STALE WORKER FENCING
    # ========================================================

    def test_old_worker_cannot_finalize_after_recovery_claim(self):
        """An old worker must not finalize after losing its lease."""

        old_owner = uuid4()

        expired_time = datetime.now(timezone.utc) - timedelta(
            minutes=5
        )

        self.create_records(
            operation="start",
            sandbox_status="starting",
            container_id=self.container_id,
            lease_owner=old_owner,
            lease_expires_at=expired_time,
            lease_generation=1,
        )

        recovery = SandboxRecoveryService(
            self.db,
            self.ide_service,
        )

        claimed = recovery._claim_recovery_lease(
            request_id=self.request_id,
            older_than_minutes=5,
        )

        self.assertIsNotNone(claimed)

        # Simulate the original worker attempting to save
        # its result with the previous lease credentials.
        with Session(self.engine) as stale_db:

            result = stale_db.execute(
                text(
                    """
                    UPDATE runner.sandbox_operation_requests
                    SET
                        status = 'completed',
                        completed_at = NOW()
                    WHERE request_id = :request_id
                      AND status = 'pending'
                      AND lease_owner = :lease_owner
                      AND lease_generation = :lease_generation
                      AND lease_expires_at > NOW()
                    """
                ),
                {
                    "request_id": self.request_id,
                    "lease_owner": old_owner,
                    "lease_generation": 1,
                },
            )

            self.assertEqual(result.rowcount, 0)

            stale_db.rollback()

        _, operation = self.read_records()

        self.assertEqual(
            operation["status"],
            "pending",
        )

        self.assertEqual(
            operation["lease_generation"],
            2,
        )


if __name__ == "__main__":
    unittest.main()