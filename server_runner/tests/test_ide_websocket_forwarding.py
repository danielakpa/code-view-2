"""Tests for CodeView IDE WebSocket message forwarding."""

import asyncio
import unittest

from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from server_runner.ide_gateway_service.websocket_proxy import (
    proxy_ide_websocket,
)

from server_runner.ide_gateway_service.sessions import (
    ide_session_store,
)


class FakeIDEWebSocket:
    """Simulate the WebSocket connection to VS Code."""

    def __init__(self):
        # Store messages received from the browser.
        self.received_messages = []

        # Queue messages that will be sent to the browser.
        self.outgoing_messages = asyncio.Queue()

    async def send(self, message):
        """Receive a message forwarded from the browser."""

        # Save the message for later verification.
        self.received_messages.append(message)

        # Simulate VS Code responding to binary data.
        if isinstance(message, bytes):
            await self.outgoing_messages.put(
                b"IDE received binary"
            )

        # Simulate VS Code responding to text.
        else:
            await self.outgoing_messages.put(
                "IDE received: " + message
            )

    def __aiter__(self):
        """Allow asynchronous iteration over IDE messages."""

        return self

    async def __anext__(self):
        """Wait for the next simulated IDE message."""

        message = await self.outgoing_messages.get()

        if message is None:
            raise StopAsyncIteration

        return message


class FakeIDEConnection:
    """Simulate the WebSocket connection context manager."""

    def __init__(self, fake_ide):
        self.fake_ide = fake_ide

    async def __aenter__(self):
        """Return the simulated IDE connection."""

        return self.fake_ide

    async def __aexit__(self, exc_type, exc_value, traceback):
        """Close the simulated IDE connection."""

        await self.fake_ide.outgoing_messages.put(None)


class TestIDEWebSocketForwarding(unittest.TestCase):
    """Test communication between the browser and IDE."""

    def setUp(self):
        """Prepare an isolated WebSocket test application."""

        # 1. Create a unique test sandbox.
        self.sandbox_id = uuid4()

        # 2. Create a valid session for this sandbox.
        self.session_token = ide_session_store.create_session(
            self.sandbox_id
        )

        # 3. Create a fake IDE.
        self.fake_ide = FakeIDEWebSocket()

        # 4. Create a small FastAPI application.
        self.app = FastAPI()

        sandbox_id = self.sandbox_id

        @self.app.websocket("/test-ide/{path:path}")
        async def test_ide_websocket(
            websocket: WebSocket,
            path: str,
        ):
            """Forward test browser messages to the fake IDE."""

            # Read the session token from the browser cookie.
            session_token = websocket.cookies.get(
                "codeview_ide_session"
            )

            # Call the real WebSocket proxy.
            await proxy_ide_websocket(
                websocket=websocket,
                sandbox_id=sandbox_id,
                path=path,
                session_token=session_token,
            )

        # 5. Replace filesystem checks with test values.
        self.socket_path_patch = patch(
            "server_runner.ide_gateway_service.websocket_proxy."
            "get_ide_socket_path",
            return_value="/tmp/test-ide.sock",
        )

        self.validate_patch = patch(
            "server_runner.ide_gateway_service.websocket_proxy."
            "validate_ide_socket",
            return_value=None,
        )

        # 6. Replace the real Unix socket connection.
        self.connection_patch = patch(
            "server_runner.ide_gateway_service.websocket_proxy."
            "unix_connect",
            return_value=FakeIDEConnection(self.fake_ide),
        )

        # Start all three mocks.
        self.socket_path_patch.start()
        self.validate_patch.start()

        # Save the connection mock so we can inspect its calls.
        self.mock_connection = self.connection_patch.start()

        # 7. Create the browser test client.
        self.client = TestClient(self.app)

        # 8. Attach the valid IDE session cookie.
        self.client.cookies.set(
            "codeview_ide_session",
            self.session_token,
            path="/test-ide",
        )

    def tearDown(self):
        """Remove test resources."""

        # Close the browser test client.
        self.client.close()

        # Revoke the session created for this test.
        ide_session_store.revoke_session(
            self.session_token
        )

        # Restore the original functions.
        self.connection_patch.stop()
        self.validate_patch.stop()
        self.socket_path_patch.stop()

    def test_browser_text_reaches_ide(self):
        """Text messages should reach the IDE."""

        with self.client.websocket_connect(
            "/test-ide/editor"
        ) as websocket:

            # Send a message from the browser.
            websocket.send_text("Hello VS Code")

            # Receive the simulated IDE response.
            response = websocket.receive_text()

            # Verify the response.
            self.assertEqual(
                response,
                "IDE received: Hello VS Code",
            )

        # Verify that the IDE received the original message.
        self.assertIn(
            "Hello VS Code",
            self.fake_ide.received_messages,
        )

    def test_browser_binary_reaches_ide(self):
        """Binary messages should reach the IDE."""

        with self.client.websocket_connect(
            "/test-ide/editor"
        ) as websocket:

            # Send binary data from the browser.
            websocket.send_bytes(b"binary-data")

            # Receive the simulated IDE response.
            response = websocket.receive_bytes()

            # Verify the response.
            self.assertEqual(
                response,
                b"IDE received binary",
            )

        # Verify that the IDE received the binary data.
        self.assertIn(
            b"binary-data",
            self.fake_ide.received_messages,
        )

    def test_upstream_path_forwarded(self):
        """The proxy should connect to the requested IDE path."""

        with self.client.websocket_connect(
            "/test-ide/editor?workspace=123"
        ) as websocket:

            # Send a message to establish communication.
            websocket.send_text("test")

            # Receive the IDE response.
            response = websocket.receive_text()

            self.assertEqual(
                response,
                "IDE received: test",
            )

        # Verify that the Unix socket connection was called.
        self.mock_connection.assert_called_once()

        # Get the arguments passed to unix_connect().
        connection_arguments = (
            self.mock_connection.call_args.kwargs
        )

        # Verify that the correct IDE resource path was used.
        self.assertEqual(
            connection_arguments["uri"],
            "ws://localhost/editor?workspace=123",
        )

        # Verify that the correct Unix socket was selected.
        self.assertEqual(
            connection_arguments["path"],
            "/tmp/test-ide.sock",
        )

    def test_missing_session_rejected(self):
        """The proxy must reject connections without a session."""

        # Remove the browser's session cookie.
        self.client.cookies.clear()

        # Attempt to connect without authentication.
        from starlette.websockets import WebSocketDisconnect

        with self.assertRaises(WebSocketDisconnect) as context:
            with self.client.websocket_connect(
                "/test-ide/editor"
            ):
                pass

        # The proxy should reject the connection.
        self.assertEqual(
            context.exception.code,
            1008,
        )

        # The fake IDE must never be contacted.
        self.mock_connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()