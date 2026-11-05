"""Test simultaneous WebSocket events in the CodeView IDE Gateway."""

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
    """Simulate an IDE WebSocket connection."""

    def __init__(self):
        # Store messages waiting to reach the browser.
        self.outgoing_messages = asyncio.Queue()

        # Track whether the IDE connection was cleaned up.
        self.closed = False

        # Allow tests to trigger an IDE forwarding error.
        self.send_error = None

    async def send(self, message):
        """Receive a browser message."""

        # Simulate an unexpected forwarding failure.
        if self.send_error is not None:
            raise self.send_error

        # Return a response to the browser.
        await self.outgoing_messages.put(
            f"IDE received: {message}"
        )

    def __aiter__(self):
        """Allow asynchronous iteration over IDE messages."""
        return self

    async def __anext__(self):
        """Wait for the next IDE message."""

        message = await self.outgoing_messages.get()

        # None represents the IDE disconnecting.
        if message is None:
            raise StopAsyncIteration

        return message


class FakeIDEConnection:
    """Simulate the Unix socket connection to an IDE."""

    def __init__(self, fake_ide):
        self.fake_ide = fake_ide

    async def __aenter__(self):
        """Open the simulated IDE connection."""
        return self.fake_ide

    async def __aexit__(self, exc_type, exc_value, traceback):
        """Close the simulated IDE connection."""

        self.fake_ide.closed = True


class TestIDEWebSocketRaceConditions(unittest.TestCase):
    """Test competing WebSocket shutdown events."""

    def setUp(self):
        """Create an authenticated IDE Gateway test environment."""

        # Create a unique developer sandbox.
               # STEP 1: Define the developer and group identities.
        self.developer_id = 12
        self.group_id = 5

        # STEP 2: Create a unique developer sandbox.
        self.sandbox_id = uuid4()

        # STEP 3: Generate the developer's membership generation.
        self.membership_generation = uuid4()

        # STEP 4: Create a valid session with all four identities.
        self.session_token = ide_session_store.create_session(
            developer_id=self.developer_id,
            group_id=self.group_id,
            sandbox_id=self.sandbox_id,
            membership_generation=self.membership_generation,
        )

        # Create the fake IDE.
        self.fake_ide = FakeIDEWebSocket()

        # Create an isolated FastAPI application.
        self.app = FastAPI()

        sandbox_id = self.sandbox_id

        @self.app.websocket("/test-ide/{path:path}")
        async def test_websocket(
            websocket: WebSocket,
            path: str,
        ):
            """Forward browser traffic through the real proxy."""

            session_token = websocket.cookies.get(
                "codeview_ide_session"
            )

            await proxy_ide_websocket(
                websocket=websocket,
                sandbox_id=sandbox_id,
                path=path,
                session_token=session_token,
            )

        # Replace the real socket path.
        self.socket_patch = patch(
            "server_runner.ide_gateway_service.websocket_proxy."
            "get_ide_socket_path",
            return_value="/tmp/test-ide.sock",
        )

        # Skip filesystem socket validation.
        self.validation_patch = patch(
            "server_runner.ide_gateway_service.websocket_proxy."
            "validate_ide_socket",
            return_value=None,
        )

        # Use our simulated IDE connection.
        self.connection_patch = patch(
            "server_runner.ide_gateway_service.websocket_proxy."
            "unix_connect",
            return_value=FakeIDEConnection(self.fake_ide),
        )

        # Check sessions frequently during tests.
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

        # Create the browser test client.
        self.client = TestClient(self.app)

        # Attach the authenticated session cookie.
        self.client.cookies.set(
            "codeview_ide_session",
            self.session_token,
            path="/test-ide",
        )

    def tearDown(self):
        """Release all test resources."""

        # Remove the developer's session.
        ide_session_store.revoke_session(
            self.session_token
        )

        # Close the test client.
        self.client.close()

        # Restore the original functions.
        self.interval_patch.stop()
        self.connection_patch.stop()
        self.validation_patch.stop()
        self.socket_patch.stop()

    def test_revocation_and_ide_disconnect(self):
        """Session revocation must take priority over IDE closure."""

        with self.client.websocket_connect(
            "/test-ide/editor"
        ) as websocket:

            # Confirm that communication works.
            websocket.send_text("Hello IDE")

            self.assertEqual(
                websocket.receive_text(),
                "IDE received: Hello IDE",
            )

            # Revoke authorization.
            ide_session_store.revoke_session(
                self.session_token
            )

            # Immediately simulate the IDE disconnecting.
            self.fake_ide.outgoing_messages.put_nowait(None)

            # The Gateway should prioritize authorization loss.
            with self.assertRaises(
                WebSocketDisconnect
            ) as context:

                websocket.receive_text()

            self.assertEqual(
                context.exception.code,
                1008,
            )

        # Confirm that the upstream connection was closed.
        self.assertTrue(self.fake_ide.closed)

    def test_ide_disconnect_with_valid_session(self):
        """An IDE disconnect should return 1011 if authorized."""

        with self.client.websocket_connect(
            "/test-ide/editor"
        ) as websocket:

            # Verify that the connection works.
            websocket.send_text("Hello")

            self.assertEqual(
                websocket.receive_text(),
                "IDE received: Hello",
            )

            # Disconnect the IDE without revoking authorization.
            self.fake_ide.outgoing_messages.put_nowait(None)

            with self.assertRaises(
                WebSocketDisconnect
            ) as context:

                websocket.receive_text()

            self.assertEqual(
                context.exception.code,
                1011,
            )

        self.assertTrue(self.fake_ide.closed)

    def test_forwarding_error_closes_connection(self):
        """A forwarding error should close the browser with 1011."""

        # Make the fake IDE fail when it receives a message.
        self.fake_ide.send_error = OSError(
            "Simulated IDE forwarding failure"
        )

        with self.client.websocket_connect(
            "/test-ide/editor"
        ) as websocket:

            # Trigger the forwarding error.
            websocket.send_text("Trigger error")

            # The Gateway should close the browser.
            with self.assertRaises(
                WebSocketDisconnect
            ) as context:

                websocket.receive_text()

            self.assertEqual(
                context.exception.code,
                1011,
            )

        # Confirm cleanup happened.
        self.assertTrue(self.fake_ide.closed)


    def test_unexpected_forwarding_error_closes_connection(self):
        """Unexpected forwarding errors must close the browser with 1011."""

        # Simulate an unexpected programming error inside the IDE.
        self.fake_ide.send_error = RuntimeError(
            "Simulated unexpected forwarding error"
        )

        # Open an authenticated WebSocket connection.
        with self.client.websocket_connect(
            "/test-ide/editor"
        ) as websocket:

            # Send a message that triggers the unexpected error.
            websocket.send_text("Trigger unexpected error")

            # The Gateway must close the browser connection.
            with self.assertRaises(
                WebSocketDisconnect
            ) as context:

                websocket.receive_text()

            # 1011 means an internal server error occurred.
            self.assertEqual(
                context.exception.code,
                1011,
            )

        # Verify that the upstream IDE connection was released.
        self.assertTrue(
            self.fake_ide.closed
        )

    def test_server_cancellation_cleans_up_ide(self):
        """Cancelling the Gateway must release the IDE connection."""

        from unittest.mock import AsyncMock

        from server_runner.ide_gateway_service import (
            websocket_proxy,
        )

        async def run_cancellation_test():
            """Run the proxy and cancel its main task."""

            # Create a simulated browser WebSocket.
            browser = AsyncMock(spec=WebSocket)

            # Pretend the browser is already connected.
            browser.scope = {
                "query_string": b"",
            }

            # Keep browser receiving active until cancellation.
            async def wait_for_browser_message():
                await asyncio.Event().wait()

            browser.receive.side_effect = wait_for_browser_message

            # Track when the proxy accepts the browser.
            browser_accepted = asyncio.Event()

            async def accept_browser():
                browser_accepted.set()

            browser.accept.side_effect = accept_browser

            # Start the actual CodeView WebSocket proxy.
            proxy_task = asyncio.create_task(
                websocket_proxy.proxy_ide_websocket(
                    websocket=browser,
                    sandbox_id=self.sandbox_id,
                    path="editor",
                    session_token=self.session_token,
                )
            )

            try:
                # Wait until the browser has been accepted.
                await asyncio.wait_for(
                    browser_accepted.wait(),
                    timeout=2,
                )

                # Cancel the main proxy task.
                proxy_task.cancel()

                # Cancellation must propagate to the caller.
                with self.assertRaises(asyncio.CancelledError):
                    await proxy_task

                # The upstream IDE connection must be closed.
                self.assertTrue(
                    self.fake_ide.closed
                )

            finally:
                # Avoid leaving the proxy running if an assertion fails.
                if not proxy_task.done():
                    proxy_task.cancel()

                await asyncio.gather(
                    proxy_task,
                    return_exceptions=True,
                )

        # Run the asynchronous test.
        asyncio.run(run_cancellation_test())

    def test_server_cancellation_stops_all_background_tasks(self):
        """Server cancellation must stop all WebSocket background tasks."""

        from unittest.mock import AsyncMock

        from server_runner.ide_gateway_service import (
            websocket_proxy,
        )

        async def run_test():
            """Start the proxy, cancel it, and inspect its tasks."""

            # Simulate a browser connection.
            browser = AsyncMock(spec=WebSocket)

            browser.scope = {
                "query_string": b"",
            }

            # Keep the browser waiting for incoming messages.
            async def wait_for_browser_message():
                await asyncio.Event().wait()

            browser.receive.side_effect = wait_for_browser_message

            # Track when the browser is accepted.
            browser_accepted = asyncio.Event()

            async def accept_browser():
                browser_accepted.set()

            browser.accept.side_effect = accept_browser

            # Record tasks created by the Gateway.
            created_tasks = []

            # Save the original asyncio.create_task function.
            original_create_task = asyncio.create_task

            def track_task(coroutine):
                """Record each background task created by the proxy."""

                task = original_create_task(coroutine)

                created_tasks.append(task)

                return task

            # Start the actual WebSocket proxy.
            proxy_task = original_create_task(
                websocket_proxy.proxy_ide_websocket(
                    websocket=browser,
                    sandbox_id=self.sandbox_id,
                    path="editor",
                    session_token=self.session_token,
                )
            )

            try:
                # Track tasks created while the proxy runs.
                with patch.object(
                    websocket_proxy.asyncio,
                    "create_task",
                    side_effect=track_task,
                ):
                    await asyncio.wait_for(
                        browser_accepted.wait(),
                        timeout=2,
                    )

                    # Cancel the running Gateway.
                    proxy_task.cancel()

                    # Cancellation must propagate.
                    with self.assertRaises(
                        asyncio.CancelledError
                    ):
                        await proxy_task

                # The Gateway creates exactly three background tasks.
                self.assertEqual(
                    len(created_tasks),
                    3,
                )

                # Every background task must be finished.
                for task in created_tasks:
                    self.assertTrue(
                        task.done(),
                    )

                    self.assertTrue(
                        task.cancelled(),
                    )

                # The IDE connection must also be closed.
                self.assertTrue(
                    self.fake_ide.closed
                )

            finally:
                # Ensure the proxy is not left running.
                if not proxy_task.done():
                    proxy_task.cancel()

                await asyncio.gather(
                    proxy_task,
                    return_exceptions=True,
                )

        asyncio.run(run_test())

if __name__ == "__main__":
    unittest.main()