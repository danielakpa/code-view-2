"""Test WebSocket connection cleanup in CodeView."""

import asyncio
import unittest

from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from server_runner.ide_gateway_service.sessions import (
    ide_session_store,
)

from server_runner.ide_gateway_service.websocket_proxy import (
    proxy_ide_websocket,
)


class FakeIDEWebSocket:
    """Simulate an IDE WebSocket connection."""

    def __init__(self):
        # Store messages received from the browser.
        self.received_messages = []

        # Queue messages sent from the IDE to the browser.
        self.outgoing_messages = asyncio.Queue()

        # Track whether the IDE connection was closed.
        self.closed = False

        # Track whether the IDE receiving task was cancelled.
        self.receive_cancelled = False

    async def send(self, message):
        """Receive a message from the browser."""

        self.received_messages.append(message)

        # Simulate an IDE response.
        await self.outgoing_messages.put(
            f"IDE received: {message}"
        )

    def __aiter__(self):
        """Allow asynchronous iteration over IDE messages."""

        return self

    async def __anext__(self):
        """Wait for messages from the IDE."""

        try:
            message = await self.outgoing_messages.get()

        except asyncio.CancelledError:
            # Record that the receiving task was cancelled.
            self.receive_cancelled = True
            raise

        if message is None:
            raise StopAsyncIteration

        return message


class FakeIDEConnection:
    """Simulate the IDE Unix-socket connection."""

    def __init__(self, fake_ide):
        self.fake_ide = fake_ide

    async def __aenter__(self):
        """Open the fake IDE connection."""

        return self.fake_ide

    async def __aexit__(self, exc_type, exc_value, traceback):
        """Record when the connection is closed."""

        self.fake_ide.closed = True

        await self.fake_ide.outgoing_messages.put(None)


class TestIDEWebSocketCleanup(unittest.TestCase):
    """Verify WebSocket forwarding tasks are cleaned up."""

    def setUp(self):
        """Prepare the browser and fake IDE."""

        # Create an isolated sandbox.
        self.sandbox_id = uuid4()

        # Create a valid session.
        self.session_token = ide_session_store.create_session(
            developer_id=12,
            group_id=5,
            sandbox_id=self.sandbox_id,
        )

        # Create a simulated IDE.
        self.fake_ide = FakeIDEWebSocket()

        # Create a test application.
        self.app = FastAPI()

        sandbox_id = self.sandbox_id

        @self.app.websocket("/test-ide/{path:path}")
        async def test_ide_websocket(
            websocket: WebSocket,
            path: str,
        ):
            """Forward messages through the real proxy."""

            session_token = websocket.cookies.get(
                "codeview_ide_session"
            )

            await proxy_ide_websocket(
                websocket=websocket,
                sandbox_id=sandbox_id,
                path=path,
                session_token=session_token,
            )

        # Mock the Unix socket location.
        self.socket_patch = patch(
            "server_runner.ide_gateway_service.websocket_proxy."
            "get_ide_socket_path",
            return_value="/tmp/test-ide.sock",
        )

        # Skip real socket validation.
        self.validation_patch = patch(
            "server_runner.ide_gateway_service.websocket_proxy."
            "validate_ide_socket",
            return_value=None,
        )

        # Replace the real IDE connection.
        self.connection_patch = patch(
            "server_runner.ide_gateway_service.websocket_proxy."
            "unix_connect",
            return_value=FakeIDEConnection(self.fake_ide),
        )

        # Reduce the session monitoring interval.
        self.interval_patch = patch(
            "server_runner.ide_gateway_service.websocket_proxy."
            "SESSION_CHECK_INTERVAL",
            0.05,
        )

        # Activate the patches.
        self.socket_patch.start()
        self.validation_patch.start()
        self.connection_patch.start()
        self.interval_patch.start()

        # Create the browser.
        self.client = TestClient(self.app)

        # Authenticate the browser.
        self.client.cookies.set(
            "codeview_ide_session",
            self.session_token,
            path="/test-ide",
        )

    def tearDown(self):
        """Clean up the test environment."""

        # Revoke the test session.
        ide_session_store.revoke_session(
            self.session_token
        )

        # Close the browser.
        self.client.close()

        # Restore the original functions.
        self.interval_patch.stop()
        self.connection_patch.stop()
        self.validation_patch.stop()
        self.socket_patch.stop()

    def test_browser_disconnect_closes_ide(self):
        """Browser disconnection must close the IDE connection."""

        import threading

        # Track when the fake IDE finishes closing.
        cleanup_complete = threading.Event()

        # Save the original fake connection exit method.
        original_exit = FakeIDEConnection.__aexit__

        async def track_connection_close(
            connection,
            exc_type,
            exc_value,
            traceback,
        ):
            """Record when IDE connection cleanup finishes."""

            try:
                await original_exit(
                    connection,
                    exc_type,
                    exc_value,
                    traceback,
                )
            finally:
                cleanup_complete.set()

        # Monitor cleanup without changing production code.
        with patch.object(
            FakeIDEConnection,
            "__aexit__",
            track_connection_close,
        ):

            with self.client.websocket_connect(
                "/test-ide/editor"
            ) as websocket:

                # Confirm that forwarding works.
                websocket.send_text("Hello CodeView")

                response = websocket.receive_text()

                self.assertEqual(
                    response,
                    "IDE received: Hello CodeView",
                )

                # Send a normal browser disconnect.
                websocket.close(code=1000)

                # Wait until the fake IDE connection is released.
                self.assertTrue(
                    cleanup_complete.wait(timeout=2),
                    "IDE connection cleanup did not finish.",
                )

        # The IDE connection must be closed.
        self.assertTrue(
            self.fake_ide.closed
        )

        # The IDE receiving task must have been cancelled.
        self.assertTrue(
            self.fake_ide.receive_cancelled
        )

    def test_session_revocation_closes_ide(self):
        """Revoking a session must close the IDE connection."""

        from starlette.websockets import WebSocketDisconnect

        with self.client.websocket_connect(
            "/test-ide/editor"
        ) as websocket:

            # Confirm the connection works initially.
            websocket.send_text("Before revocation")

            response = websocket.receive_text()

            self.assertEqual(
                response,
                "IDE received: Before revocation",
            )

            # Revoke the active session.
            ide_session_store.revoke_session(
                self.session_token
            )

            # Wait for the monitor to close the browser connection.
            with self.assertRaises(
                WebSocketDisconnect
            ) as context:

                websocket.receive_text()

            self.assertEqual(
                context.exception.code,
                1008,
            )

        # The upstream IDE connection must be closed.
        self.assertTrue(
            self.fake_ide.closed
        )

    def test_ide_disconnect_closes_connection(self):
        """An IDE disconnect must release the connection."""

        with self.client.websocket_connect(
            "/test-ide/editor"
        ) as websocket:

            # Establish communication.
            websocket.send_text("Hello IDE")

            response = websocket.receive_text()

            self.assertEqual(
                response,
                "IDE received: Hello IDE",
            )

            # Simulate the IDE closing its connection.
            self.fake_ide.outgoing_messages.put_nowait(None)

        # The proxy should exit and close the IDE connection.
        self.assertTrue(
            self.fake_ide.closed
        )


if __name__ == "__main__":
    unittest.main()