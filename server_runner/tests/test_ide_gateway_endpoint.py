"""Tests for the CodeView IDE Gateway authorization endpoint."""

import os
import unittest

from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from server_runner.ide_gateway_service.main import app

from server_runner.ide_gateway_service.handoff import (
    ide_handoff_store,
)

from server_runner.ide_gateway_service.runner_client import (
    RunnerAuthorizationError,
    RunnerUnavailableError,
    VerifiedIDEIdentity,
)


class TestIDEGatewayEndpoint(unittest.TestCase):
    """Test Gateway authentication and IDE authorization."""

    def setUp(self):
        """Prepare an isolated Gateway test environment."""

        # STEP 1: Create the FastAPI test client.
        self.client = TestClient(app)

        # STEP 2: Create a test-only Gateway authentication token.
        self.token = "codeview-test-gateway-token"

        # STEP 3: Configure Gateway authentication temporarily.
        self.environment = patch.dict(
            os.environ,
            {
                "CODEVIEW_IDE_GATEWAY_TOKEN": self.token,
            },
        )

        self.environment.start()

        # STEP 4: Prepare valid authentication headers.
        self.headers = {
            "Authorization": f"Bearer {self.token}",
        }

        # STEP 5: Define the developer and group identities.
        self.developer_id = 12
        self.group_id = 5

        # STEP 6: Generate unique sandbox and membership identities.
        self.sandbox_id = uuid4()
        self.membership_generation = uuid4()

        # STEP 7: Track tickets created during this test.
        self.created_tickets = []

    def tearDown(self):
        """Restore dependencies and remove test resources."""

        # Remove any unconsumed handoff tickets created by this test.
        for ticket in self.created_tickets:
            ide_handoff_store.consume_ticket(ticket)

        # Close the FastAPI test client.
        self.client.close()

        # Restore the original environment variables.
        self.environment.stop()

    def create_test_identity(self):
        """Create a complete identity verified by the Runner."""

        return VerifiedIDEIdentity(
            developer_id=self.developer_id,
            group_id=self.group_id,
            sandbox_id=self.sandbox_id,
            membership_generation=self.membership_generation,
        )

    def authorize_grant(self, grant="valid-test-grant", headers=None):
        """Send an authorization request to the Gateway."""

        if headers is None:
            headers = self.headers

        return self.client.post(
            "/internal/ide/authorize",
            headers=headers,
            json={
                "grant": grant,
            },
        )

    def test_health_check(self):
        """The health endpoint should remain accessible."""

        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            response.json()["status"],
            "healthy",
        )

    @patch(
        "server_runner.ide_gateway_service.main.consume_grant_with_runner",
        new_callable=AsyncMock,
    )
    def test_valid_grant_accepted(self, mock_consume):
        """Accept a complete identity verified by the Runner."""

        # STEP 1: Create the identity returned by the Runner.
        identity = self.create_test_identity()

        mock_consume.return_value = identity

        # STEP 2: Submit the authorization grant.
        response = self.authorize_grant()

        # STEP 3: Confirm the Gateway accepted the grant.
        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertTrue(data["authorized"])

        # STEP 4: Confirm the correct sandbox was returned.
        self.assertEqual(
            data["sandbox_id"],
            str(identity.sandbox_id),
        )

        # STEP 5: Confirm a browser handoff ticket was created.
        self.assertTrue(data["handoff_ticket"])

        # Track the ticket for cleanup.
        self.created_tickets.append(
            data["handoff_ticket"]
        )

        # STEP 6: Confirm the Runner was contacted correctly.
        mock_consume.assert_awaited_once_with(
            "valid-test-grant"
        )

    @patch(
        "server_runner.ide_gateway_service.main.consume_grant_with_runner",
        new_callable=AsyncMock,
    )
    def test_invalid_grant_rejected(self, mock_consume):
        """Reject invalid or previously consumed grants."""

        # Simulate Runner authorization failure.
        mock_consume.side_effect = RunnerAuthorizationError(
            "Invalid grant."
        )

        response = self.authorize_grant(
            grant="invalid-test-grant"
        )

        self.assertEqual(response.status_code, 403)

        mock_consume.assert_awaited_once_with(
            "invalid-test-grant"
        )

    @patch(
        "server_runner.ide_gateway_service.main.consume_grant_with_runner",
        new_callable=AsyncMock,
    )
    def test_runner_unavailable(self, mock_consume):
        """Return HTTP 503 when the Runner is unavailable."""

        # Simulate a Runner connection failure.
        mock_consume.side_effect = RunnerUnavailableError(
            "Runner unavailable."
        )

        response = self.authorize_grant(
            grant="test-grant"
        )

        self.assertEqual(response.status_code, 503)

        mock_consume.assert_awaited_once_with(
            "test-grant"
        )

    @patch(
        "server_runner.ide_gateway_service.main.consume_grant_with_runner",
        new_callable=AsyncMock,
    )
    def test_missing_authentication_rejected(self, mock_consume):
        """Reject requests without backend authentication."""

        response = self.client.post(
            "/internal/ide/authorize",
            json={
                "grant": "test-grant",
            },
        )

        self.assertEqual(response.status_code, 401)

        # The Runner must not receive unauthorized requests.
        mock_consume.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.consume_grant_with_runner",
        new_callable=AsyncMock,
    )
    def test_invalid_authentication_rejected(self, mock_consume):
        """Reject requests containing an incorrect Gateway token."""

        response = self.authorize_grant(
            grant="test-grant",
            headers={
                "Authorization": "Bearer incorrect-token",
            },
        )

        self.assertEqual(response.status_code, 401)

        mock_consume.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.consume_grant_with_runner",
        new_callable=AsyncMock,
    )
    def test_empty_grant_rejected(self, mock_consume):
        """Reject an empty authorization grant."""

        response = self.authorize_grant(
            grant=""
        )

        self.assertEqual(response.status_code, 422)

        mock_consume.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.consume_grant_with_runner",
        new_callable=AsyncMock,
    )
    def test_handoff_preserves_verified_identity(self, mock_consume):
        """The Gateway must preserve all four verified identity values."""

        # STEP 1: Create a verified Runner identity.
        identity = self.create_test_identity()

        mock_consume.return_value = identity

        # STEP 2: Authorize the developer.
        response = self.authorize_grant()

        self.assertEqual(response.status_code, 200)

        # STEP 3: Retrieve the generated handoff ticket.
        ticket = response.json()["handoff_ticket"]

        self.created_tickets.append(ticket)

        # STEP 4: Consume the ticket to inspect its contents.
        handoff = ide_handoff_store.consume_ticket(ticket)

        self.assertIsNotNone(handoff)

        # STEP 5: Verify the developer identity.
        self.assertEqual(
            handoff.developer_id,
            identity.developer_id,
        )

        # STEP 6: Verify the group identity.
        self.assertEqual(
            handoff.group_id,
            identity.group_id,
        )

        # STEP 7: Verify the sandbox identity.
        self.assertEqual(
            handoff.sandbox_id,
            identity.sandbox_id,
        )

        # STEP 8: Verify the membership generation.
        self.assertEqual(
            handoff.membership_generation,
            identity.membership_generation,
        )

    @patch(
        "server_runner.ide_gateway_service.main.consume_grant_with_runner",
        new_callable=AsyncMock,
    )
    def test_missing_gateway_token_configuration_rejected(
        self,
        mock_consume,
    ):
        """The Gateway must reject requests when its token is unset."""

        # Temporarily remove the configured Gateway token.
        with patch.dict(
            os.environ,
            {
                "CODEVIEW_IDE_GATEWAY_TOKEN": "",
            },
        ):
            response = self.authorize_grant()

        # A missing authentication configuration must not grant access.
        self.assertNotEqual(response.status_code, 200)

        # The Runner must not be contacted.
        mock_consume.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.consume_grant_with_runner",
        new_callable=AsyncMock,
    )
    def test_bearer_token_required(self, mock_consume):
        """A backend request must use Bearer authentication."""

        response = self.authorize_grant(
            headers={
                "Authorization": self.token,
            },
        )

        self.assertEqual(response.status_code, 401)

        mock_consume.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()