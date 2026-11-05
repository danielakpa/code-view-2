"""Secure WebSocket forwarding between CodeView and developer IDEs."""

import asyncio
import logging

from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from websockets.asyncio.client import unix_connect
from websockets.exceptions import ConnectionClosed

from server_runner.ide_gateway_service.socket_connection import (
    get_ide_socket_path,
    validate_ide_socket,
)

from server_runner.ide_gateway_service.sessions import (
    ide_session_store,
)


# Create a logger for WebSocket Gateway errors.
logger = logging.getLogger(__name__)

# Check the developer's session every 5 seconds.
SESSION_CHECK_INTERVAL = 5


async def close_websocket_safely(
    websocket: WebSocket,
    code: int = 1000,
) -> None:
    """Close a WebSocket without raising expected disconnect errors."""

    # Avoid sending a second close frame.
    if websocket.application_state == WebSocketState.DISCONNECTED:
        return

    try:
        await websocket.close(code=code)

    except (RuntimeError, OSError):
        # The browser may already have disconnected.
        pass


async def proxy_ide_websocket(
    websocket: WebSocket,
    sandbox_id: UUID,
    path: str = "",
    session_token: str | None = None,
) -> None:
    """Forward IDE messages while checking session authorization."""

    # 1. Reject unsafe resource paths.
    if (
        path.startswith("/")
        or "\\" in path
        or any(part in {".", ".."} for part in path.split("/"))
    ):
        await close_websocket_safely(websocket, code=1008)
        return

    # 2. Require an authenticated IDE session.
    if not session_token:
        await close_websocket_safely(websocket, code=1008)
        return

    session = ide_session_store.get_session(session_token)

    # Reject sessions belonging to another sandbox.
    if session is None or session.sandbox_id != sandbox_id:
        await close_websocket_safely(websocket, code=1008)
        return

    # 3. Locate and validate the developer's Unix socket.
    try:
        socket_path = get_ide_socket_path(sandbox_id)

        validate_ide_socket(socket_path)

    except (OSError, ValueError):
        logger.exception(
            "Unable to access IDE socket for sandbox %s",
            sandbox_id,
        )

        await close_websocket_safely(websocket, code=1011)
        return

    # 4. Construct the upstream WebSocket resource path.
    upstream_path = f"/{path}" if path else "/"

    query_string = websocket.scope.get("query_string", b"")

    if query_string:
        upstream_path += "?" + query_string.decode("ascii")

    try:
        # 5. Connect to the developer's IDE through its Unix socket.
        async with unix_connect(
            path=str(socket_path),
            uri=f"ws://localhost{upstream_path}",
            open_timeout=10,
            max_size=16 * 1024 * 1024,
        ) as upstream:

            # Accept the authenticated browser connection.
            await websocket.accept()

            async def browser_to_ide():
                """Forward browser messages to the IDE."""

                while True:
                    try:
                        # Wait for a browser message.
                        message = await websocket.receive()

                    except WebSocketDisconnect:
                        # The browser disconnected normally.
                        return

                    except asyncio.CancelledError:
                        # Preserve task cancellation.
                        raise

                    # Stop when the browser disconnects.
                    if message["type"] == "websocket.disconnect":
                        return

                    # Forward binary messages.
                    if message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

                    # Forward text messages.
                    elif message.get("text") is not None:
                        await upstream.send(message["text"])

            async def ide_to_browser():
                """Forward IDE messages to the browser."""

                async for message in upstream:

                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)

                    else:
                        await websocket.send_text(message)

            async def monitor_session():
                """Monitor the developer's authorization."""

                while True:
                    await asyncio.sleep(SESSION_CHECK_INTERVAL)

                    current_session = ide_session_store.get_session(
                        session_token
                    )

                    # Stop if the session expires or is revoked.
                    if (
                        current_session is None
                        or current_session.sandbox_id != sandbox_id
                    ):
                        return

            # 6. Start the forwarding and monitoring tasks.
            tasks = [
                asyncio.create_task(browser_to_ide()),
                asyncio.create_task(ide_to_browser()),
                asyncio.create_task(monitor_session()),
            ]

            try:
                # Wait until one task finishes.
                done, pending = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                browser_task = tasks[0]
                ide_task = tasks[1]
                monitor_task = tasks[2]

                # Propagate unexpected errors from completed tasks.
                for task in done:
                    if not task.cancelled():
                        error = task.exception()

                        if error is not None:
                            raise error

                # Check authorization before choosing a close code.
                current_session = ide_session_store.get_session(
                    session_token
                )

                session_invalid = (
                    current_session is None
                    or current_session.sandbox_id != sandbox_id
                )

                if session_invalid or monitor_task in done:
                    # Session expired or was revoked.
                    await close_websocket_safely(
                        websocket,
                        code=1008,
                    )

                elif ide_task in done:
                    # The IDE connection ended unexpectedly.
                    await close_websocket_safely(
                        websocket,
                        code=1011,
                    )

                elif browser_task in done:
                    # Normal browser disconnect.
                    pass

            finally:
                # Cancel unfinished background tasks.
                for task in tasks:
                    if not task.done():
                        task.cancel()

                # Wait for background tasks to finish.
                await asyncio.gather(
                    *tasks,
                    return_exceptions=True,
                )

    except WebSocketDisconnect:
        # The browser disconnected.
        return

    except asyncio.CancelledError:
        # Preserve cancellation during ASGI server shutdown.
        raise

    except (OSError, ConnectionClosed, TimeoutError, ValueError):
        # Handle known IDE connection failures.
        logger.exception(
            "IDE WebSocket connection failed for sandbox %s",
            sandbox_id,
        )

        await close_websocket_safely(
            websocket,
            code=1011,
        )

    except Exception:
        # Log unexpected server-side errors.
        # Internal exception details are not sent to the browser.
        logger.exception(
            "Unexpected IDE WebSocket error for sandbox %s",
            sandbox_id,
        )

        # Notify the browser of an internal server error.
        await close_websocket_safely(
            websocket,
            code=1011,
        )