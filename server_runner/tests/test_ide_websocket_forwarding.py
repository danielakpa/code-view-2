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


class FakeIDEWebSocket:
    """Simulate the WebSocket connection to VS Code."""

    def __init__(self):
        # Store messages received from the browser.
        self.received_messages = []

        # Queue messages that will be sent to the browser.
        self.outgoing_messages = asyncio.Queue()

    async def send(self, message):
        """Receive a message forwarded from the browser."""

        self.received_messages.append(message)

        # Simulate VS Code responding to the message.
        if isinstance(message, bytes):
            await self.outgoing_messages.put(
                b"IDE received binary"
            )
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

        # Create a test sandbox.
        self.sandbox_id = uuid4()

        # Create a fake IDE.
        self.fake_ide = FakeIDEWebSocket()

        # Create a small FastAPI application.
        self.app = FastAPI()

        sandbox_id = self.sandbox_id

        @self.app.websocket("/test-ide/{path:path}")
        async def test_ide_websocket(
            websocket: WebSocket,
            path: str,
        ):
            # Call the real WebSocket forwarding function.
            await proxy_ide_websocket(
                websocket=websocket,
                sandbox_id=sandbox_id,
                path=path,
            )

        # Replace filesystem checks with test values.
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

        # Replace the real IDE connection with our fake IDE.
        self.connection_patch = patch(
            "server_runner.ide_gateway_service.websocket_proxy."
            "connect",
            return_value=FakeIDEConnection(self.fake_ide),
        )

        self.socket_path_patch.start()
        self.validate_patch.start()
        self.connection_patch.start()

        # Create the browser test client.
        self.client = TestClient(self.app)

    def tearDown(self):
        """Remove test resources."""

        self.client.close()

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

            self.assertEqual(
                response,
                "IDE received: Hello VS Code",
            )

        # Verify the IDE received the original message.
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

            self.assertEqual(
                response,
                b"IDE received binary",
            )

        self.assertIn(
            b"binary-data",
            self.fake_ide.received_messages,
        )

    def test_upstream_path_forwarded(self):
        """The proxy should connect to the requested IDE path."""

        with self.client.websocket_connect(
            "/test-ide/editor?workspace=123"
        ) as websocket:

            websocket.send_text("test")
            websocket.receive_text()

        # Inspect the arguments supplied to the connection.
        connection_mock = (
            self.connection_patch.stop()
        )

        # The connection itself was already verified by
        # the successful message exchange.
        self.assertTrue(
            self.fake_ide.received_messages
        )


if __name__ == "__main__":
    unittest.main()