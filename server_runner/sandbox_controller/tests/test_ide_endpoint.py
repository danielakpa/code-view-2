"""Test CodeView's internal IDE operation endpoint."""

import os
import unittest

from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from server_runner.sandbox_controller.main import app

from server_runner.runner_service.database import get_db

from server_runner.sandbox_controller.operation import (
    SandboxOperation,
)

from server_runner.sandbox_controller.replay import (
    ReplayProtectionError,
)


# ============================================================
# 1. IDE ENDPOINT TESTS
# ============================================================

class TestIDEEndpoint(unittest.TestCase):
    """Verify authentication, validation, and IDE operation handling."""

    @patch(
        "server_runner.sandbox_controller.main.SandboxOperationHandler"
    )
    def test_database_session_closes_after_success(self, mock_handler):
        """Verify the database session closes after a successful IDE operation."""

        # Simulate a successful Docker operation.
        mock_handler.return_value.handle_ide_operation.return_value = {
            "request_id": str(self.request_id),
            "sandbox_id": str(self.sandbox_id),
            "operation": "start",
            "status": "completed",
            "sandbox_status": "running",
            "docker_status": "running",
            "error": None,
        }

        # Send the IDE operation request.
        response = self.client.post(
            "/internal/ide/operations",
            json=self.payload(),
            headers=self.headers(),
        )

        # Confirm the operation succeeded.
        self.assertEqual(response.status_code, 200)

        # Confirm the database session was closed.
        self.db.close.assert_called_once()

    @patch(
        "server_runner.sandbox_controller.main.SandboxOperationHandler"
    )
    def test_database_session_closes_after_failure(self, mock_handler):
        """Verify the database session closes when an IDE operation fails."""

        # Simulate a database operation failure.
        mock_handler.return_value.handle_ide_operation.side_effect = (
            OperationalError(
                "SELECT 1",
                {},
                Exception("Database unavailable"),
            )
        )

        # Send the IDE operation request.
        response = self.client.post(
            "/internal/ide/operations",
            json=self.payload(),
            headers=self.headers(),
        )

        # Confirm the API returns a database error.
        self.assertEqual(response.status_code, 503)

        # Confirm the database session was still closed.
        self.db.close.assert_called_once()

    def setUp(self):
        """Prepare an isolated test environment."""

        # Create a test authentication token.
        self.token = "codeview-test-controller-token"

        # Save the existing environment variable.
        self.original_token = os.environ.get(
            "CODEVIEW_CONTROLLER_TOKEN"
        )

        # Configure the token used during testing.
        os.environ["CODEVIEW_CONTROLLER_TOKEN"] = self.token

        # Replace the real database dependency with a mock.
        self.db = Mock()

        app.dependency_overrides[get_db] = lambda: self.db
        
        # Mock the database session factory used by IDE operations.
        self.session_patcher = patch(
            "server_runner.sandbox_controller.main.SessionLocal",
            return_value=self.db,
        )

        # Start the patch before making HTTP requests.
        self.session_patcher.start()

        # Create the HTTP test client.
        self.client = TestClient(app)

        # Generate valid UUIDs.
        self.sandbox_id = uuid4()
        self.request_id = uuid4()

    def tearDown(self):
        """Restore the environment after each test."""

        # Stop mocking the database session factory.
        self.session_patcher.stop()
        # Remove test dependency overrides.
        app.dependency_overrides.clear()

        # Restore the original authentication token.
        if self.original_token is None:
            os.environ.pop(
                "CODEVIEW_CONTROLLER_TOKEN",
                None,
            )
        else:
            os.environ[
                "CODEVIEW_CONTROLLER_TOKEN"
            ] = self.original_token

        # Close the HTTP test client.
        self.client.close()

    def headers(self, token=None):
        """Build an authenticated request header."""

        return {
            "Authorization": f"Bearer {token or self.token}"
        }

    def payload(self, operation="start"):
        """Build a valid IDE operation request."""

        return {
            "request_id": str(self.request_id),
            "sandbox_id": str(self.sandbox_id),
            "developer_id": 1,
            "group_id": 1,
            "operation": operation,
        }

    # ========================================================
    # 2. SUCCESSFUL OPERATIONS
    # ========================================================

    @patch(
        "server_runner.sandbox_controller.main.SandboxOperationHandler"
    )
    def test_start_operation(self, mock_handler):
        """A valid start request executes the IDE operation handler."""

        # Simulate a successful Docker IDE startup.
        mock_handler.return_value.handle_ide_operation.return_value = {
            "request_id": str(self.request_id),
            "sandbox_id": str(self.sandbox_id),
            "operation": "start",
            "status": "completed",
            "sandbox_status": "running",
            "docker_status": "running",
            "error": None,
        }

        # Send the IDE startup request.
        response = self.client.post(
            "/internal/ide/operations",
            json=self.payload(),
            headers=self.headers(),
        )

        # Verify the HTTP response.
        self.assertEqual(response.status_code, 200)

        # Verify that Docker startup was reported as completed.
        self.assertEqual(
            response.json()["status"],
            "completed",
        )

        # Verify the sandbox lifecycle status.
        self.assertEqual(
            response.json()["sandbox_status"],
            "running",
        )

        # Verify Docker's reported status.
        self.assertEqual(
            response.json()["docker_status"],
            "running",
        )

        # Verify the handler was initialized with our test database.
        mock_handler.assert_called_once_with(self.db)

        # Verify the executable method was called exactly once.
        mock_handler.return_value.handle_ide_operation.assert_called_once()

        # Verify the old recording-only method was not called.
        mock_handler.return_value.handle.assert_not_called()

        # Verify the request passed to the handler.
        operation_request = (
            mock_handler.return_value
            .handle_ide_operation
            .call_args.args[0]
        )

        self.assertEqual(
            operation_request.request_id,
            self.request_id,
        )

        self.assertEqual(
            operation_request.sandbox_id,
            self.sandbox_id,
        )

        self.assertEqual(
            operation_request.operation,
            SandboxOperation.START,
        )

    @patch(
        "server_runner.sandbox_controller.main.SandboxOperationHandler"
    )
    def test_inspect_operation(self, mock_handler):
        """An inspect request executes the IDE inspection handler."""

        # Simulate Docker confirming a running IDE.
        mock_handler.return_value.handle_ide_operation.return_value = {
            "request_id": str(self.request_id),
            "sandbox_id": str(self.sandbox_id),
            "operation": "inspect",
            "status": "completed",
            "sandbox_status": "running",
            "docker_status": "running",
            "error": None,
        }

        # Send the inspection request.
        response = self.client.post(
            "/internal/ide/operations",
            json=self.payload("inspect"),
            headers=self.headers(),
        )

        # Verify the response.
        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            response.json()["status"],
            "completed",
        )

        self.assertEqual(
            response.json()["docker_status"],
            "running",
        )

        # Verify the executable handler was called once.
        mock_handler.return_value.handle_ide_operation.assert_called_once()

        # Verify the operation type.
        operation_request = (
            mock_handler.return_value
            .handle_ide_operation
            .call_args.args[0]
        )

        self.assertEqual(
            operation_request.operation,
            SandboxOperation.INSPECT,
        )

    # ========================================================
    # 3. AUTHENTICATION
    # ========================================================

    def test_missing_token(self):
        """Reject requests without an authentication token."""

        response = self.client.post(
            "/internal/ide/operations",
            json=self.payload(),
        )

        self.assertIn(
            response.status_code,
            (401, 403),
        )

    def test_invalid_token(self):
        """Reject requests with an incorrect token."""

        response = self.client.post(
            "/internal/ide/operations",
            json=self.payload(),
            headers=self.headers("incorrect-token"),
        )

        self.assertIn(
            response.status_code,
            (401, 403),
        )

    # ========================================================
    # 4. REQUEST VALIDATION
    # ========================================================

    def test_invalid_developer_id(self):
        """Reject invalid developer IDs."""

        payload = self.payload()

        # Developer IDs must be positive.
        payload["developer_id"] = -1

        response = self.client.post(
            "/internal/ide/operations",
            json=payload,
            headers=self.headers(),
        )

        self.assertEqual(response.status_code, 422)

    def test_invalid_sandbox_id(self):
        """Reject malformed sandbox UUIDs."""

        payload = self.payload()

        # Replace the valid UUID with invalid text.
        payload["sandbox_id"] = "not-a-uuid"

        response = self.client.post(
            "/internal/ide/operations",
            json=payload,
            headers=self.headers(),
        )

        self.assertEqual(response.status_code, 422)

    def test_run_operation_rejected(self):
        """Code execution must use the separate execution endpoint."""

        response = self.client.post(
            "/internal/ide/operations",
            json=self.payload("run"),
            headers=self.headers(),
        )

        self.assertEqual(response.status_code, 422)

    # ========================================================
    # 5. OWNERSHIP AND REPLAY PROTECTION
    # ========================================================

    @patch(
        "server_runner.sandbox_controller.main.SandboxOperationHandler"
    )
    def test_unauthorized_sandbox(self, mock_handler):
        """Reject operations targeting another developer's sandbox."""

        # Simulate the handler rejecting sandbox ownership.
        mock_handler.return_value.handle_ide_operation.side_effect = (
            PermissionError(
                "Sandbox not found or access denied."
            )
        )

        response = self.client.post(
            "/internal/ide/operations",
            json=self.payload(),
            headers=self.headers(),
        )

        # Unauthorized access must be rejected.
        self.assertEqual(response.status_code, 403)

        # Verify the executable handler was called.
        mock_handler.return_value.handle_ide_operation.assert_called_once()

    @patch(
        "server_runner.sandbox_controller.main.SandboxOperationHandler"
    )
    def test_duplicate_request(self, mock_handler):
        """Reject replayed IDE operation requests."""

        # Simulate PostgreSQL replay protection rejecting the request.
        mock_handler.return_value.handle_ide_operation.side_effect = (
            ReplayProtectionError(
                "Duplicate operation request."
            )
        )

        response = self.client.post(
            "/internal/ide/operations",
            json=self.payload(),
            headers=self.headers(),
        )

        # Duplicate requests must return HTTP 409.
        self.assertEqual(response.status_code, 409)

        # Verify the executable handler was called.
        mock_handler.return_value.handle_ide_operation.assert_called_once()

    # ========================================================
    # 6. DATABASE FAILURE
    # ========================================================

    @patch(
        "server_runner.sandbox_controller.main.SandboxOperationHandler"
    )
    def test_database_failure(self, mock_handler):
        """Return 503 when the authorization database fails."""

        # Simulate a PostgreSQL connection or query failure.
        mock_handler.return_value.handle_ide_operation.side_effect = (
            OperationalError(
                "SELECT 1",
                {},
                Exception("Database unavailable"),
            )
        )

        response = self.client.post(
            "/internal/ide/operations",
            json=self.payload(),
            headers=self.headers(),
        )

        # Database failures must return HTTP 503.
        self.assertEqual(response.status_code, 503)

        # Verify the executable handler was called.
        mock_handler.return_value.handle_ide_operation.assert_called_once()


if __name__ == "__main__":
    unittest.main()