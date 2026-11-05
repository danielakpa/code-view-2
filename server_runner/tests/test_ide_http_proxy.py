"""Tests for CodeView IDE HTTP proxy routing."""

import unittest

import os

from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from fastapi.responses import Response

from server_runner.ide_gateway_service.main import app
from server_runner.ide_gateway_service.sessions import (
    ide_session_store,
)


class TestIDEHTTPProxy(unittest.TestCase):
    """Test authenticated IDE HTTP routing."""

    def setUp(self):
        """Prepare an isolated browser session."""

        self.client = TestClient(app)

        # Create a sandbox identity.
        self.sandbox_id = uuid4()

        # Create a valid browser session.
        self.session_token = ide_session_store.create_session(
            developer_id=12,
            group_id=5,
            sandbox_id=self.sandbox_id,
        )

        # Configure the test client's browser cookie.
        self.client.cookies.set(
            "codeview_ide_session",
            self.session_token,
            path="/ide",
        )

        # Configure the trusted IDE Origin for testing.
        self.origin_patch = patch.dict(
            os.environ,
            {
                "CODEVIEW_IDE_PUBLIC_ORIGIN": (
                    "http://127.0.0.1:8002"
                ),
            },
        )

        # Activate the test environment configuration.
        self.origin_patch.start()

    def tearDown(self):
        """Remove the test session."""

        ide_session_store.revoke_session(
            self.session_token
        )

        self.client.close()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_valid_session_reaches_proxy(self, mock_proxy):
        """An authorized request should reach the HTTP proxy."""

        mock_proxy.return_value = Response(
            content="IDE ready",
            status_code=200,
        )

        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "IDE ready")

        # Confirm that the correct sandbox was selected.
        self.assertEqual(
            mock_proxy.call_args.kwargs["sandbox_id"],
            self.sandbox_id,
        )

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

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_missing_session_rejected(self, mock_proxy):
        """Requests without a session must be rejected."""

        # Remove the browser cookie.
        self.client.cookies.clear()

        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 401)
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_invalid_session_rejected(self, mock_proxy):
        """Invalid sessions must not reach the proxy."""

        self.client.cookies.clear()

        self.client.cookies.set(
            "codeview_ide_session",
            "invalid-token",
            path="/ide",
        )

        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 401)
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_session_cannot_select_another_sandbox(
        self,
        mock_proxy,
    ):
        """A browser must not choose another sandbox."""

        other_sandbox = uuid4()

        mock_proxy.return_value = Response(
            content="IDE ready",
            status_code=200,
        )

        # Attempt to supply another sandbox ID.
        response = self.client.get(
            f"/ide/?sandbox_id={other_sandbox}"
        )

        self.assertEqual(response.status_code, 200)

        # The Gateway must use the authenticated sandbox.
        self.assertEqual(
            mock_proxy.call_args.kwargs["sandbox_id"],
            self.sandbox_id,
        )

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )

     
    def test_post_request_forwarded(self, mock_proxy):
        """POST requests should reach the authorized proxy."""

        # Simulate a successful response from the IDE.
        mock_proxy.return_value = Response(
            content="Saved",
            status_code=200,
        )

        # Send a POST request with a trusted Origin.
        response = self.client.post(
            "/ide/api/save",
            content=b"test-data",
            headers={
                "Origin": "http://127.0.0.1:8002",
            },
        )

        # Verify that the request was successful.
        self.assertEqual(response.status_code, 200)

        # Verify that the correct IDE path was forwarded.
        self.assertEqual(
            mock_proxy.call_args.kwargs["path"],
            "api/save",
        )

if __name__ == "__main__":
    unittest.main()
    