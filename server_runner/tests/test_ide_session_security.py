"""Test session security for the CodeView IDE Gateway."""

import os
import unittest

from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.responses import Response
from fastapi.testclient import TestClient

from server_runner.ide_gateway_service.main import app
from server_runner.ide_gateway_service.sessions import (
    ide_session_store,
)


class TestIDESessionSecurity(unittest.TestCase):
    """Verify that IDE sessions cannot be misused."""

    def setUp(self):
        """Create a test environment and an authenticated session."""

        # STEP 1: Configure the trusted IDE origin.
        self.origin = "http://127.0.0.1:8002"

        self.environment = patch.dict(
            os.environ,
            {"CODEVIEW_IDE_PUBLIC_ORIGIN": self.origin},
        )
        self.environment.start()

        # STEP 2: Define the developer and group identities.
        self.developer_id = 12
        self.group_id = 5

        # STEP 3: Generate a sandbox UUID.
        self.sandbox_id = uuid4()

        # STEP 4: Create a browser session with all three identities.
        self.session_token = ide_session_store.create_session(
            developer_id=self.developer_id,
            group_id=self.group_id,
            sandbox_id=self.sandbox_id,
        )

        # STEP 5: Create a browser test client.
        self.client = TestClient(app)

        # STEP 6: Attach the session cookie.
        self.client.cookies.set(
            "codeview_ide_session",
            self.session_token,
            path="/ide",
        )

    def tearDown(self):
        """Remove test sessions and restore the environment."""

        ide_session_store.revoke_session(self.session_token)

        self.client.close()
        self.environment.stop()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_valid_session_allowed(self, mock_proxy):
        """A valid session should access its IDE."""

        mock_proxy.return_value = Response(
            content="IDE ready",
            status_code=200,
        )

        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 200)
        mock_proxy.assert_awaited_once()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_revoked_session_rejected(self, mock_proxy):
        """A revoked session must not access the IDE."""

        # Revoke the developer's session.
        ide_session_store.revoke_session(
            self.session_token
        )

        # Attempt to access the IDE using the revoked cookie.
        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 401)
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_invalid_session_rejected(self, mock_proxy):
        """A fake session token must be rejected."""

        # Replace the valid cookie with a fake token.
        self.client.cookies.set(
            "codeview_ide_session",
            "invalid-session-token",
            path="/ide",
        )

        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 401)
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_missing_session_rejected(self, mock_proxy):
        """A request without a session must be rejected."""

        # Remove all browser cookies.
        self.client.cookies.clear()

        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 401)
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

        # Confirm the Gateway uses the authorized sandbox.
        self.assertEqual(
            mock_proxy.call_args.kwargs["sandbox_id"],
            self.sandbox_id,
        )

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
            headers={"Origin": self.origin},
            content=b"print('Hello CodeView')",
        )

        self.assertEqual(response.status_code, 401)
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_group_revocation_blocks_ide_access(self, mock_proxy):
        """
        Revoking a developer's group sessions must block IDE access.

        This tests the Gateway's revocation mechanism.
        """

        # STEP 1: Revoke all sessions for this developer in Group 5.
        removed = ide_session_store.revoke_developer_group_sessions(
            developer_id=self.developer_id,
            group_id=self.group_id,
        )

        # STEP 2: Confirm the session was removed.
        self.assertEqual(removed, 1)

        # STEP 3: Attempt to access the IDE using the old cookie.
        response = self.client.get("/ide/")

        # STEP 4: Verify access is denied.
        self.assertEqual(response.status_code, 401)

        # STEP 5: Confirm the request never reaches the IDE.
        mock_proxy.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()