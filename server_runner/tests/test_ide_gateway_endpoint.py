"""Tests for the CodeView IDE Gateway authorization endpoint."""

import os
import unittest

from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from server_runner.ide_gateway_service.main import app
from server_runner.ide_gateway_service.runner_client import (
    RunnerAuthorizationError,
    RunnerUnavailableError,
)


class TestIDEGatewayEndpoint(unittest.TestCase):
    """Test Gateway authentication and IDE authorization."""

    def setUp(self):
        """Prepare a test client and authentication token."""

        # Create the FastAPI test client.
        self.client = TestClient(app)

        # Use a test-only backend authentication token.
        self.token = "codeview-test-gateway-token"

        # Set the expected token temporarily.
        self.environment = patch.dict(
            os.environ,
            {"CODEVIEW_IDE_GATEWAY_TOKEN": self.token},
        )
        self.environment.start()

        # Prepare valid authentication headers.
        self.headers = {
            "Authorization": f"Bearer {self.token}",
        }

    def tearDown(self):
        """Restore the original environment."""

        self.client.close()
        self.environment.stop()

    def test_health_check(self):
        """The health endpoint should be accessible."""

        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")

    @patch(
        "server_runner.ide_gateway_service.main.consume_grant_with_runner",
        new_callable=AsyncMock,
    )
    def test_valid_grant_accepted(self, mock_consume):
        """Accept a grant verified by the Runner."""

        # Simulate a verified sandbox.
        sandbox_id = uuid4()
        mock_consume.return_value = sandbox_id

        response = self.client.post(
            "/internal/ide/authorize",
            headers=self.headers,
            json={"grant": "valid-test-grant"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["authorized"], True)
        self.assertEqual(
            response.json()["sandbox_id"],
            str(sandbox_id),
        )

        mock_consume.assert_awaited_once_with(
            "valid-test-grant"
        )

    @patch(
        "server_runner.ide_gateway_service.main.consume_grant_with_runner",
        new_callable=AsyncMock,
    )
    def test_invalid_grant_rejected(self, mock_consume):
        """Reject an invalid or previously consumed grant."""

        mock_consume.side_effect = RunnerAuthorizationError(
            "Invalid grant."
        )

        response = self.client.post(
            "/internal/ide/authorize",
            headers=self.headers,
            json={"grant": "invalid-test-grant"},
        )

        self.assertEqual(response.status_code, 403)

    @patch(
        "server_runner.ide_gateway_service.main.consume_grant_with_runner",
        new_callable=AsyncMock,
    )
    def test_runner_unavailable(self, mock_consume):
        """Return 503 when the Runner cannot be reached."""

        mock_consume.side_effect = RunnerUnavailableError(
            "Runner unavailable."
        )

        response = self.client.post(
            "/internal/ide/authorize",
            headers=self.headers,
            json={"grant": "test-grant"},
        )

        self.assertEqual(response.status_code, 503)

    @patch(
        "server_runner.ide_gateway_service.main.consume_grant_with_runner",
        new_callable=AsyncMock,
    )
    def test_missing_authentication_rejected(self, mock_consume):
        """Reject requests without backend authentication."""

        response = self.client.post(
            "/internal/ide/authorize",
            json={"grant": "test-grant"},
        )

        self.assertEqual(response.status_code, 401)
        mock_consume.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.consume_grant_with_runner",
        new_callable=AsyncMock,
    )
    def test_invalid_authentication_rejected(self, mock_consume):
        """Reject requests with an incorrect backend token."""

        response = self.client.post(
            "/internal/ide/authorize",
            headers={
                "Authorization": "Bearer incorrect-token",
            },
            json={"grant": "test-grant"},
        )

        self.assertEqual(response.status_code, 401)
        mock_consume.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.consume_grant_with_runner",
        new_callable=AsyncMock,
    )
    def test_empty_grant_rejected(self, mock_consume):
        """Reject an empty authorization grant."""

        response = self.client.post(
            "/internal/ide/authorize",
            headers=self.headers,
            json={"grant": ""},
        )

        self.assertEqual(response.status_code, 422)
        mock_consume.assert_not_awaited()


