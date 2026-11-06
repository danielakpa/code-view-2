"""Test session and membership security for the CodeView IDE Gateway."""

import os
import unittest

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


class TestIDESessionSecurity(unittest.TestCase):
    """Verify that IDE sessions cannot be misused."""

    def setUp(self):
        """Create an isolated test environment."""

        # STEP 1: Configure the trusted IDE origin.
        self.origin = "http://127.0.0.1:8002"

        self.environment = patch.dict(
            os.environ,
            {
                "CODEVIEW_IDE_PUBLIC_ORIGIN": self.origin,
            },
        )

        self.environment.start()

        # STEP 2: Define developer and group identities.
        self.developer_id = 12
        self.group_id = 5

        # STEP 3: Generate a unique sandbox identity.
        self.sandbox_id = uuid4()

        # STEP 4: Generate a unique membership generation.
        self.membership_generation = uuid4()

        # STEP 5: Mock Django membership validation.
        # Unit tests should not contact a real Django server.
        self.membership_patch = patch(
            "server_runner.ide_gateway_service.main."
            "validate_ide_membership",
            new_callable=AsyncMock,
        )

        self.mock_membership = self.membership_patch.start()

        # By default, Django approves the developer's membership.
        self.mock_membership.return_value = True

        # STEP 6: Create a valid browser session.
        self.session_token = ide_session_store.create_session(
            developer_id=self.developer_id,
            group_id=self.group_id,
            sandbox_id=self.sandbox_id,
            membership_generation=self.membership_generation,
        )

        # STEP 7: Create the FastAPI test client.
        self.client = TestClient(app)

        # STEP 8: Attach the session cookie.
        self.client.cookies.set(
            "codeview_ide_session",
            self.session_token,
            path="/ide",
        )

    def tearDown(self):
        """Clean up the test environment."""

        # Revoke the browser session.
        ide_session_store.revoke_session(
            self.session_token
        )

        # Close the test client.
        self.client.close()

        # Restore the real membership validator.
        self.membership_patch.stop()

        # Restore environment variables.
        self.environment.stop()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_valid_session_allowed(self, mock_proxy):
        """A valid session and membership should access the IDE."""

        mock_proxy.return_value = Response(
            content="IDE ready",
            status_code=200,
        )

        response = self.client.get("/ide/")

        # Confirm successful access.
        self.assertEqual(response.status_code, 200)

        # Confirm the request reached the IDE.
        mock_proxy.assert_awaited_once()

        # Confirm Django validated the correct identities.
        self.mock_membership.assert_awaited_once_with(
            developer_id=self.developer_id,
            group_id=self.group_id,
            membership_generation=self.membership_generation,
        )

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_revoked_session_rejected(self, mock_proxy):
        """A revoked session must not access the IDE."""

        # Revoke the developer's browser session.
        ide_session_store.revoke_session(
            self.session_token
        )

        # Attempt to access the IDE.
        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 401)

        # Neither Django nor the IDE should receive the request.
        self.mock_membership.assert_not_awaited()
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_invalid_session_rejected(self, mock_proxy):
        """A fake session token must be rejected."""

        # Remove the valid cookie first.
        self.client.cookies.clear()

        # Replace it with an invalid session token.
        self.client.cookies.set(
            "codeview_ide_session",
            "invalid-session-token",
            path="/ide",
        )

        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 401)

        self.mock_membership.assert_not_awaited()
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_missing_session_rejected(self, mock_proxy):
        """A request without a browser session must be rejected."""

        # Remove all browser cookies.
        self.client.cookies.clear()

        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 401)

        self.mock_membership.assert_not_awaited()
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_session_uses_correct_sandbox(self, mock_proxy):
        """A session must connect only to its assigned sandbox."""

        mock_proxy.return_value = Response(
            content="IDE ready",
            status_code=200,
        )

        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 200)

        # The Gateway must use the sandbox stored in the session.
        self.assertEqual(
            mock_proxy.call_args.kwargs["sandbox_id"],
            self.sandbox_id,
        )

        # Django must validate membership before proxy access.
        self.mock_membership.assert_awaited_once()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_revoked_session_cannot_post(self, mock_proxy):
        """A revoked session cannot modify IDE files."""

        # Revoke the developer's session.
        ide_session_store.revoke_session(
            self.session_token
        )

        # Attempt to modify IDE files.
        response = self.client.post(
            "/ide/api/save",
            headers={
                "Origin": self.origin,
            },
            content=b"print('Hello CodeView')",
        )

        self.assertEqual(response.status_code, 401)

        self.mock_membership.assert_not_awaited()
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_group_revocation_blocks_ide_access(self, mock_proxy):
        """Local group-session revocation must block IDE access."""

        # STEP 1: Revoke all sessions for this developer and group.
        removed = ide_session_store.revoke_developer_group_sessions(
            developer_id=self.developer_id,
            group_id=self.group_id,
        )

        # STEP 2: Confirm the session was removed.
        self.assertEqual(removed, 1)

        # STEP 3: Attempt access with the old browser cookie.
        response = self.client.get("/ide/")

        # STEP 4: Confirm the Gateway rejected the request.
        self.assertEqual(response.status_code, 401)

        # STEP 5: No membership check or proxy request is needed.
        self.mock_membership.assert_not_awaited()
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_removed_group_member_rejected(self, mock_proxy):
        """Django must prevent removed members from accessing IDEs."""

        # Simulate Django rejecting the developer's membership.
        self.mock_membership.side_effect = (
            MembershipAuthorizationError(
                "Developer is no longer a group member."
            )
        )

        response = self.client.get("/ide/")

        # Removed developers must receive HTTP 403.
        self.assertEqual(response.status_code, 403)

        # The IDE must never receive the request.
        mock_proxy.assert_not_awaited()

        # The Gateway must revoke the browser session.
        self.assertIsNone(
            ide_session_store.get_session(
                self.session_token
            )
        )

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_revoked_membership_session_cannot_be_reused(
        self,
        mock_proxy,
    ):
        """A session revoked by Django must remain invalid."""

        # STEP 1: Django rejects the developer's membership.
        self.mock_membership.side_effect = (
            MembershipAuthorizationError(
                "Membership revoked."
            )
        )

        first_response = self.client.get("/ide/")

        self.assertEqual(first_response.status_code, 403)

        # STEP 2: Simulate Django accepting membership again.
        self.mock_membership.side_effect = None
        self.mock_membership.return_value = True

        # STEP 3: Attempt to reuse the old browser session.
        second_response = self.client.get("/ide/")

        # The revoked session must remain invalid.
        self.assertEqual(second_response.status_code, 401)

        # Neither request should reach the IDE.
        mock_proxy.assert_not_awaited()

        # Only the first request should contact Django.
        self.mock_membership.assert_awaited_once()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_django_membership_service_unavailable(self, mock_proxy):
        """Django outages must not grant IDE access."""

        # Simulate the Django membership service being unavailable.
        self.mock_membership.side_effect = (
            MembershipServiceUnavailableError(
                "Django membership service unavailable."
            )
        )

        response = self.client.get("/ide/")

        # The Gateway must fail closed.
        self.assertEqual(response.status_code, 503)

        # No request may reach the IDE.
        mock_proxy.assert_not_awaited()

        # A temporary outage should not automatically revoke
        # an otherwise valid browser session.
        self.assertIsNotNone(
            ide_session_store.get_session(
                self.session_token
            )
        )

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_old_membership_generation_rejected(self, mock_proxy):
        """Old membership generations must not authorize IDE access."""

        # Simulate Django rejecting an outdated generation UUID.
        self.mock_membership.side_effect = (
            MembershipAuthorizationError(
                "Membership generation no longer matches."
            )
        )

        response = self.client.get("/ide/")

        # The outdated membership must be rejected.
        self.assertEqual(response.status_code, 403)

        # The Gateway must revoke the old session.
        self.assertIsNone(
            ide_session_store.get_session(
                self.session_token
            )
        )

        # The IDE must not receive the request.
        mock_proxy.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()