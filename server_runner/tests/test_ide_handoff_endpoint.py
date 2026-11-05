"""Tests for CodeView IDE Gateway browser handoff."""

import os
import unittest

from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from server_runner.ide_gateway_service.main import app

from server_runner.ide_gateway_service.handoff import (
    ide_handoff_store,
)

from server_runner.ide_gateway_service.sessions import (
    ide_session_store,
)

from server_runner.ide_gateway_service.runner_client import (
    VerifiedIDEIdentity,
)


class TestIDEHandoffEndpoint(unittest.TestCase):
    """Test authorization, handoff, and browser sessions."""

    def setUp(self):
        """Prepare the Gateway test environment."""

        # STEP 1: Create a test-only Gateway authentication token.
        self.gateway_token = "codeview-test-gateway-token"

        # STEP 2: Temporarily configure Gateway authentication.
        self.environment = patch.dict(
            os.environ,
            {
                "CODEVIEW_IDE_GATEWAY_TOKEN": self.gateway_token,
            },
        )
        self.environment.start()

        # STEP 3: Create the FastAPI test client.
        self.client = TestClient(app)

        # STEP 4: Prepare backend authentication headers.
        self.headers = {
            "Authorization": f"Bearer {self.gateway_token}",
        }

        # STEP 5: Define the test developer and group.
        self.developer_id = 12
        self.group_id = 5

        # STEP 6: Clear previous test tickets and sessions.
        ide_handoff_store._tickets.clear()
        ide_session_store._sessions.clear()

    def tearDown(self):
        """Clean up the test environment."""

        # Close the FastAPI test client.
        self.client.close()

        # Remove test tickets and sessions.
        ide_handoff_store._tickets.clear()
        ide_session_store._sessions.clear()

        # Restore the original environment.
        self.environment.stop()

    def create_test_identity(self):
        """Create an identity verified by the Runner."""

        return VerifiedIDEIdentity(
            developer_id=self.developer_id,
            group_id=self.group_id,
            sandbox_id=uuid4(),
        )

    @patch(
        "server_runner.ide_gateway_service.main.consume_grant_with_runner",
        new_callable=AsyncMock,
    )
    def test_authorization_creates_handoff_ticket(self, mock_consume):
        """A verified grant should produce a handoff ticket."""

        # STEP 1: Simulate a verified Runner identity.
        identity = self.create_test_identity()

        mock_consume.return_value = identity

        # STEP 2: Request IDE authorization.
        response = self.client.post(
            "/internal/ide/authorize",
            headers=self.headers,
            json={"grant": "valid-test-grant"},
        )

        # STEP 3: Verify authorization succeeded.
        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertTrue(data["authorized"])

        self.assertEqual(
            data["sandbox_id"],
            str(identity.sandbox_id),
        )

        self.assertTrue(data["handoff_ticket"])

        # STEP 4: Verify the handoff preserves the identities.
        handoff = ide_handoff_store.consume_ticket(
            data["handoff_ticket"]
        )

        self.assertIsNotNone(handoff)

        self.assertEqual(
            handoff.developer_id,
            identity.developer_id,
        )

        self.assertEqual(
            handoff.group_id,
            identity.group_id,
        )

        self.assertEqual(
            handoff.sandbox_id,
            identity.sandbox_id,
        )

    def test_valid_handoff_creates_browser_session(self):
        """A valid ticket should create a browser session."""

        # STEP 1: Create a verified identity.
        identity = self.create_test_identity()

        # STEP 2: Generate a one-time handoff ticket.
        ticket = ide_handoff_store.create_ticket(
            identity
        )

        # STEP 3: Exchange the ticket for a browser session.
        response = self.client.get(
            f"/ide/handoff/{ticket}",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)

        self.assertEqual(
            response.headers["location"],
            "/ide/",
        )

        # STEP 4: Verify the secure cookie exists.
        cookie_header = response.headers.get(
            "set-cookie",
            "",
        )

        self.assertIn(
            "codeview_ide_session=",
            cookie_header,
        )

        self.assertIn(
            "httponly",
            cookie_header.lower(),
        )

        self.assertIn(
            "secure",
            cookie_header.lower(),
        )

        self.assertIn(
            "samesite=strict",
            cookie_header.lower(),
        )

        # STEP 5: Retrieve the session token.
        session_token = response.cookies.get(
            "codeview_ide_session"
        )

        # STEP 6: Find the browser session.
        session = ide_session_store.get_session(
            session_token
        )

        self.assertIsNotNone(session)

        # STEP 7: Verify all three identities.
        self.assertEqual(
            session.developer_id,
            identity.developer_id,
        )

        self.assertEqual(
            session.group_id,
            identity.group_id,
        )

        self.assertEqual(
            session.sandbox_id,
            identity.sandbox_id,
        )

    def test_handoff_ticket_cannot_be_reused(self):
        """A consumed ticket must be rejected on its second use."""

        # Create a valid identity and ticket.
        identity = self.create_test_identity()

        ticket = ide_handoff_store.create_ticket(
            identity
        )

        # First attempt should succeed.
        first_response = self.client.get(
            f"/ide/handoff/{ticket}",
            follow_redirects=False,
        )

        # Second attempt should fail.
        second_response = self.client.get(
            f"/ide/handoff/{ticket}",
            follow_redirects=False,
        )

        self.assertEqual(
            first_response.status_code,
            303,
        )

        self.assertEqual(
            second_response.status_code,
            403,
        )

    def test_invalid_handoff_ticket_rejected(self):
        """An unknown ticket must not create a browser session."""

        response = self.client.get(
            "/ide/handoff/invalid-ticket",
            follow_redirects=False,
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_handoff_disables_caching(self):
        """The handoff response must not be cached."""

        # Create a valid identity and ticket.
        identity = self.create_test_identity()

        ticket = ide_handoff_store.create_ticket(
            identity
        )

        # Exchange the ticket.
        response = self.client.get(
            f"/ide/handoff/{ticket}",
            follow_redirects=False,
        )

        # Verify sensitive responses cannot be cached.
        self.assertEqual(
            response.headers["cache-control"],
            "no-store",
        )

        # Verify the ticket URL cannot leak through referrers.
        self.assertEqual(
            response.headers["referrer-policy"],
            "no-referrer",
        )


if __name__ == "__main__":
    unittest.main()