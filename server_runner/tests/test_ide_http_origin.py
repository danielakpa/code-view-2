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
    """Verify IDE session security and live membership authorization."""

    def setUp(self):
        """Prepare a fresh test environment."""

        # STEP 1: Create the FastAPI test client.
        self.client = TestClient(app)

        # STEP 2: Define the developer and group identities.
        self.developer_id = 12
        self.group_id = 5

        # STEP 3: Generate unique sandbox and membership identities.
        self.sandbox_id = uuid4()
        self.membership_generation = uuid4()

        # STEP 4: Track sessions created during this test.
        self.tokens = []

        # STEP 5: Mock Django membership validation.
        # We do not want unit tests making real HTTP requests.
        self.membership_patch = patch(
            "server_runner.ide_gateway_service.main."
            "validate_ide_membership",
            new_callable=AsyncMock,
        )

        self.mock_membership = self.membership_patch.start()

        # By default, Django approves the membership.
        self.mock_membership.return_value = True

    def tearDown(self):
        """Clean up sessions and restore the real validator."""

        # Revoke only sessions created by this test.
        for token in self.tokens:
            ide_session_store.revoke_session(token)

        # Restore the real Django membership validator.
        self.membership_patch.stop()

        # Close the test client.
        self.client.close()

    def create_test_session(self, sandbox_id=None):
        """Create an IDE session with a verified test identity."""

        # Use the default sandbox unless another is supplied.
        if sandbox_id is None:
            sandbox_id = self.sandbox_id

        # Create a session containing all four identity values.
        token = ide_session_store.create_session(
            developer_id=self.developer_id,
            group_id=self.group_id,
            sandbox_id=sandbox_id,
            membership_generation=self.membership_generation,
        )

        # Save the token for cleanup.
        self.tokens.append(token)

        return token

    def test_missing_session_rejected(self):
        """A browser without a session must be rejected."""

        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 401)

        # Django should not be contacted without a valid session.
        self.mock_membership.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_valid_session_accepted(self, mock_proxy):
        """A valid session and membership should authorize IDE access."""

        # STEP 1: Create a valid session.
        token = self.create_test_session()

        # STEP 2: Simulate a successful IDE response.
        mock_proxy.return_value = Response(
            content="IDE ready",
            status_code=200,
        )

        # STEP 3: Request IDE access using the browser session.
        response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": token,
            },
        )

        # STEP 4: Confirm access was allowed.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "IDE ready")

        # STEP 5: Confirm the request reached the correct sandbox.
        mock_proxy.assert_awaited_once()

        self.assertEqual(
            mock_proxy.call_args.kwargs["sandbox_id"],
            self.sandbox_id,
        )

        # STEP 6: Confirm Django received the correct identities.
        self.mock_membership.assert_awaited_once_with(
            developer_id=self.developer_id,
            group_id=self.group_id,
            membership_generation=self.membership_generation,
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

        self.mock_membership.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_expired_session_rejected(self, mock_proxy):
        """An expired session must not authorize access."""

        # STEP 1: Create a valid session.
        token = self.create_test_session()

        # STEP 2: Calculate the stored SHA-256 session hash.
        token_hash = hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

        # STEP 3: Retrieve the existing session.
        session = ide_session_store.get_session(token)

        self.assertIsNotNone(session)

        # STEP 4: Change the session expiration to the past.
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

        # STEP 6: Confirm access was denied.
        self.assertEqual(response.status_code, 401)

        mock_proxy.assert_not_awaited()
        self.mock_membership.assert_not_awaited()

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

        # STEP 3: Attempt access using the revoked token.
        response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": token,
            },
        )

        # STEP 4: Confirm access was denied.
        self.assertEqual(response.status_code, 401)

        mock_proxy.assert_not_awaited()
        self.mock_membership.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_session_belongs_to_correct_sandbox(self, mock_proxy):
        """Each session must forward requests to its own sandbox."""

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

        # STEP 4: Access the first sandbox.
        first_response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": first_token,
            },
        )

        # STEP 5: Access the second sandbox.
        second_response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": second_token,
            },
        )

        # STEP 6: Confirm both requests succeeded.
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)

        # STEP 7: Verify both requests reached the proxy.
        self.assertEqual(mock_proxy.await_count, 2)

        # STEP 8: Verify each request used its own sandbox.
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

        # Django must validate both requests.
        self.assertEqual(self.mock_membership.await_count, 2)

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_removed_member_rejected(self, mock_proxy):
        """A removed group member must lose IDE access."""

        token = self.create_test_session()

        # Simulate Django rejecting the developer's membership.
        self.mock_membership.side_effect = (
            MembershipAuthorizationError(
                "Developer was removed from the group."
            )
        )

        response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": token,
            },
        )

        # The Gateway must deny access.
        self.assertEqual(response.status_code, 403)

        # The request must never reach the sandbox.
        mock_proxy.assert_not_awaited()

        # The session must be revoked.
        self.assertIsNone(
            ide_session_store.get_session(token)
        )

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_django_unavailable_rejected(self, mock_proxy):
        """An unavailable membership service must not grant access."""

        token = self.create_test_session()

        # Simulate a Django connection failure.
        self.mock_membership.side_effect = (
            MembershipServiceUnavailableError(
                "Django membership service unavailable."
            )
        )

        response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": token,
            },
        )

        # Fail closed when membership cannot be verified.
        self.assertEqual(response.status_code, 503)

        # No request should reach the IDE.
        mock_proxy.assert_not_awaited()

        # Temporary outages do not automatically revoke sessions.
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

        # STEP 1: Django rejects the developer.
        self.mock_membership.side_effect = (
            MembershipAuthorizationError(
                "Membership revoked."
            )
        )

        first_response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": token,
            },
        )

        self.assertEqual(first_response.status_code, 403)

        # STEP 2: Simulate Django accepting membership again.
        self.mock_membership.side_effect = None
        self.mock_membership.return_value = True

        # STEP 3: Try the original browser session again.
        second_response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": token,
            },
        )

        # The old session must remain invalid.
        self.assertEqual(second_response.status_code, 401)

        # Neither request may reach the IDE.
        mock_proxy.assert_not_awaited()

        # Django should have been contacted only once.
        self.mock_membership.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()