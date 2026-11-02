"""Test CodeView's internal IDE operation endpoint."""

import os
import unittest

from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from server_runner.sandbox_controller.main import app
from server_runner.runner_service.database import get_db

from server_runner.sandbox_controller.replay import (
    ReplayProtectionError,
)


# ============================================================
# 1. IDE ENDPOINT TESTS
# ============================================================

class TestIDEEndpoint(unittest.TestCase):
    """Verify authentication, validation, and IDE operation handling."""

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

        # Create the HTTP test client.
        self.client = TestClient(app)

        # Generate valid UUIDs.
        self.sandbox_id = uuid4()
        self.request_id = uuid4()

    def tearDown(self):
        """Restore the environment after each test."""

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

        self.client.close()

    def headers(self, token=None):
        """Build an authenticated request header."""

        return {
            "Authorization": (
                f"Bearer {token or self.token}"
            )
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
        """A valid start request reaches the operation handler."""

        mock_handler.return_value.handle.return_value = {
            "request_id": str(self.request_id),
            "sandbox_id": str(self.sandbox_id),
            "operation": "start",
            "status": "recorded",
            "sandbox_status": "starting",
        }

        response = self.client.post(
            "/internal/ide/operations",
            json=self.payload(),
            headers=self.headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["sandbox_status"],
            "starting",
        )

        mock_handler.return_value.handle.assert_called_once()

    @patch(
        "server_runner.sandbox_controller.main.SandboxOperationHandler"
    )
    def test_inspect_operation(self, mock_handler):
        """An inspect request can be recorded."""

        mock_handler.return_value.handle.return_value = {
            "status": "recorded",
            "sandbox_status": "running",
        }

        response = self.client.post(
            "/internal/ide/operations",
            json=self.payload("inspect"),
            headers=self.headers(),
        )

        self.assertEqual(response.status_code, 200)

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

        mock_handler.return_value.handle.side_effect = (
            PermissionError("Sandbox not found or access denied.")
        )

        response = self.client.post(
            "/internal/ide/operations",
            json=self.payload(),
            headers=self.headers(),
        )

        self.assertEqual(response.status_code, 403)

    @patch(
        "server_runner.sandbox_controller.main.SandboxOperationHandler"
    )
    def test_duplicate_request(self, mock_handler):
        """Reject replayed operation requests."""

        mock_handler.return_value.handle.side_effect = (
            ReplayProtectionError("Duplicate operation request.")
        )

        response = self.client.post(
            "/internal/ide/operations",
            json=self.payload(),
            headers=self.headers(),
        )

        self.assertEqual(response.status_code, 409)

    # ========================================================
    # 6. DATABASE FAILURE
    # ========================================================

    @patch(
        "server_runner.sandbox_controller.main.SandboxOperationHandler"
    )
    def test_database_failure(self, mock_handler):
        """Return 503 when the authorization database fails."""

        from sqlalchemy.exc import OperationalError

        mock_handler.return_value.handle.side_effect = (
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

        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()