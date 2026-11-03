"""Tests for lease-aware recovery of interrupted Docker IDE operations."""

import unittest

from unittest.mock import MagicMock, patch
from uuid import uuid4

from server_runner.sandbox_controller.recovery import (
    SandboxRecoveryService,
)


class TestSandboxRecoveryService(unittest.TestCase):
    """Test IDE recovery without running real Docker commands."""

    def setUp(self):
        """Prepare a mocked database, sandbox, and Docker service."""

        self.sandbox_id = uuid4()
        self.request_id = uuid4()
        self.lease_owner = uuid4()
        self.lease_generation = 2

        self.container_id = "a" * 64

        # Mock PostgreSQL.
        self.db = MagicMock()

        # Mock Docker.
        self.ide_service = MagicMock()

        # Create the recovery service.
        self.recovery = SandboxRecoveryService(
            db=self.db,
            ide_service=self.ide_service,
        )

        # Simulate an existing sandbox.
        self.sandbox = MagicMock()
        self.sandbox.sandbox_id = self.sandbox_id
        self.sandbox.container_id = self.container_id
        self.sandbox.status = "starting"

        # Simulate a successfully claimed recovery lease.
        self.operation = {
            "request_id": self.request_id,
            "sandbox_id": self.sandbox_id,
            "operation": "start",
            "lease_owner": self.lease_owner,
            "lease_generation": self.lease_generation,
        }

        # Simulate Docker reporting a running container.
        self.ide_service.executor.execute.return_value = MagicMock(
            return_code=0,
            timed_out=False,
            stdout=f"{self.container_id}|running",
            stderr="",
        )

        # Mock recovery lease claiming.
        self.claim_patcher = patch.object(
            self.recovery,
            "_claim_recovery_lease",
            return_value=self.operation,
        )

        self.mock_claim = self.claim_patcher.start()
        self.addCleanup(self.claim_patcher.stop)

        # Mock the heartbeat to avoid real background threads.
        self.heartbeat_patcher = patch(
            "server_runner.sandbox_controller.recovery.IDELeaseHeartbeat"
        )

        self.mock_heartbeat_class = self.heartbeat_patcher.start()
        self.addCleanup(self.heartbeat_patcher.stop)

        self.mock_heartbeat = self.mock_heartbeat_class.return_value

        # Simulate a healthy heartbeat.
        self.mock_heartbeat.lost_lease = False

        # Simulate successful lease renewal during handoff.
        self.mock_heartbeat.stop_and_renew.return_value = True

        # Configure database responses for finalization:
        #
        # 1. Lock sandbox.
        # 2. Verify recovery ownership.
        # 3. Update operation status.
        self.db.execute.side_effect = [
            MagicMock(
                scalar_one_or_none=lambda: self.sandbox
            ),
            MagicMock(
                one_or_none=lambda: (self.request_id,)
            ),
            MagicMock(rowcount=1),
        ]

    # ========================================================
    # EXISTING RECOVERY TESTS
    # ========================================================

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
        """Recover a Docker container ID missing from PostgreSQL."""

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
        """INSPECT must not change the sandbox lifecycle."""

        self.operation["operation"] = "inspect"
        self.sandbox.status = "running"

        result = self.recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(self.sandbox.status, "running")

        self.db.commit.assert_called_once()

    def test_docker_failure_keeps_operation_pending(self):
        """Docker failures must not produce false recovery success."""

        self.ide_service.executor.execute.return_value = MagicMock(
            return_code=1,
            timed_out=False,
            stdout="",
            stderr="Docker unavailable",
        )

        result = self.recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "unresolved")
        self.assertEqual(self.sandbox.status, "starting")

        self.db.commit.assert_not_called()

    def test_container_identity_mismatch(self):
        """Reject Docker containers with unexpected IDs."""

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
        """Skip operations that cannot claim recovery ownership."""

        self.mock_claim.return_value = None

        result = self.recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "skipped")

        self.ide_service.executor.execute.assert_not_called()
        self.mock_heartbeat_class.assert_not_called()

    def test_invalid_request_id(self):
        """Reject invalid request IDs."""

        with self.assertRaises(ValueError):
            self.recovery.recover_operation(
                "not-a-uuid"
            )

        self.mock_claim.assert_not_called()

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
        """A stopped container does not prove startup succeeded."""

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
        """Do not finalize recovery without a sandbox record."""

        self.db.execute.side_effect = [
            MagicMock(
                scalar_one_or_none=lambda: None
            )
        ]

        result = self.recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "unresolved")

        self.db.commit.assert_not_called()

    def test_recovery_uses_trusted_container_name(self):
        """Docker inspection must use the trusted sandbox UUID."""

        self.recovery.recover_operation(
            self.request_id
        )

        expected_name = f"codeview-{self.sandbox_id.hex}"

        command = (
            self.ide_service.executor.execute.call_args.args[0]
        )

        self.assertEqual(command[-1], expected_name)

    def test_find_pending_operations_rejects_invalid_age(self):
        """Reject invalid recovery age values."""

        with self.assertRaises(ValueError):
            self.recovery.find_pending_operations(
                older_than_minutes=0
            )

        self.db.execute.assert_not_called()


    def test_find_pending_operations_uses_batch_limit(self):
        """Recovery must retrieve at most 50 operations per cycle."""

        # Simulate PostgreSQL returning no pending operations.
        self.db.execute.return_value.mappings.return_value.all.return_value = []

        # Find pending operations.
        results = self.recovery.find_pending_operations()

        # Confirm the result is empty.
        self.assertEqual(results, [])

        # Get the SQL query and its parameters.
        statement, parameters = self.db.execute.call_args.args

        # Verify that SQL limits the number of operations.
        self.assertIn("LIMIT :batch_size", str(statement))

        # Verify that the limit is 50.
        self.assertEqual(parameters["batch_size"], 50)

        
    def test_lost_recovery_lease_cannot_finalize(self):
        """A recovery worker that loses ownership must not save."""

        self.db.execute.side_effect = [
            MagicMock(
                scalar_one_or_none=lambda: self.sandbox
            ),
            MagicMock(
                one_or_none=lambda: None
            ),
        ]

        result = self.recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "skipped")

        self.db.commit.assert_not_called()

    def test_failed_fenced_update_rolls_back(self):
        """Reject finalization if the recovery lease expires."""

        self.db.execute.side_effect = [
            MagicMock(
                scalar_one_or_none=lambda: self.sandbox
            ),
            MagicMock(
                one_or_none=lambda: (self.request_id,)
            ),
            MagicMock(rowcount=0),
        ]

        with self.assertRaises(RuntimeError):
            self.recovery.recover_operation(
                self.request_id
            )

        self.db.rollback.assert_called()

    # ========================================================
    # NEW HEARTBEAT TESTS
    # ========================================================

    def test_heartbeat_starts_after_claim(self):
        """Start heartbeat only after recovery claims ownership."""

        result = self.recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "completed")

        self.mock_heartbeat_class.assert_called_once_with(
            request_id=self.request_id,
            lease_owner=self.lease_owner,
            lease_generation=self.lease_generation,
        )

        self.mock_heartbeat.start.assert_called_once()

    def test_heartbeat_stops_after_inspection(self):
        """Heartbeat must stop after Docker inspection."""

        self.recovery.recover_operation(
            self.request_id
        )

        self.mock_heartbeat.stop_and_renew.assert_called_once()

    def test_heartbeat_failure_prevents_finalization(self):
        """A lost heartbeat must prevent database finalization."""

        self.mock_heartbeat.lost_lease = True

        result = self.recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "skipped")

        self.db.commit.assert_not_called()

        # No final database queries should execute.
        self.db.execute.assert_not_called()

    def test_docker_exception_stops_heartbeat(self):
        """Docker exceptions must not leave the heartbeat running."""

        self.ide_service.executor.execute.side_effect = (
            RuntimeError("Docker inspection crashed")
        )

        result = self.recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "unresolved")
        self.mock_heartbeat.stop_and_renew.assert_called_once()

        self.db.commit.assert_not_called()

    def test_heartbeat_start_failure_is_unresolved(self):
        """A heartbeat that cannot start must not finalize recovery."""

        # Simulate a heartbeat startup failure.
        self.mock_heartbeat.start.side_effect = RuntimeError(
            "Heartbeat thread failed"
        )

        result = self.recovery.recover_operation(
            self.request_id
        )

        # Recovery must not report success.
        self.assertEqual(result.status, "unresolved")

        # Verify that heartbeat startup was attempted.
        self.mock_heartbeat.start.assert_called_once()

        # Cleanup should run after startup failure.
        self.mock_heartbeat.stop.assert_called_once()

        # Final lease handoff must not happen.
        self.mock_heartbeat.stop_and_renew.assert_not_called()

        # No Docker inspection should occur.
        self.ide_service.executor.execute.assert_not_called()

        # No operation should be finalized.
        self.db.commit.assert_not_called()

    def test_heartbeat_handoff_before_database_finalization(self):
        """Renew the lease before committing recovery."""

        events = []

        # Record when the heartbeat handoff happens.
        self.mock_heartbeat.stop_and_renew.side_effect = (
            lambda: events.append("lease_handoff") or True
        )

        # Record when PostgreSQL commits.
        self.db.commit.side_effect = (
            lambda: events.append("database_committed")
        )

        result = self.recovery.recover_operation(
            self.request_id
        )

        self.assertEqual(result.status, "completed")

        self.assertEqual(
            events,
            [
                "lease_handoff",
                "database_committed",
            ],
        )
    def test_failed_lease_handoff_prevents_finalization(self):
        """Never finalize recovery when lease renewal fails."""

        # Simulate a failed lease handoff.
        self.mock_heartbeat.stop_and_renew.return_value = False

        result = self.recovery.recover_operation(
            self.request_id
        )

        # Recovery must not report success.
        self.assertEqual(result.status, "skipped")

        # No completed operation should be saved.
        self.db.commit.assert_not_called()

        # Finalization queries must not execute.
        self.db.execute.assert_not_called()

        # Verify the handoff was attempted.
        self.mock_heartbeat.stop_and_renew.assert_called_once()


if __name__ == "__main__":
    unittest.main()