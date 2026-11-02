"""Tests for transactional sandbox operation handling."""

import unittest

from unittest.mock import MagicMock

from uuid import uuid4
from datetime import datetime, timedelta, timezone

from server_runner.sandbox_controller.operation import (
    SandboxOperation,
    SandboxOperationRequest,
)

from server_runner.sandbox_controller.operation_handler import (
    SandboxOperationHandler,
)


class TestSandboxOperationHandler(unittest.TestCase):

    def setUp(self):
        """Prepare mocked PostgreSQL records."""

        self.db = MagicMock()

        self.sandbox_id = uuid4()

        self.sandbox = MagicMock()
        self.sandbox.sandbox_id = self.sandbox_id
        self.sandbox.developer_id = 12
        self.sandbox.group_id = 5
        self.sandbox.status = "prepared"

        self.db.execute.return_value.scalar_one_or_none.return_value = (
            self.sandbox
        )

        # Create the operation handler using the mocked database.
        self.handler = SandboxOperationHandler(self.db)

        # Simulate successful PostgreSQL replay protection.
        self.handler.replay_store.claim_request = MagicMock(
            return_value=True
        )

        # Give the mocked worker a valid lease owner.
        self.lease_owner = uuid4()

        # Simulate PostgreSQL returning an active IDE lease.
        self.handler.replay_store.get_lease = MagicMock(
            return_value={
                "lease_owner": self.lease_owner,
                "lease_expires_at": (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=120)
                ),
                "lease_generation": 1,
                "status": "pending",
            }
        )

        # Simulate PostgreSQL confirming lease ownership.
        self.handler.replay_store.owns_lease = MagicMock(
            return_value=True
        )

                # Simulate PostgreSQL successfully updating one operation.
        #
        # Our lease-fencing method checks result.rowcount.
        # A value of 1 means the worker still owns the lease
        # and PostgreSQL accepted the update.
        self.db.execute.return_value.rowcount = 1

    def make_request(self, operation):
        """Build a valid sandbox operation request."""

        return SandboxOperationRequest(
            request_id=uuid4(),
            sandbox_id=self.sandbox_id,
            developer_id=12,
            group_id=5,
            operation=operation,
        )

    def test_start_updates_status(self):
        """Record startup and change the sandbox to starting."""

        result = self.handler.handle(
            self.make_request(SandboxOperation.START)
        )

        self.assertEqual(result["sandbox_status"], "starting")
        self.assertEqual(self.sandbox.status, "starting")
        self.db.commit.assert_called_once()

    def test_inspect_does_not_change_status(self):
        """Inspect should not change lifecycle status."""

        result = self.handler.handle(
            self.make_request(SandboxOperation.INSPECT)
        )

        self.assertEqual(result["sandbox_status"], "prepared")
        self.db.commit.assert_called_once()

    def test_invalid_transition_rolls_back(self):
        """Reject invalid lifecycle changes."""

        self.sandbox.status = "running"

        with self.assertRaises(ValueError):
            self.handler.handle(
                self.make_request(SandboxOperation.START)
            )

        self.db.commit.assert_not_called()
        self.db.rollback.assert_called_once()

    def test_missing_sandbox_rejected(self):
        """Reject unknown sandbox records."""

        self.db.execute.return_value.scalar_one_or_none.return_value = None

        with self.assertRaises(PermissionError):
            self.handler.handle(
                self.make_request(SandboxOperation.START)
            )

        self.db.commit.assert_not_called()
        self.db.rollback.assert_called_once()

    def test_replay_failure_rolls_back(self):
        """Roll back lifecycle changes when replay insertion fails."""

        self.handler.operation_service.replay_store.claim_request.side_effect = (
            RuntimeError("Replay database failure")
        )

        with self.assertRaises(RuntimeError):
            self.handler.handle(
                self.make_request(SandboxOperation.START)
            )

        self.db.commit.assert_not_called()
        self.db.rollback.assert_called_once()

    def test_stopped_sandbox_can_restart(self):
        """Allow a stopped sandbox to start again."""

        self.sandbox.status = "stopped"

        result = self.handler.handle(
            self.make_request(SandboxOperation.START)
        )

        self.assertEqual(result["sandbox_status"], "starting")


    def test_run_from_prepared_sandbox(self):
        """Allow temporary execution without starting the main sandbox."""

        # Create a Run operation request.
        request = self.make_request(SandboxOperation.RUN)

        # Record the operation.
        result = self.handler.handle(request)

        # Verify that the request was recorded.
        self.assertEqual(result["operation"], "run")
        self.assertEqual(result["status"], "recorded")

        # Temporary execution must not change the main sandbox status.
        self.assertEqual(self.sandbox.status, "prepared")

        # Verify that the transaction was committed.
        self.db.commit.assert_called_once()


    def test_run_from_failed_sandbox_is_rejected(self):
        """Reject temporary execution when the sandbox has failed."""

        # Simulate a failed sandbox.
        self.sandbox.status = "failed"

        # Attempt to record a Run operation.
        with self.assertRaises(ValueError):
            self.handler.handle(
                self.make_request(SandboxOperation.RUN)
            )

        # Failed operations must not be committed.
        self.db.commit.assert_not_called()

        # The database transaction must be rolled back.
        self.db.rollback.assert_called_once()

    def test_run_replay_failure_rolls_back(self):
        """Reject Run requests when replay protection fails."""

        # Simulate a duplicate or rejected request ID.
        self.handler.operation_service.replay_store.claim_request.side_effect = (
            RuntimeError("Duplicate Run request")
        )

        # Attempt to record the Run operation.
        with self.assertRaises(RuntimeError):
            self.handler.handle(
                self.make_request(SandboxOperation.RUN)
            )

        # Do not commit rejected requests.
        self.db.commit.assert_not_called()

        # Undo the transaction.
        self.db.rollback.assert_called_once()

    # ========================================================
    # IDE OPERATIONS — CODEVIEW 2.0
    # ========================================================

    def test_ide_create_saves_container_id(self):
        """Creating an IDE must save its Docker container ID."""

        from unittest.mock import patch
        from server_runner.sandbox_controller.developer_ide import (
            DeveloperIDEResult,
        )

        # Simulate a sandbox without a Docker container.
        self.sandbox.container_id = None

        # Simulate Docker returning a valid container ID.
        container_id = "a" * 64

        with patch(
            "server_runner.sandbox_controller.operation_handler.DeveloperIDEService"
        ) as mock_service:

            mock_service.return_value.create_ide.return_value = (
                DeveloperIDEResult(
                    success=True,
                    output=container_id,
                    error="",
                )
            )
            # Simulate Docker confirming that the container exists.
            mock_service.return_value.inspect_ide.return_value = (
                DeveloperIDEResult(
                    success=True,
                    output="created",
                    error="",
                )
            )

            # Execute the IDE creation operation.
            result = self.handler.handle_ide_operation(
                self.make_request(SandboxOperation.CREATE)
            )

        # The database must contain the created container ID.
        self.assertEqual(
            self.sandbox.container_id,
            container_id,
        )

        self.assertEqual(result["status"], "completed")

    def test_ide_start_uses_saved_container_id(self):
        """Start the container assigned to the developer."""

        from unittest.mock import patch
        from server_runner.sandbox_controller.developer_ide import (
            DeveloperIDEResult,
        )

        container_id = "b" * 64

        # The container has already been created.
        self.sandbox.container_id = container_id

        with patch(
            "server_runner.sandbox_controller.operation_handler.DeveloperIDEService"
        ) as mock_service:

            # Simulate successful Docker startup.
            mock_service.return_value.start_ide.return_value = (
                DeveloperIDEResult(True, container_id, "")
            )

            # Docker confirms the container is running.
            mock_service.return_value.inspect_ide.return_value = (
                DeveloperIDEResult(True, "running", "")
            )

            result = self.handler.handle_ide_operation(
                self.make_request(SandboxOperation.START)
            )

            # Verify that Docker received the stored ID.
            mock_service.return_value.start_ide.assert_called_once_with(
                container_id
            )

        self.assertEqual(
            self.sandbox.status,
            "running",
        )

        self.assertEqual(result["status"], "completed")

    def test_ide_stop_confirms_docker_stopped(self):
        """Stop an IDE only after confirming Docker's state."""

        from unittest.mock import patch
        from server_runner.sandbox_controller.developer_ide import (
            DeveloperIDEResult,
        )

        container_id = "c" * 64

        self.sandbox.container_id = container_id
        self.sandbox.status = "running"

        with patch(
            "server_runner.sandbox_controller.operation_handler.DeveloperIDEService"
        ) as mock_service:

            mock_service.return_value.stop_ide.return_value = (
                DeveloperIDEResult(True, container_id, "")
            )

            mock_service.return_value.inspect_ide.return_value = (
                DeveloperIDEResult(True, "exited", "")
            )

            result = self.handler.handle_ide_operation(
                self.make_request(SandboxOperation.STOP)
            )

        self.assertEqual(self.sandbox.status, "stopped")
        self.assertEqual(result["status"], "completed")

    def test_ide_start_failure_does_not_report_running(self):
        """A failed Docker startup must not be reported as running."""

        from unittest.mock import patch
        from server_runner.sandbox_controller.developer_ide import (
            DeveloperIDEResult,
        )

        self.sandbox.container_id = "d" * 64

        with patch(
            "server_runner.sandbox_controller.operation_handler.DeveloperIDEService"
        ) as mock_service:

            mock_service.return_value.start_ide.return_value = (
                DeveloperIDEResult(
                    success=False,
                    output="",
                    error="Docker startup failed",
                )
            )

            result = self.handler.handle_ide_operation(
                self.make_request(SandboxOperation.START)
            )

        self.assertNotEqual(self.sandbox.status, "running")
        self.assertEqual(result["status"], "failed")

    def test_ide_missing_sandbox_never_calls_docker(self):
        """Unauthorized sandbox requests must not reach Docker."""

        from unittest.mock import patch

        # Simulate an unknown or unauthorized sandbox.
        self.db.execute.return_value.scalar_one_or_none.return_value = None

        with patch(
            "server_runner.sandbox_controller.operation_handler.DeveloperIDEService"
        ) as mock_service:

            with self.assertRaises(PermissionError):
                self.handler.handle_ide_operation(
                    self.make_request(SandboxOperation.START)
                )

            mock_service.assert_not_called()

    def test_ide_duplicate_request_never_calls_docker(self):
        """A replayed operation must not execute Docker again."""

        from unittest.mock import patch

        from server_runner.sandbox_controller.replay import (
            ReplayProtectionError,
        )

        self.sandbox.container_id = "e" * 64

        # Simulate PostgreSQL rejecting a duplicate request ID.
        self.handler.operation_service.replay_store.claim_request.side_effect = (
            ReplayProtectionError("Duplicate request")
        )

        with patch(
            "server_runner.sandbox_controller.operation_handler.DeveloperIDEService"
        ) as mock_service:

            with self.assertRaises(ReplayProtectionError):
                self.handler.handle_ide_operation(
                    self.make_request(SandboxOperation.START)
                )

            mock_service.assert_not_called()

    def test_ide_inspect_does_not_start_container(self):
        """Inspecting an IDE must not start or stop Docker."""

        from unittest.mock import patch
        from server_runner.sandbox_controller.developer_ide import (
            DeveloperIDEResult,
        )

        container_id = "f" * 64

        self.sandbox.container_id = container_id
        self.sandbox.status = "running"

        with patch(
            "server_runner.sandbox_controller.operation_handler.DeveloperIDEService"
        ) as mock_service:

            mock_service.return_value.inspect_ide.return_value = (
                DeveloperIDEResult(True, "running", "")
            )

            result = self.handler.handle_ide_operation(
                self.make_request(SandboxOperation.INSPECT)
            )

            mock_service.return_value.start_ide.assert_not_called()
            mock_service.return_value.stop_ide.assert_not_called()

        self.assertEqual(result["docker_status"], "running")
        
    def test_ide_missing_lease_is_rejected(self):
        """Reject an IDE operation without a lease record."""

        # Simulate PostgreSQL returning no lease.
        self.handler.replay_store.get_lease.return_value = None

        # The helper must reject the missing lease.
        with self.assertRaises(RuntimeError):
            self.handler._get_ide_lease(uuid4())

    def test_ide_expired_lease_is_rejected(self):
        """Reject an IDE operation whose ownership has expired."""

        # Simulate PostgreSQL rejecting lease ownership.
        self.handler.replay_store.owns_lease.return_value = False

        # The handler must not accept an expired lease.
        with self.assertRaises(RuntimeError):
            self.handler._verify_ide_lease(
                request_id=uuid4(),
                lease_owner=self.lease_owner,
                lease_generation=1,
            )


    def test_ide_expired_lease_cannot_save_result(self):
        """
        An expired worker must not save its Docker operation result.

        PostgreSQL rejects the update by returning rowcount = 0.
        The handler must roll back the transaction.
        """

        from unittest.mock import patch

        from server_runner.sandbox_controller.developer_ide import (
            DeveloperIDEResult,
        )

        # Simulate an existing Docker container.
        container_id = "a" * 64

        self.sandbox.container_id = container_id
        self.sandbox.status = "running"

        # Simulate PostgreSQL rejecting the operation update.
        #
        # rowcount = 0 means the lease no longer matches,
        # has expired, or the operation has already completed.
        self.db.execute.return_value.rowcount = 0

        # Mock Docker so no real container is stopped.
        with patch(
            "server_runner.sandbox_controller.operation_handler.DeveloperIDEService"
        ) as mock_service:

            # Simulate Docker successfully stopping the container.
            mock_service.return_value.stop_ide.return_value = (
                DeveloperIDEResult(
                    success=True,
                    output=container_id,
                    error="",
                )
            )

            # Simulate Docker confirming the stopped state.
            mock_service.return_value.inspect_ide.return_value = (
                DeveloperIDEResult(
                    success=True,
                    output="exited",
                    error="",
                )
            )

            # PostgreSQL must reject the expired worker.
            with self.assertRaisesRegex(
                RuntimeError,
                "lease expired or ownership was lost",
            ):
                self.handler.handle_ide_operation(
                    self.make_request(SandboxOperation.STOP)
                )

        # The handler must not commit the rejected result.
        #
        # Note: the initial operation claim was already committed,
        # so we expect exactly one commit, not zero.
        self.assertEqual(self.db.commit.call_count, 1)

        # The rejected completion transaction must be rolled back.
        self.assertGreaterEqual(
            self.db.rollback.call_count,
            1,
        )
        
if __name__ == "__main__":
    unittest.main()