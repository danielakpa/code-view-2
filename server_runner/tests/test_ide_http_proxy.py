"""Tests for CodeView IDE HTTP proxy routing."""

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


class TestIDEHTTPProxy(unittest.TestCase):
    """Test authenticated IDE HTTP routing."""

    def setUp(self):
        """Prepare an isolated browser session."""

        # STEP 1: Define the developer and group identities.
        self.developer_id = 12
        self.group_id = 5

        # STEP 2: Generate a unique sandbox identity.
        self.sandbox_id = uuid4()

        # STEP 3: Generate the developer's membership generation.
        self.membership_generation = uuid4()

        # STEP 4: Configure the trusted IDE Origin.
        self.origin = "http://127.0.0.1:8002"

        self.origin_patch = patch.dict(
            os.environ,
            {
                "CODEVIEW_IDE_PUBLIC_ORIGIN": self.origin,
            },
        )

        # Activate the test environment configuration.
        self.origin_patch.start()

        # STEP 5: Create a valid browser session.
        # The session must preserve all four identity values.
        self.session_token = ide_session_store.create_session(
            developer_id=self.developer_id,
            group_id=self.group_id,
            sandbox_id=self.sandbox_id,
            membership_generation=self.membership_generation,
        )

        # STEP 6: Create the browser test client.
        self.client = TestClient(app)

        # STEP 7: Attach the session cookie.
        self.client.cookies.set(
            "codeview_ide_session",
            self.session_token,
            path="/ide",
        )

    def tearDown(self):
        """Remove the test session and restore the environment."""

        # STEP 1: Revoke the browser session.
        ide_session_store.revoke_session(
            self.session_token
        )

        # STEP 2: Close the browser test client.
        self.client.close()

        # STEP 3: Restore the original environment.
        self.origin_patch.stop()

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

        # Confirm the correct sandbox was selected.
        self.assertEqual(
            mock_proxy.call_args.kwargs["sandbox_id"],
            self.sandbox_id,
        )

        # Confirm the proxy was called once.
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

        # Confirm the nested resource path was forwarded.
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

        # The unauthorized request must not reach the IDE.
        mock_proxy.assert_not_awaited()

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_http",
        new_callable=AsyncMock,
    )
    def test_invalid_session_rejected(self, mock_proxy):
        """Invalid sessions must not reach the proxy."""

        # Remove the existing session cookie.
        self.client.cookies.clear()

        # Replace it with an invalid session token.
        self.client.cookies.set(
            "codeview_ide_session",
            "invalid-token",
            path="/ide",
        )

        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 401)

        # The invalid session must not reach the IDE.
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

        # Generate a sandbox belonging to another developer.
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

        # The Gateway must ignore the supplied sandbox ID.
        # It must use the sandbox stored in the session.
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
                "Origin": self.origin,
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