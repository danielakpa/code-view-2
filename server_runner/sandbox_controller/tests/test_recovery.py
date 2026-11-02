"""Tests for recovery of interrupted Docker IDE operations."""

import unittest

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

from server_runner.sandbox_controller.recovery import (
    SandboxRecoveryService,
)


class TestSandboxRecoveryService(unittest.TestCase):
    """Test recovery without executing real Docker commands."""

    def setUp(self):
        """Prepare a mocked database, sandbox, and Docker service."""

        # Create a unique sandbox and operation ID.
        self.sandbox_id = uuid4()
        self.request_id = uuid4()

        # Simulate an existing Docker container.
        self.container_id = "a" * 64

        # Mock the PostgreSQL session.
        self.db = MagicMock()

        # Mock the Docker IDE service.
        self.ide_service = MagicMock()

        # Initialize the existing recovery service.
        self.recovery = SandboxRecoveryService(
            db=self.db,
            ide_service=self.ide_service,
        )

        # Simulate the sandbox record.
        self.sandbox = MagicMock()
        self.sandbox.sandbox_id = self.sandbox_id
        self.sandbox.container_id = self.container_id
        self.sandbox.status = "starting"

        # The first query returns the pending operation.
        self.operation = {
            "request_id": self.request_id,
            "sandbox_id": self.sandbox_id,
            "operation": "start",
            "status": "pending",
        }

        # Configure the two database queries:
        # 1. Find the pending operation.
        # 2. Find the sandbox record.
        self.db.execute.side_effect = [
            MagicMock(
                mappings=lambda: MagicMock(
                    one_or_none=lambda: self.operation
                )
            ),
            MagicMock(
                scalar_one_or_none=lambda: self.sandbox
            ),
            MagicMock(),  # UPDATE operation status.
        ]

        # Simulate Docker returning a running container.
        self.ide_service.executor.execute.return_value = (
            MagicMock(
                return_code=0,
                timed_out=False,
                stdout=f"{self.container_id}|running",
                stderr="",
            )
        )

    def test_start_recovery(self):
        """Recover an IDE that Docker successfully started."""

        result = self.recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.docker_status, "running")
        self.assertEqual(self.sandbox.status, "running")

        self.db.commit.assert_called_once()

    def test_stop_recovery(self):
        """Recover an IDE that Docker successfully stopped."""

        self.operation["operation"] = "stop"
        self.sandbox.status = "running"

        self.ide_service.executor.execute.return_value.stdout = (
            f"{self.container_id}|exited"
        )

        result = self.recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(self.sandbox.status, "stopped")

        self.db.commit.assert_called_once()

    def test_create_recovery_saves_container_id(self):
        """Recover a container ID missing from PostgreSQL."""

        self.operation["operation"] = "create"
        self.sandbox.container_id = None
        self.sandbox.status = "prepared"

        self.ide_service.executor.execute.return_value.stdout = (
            f"{self.container_id}|created"
        )

        result = self.recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            self.sandbox.container_id,
            self.container_id,
        )

        self.db.commit.assert_called_once()

    def test_inspect_recovery_does_not_change_lifecycle(self):
        """Inspection must not change the sandbox lifecycle."""

        self.operation["operation"] = "inspect"
        self.sandbox.status = "running"

        result = self.recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(self.sandbox.status, "running")

        self.db.commit.assert_called_once()

    def test_docker_failure_keeps_operation_pending(self):
        """Do not claim recovery succeeded if Docker is unavailable."""

        self.ide_service.executor.execute.return_value = (
            MagicMock(
                return_code=1,
                timed_out=False,
                stdout="",
                stderr="Docker unavailable",
            )
        )

        result = self.recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "unresolved")
        self.assertEqual(self.sandbox.status, "starting")

        self.db.commit.assert_not_called()
        self.db.rollback.assert_called_once()

    def test_container_identity_mismatch(self):
        """Reject a container that differs from the saved ID."""

        different_id = "b" * 64

        self.ide_service.executor.execute.return_value.stdout = (
            f"{different_id}|running"
        )

        result = self.recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "unresolved")
        self.assertEqual(self.sandbox.status, "starting")

        self.db.commit.assert_not_called()

    def test_operation_not_eligible(self):
        """Skip operations that are not pending or are locked."""

        self.db.execute.side_effect = [
            MagicMock(
                mappings=lambda: MagicMock(
                    one_or_none=lambda: None
                )
            )
        ]

        result = self.recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "skipped")

        # Docker must not be contacted.
        self.ide_service.executor.execute.assert_not_called()

    def test_invalid_request_id(self):
        """Reject invalid recovery request identifiers."""

        with self.assertRaises(ValueError):
            self.recovery.recover_operation(
                "not-a-uuid"
            )

        self.db.execute.assert_not_called()

    def test_invalid_recovery_age(self):
        """Reject invalid recovery cutoff values."""

        for invalid_age in (0, -1, True, "five"):
            with self.subTest(age=invalid_age):
                with self.assertRaises(ValueError):
                    self.recovery.recover_operation(
                        self.request_id,
                        older_than_minutes=invalid_age,
                    )

    def test_unconfirmed_start_does_not_report_success(self):
        """A stopped Docker container does not prove startup succeeded."""

        self.ide_service.executor.execute.return_value.stdout = (
            f"{self.container_id}|exited"
        )

        result = self.recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "unresolved")
        self.assertNotEqual(self.sandbox.status, "running")

        self.db.commit.assert_not_called()

    def test_missing_sandbox_is_unresolved(self):
        """Do not recover an operation without its sandbox record."""

        self.db.execute.side_effect = [
            MagicMock(
                mappings=lambda: MagicMock(
                    one_or_none=lambda: self.operation
                )
            ),
            MagicMock(
                scalar_one_or_none=lambda: None
            ),
        ]

        result = self.recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "unresolved")

        # Docker must not run without a trusted sandbox.
        self.ide_service.executor.execute.assert_not_called()

        self.db.commit.assert_not_called()

    def test_recovery_uses_trusted_container_name(self):
        """Docker inspection must use CodeView's sandbox name."""

        self.recovery.recover_operation(
            self.request_id
        )

        expected_name = f"codeview-{self.sandbox_id.hex}"

        command = (
            self.ide_service.executor.execute.call_args.args[0]
        )

        self.assertEqual(command[-1], expected_name)

    def test_find_pending_operations_rejects_invalid_age(self):
        """Validate recovery age before querying PostgreSQL."""

        with self.assertRaises(ValueError):
            self.recovery.find_pending_operations(
                older_than_minutes=0
            )

        self.db.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()