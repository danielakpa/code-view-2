"""Test active WebSocket session security in CodeView."""

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
    """Simulate a connected VS Code IDE."""

    def __init__(self):
        # Store messages received by the fake IDE.
        self.received_messages = []

        # Store messages waiting to be sent to the browser.
        self.outgoing_messages = asyncio.Queue()

    async def send(self, message):
        """Receive messages forwarded from the browser."""

        self.received_messages.append(message)

        # Respond to the browser.
        await self.outgoing_messages.put(
            f"IDE received: {message}"
        )

    def __aiter__(self):
        """Allow the proxy to receive IDE messages."""

        return self

    async def __anext__(self):
        """Wait for a message from the IDE."""

        message = await self.outgoing_messages.get()

        if message is None:
            raise StopAsyncIteration

        return message


class FakeIDEConnection:
    """Simulate the IDE's Unix-socket connection."""

    def __init__(self, fake_ide):
        self.fake_ide = fake_ide

    async def __aenter__(self):
        """Open the simulated connection."""

        return self.fake_ide

    async def __aexit__(self, exc_type, exc_value, traceback):
        """Close the simulated connection."""

        await self.fake_ide.outgoing_messages.put(None)


class TestIDEWebSocketSession(unittest.TestCase):
    """Verify security for active IDE WebSocket sessions."""

    def setUp(self):
        """Prepare the test sandbox and browser."""

        # Create a unique sandbox.
        self.sandbox_id = uuid4()

        # Create an authenticated IDE session.
        self.session_token = ide_session_store.create_session(
            self.sandbox_id
        )

        # Create a simulated IDE.
        self.fake_ide = FakeIDEWebSocket()

        # Create an isolated FastAPI application.
        self.app = FastAPI()

        sandbox_id = self.sandbox_id
        @self.app.websocket("/test-ide/{path:path}")
        async def test_ide_websocket(
            websocket: WebSocket,
            path: str,
        ):
            """Connect the test browser to the IDE proxy."""

            session_token = websocket.cookies.get(
                "codeview_ide_session"
            )

            await proxy_ide_websocket(
                websocket=websocket,
                sandbox_id=sandbox_id,
                path=path,
                session_token=session_token,
            )

        # Avoid accessing a real Unix socket.
        self.socket_patch = patch(
            "server_runner.ide_gateway_service.websocket_proxy."
            "get_ide_socket_path",
            return_value="/tmp/test-ide.sock",
        )

        self.validation_patch = patch(
            "server_runner.ide_gateway_service.websocket_proxy."
            "validate_ide_socket",
            return_value=None,
        )

        # Replace the real IDE with our fake connection.
        self.connection_patch = patch(
            "server_runner.ide_gateway_service.websocket_proxy."
            "unix_connect",
            return_value=FakeIDEConnection(self.fake_ide),
        )

        # Make session monitoring fast during tests.
        self.interval_patch = patch(
            "server_runner.ide_gateway_service.websocket_proxy."
            "SESSION_CHECK_INTERVAL",
            0.05,
        )

        # Activate all test patches.
        self.socket_patch.start()
        self.validation_patch.start()
        self.connection_patch.start()
        self.interval_patch.start()

        # Create the test browser.
        self.client = TestClient(self.app)

        # Attach the authenticated session cookie.
        self.client.cookies.set(
            "codeview_ide_session",
            self.session_token,
            path="/test-ide",
        )
    def test_expired_session_disconnects_active_websocket(self):
        """An expired session must disconnect an active WebSocket."""

        # Establish a valid WebSocket connection.
        with self.client.websocket_connect(
            "/test-ide/editor"
        ) as websocket:

            # Confirm that the connection works initially.
            websocket.send_text("Before expiration")

            response = websocket.receive_text()

            self.assertEqual(
                response,
                "IDE received: Before expiration",
            )

            # Simulate session expiration.
            # Every future session lookup will return None.
            with patch.object(
                ide_session_store,
                "get_session",
                return_value=None,
            ):

                # Wait for the session monitor to detect expiration.
                with self.assertRaises(
                    WebSocketDisconnect
                ) as context:

                    websocket.receive_text()

                # The Gateway must close the connection.
                self.assertEqual(
                    context.exception.code,
                    1008,
                )
    def test_browser_disconnect_stops_websocket_forwarding(self):
        """Closing the browser connection stops IDE forwarding."""

        # Open an authenticated WebSocket connection.
        with self.client.websocket_connect(
            "/test-ide/editor"
        ) as websocket:

            # Send a message to the IDE.
            websocket.send_text("Before disconnect")

            # Confirm that the IDE responded.
            response = websocket.receive_text()

            self.assertEqual(
                response,
                "IDE received: Before disconnect",
            )

            # Explicitly tell the Gateway that the browser is closing.
            websocket.close(code=1000)

        # Verify that the IDE received the original message.
        self.assertIn(
            "Before disconnect",
            self.fake_ide.received_messages,
        )

    def tearDown(self):
        """Clean up after each test."""

        # Revoke the test session.
        ide_session_store.revoke_session(
            self.session_token
        )

        # Close the test browser.
        self.client.close()

        # Restore the original functions.
        self.interval_patch.stop()
        self.connection_patch.stop()
        self.validation_patch.stop()
        self.socket_patch.stop()

    def test_revoked_session_disconnects_active_websocket(self):
        """Revoking a session must disconnect an active WebSocket."""

        # Establish an authenticated WebSocket connection.
        with self.client.websocket_connect(
            "/test-ide/editor"
        ) as websocket:

            # Verify that communication works initially.
            websocket.send_text("Hello CodeView")

            response = websocket.receive_text()

            self.assertEqual(
                response,
                "IDE received: Hello CodeView",
            )

            # Revoke the session while the WebSocket is open.
            ide_session_store.revoke_session(
                self.session_token
            )

            # The monitoring task should close the connection.
            with self.assertRaises(
                WebSocketDisconnect
            ) as context:

                websocket.receive_text()

            # 1008 means the connection violated a security policy.
            self.assertEqual(
                context.exception.code,
                1008,
            )

        # Confirm the IDE received the initial message.
        self.assertIn(
            "Hello CodeView",
            self.fake_ide.received_messages,
        )

    def test_invalid_session_cannot_connect(self):
        """A revoked session must not establish a new connection."""

        # Revoke the session before connecting.
        ide_session_store.revoke_session(
            self.session_token
        )

        # Attempt to open the IDE.
        with self.assertRaises(
            WebSocketDisconnect
        ) as context:

            with self.client.websocket_connect(
                "/test-ide/editor"
            ):
                pass

        # The Gateway should reject the connection.
        self.assertEqual(
            context.exception.code,
            1008,
        )

    def test_session_cannot_access_another_sandbox(self):
        """A session must not connect to another developer's IDE."""

        # Create another sandbox belonging to a different developer.
        another_sandbox_id = uuid4()

        # Create a separate test application.
        other_app = FastAPI()

        @other_app.websocket("/other-ide")
        async def other_ide(websocket: WebSocket):
            """Attempt to access a different sandbox."""

            await proxy_ide_websocket(
                websocket=websocket,
                sandbox_id=another_sandbox_id,
                session_token=self.session_token,
            )

        with TestClient(other_app) as other_client:

            # Attempt to connect using the first developer's session.
            with self.assertRaises(
                WebSocketDisconnect
            ) as context:

                with other_client.websocket_connect(
                    "/other-ide"
                ):
                    pass

            # Reject access to another developer's sandbox.
            self.assertEqual(
                context.exception.code,
                1008,
            )


if __name__ == "__main__":
    unittest.main()