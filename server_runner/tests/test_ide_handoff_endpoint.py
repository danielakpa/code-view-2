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

        # STEP 2: Configure Gateway authentication temporarily.
        self.environment = patch.dict(
            os.environ,
            {
                "CODEVIEW_IDE_GATEWAY_TOKEN": self.gateway_token,
            },
        )

        self.environment.start()

        # STEP 3: Create the FastAPI test client.
        self.client = TestClient(app)

        # STEP 4: Prepare authenticated backend headers.
        self.headers = {
            "Authorization": f"Bearer {self.gateway_token}",
        }

        # STEP 5: Define the test developer and group.
        self.developer_id = 12
        self.group_id = 5

        # STEP 6: Generate a membership generation.
        self.membership_generation = uuid4()

        # STEP 7: Track tickets and sessions created by these tests.
        self.created_tickets = []
        self.created_sessions = []

    def tearDown(self):
        """Clean up only the resources created by this test."""

        # Remove any remaining test handoff tickets.
        for ticket in self.created_tickets:
            ide_handoff_store.consume_ticket(ticket)

        # Revoke sessions created during this test.
        for token in self.created_sessions:
            ide_session_store.revoke_session(token)

        # Close the FastAPI test client.
        self.client.close()

        # Restore the original environment.
        self.environment.stop()

    def create_test_identity(self):
        """Create a complete identity verified by the Runner."""

        return VerifiedIDEIdentity(
            developer_id=self.developer_id,
            group_id=self.group_id,
            sandbox_id=uuid4(),

            # This generation identifies the current membership.
            membership_generation=self.membership_generation,
        )

    def create_test_ticket(self, identity):
        """Create and track a one-time browser handoff ticket."""

        ticket = ide_handoff_store.create_ticket(identity)

        self.created_tickets.append(ticket)

        return ticket

    def exchange_ticket(self, ticket):
        """Exchange a handoff ticket without following redirects."""

        response = self.client.get(
            f"/ide/handoff/{ticket}",
            follow_redirects=False,
        )

        # Track the browser session if the handoff succeeded.
        if response.status_code == 303:
            session_token = response.cookies.get(
                "codeview_ide_session"
            )

            if session_token:
                self.created_sessions.append(session_token)

        return response

    @patch(
        "server_runner.ide_gateway_service.main.consume_grant_with_runner",
        new_callable=AsyncMock,
    )
    def test_authorization_creates_handoff_ticket(self, mock_consume):
        """A verified Runner grant should produce a handoff ticket."""

        # STEP 1: Simulate an identity verified by the Runner.
        identity = self.create_test_identity()

        mock_consume.return_value = identity

        # STEP 2: Request IDE authorization.
        response = self.client.post(
            "/internal/ide/authorize",
            headers=self.headers,
            json={
                "grant": "valid-test-grant",
            },
        )

        # STEP 3: Confirm authorization succeeded.
        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertTrue(data["authorized"])

        self.assertEqual(
            data["sandbox_id"],
            str(identity.sandbox_id),
        )

        self.assertTrue(data["handoff_ticket"])

        # Track the ticket for cleanup.
        self.created_tickets.append(data["handoff_ticket"])

        # STEP 4: Consume the ticket and inspect its identity.
        handoff = ide_handoff_store.consume_ticket(
            data["handoff_ticket"]
        )

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

        # Verify the Runner was contacted once.
        mock_consume.assert_awaited_once()

    def test_valid_handoff_creates_browser_session(self):
        """A valid ticket should create a browser session."""

        # STEP 1: Create a complete verified identity.
        identity = self.create_test_identity()

        # STEP 2: Generate a one-time handoff ticket.
        ticket = self.create_test_ticket(identity)

        # STEP 3: Exchange the ticket for a browser session.
        response = self.exchange_ticket(ticket)

        self.assertEqual(response.status_code, 303)

        self.assertEqual(
            response.headers["location"],
            "/ide/",
        )

        # STEP 4: Verify the secure browser cookie exists.
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

        # STEP 5: Retrieve the browser session token.
        session_token = response.cookies.get(
            "codeview_ide_session"
        )

        self.assertIsNotNone(session_token)

        # STEP 6: Retrieve the stored browser session.
        session = ide_session_store.get_session(
            session_token
        )

        self.assertIsNotNone(session)

        # STEP 7: Verify the developer identity.
        self.assertEqual(
            session.developer_id,
            identity.developer_id,
        )

        # STEP 8: Verify the group identity.
        self.assertEqual(
            session.group_id,
            identity.group_id,
        )

        # STEP 9: Verify the sandbox identity.
        self.assertEqual(
            session.sandbox_id,
            identity.sandbox_id,
        )

        # STEP 10: Verify the membership generation.
        self.assertEqual(
            session.membership_generation,
            identity.membership_generation,
        )

    def test_handoff_ticket_cannot_be_reused(self):
        """A consumed ticket must be rejected on its second use."""

        # STEP 1: Create a valid identity.
        identity = self.create_test_identity()

        # STEP 2: Create a one-time handoff ticket.
        ticket = self.create_test_ticket(identity)

        # STEP 3: Exchange the ticket successfully.
        first_response = self.exchange_ticket(ticket)

        # STEP 4: Attempt to reuse the same ticket.
        second_response = self.exchange_ticket(ticket)

        # The first exchange must succeed.
        self.assertEqual(
            first_response.status_code,
            303,
        )

        # The second exchange must fail.
        self.assertEqual(
            second_response.status_code,
            403,
        )

    def test_invalid_handoff_ticket_rejected(self):
        """An unknown ticket must not create a browser session."""

        response = self.exchange_ticket(
            "invalid-ticket"
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        # No session should be created by an invalid ticket.
        self.assertEqual(
            self.created_sessions,
            [],
        )

    def test_handoff_disables_caching(self):
        """Sensitive handoff responses must not be cached."""

        # STEP 1: Create a valid identity.
        identity = self.create_test_identity()

        # STEP 2: Generate a handoff ticket.
        ticket = self.create_test_ticket(identity)

        # STEP 3: Exchange the ticket.
        response = self.exchange_ticket(ticket)

        self.assertEqual(response.status_code, 303)

        # STEP 4: Prevent caching of sensitive responses.
        self.assertEqual(
            response.headers["cache-control"],
            "no-store",
        )

        # STEP 5: Prevent ticket URLs leaking through referrers.
        self.assertEqual(
            response.headers["referrer-policy"],
            "no-referrer",
        )

    def test_different_membership_generations_remain_separate(self):
        """Different membership generations must remain distinguishable."""

        # STEP 1: Create the original verified identity.
        old_identity = self.create_test_identity()

        # STEP 2: Create a second identity with a new generation.
        new_identity = VerifiedIDEIdentity(
            developer_id=self.developer_id,
            group_id=self.group_id,
            sandbox_id=old_identity.sandbox_id,
            membership_generation=uuid4(),
        )

        # STEP 3: Create separate handoff tickets.
        old_ticket = self.create_test_ticket(old_identity)
        new_ticket = self.create_test_ticket(new_identity)

        # STEP 4: Exchange both tickets for sessions.
        old_response = self.exchange_ticket(old_ticket)
        new_response = self.exchange_ticket(new_ticket)

        self.assertEqual(old_response.status_code, 303)
        self.assertEqual(new_response.status_code, 303)

        # STEP 5: Retrieve both browser sessions.
        old_token = old_response.cookies.get(
            "codeview_ide_session"
        )

        new_token = new_response.cookies.get(
            "codeview_ide_session"
        )

        old_session = ide_session_store.get_session(old_token)
        new_session = ide_session_store.get_session(new_token)

        self.assertIsNotNone(old_session)
        self.assertIsNotNone(new_session)

        # STEP 6: Verify each session retains its own generation.
        self.assertEqual(
            old_session.membership_generation,
            old_identity.membership_generation,
        )

        self.assertEqual(
            new_session.membership_generation,
            new_identity.membership_generation,
        )

        self.assertNotEqual(
            old_session.membership_generation,
            new_session.membership_generation,
        )


if __name__ == "__main__":
    unittest.main()