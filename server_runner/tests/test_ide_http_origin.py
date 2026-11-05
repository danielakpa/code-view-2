"""Test HTTP Origin protection for the CodeView IDE Gateway."""

import os
import unittest

from unittest.mock import patch, AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient
from fastapi.responses import Response

from server_runner.ide_gateway_service.main import app
from server_runner.ide_gateway_service.sessions import (
    ide_session_store,
)


class TestIDEHTTPOrigin(unittest.TestCase):
    """Verify that only trusted HTTP requests reach the IDE."""

    def setUp(self):
        """Prepare an authenticated browser session."""

        # Define the trusted browser origin.
        self.origin = "http://127.0.0.1:8002"

        # Configure the Gateway's trusted origin.
        self.environment = patch.dict(
            os.environ,
            {
                "CODEVIEW_IDE_PUBLIC_ORIGIN": self.origin,
            },
        )
        self.environment.start()

        # Create a test sandbox and browser session.
        self.sandbox_id = uuid4()
        self.session_token = ide_session_store.create_session(
            developer_id=12,
            group_id=5,
            sandbox_id=self.sandbox_id,
        )

        # Create an authenticated test client.
        self.client = TestClient(app)

        self.client.cookies.set(
            "codeview_ide_session",
            self.session_token,
            path="/ide",
        )

    def tearDown(self):
        """Clean up after each test."""

        ide_session_store.revoke_session(
            self.session_token
        )

        self.client.close()
        self.environment.stop()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_trusted_post_allowed(self, mock_proxy):
        """A POST request from the trusted origin is allowed."""

        mock_proxy.return_value = Response(
            content="Saved",
            status_code=200,
        )

        response = self.client.post(
            "/ide/api/save",
            headers={"Origin": self.origin},
            content=b"test-data",
        )

        self.assertEqual(response.status_code, 200)
        mock_proxy.assert_awaited_once()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_untrusted_post_rejected(self, mock_proxy):
        """A POST request from another website is rejected."""

        response = self.client.post(
            "/ide/api/save",
            headers={
                "Origin": "https://malicious.example.com",
            },
            content=b"test-data",
        )

        self.assertEqual(response.status_code, 403)
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_post_without_origin_rejected(self, mock_proxy):
        """A POST request without an Origin is rejected."""

        response = self.client.post(
            "/ide/api/save",
            content=b"test-data",
        )

        self.assertEqual(response.status_code, 403)
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_get_without_origin_allowed(self, mock_proxy):
        """An authenticated GET without Origin is allowed."""

        mock_proxy.return_value = Response(
            content="IDE page",
            status_code=200,
        )

        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 200)
        mock_proxy.assert_awaited_once()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_untrusted_get_rejected(self, mock_proxy):
        """An untrusted Origin is rejected even for GET."""

        response = self.client.get(
            "/ide/",
            headers={
                "Origin": "https://malicious.example.com",
            },
        )

        self.assertEqual(response.status_code, 403)
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_missing_session_rejected(self, mock_proxy):
        """An unauthenticated request cannot access the IDE."""

        # Remove the authenticated session cookie.
        self.client.cookies.clear()

        response = self.client.post(
            "/ide/api/save",
            headers={"Origin": self.origin},
            content=b"test-data",
        )

        self.assertEqual(response.status_code, 401)
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_trusted_delete_allowed(self, mock_proxy):
        """An authenticated DELETE with trusted Origin is allowed."""

        mock_proxy.return_value = Response(status_code=204)

        response = self.client.delete(
            "/ide/api/file",
            headers={"Origin": self.origin},
        )

        self.assertEqual(response.status_code, 204)
        mock_proxy.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()