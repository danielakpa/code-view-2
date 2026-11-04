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

        # Configure the trusted IDE origin.
        self.origin = "http://127.0.0.1:8002"

        self.environment = patch.dict(
            os.environ,
            {"CODEVIEW_IDE_PUBLIC_ORIGIN": self.origin},
        )
        self.environment.start()

        # Create a sandbox and a session belonging to it.
        self.sandbox_id = uuid4()

        self.session_token = ide_session_store.create_session(
            self.sandbox_id
        )

        # Create a browser client.
        self.client = TestClient(app)

        # Attach the session cookie.
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
        ide_session_store.revoke_session(self.session_token)

        # Try to access the IDE using the revoked cookie.
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

        # Verify that the Gateway selected the sandbox
        # associated with the authenticated session.
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
        ide_session_store.revoke_session(self.session_token)

        response = self.client.post(
            "/ide/api/save",
            headers={"Origin": self.origin},
            content=b"print('Hello CodeView')",
        )

        self.assertEqual(response.status_code, 401)
        mock_proxy.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()