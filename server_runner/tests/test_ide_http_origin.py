"""Tests for CodeView's protected browser IDE route."""

import hashlib
import unittest

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.responses import Response
from fastapi.testclient import TestClient

from server_runner.ide_gateway_service.main import app
from server_runner.ide_gateway_service.sessions import (
    ide_session_store,
)


class TestIDEProtectedRoute(unittest.TestCase):
    """Verify that only valid IDE sessions can access the route."""

    def setUp(self):
        """Prepare a fresh test environment."""

        # Create the FastAPI test client.
        self.client = TestClient(app)

        # Define test identities.
        self.developer_id = 12
        self.group_id = 5

        # Create a unique sandbox for testing.
        self.sandbox_id = uuid4()

        # Generate the developer's membership generation.
        self.membership_generation = uuid4()

        # Keep track of sessions created by this test.
        self.tokens = []

    def tearDown(self):
        """Clean up after each test."""

        # Revoke only sessions created by this test.
        for token in self.tokens:
            ide_session_store.revoke_session(token)

        # Close the test client.
        self.client.close()

    def create_test_session(self, sandbox_id=None):
        """Create an IDE session with a verified test identity."""

        # Use the default sandbox unless another is provided.
        if sandbox_id is None:
            sandbox_id = self.sandbox_id

        # Create a session containing all four identity values.
        token = ide_session_store.create_session(
            developer_id=self.developer_id,
            group_id=self.group_id,
            sandbox_id=sandbox_id,
            membership_generation=self.membership_generation,
        )

        # Save the token so tearDown can revoke it.
        self.tokens.append(token)

        return token

    def test_missing_session_rejected(self):
        """A browser without a session must be rejected."""

        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 401)

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_valid_session_accepted(self, mock_proxy):
        """A valid session should authorize IDE access."""

        # STEP 1: Create a valid session.
        token = self.create_test_session()

        # STEP 2: Simulate a successful response from the IDE.
        mock_proxy.return_value = Response(
            content="IDE ready",
            status_code=200,
        )

        # STEP 3: Send the session cookie to the Gateway.
        response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": token,
            },
        )

        # STEP 4: Confirm the Gateway allowed access.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "IDE ready")

        # STEP 5: Confirm the Gateway forwarded the request.
        mock_proxy.assert_awaited_once()

        # STEP 6: Confirm the correct sandbox was selected.
        self.assertEqual(
            mock_proxy.call_args.kwargs["sandbox_id"],
            self.sandbox_id,
        )

    def test_invalid_session_rejected(self):
        """An unknown session token must be rejected."""

        response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": "invalid-token",
            },
        )

        self.assertEqual(response.status_code, 401)

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_expired_session_rejected(self, mock_proxy):
        """An expired session must not authorize access."""

        # STEP 1: Create a valid session.
        token = self.create_test_session()

        # STEP 2: Calculate its stored SHA-256 hash.
        token_hash = hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

        # STEP 3: Read the existing session.
        session = ide_session_store.get_session(token)

        self.assertIsNotNone(session)

        # STEP 4: Replace its expiration with a past time.
        # dataclasses.replace preserves the developer, group,
        # sandbox, membership generation, and creation time.
        with ide_session_store._lock:
            ide_session_store._sessions[token_hash] = replace(
                session,
                expires_at=(
                    datetime.now(timezone.utc)
                    - timedelta(seconds=1)
                ),
            )

        # STEP 5: Attempt access using the expired session.
        response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": token,
            },
        )

        # STEP 6: Confirm access was rejected.
        self.assertEqual(response.status_code, 401)

        # The request must never reach the IDE.
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_revoked_session_rejected(self, mock_proxy):
        """A revoked session must no longer work."""

        # STEP 1: Create a valid session.
        token = self.create_test_session()

        # STEP 2: Revoke the session.
        ide_session_store.revoke_session(token)

        # STEP 3: Attempt access with the revoked token.
        response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": token,
            },
        )

        # STEP 4: Confirm access is denied.
        self.assertEqual(response.status_code, 401)

        # The request must never reach the IDE.
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_session_belongs_to_correct_sandbox(self, mock_proxy):
        """The Gateway must forward each session to its own sandbox."""

        # STEP 1: Create two different sandboxes.
        first_sandbox = uuid4()
        second_sandbox = uuid4()

        # STEP 2: Create independent sessions.
        first_token = self.create_test_session(
            sandbox_id=first_sandbox,
        )

        second_token = self.create_test_session(
            sandbox_id=second_sandbox,
        )

        # STEP 3: Simulate successful IDE responses.
        mock_proxy.return_value = Response(
            content="IDE ready",
            status_code=200,
        )

        # STEP 4: Access the IDE using the first session.
        first_response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": first_token,
            },
        )

        # STEP 5: Access the IDE using the second session.
        second_response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": second_token,
            },
        )

        # STEP 6: Both authorized requests should succeed.
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)

        # STEP 7: Verify that both requests reached the proxy.
        self.assertEqual(mock_proxy.await_count, 2)

        # STEP 8: Inspect the sandbox selected for each request.
        first_call = mock_proxy.await_args_list[0]
        second_call = mock_proxy.await_args_list[1]

        self.assertEqual(
            first_call.kwargs["sandbox_id"],
            first_sandbox,
        )

        self.assertEqual(
            second_call.kwargs["sandbox_id"],
            second_sandbox,
        )


if __name__ == "__main__":
    unittest.main()