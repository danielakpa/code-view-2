"""Tests for CodeView IDE HTTP proxy routing and membership security."""

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


class TestIDEHTTPProxy(unittest.TestCase):
    """Test authenticated IDE HTTP routing and membership revocation."""

    def setUp(self):
        """Prepare an isolated developer session."""

        # STEP 1: Define the developer and group identities.
        self.developer_id = 12
        self.group_id = 5

        # STEP 2: Generate unique sandbox and membership identities.
        self.sandbox_id = uuid4()
        self.membership_generation = uuid4()

        # STEP 3: Configure the trusted browser origin.
        self.origin = "http://127.0.0.1:8002"

        self.origin_patch = patch.dict(
            os.environ,
            {
                "CODEVIEW_IDE_PUBLIC_ORIGIN": self.origin,
            },
        )

        self.origin_patch.start()

        # STEP 4: Mock Django's membership-validation client.
        # This prevents tests from making real network requests.
        self.membership_patch = patch(
            "server_runner.ide_gateway_service.main."
            "validate_ide_membership",
            new_callable=AsyncMock,
        )

        self.mock_membership = self.membership_patch.start()

        # By default, Django confirms valid membership.
        self.mock_membership.return_value = True

        # STEP 5: Create a browser IDE session.
        self.session_token = ide_session_store.create_session(
            developer_id=self.developer_id,
            group_id=self.group_id,
            sandbox_id=self.sandbox_id,
            membership_generation=self.membership_generation,
        )

        # STEP 6: Create the FastAPI test client.
        self.client = TestClient(app)

        # STEP 7: Attach the developer's session cookie.
        self.client.cookies.set(
            "codeview_ide_session",
            self.session_token,
            path="/ide",
        )

    def tearDown(self):
        """Clean up the test session and patches."""

        # Revoke the test session.
        ide_session_store.revoke_session(
            self.session_token
        )

        # Close the test client.
        self.client.close()

        # Restore the real membership validator.
        self.membership_patch.stop()

        # Restore environment variables.
        self.origin_patch.stop()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_valid_session_reaches_proxy(self, mock_proxy):
        """A valid session and membership should reach the IDE."""

        mock_proxy.return_value = Response(
            content="IDE ready",
            status_code=200,
        )

        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "IDE ready")

        # Confirm the Gateway selected the correct sandbox.
        self.assertEqual(
            mock_proxy.call_args.kwargs["sandbox_id"],
            self.sandbox_id,
        )

        # Confirm Django checked the developer's membership.
        self.mock_membership.assert_awaited_once_with(
            developer_id=self.developer_id,
            group_id=self.group_id,
            membership_generation=self.membership_generation,
        )

        mock_proxy.assert_awaited_once()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_nested_path_forwarded(self, mock_proxy):
        """IDE resource paths should reach the proxy."""

        mock_proxy.return_value = Response(
            content="JavaScript",
            status_code=200,
        )

        response = self.client.get(
            "/ide/static/app.js"
        )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            mock_proxy.call_args.kwargs["path"],
            "static/app.js",
        )

        self.mock_membership.assert_awaited_once()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_missing_session_rejected(self, mock_proxy):
        """Requests without a browser session must be rejected."""

        self.client.cookies.clear()

        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 401)

        # No membership lookup is needed without a valid session.
        self.mock_membership.assert_not_awaited()

        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_invalid_session_rejected(self, mock_proxy):
        """Invalid browser sessions must not reach the IDE."""

        self.client.cookies.clear()

        self.client.cookies.set(
            "codeview_ide_session",
            "invalid-token",
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
    def test_session_cannot_select_another_sandbox(
        self,
        mock_proxy,
    ):
        """A browser cannot choose another developer's sandbox."""

        other_sandbox = uuid4()

        mock_proxy.return_value = Response(
            content="IDE ready",
            status_code=200,
        )

        # Attempt to override the sandbox through a query parameter.
        response = self.client.get(
            f"/ide/?sandbox_id={other_sandbox}"
        )

        self.assertEqual(response.status_code, 200)

        # The Gateway must use the sandbox stored in the session.
        self.assertEqual(
            mock_proxy.call_args.kwargs["sandbox_id"],
            self.sandbox_id,
        )

        self.mock_membership.assert_awaited_once()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_post_request_forwarded(self, mock_proxy):
        """Authorized POST requests should reach the IDE."""

        mock_proxy.return_value = Response(
            content="Saved",
            status_code=200,
        )

        response = self.client.post(
            "/ide/api/save",
            content=b"test-data",
            headers={
                "Origin": self.origin,
            },
        )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            mock_proxy.call_args.kwargs["path"],
            "api/save",
        )

        self.mock_membership.assert_awaited_once()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_removed_member_rejected(self, mock_proxy):
        """Removed developers must lose IDE HTTP access."""

        # Simulate Django reporting that membership was removed.
        self.mock_membership.side_effect = (
            MembershipAuthorizationError(
                "Developer is no longer a group member."
            )
        )

        response = self.client.get("/ide/")

        # The Gateway must reject the developer.
        self.assertEqual(response.status_code, 403)

        # No request may reach the IDE.
        mock_proxy.assert_not_awaited()

        # The revoked session must no longer exist.
        self.assertIsNone(
            ide_session_store.get_session(
                self.session_token
            )
        )

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_revoked_session_cannot_be_reused(self, mock_proxy):
        """A revoked browser session cannot regain access."""

        # First request: Django reports removed membership.
        self.mock_membership.side_effect = (
            MembershipAuthorizationError(
                "Membership revoked."
            )
        )

        first_response = self.client.get("/ide/")

        self.assertEqual(first_response.status_code, 403)

        # Simulate Django becoming available again.
        self.mock_membership.side_effect = None
        self.mock_membership.return_value = True

        # Second request: the original session is already revoked.
        second_response = self.client.get("/ide/")

        self.assertEqual(second_response.status_code, 401)

        # Neither request may reach the IDE.
        mock_proxy.assert_not_awaited()

        # Only the first request should contact Django.
        self.mock_membership.assert_awaited_once()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_django_unavailable_rejected(self, mock_proxy):
        """Django outages must not grant IDE access."""

        # Simulate a Django service outage.
        self.mock_membership.side_effect = (
            MembershipServiceUnavailableError(
                "Django membership service unavailable."
            )
        )

        response = self.client.get("/ide/")

        # Fail closed when membership cannot be verified.
        self.assertEqual(response.status_code, 503)

        # The IDE must not receive the request.
        mock_proxy.assert_not_awaited()

        # A temporary outage should not automatically revoke
        # an otherwise valid session.
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
        """Sessions from previous group memberships must be denied."""

        # Django rejects the generation stored in the old session.
        self.mock_membership.side_effect = (
            MembershipAuthorizationError(
                "Membership generation no longer matches."
            )
        )

        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 403)

        # The old session must be revoked.
        self.assertIsNone(
            ide_session_store.get_session(
                self.session_token
            )
        )

        mock_proxy.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()