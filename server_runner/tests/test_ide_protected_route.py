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

from server_runner.ide_gateway_service.membership_client import (
    MembershipAuthorizationError,
    MembershipServiceUnavailableError,
)

from server_runner.ide_gateway_service.sessions import (
    ide_session_store,
)


class TestIDEProtectedRoute(unittest.TestCase):
    """Test IDE browser sessions and membership security."""

    def setUp(self):
        """Prepare an isolated test environment."""

        # STEP 1: Create the FastAPI test client.
        self.client = TestClient(app)

        # STEP 2: Define the developer and group.
        self.developer_id = 12
        self.group_id = 5

        # STEP 3: Generate unique sandbox and membership identities.
        self.sandbox_id = uuid4()
        self.membership_generation = uuid4()

        # STEP 4: Track sessions created by this test.
        self.tokens = []

        # STEP 5: Mock Django membership validation.
        # Tests must not make real network requests.
        self.membership_patch = patch(
            "server_runner.ide_gateway_service.main."
            "validate_ide_membership",
            new_callable=AsyncMock,
        )

        self.mock_membership = self.membership_patch.start()

        # By default, Django approves valid membership.
        self.mock_membership.return_value = True

    def tearDown(self):
        """Clean up test sessions and restore dependencies."""

        # Revoke every session created during the test.
        for token in self.tokens:
            ide_session_store.revoke_session(token)

        # Restore the real membership validator.
        self.membership_patch.stop()

        # Close the test client.
        self.client.close()

    def create_test_session(self, sandbox_id=None):
        """Create an IDE session with verified identity values."""

        # Use the default sandbox unless another is supplied.
        if sandbox_id is None:
            sandbox_id = self.sandbox_id

        # Create a session containing all four identities.
        token = ide_session_store.create_session(
            developer_id=self.developer_id,
            group_id=self.group_id,
            sandbox_id=sandbox_id,
            membership_generation=self.membership_generation,
        )

        # Track the token for cleanup.
        self.tokens.append(token)

        return token

    def request_ide(self, token):
        """Request IDE access using a particular session token."""

        return self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": token,
            },
        )

    def test_missing_session_rejected(self):
        """Reject requests without a browser session."""

        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 401)

        # No membership lookup should happen.
        self.mock_membership.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_valid_session_accepted(self, mock_proxy):
        """Allow valid sessions with current group membership."""

        # Create a valid browser session.
        token = self.create_test_session()

        # Simulate the developer IDE responding successfully.
        mock_proxy.return_value = Response(
            content="IDE ready",
            status_code=200,
        )

        # Request access.
        response = self.request_ide(token)

        # Confirm access succeeded.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "IDE ready")

        # Confirm the request reached the IDE.
        mock_proxy.assert_awaited_once()

        # Confirm the correct sandbox was selected.
        self.assertEqual(
            mock_proxy.call_args.kwargs["sandbox_id"],
            self.sandbox_id,
        )

        # Confirm Django validated the correct identities.
        self.mock_membership.assert_awaited_once_with(
            developer_id=self.developer_id,
            group_id=self.group_id,
            membership_generation=self.membership_generation,
        )

    def test_invalid_session_rejected(self):
        """Reject unknown browser session tokens."""

        response = self.request_ide("invalid-token")

        self.assertEqual(response.status_code, 401)

        self.mock_membership.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_expired_session_rejected(self, mock_proxy):
        """Reject sessions that have already expired."""

        # STEP 1: Create a session.
        token = self.create_test_session()

        # STEP 2: Calculate its stored SHA-256 hash.
        token_hash = hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

        # STEP 3: Retrieve the existing session.
        session = ide_session_store.get_session(token)

        self.assertIsNotNone(session)

        # STEP 4: Replace its expiration with a past timestamp.
        with ide_session_store._lock:
            ide_session_store._sessions[token_hash] = replace(
                session,
                expires_at=(
                    datetime.now(timezone.utc)
                    - timedelta(seconds=1)
                ),
            )

        # STEP 5: Attempt access using the expired session.
        response = self.request_ide(token)

        # STEP 6: Confirm access was rejected.
        self.assertEqual(response.status_code, 401)

        # Neither Django nor the IDE should receive the request.
        self.mock_membership.assert_not_awaited()
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_revoked_session_rejected(self, mock_proxy):
        """Reject a browser session after revocation."""

        # Create a valid session.
        token = self.create_test_session()

        # Revoke it.
        ide_session_store.revoke_session(token)

        # Attempt to reuse the revoked token.
        response = self.request_ide(token)

        self.assertEqual(response.status_code, 401)

        self.mock_membership.assert_not_awaited()
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_session_belongs_to_correct_sandbox(self, mock_proxy):
        """Each browser session must use its assigned sandbox."""

        # Create two independent sandboxes.
        first_sandbox = uuid4()
        second_sandbox = uuid4()

        # Create a session for each sandbox.
        first_token = self.create_test_session(
            sandbox_id=first_sandbox,
        )

        second_token = self.create_test_session(
            sandbox_id=second_sandbox,
        )

        # Simulate successful IDE responses.
        mock_proxy.return_value = Response(
            content="IDE ready",
            status_code=200,
        )

        # Access both IDE sessions.
        first_response = self.request_ide(first_token)
        second_response = self.request_ide(second_token)

        # Both requests should succeed.
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)

        # Verify the proxy handled both requests.
        self.assertEqual(mock_proxy.await_count, 2)

        # Inspect the sandbox selected for each request.
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

        # Django should validate both requests.
        self.assertEqual(self.mock_membership.await_count, 2)

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_removed_member_rejected(self, mock_proxy):
        """Removed group members must lose IDE access."""

        # Create a valid browser session.
        token = self.create_test_session()

        # Simulate Django rejecting the membership.
        self.mock_membership.side_effect = (
            MembershipAuthorizationError(
                "Developer is no longer a group member."
            )
        )

        # Attempt IDE access.
        response = self.request_ide(token)

        # The Gateway must deny access.
        self.assertEqual(response.status_code, 403)

        # The IDE must not receive the request.
        mock_proxy.assert_not_awaited()

        # The browser session must be revoked.
        self.assertIsNone(
            ide_session_store.get_session(token)
        )

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_django_unavailable_rejected(self, mock_proxy):
        """Django outages must not grant IDE access."""

        token = self.create_test_session()

        # Simulate a Django connection failure.
        self.mock_membership.side_effect = (
            MembershipServiceUnavailableError(
                "Django membership service unavailable."
            )
        )

        response = self.request_ide(token)

        # Fail closed when Django cannot validate membership.
        self.assertEqual(response.status_code, 503)

        # Do not forward the request to the IDE.
        mock_proxy.assert_not_awaited()

        # A temporary outage should not revoke the session.
        self.assertIsNotNone(
            ide_session_store.get_session(token)
        )

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_revoked_session_cannot_be_reused(self, mock_proxy):
        """A revoked session must remain invalid."""

        token = self.create_test_session()

        # First request: Django rejects membership.
        self.mock_membership.side_effect = (
            MembershipAuthorizationError(
                "Membership revoked."
            )
        )

        first_response = self.request_ide(token)

        self.assertEqual(first_response.status_code, 403)

        # Simulate Django authorizing membership again.
        self.mock_membership.side_effect = None
        self.mock_membership.return_value = True

        # Attempt to reuse the old session.
        second_response = self.request_ide(token)

        # The revoked session must still be invalid.
        self.assertEqual(second_response.status_code, 401)

        # Neither request should reach the IDE.
        mock_proxy.assert_not_awaited()

        # Only the first request should contact Django.
        self.mock_membership.assert_awaited_once()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_membership_generation_passed_to_django(self, mock_proxy):
        """The Gateway must validate the session's generation UUID."""

        token = self.create_test_session()

        mock_proxy.return_value = Response(
            content="IDE ready",
            status_code=200,
        )

        response = self.request_ide(token)

        self.assertEqual(response.status_code, 200)

        # Django must receive the generation stored in the session.
        self.mock_membership.assert_awaited_once_with(
            developer_id=self.developer_id,
            group_id=self.group_id,
            membership_generation=self.membership_generation,
        )


if __name__ == "__main__":
    unittest.main()