"""Forward CodeView IDE WebSocket messages through Unix sockets."""

import asyncio

from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from server_runner.ide_gateway_service.socket_connection import (
    get_ide_socket_path,
    validate_ide_socket,
)


async def proxy_ide_websocket(
    websocket: WebSocket,
    sandbox_id: UUID,
    path: str = "",
) -> None:
    """
    Forward WebSocket messages between a browser and code-server.

    IMPORTANT:
    The caller must authenticate the browser session and validate
    its Origin before calling this function.
    """

    # STEP 1: Reject invalid paths.
    if (
        path.startswith("/")
        or "\\" in path
        or any(part in {".", ".."} for part in path.split("/"))
    ):
        await websocket.close(code=1008)
        return

    # STEP 2: Find the sandbox's Unix socket.
    try:
        socket_path = get_ide_socket_path(sandbox_id)

        # Verify the endpoint is a Unix socket.
        validate_ide_socket(socket_path)

    except (OSError, ValueError):
        await websocket.close(code=1011)
        return

    # STEP 3: Build the upstream WebSocket URL.
    upstream_path = f"/{path}" if path else "/"

    # Preserve the browser's query string.
    query_string = websocket.scope.get("query_string", b"")

    if query_string:
        upstream_path += "?" + query_string.decode("ascii")

    # STEP 4: Connect to code-server through its Unix socket.
    try:
        async with connect(
            f"ws://localhost{upstream_path}",
            unix=True,
            path=str(socket_path),
            open_timeout=10,
            max_size=16 * 1024 * 1024,
        ) as upstream:

            # Only accept the browser after connecting upstream.
            await websocket.accept()

            # STEP 5: Forward browser messages to code-server.
            async def browser_to_ide():

                while True:
                    message = await websocket.receive()

                    if message["type"] == "websocket.disconnect":
                        break

                    if message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

                    elif message.get("text") is not None:
                        await upstream.send(message["text"])

            # STEP 6: Forward code-server messages to the browser.
            async def ide_to_browser():

                async for message in upstream:

                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)

                    else:
                        await websocket.send_text(message)

            # STEP 7: Run both directions concurrently.
            tasks = [
                asyncio.create_task(browser_to_ide()),
                asyncio.create_task(ide_to_browser()),
            ]

            try:
                # Stop when either direction finishes.
                await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )

            finally:
                # Cancel any remaining forwarding tasks.
                for task in tasks:
                    task.cancel()

                await asyncio.gather(
                    *tasks,
                    return_exceptions=True,
                )

    except (
        OSError,
        ConnectionClosed,
        TimeoutError,
        ValueError,
    ):
        # The connection may already be closed.
        try:
            await websocket.close(code=1011)
        except RuntimeError:
            pass

    except WebSocketDisconnect:
        # The browser disconnected normally.
        pass
        