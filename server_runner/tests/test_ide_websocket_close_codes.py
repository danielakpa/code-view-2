"""Test WebSocket close codes in the CodeView IDE Gateway."""

import asyncio
import unittest

from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server_runner.ide_gateway_service.sessions import (
    ide_session_store,
)

from server_runner.ide_gateway_service.websocket_proxy import (
    proxy_ide_websocket,
)


class FakeIDEWebSocket:
    """Simulate the developer's IDE WebSocket connection."""

    def __init__(self):
        # Messages waiting to be sent from the IDE to the browser.
        self.outgoing_messages = asyncio.Queue()

        # Track whether the upstream IDE connection closes.
        self.closed = False

    async def send(self, message):
        """Receive a browser message and respond."""

        await self.outgoing_messages.put(
            f"IDE received: {message}"
        )

    def __aiter__(self):
        """Allow the Gateway to read IDE messages."""

        return self

    async def __anext__(self):
        """Wait for the next IDE message."""

        message = await self.outgoing_messages.get()

        # None represents the IDE closing its connection.
        if message is None:
            raise StopAsyncIteration

        return message


class FakeIDEConnection:
    """Simulate an IDE connection through a Unix socket."""

    def __init__(self, fake_ide):
        self.fake_ide = fake_ide

    async def __aenter__(self):
        """Open the fake IDE connection."""

        return self.fake_ide

    async def __aexit__(self, exc_type, exc_value, traceback):
        """Record when the IDE connection closes."""

        self.fake_ide.closed = True


class TestIDEWebSocketCloseCodes(unittest.TestCase):
    """Verify the Gateway's WebSocket close behavior."""

    def setUp(self):
        """Create an authenticated test environment."""

              # STEP 1: Define the developer and group identities.
        self.developer_id = 12
        self.group_id = 5

        # STEP 2: Create a unique developer sandbox.
        self.sandbox_id = uuid4()

        # STEP 3: Generate the developer's membership generation.
        self.membership_generation = uuid4()

        # STEP 4: Create a valid IDE session with all four identities.
        self.session_token = ide_session_store.create_session(
            developer_id=self.developer_id,
            group_id=self.group_id,
            sandbox_id=self.sandbox_id,
            membership_generation=self.membership_generation,
        )

        # Create the simulated IDE.
        self.fake_ide = FakeIDEWebSocket()

        # Create an isolated FastAPI application.
        self.app = FastAPI()

        sandbox_id = self.sandbox_id

        @self.app.websocket("/test-ide/{path:path}")
        async def test_ide_websocket(
            websocket: WebSocket,
            path: str,
        ):
            """Connect the browser through the real IDE proxy."""

            session_token = websocket.cookies.get(
                "codeview_ide_session"
            )

            await proxy_ide_websocket(
                websocket=websocket,
                sandbox_id=sandbox_id,
                path=path,
                session_token=session_token,
            )

        # Replace the real Unix socket path.
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

        # Replace the real IDE connection with our fake IDE.
        self.connection_patch = patch(
            "server_runner.ide_gateway_service.websocket_proxy."
            "unix_connect",
            return_value=FakeIDEConnection(self.fake_ide),
        )

        # Make session checks fast during tests.
        self.interval_patch = patch(
            "server_runner.ide_gateway_service.websocket_proxy."
            "SESSION_CHECK_INTERVAL",
            0.05,
        )

        # Activate the test patches.
        self.socket_patch.start()
        self.validation_patch.start()
        self.connection_patch.start()
        self.interval_patch.start()

        # Create the browser test client.
        self.client = TestClient(self.app)

        # Attach the authenticated IDE session cookie.
        self.client.cookies.set(
            "codeview_ide_session",
            self.session_token,
            path="/test-ide",
        )

    def tearDown(self):
        """Clean up test resources."""

        # Revoke the test session.
        ide_session_store.revoke_session(
            self.session_token
        )

        # Close the browser test client.
        self.client.close()

        # Restore the original functions.
        self.interval_patch.stop()
        self.connection_patch.stop()
        self.validation_patch.stop()
        self.socket_patch.stop()

    def test_ide_disconnect_returns_1011(self):
        """An unexpected IDE disconnection returns code 1011."""

        with self.client.websocket_connect(
            "/test-ide/editor"
        ) as websocket:

            # Confirm that the connection works.
            websocket.send_text("Hello IDE")

            response = websocket.receive_text()

            self.assertEqual(
                response,
                "IDE received: Hello IDE",
            )

            # Simulate the IDE connection ending.
            self.fake_ide.outgoing_messages.put_nowait(None)

            # The Gateway should close the browser connection.
            with self.assertRaises(
                WebSocketDisconnect
            ) as context:

                websocket.receive_text()

            # Verify the close code.
            self.assertEqual(
                context.exception.code,
                1011,
            )

        # The upstream connection should be cleaned up.
        self.assertTrue(
            self.fake_ide.closed
        )

    def test_session_revocation_returns_1008(self):
        """Revoking an active IDE session returns code 1008."""

        with self.client.websocket_connect(
            "/test-ide/editor"
        ) as websocket:

            # Confirm that the connection works.
            websocket.send_text("Before revocation")

            response = websocket.receive_text()

            self.assertEqual(
                response,
                "IDE received: Before revocation",
            )

            # Revoke the developer's session.
            ide_session_store.revoke_session(
                self.session_token
            )

            # The session monitor should close the connection.
            with self.assertRaises(
                WebSocketDisconnect
            ) as context:

                websocket.receive_text()

            self.assertEqual(
                context.exception.code,
                1008,
            )

        # The upstream IDE connection should be released.
        self.assertTrue(
            self.fake_ide.closed
        )

    def test_invalid_session_returns_1008(self):
        """An invalid session must be rejected with code 1008."""

        # Revoke the session before opening the WebSocket.
        ide_session_store.revoke_session(
            self.session_token
        )

        with self.assertRaises(
            WebSocketDisconnect
        ) as context:

            with self.client.websocket_connect(
                "/test-ide/editor"
            ):
                pass

        self.assertEqual(
            context.exception.code,
            1008,
        )

    def test_browser_normal_disconnect_cleans_up(self):
        """A normal browser disconnect releases the IDE connection."""

        import threading

        # Create a signal to track IDE connection cleanup.
        cleanup_complete = threading.Event()

        # Save the original connection cleanup method.
        original_exit = FakeIDEConnection.__aexit__

        async def track_connection_close(
            connection,
            exc_type,
            exc_value,
            traceback,
        ):
            """Signal when the fake IDE connection finishes closing."""

            try:
                # Run the original cleanup method.
                await original_exit(
                    connection,
                    exc_type,
                    exc_value,
                    traceback,
                )

            finally:
                # Tell the test that cleanup has finished.
                cleanup_complete.set()

        # Monitor IDE cleanup during this test.
        with patch.object(
            FakeIDEConnection,
            "__aexit__",
            track_connection_close,
        ):

            # Open an authenticated WebSocket connection.
            with self.client.websocket_connect(
                "/test-ide/editor"
            ) as websocket:

                # Verify that communication works.
                websocket.send_text("Hello CodeView")

                response = websocket.receive_text()

                self.assertEqual(
                    response,
                    "IDE received: Hello CodeView",
                )

                # Explicitly disconnect the browser normally.
                websocket.close(code=1000)

                # Wait until the Gateway releases the IDE connection.
                self.assertTrue(
                    cleanup_complete.wait(timeout=2),
                    "IDE connection cleanup did not finish.",
                )

        # Confirm the upstream IDE connection was closed.
        self.assertTrue(
            self.fake_ide.closed
        )

if __name__ == "__main__":
    unittest.main()