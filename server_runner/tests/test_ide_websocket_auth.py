"""Tests for CodeView IDE WebSocket authentication."""

import os
import unittest

from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server_runner.ide_gateway_service.main import (
    app,
    is_allowed_websocket_origin,
)

from server_runner.ide_gateway_service.sessions import (
    ide_session_store,
)


class TestIDEWebSocketAuth(unittest.TestCase):
    """Verify WebSocket authentication and sandbox isolation."""

    def setUp(self):
        """Prepare an isolated test environment."""

        # Configure the trusted IDE browser origin.
        self.environment = patch.dict(
            os.environ,
            {
                "CODEVIEW_IDE_PUBLIC_ORIGIN": (
                    "http://127.0.0.1:8002"
                ),
            },
        )

        self.environment.start()

        # Create a FastAPI test client.
        self.client = TestClient(app)

        # Create a test sandbox.
        self.sandbox_id = uuid4()

        # Create a valid IDE browser session.
        self.session_token = ide_session_store.create_session(
            self.sandbox_id
        )

        # Define the trusted Origin.
        self.origin = "http://127.0.0.1:8002"

    def tearDown(self):
        """Clean up the test environment."""

        # Revoke the test session.
        ide_session_store.revoke_session(
            self.session_token
        )

        # Close the test client.
        self.client.close()

        # Restore environment variables.
        self.environment.stop()

    def test_missing_session_rejected(self):
        """Reject WebSocket connections without a session."""

        with self.assertRaises(WebSocketDisconnect) as error:
            with self.client.websocket_connect(
                "/ide/",
                headers={
                    "Origin": self.origin,
                },
            ):
                pass

        self.assertEqual(error.exception.code, 1008)

    def test_invalid_session_rejected(self):
        """Reject unknown browser session tokens."""

        with self.assertRaises(WebSocketDisconnect) as error:
            with self.client.websocket_connect(
                "/ide/",
                headers={
                    "Origin": self.origin,
                    "Cookie": (
                        "codeview_ide_session=invalid-token"
                    ),
                },
            ):
                pass

        self.assertEqual(error.exception.code, 1008)

    def test_untrusted_origin_rejected(self):
        """Reject connections from untrusted websites."""

        with self.assertRaises(WebSocketDisconnect) as error:
            with self.client.websocket_connect(
                "/ide/",
                headers={
                    "Origin": "https://untrusted.example.com",
                    "Cookie": (
                        f"codeview_ide_session={self.session_token}"
                    ),
                },
            ):
                pass

        self.assertEqual(error.exception.code, 1008)

    def test_missing_origin_rejected(self):
        """Reject connections without an Origin header."""

        with self.assertRaises(WebSocketDisconnect) as error:
            with self.client.websocket_connect(
                "/ide/",
                headers={
                    "Cookie": (
                        f"codeview_ide_session={self.session_token}"
                    ),
                },
            ):
                pass

        self.assertEqual(error.exception.code, 1008)

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_websocket",
        new_callable=AsyncMock,
    )
    def test_valid_session_reaches_proxy(self, mock_proxy):
        """Valid authentication must reach the WebSocket proxy."""

        # Simulate a successful WebSocket handshake.
        async def accept_websocket(*args, **kwargs):
            websocket = kwargs["websocket"]
            await websocket.accept()

        # Make the mocked proxy accept the connection.
        mock_proxy.side_effect = accept_websocket

        # Establish an authorized WebSocket connection.
        with self.client.websocket_connect(
            "/ide/",
            headers={
                "Origin": self.origin,
                "Cookie": (
                    f"codeview_ide_session={self.session_token}"
                ),
            },
        ):
            pass

        # Verify the Gateway called the proxy.
        mock_proxy.assert_awaited_once()

        # Verify that the correct sandbox was selected.
        self.assertEqual(
            mock_proxy.call_args.kwargs["sandbox_id"],
            self.sandbox_id,
        )

    @patch(
        "server_runner.ide_gateway_service.main.proxy_ide_websocket",
        new_callable=AsyncMock,
    )
    def test_browser_cannot_select_another_sandbox(
        self,
        mock_proxy,
    ):
        """The browser cannot override its authorized sandbox."""

        # Simulate a successful WebSocket handshake.
        async def accept_websocket(*args, **kwargs):
            websocket = kwargs["websocket"]
            await websocket.accept()

        # Make the mocked proxy accept the connection.
        mock_proxy.side_effect = accept_websocket

        # Generate another developer's sandbox identity.
        other_sandbox = uuid4()

        # Attempt to select another sandbox through the URL.
        with self.client.websocket_connect(
            f"/ide/?sandbox_id={other_sandbox}",
            headers={
                "Origin": self.origin,
                "Cookie": (
                    f"codeview_ide_session={self.session_token}"
                ),
            },
        ):
            pass

        # Verify the proxy was called.
        mock_proxy.assert_awaited_once()

        # The Gateway must use the authenticated sandbox,
        # not the sandbox ID supplied in the URL.
        self.assertEqual(
            mock_proxy.call_args.kwargs["sandbox_id"],
            self.sandbox_id,
        )

    def test_origin_validation(self):
        """Only the configured browser Origin should be accepted."""

        # Trusted Origin should be accepted.
        self.assertTrue(
            is_allowed_websocket_origin(
                "http://127.0.0.1:8002"
            )
        )

        # Untrusted Origin should be rejected.
        self.assertFalse(
            is_allowed_websocket_origin(
                "https://untrusted.example.com"
            )
        )

        # Missing Origin should be rejected.
        self.assertFalse(
            is_allowed_websocket_origin(None)
        )


if __name__ == "__main__":
    unittest.main()