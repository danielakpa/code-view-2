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


class TestIDEHandoffEndpoint(unittest.TestCase):
    """Test authorization, handoff, and browser sessions."""

    def setUp(self):
        """Prepare the Gateway test environment."""

        # Create a test-only Gateway authentication token.
        self.gateway_token = "codeview-test-gateway-token"

        # Temporarily configure Gateway authentication.
        self.environment = patch.dict(
            os.environ,
            {
                "CODEVIEW_IDE_GATEWAY_TOKEN": self.gateway_token,
            },
        )
        self.environment.start()

        # Create the FastAPI test client.
        self.client = TestClient(app)

        # Prepare backend authentication headers.
        self.headers = {
            "Authorization": f"Bearer {self.gateway_token}",
        }

        # Clear test tickets and sessions.
        ide_handoff_store._tickets.clear()
        ide_session_store._sessions.clear()

    def tearDown(self):
        """Clean up the test environment."""

        self.client.close()

        # Remove test tickets and sessions.
        ide_handoff_store._tickets.clear()
        ide_session_store._sessions.clear()

        # Restore the original environment.
        self.environment.stop()

    @patch(
        "server_runner.ide_gateway_service.main.consume_grant_with_runner",
        new_callable=AsyncMock,
    )
    def test_authorization_creates_handoff_ticket(self, mock_consume):
        """A verified grant should produce a handoff ticket."""

        sandbox_id = uuid4()
        mock_consume.return_value = sandbox_id

        response = self.client.post(
            "/internal/ide/authorize",
            headers=self.headers,
            json={"grant": "valid-test-grant"},
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertTrue(data["authorized"])
        self.assertEqual(data["sandbox_id"], str(sandbox_id))
        self.assertTrue(data["handoff_ticket"])

    def test_valid_handoff_creates_browser_session(self):
        """A valid ticket should create a browser session."""

        sandbox_id = uuid4()

        # Generate a one-time ticket.
        ticket = ide_handoff_store.create_ticket(sandbox_id)

        # Exchange it for a browser session.
        response = self.client.get(
            f"/ide/handoff/{ticket}",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/ide/")

        # Verify the secure cookie exists.
        cookie_header = response.headers.get("set-cookie", "")

        self.assertIn("codeview_ide_session=", cookie_header)
        self.assertIn("httponly", cookie_header.lower())
        self.assertIn("secure", cookie_header.lower())
        self.assertIn("samesite=strict", cookie_header.lower())

        # Confirm the cookie identifies the correct sandbox.
        session_token = response.cookies.get("codeview_ide_session")

        session = ide_session_store.get_session(session_token)

        self.assertIsNotNone(session)
        self.assertEqual(session.sandbox_id, sandbox_id)

    def test_handoff_ticket_cannot_be_reused(self):
        """A consumed ticket must be rejected on its second use."""

        ticket = ide_handoff_store.create_ticket(uuid4())

        first_response = self.client.get(
            f"/ide/handoff/{ticket}",
            follow_redirects=False,
        )

        second_response = self.client.get(
            f"/ide/handoff/{ticket}",
            follow_redirects=False,
        )

        self.assertEqual(first_response.status_code, 303)
        self.assertEqual(second_response.status_code, 403)

    def test_invalid_handoff_ticket_rejected(self):
        """An unknown ticket must not create a browser session."""

        response = self.client.get(
            "/ide/handoff/invalid-ticket",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 403)

    def test_handoff_disables_caching(self):
        """The handoff response must not be cached."""

        ticket = ide_handoff_store.create_ticket(uuid4())

        response = self.client.get(
            f"/ide/handoff/{ticket}",
            follow_redirects=False,
        )

        self.assertEqual(
            response.headers["cache-control"],
            "no-store",
        )

        self.assertEqual(
            response.headers["referrer-policy"],
            "no-referrer",
        )


if __name__ == "__main__":
    unittest.main()